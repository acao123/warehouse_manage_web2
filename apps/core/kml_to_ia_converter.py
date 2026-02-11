#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
KML转Ia(阿里亚斯强度)栅格文件处理模块

功能：将地震局提供的KML格式PGA等值线文件转换为Ia.tif栅格文件
处理流程：
    1. 解析KML文件获取PGA等值线（LineString）
    2. 将PGA值(g单位)转换为实际加速度值(m/s²)
    3. 根据公式 log10(Ia) = a + b * log10(PGA) 计算Ia值
    4. 使用克里金/RBF/IDW插值生成栅格
    5. 可选输出PGA.tif和Ia.tif
    6. 打印插值计算到输出文件的耗时

作者：Copilot
日期：2026-02-09
QGIS版本：3.40.15

投影说明：
    根据数据经度范围的中心经度，自动选择对应的UTM投影带。
    中国大陆经度范围约73°E~135°E，对应UTM带号13N~53N。
    计算方式：utm_zone = int((lon_center + 180) / 6) + 1
    北半球EPSG编码：326xx（如Zone 48N → EPSG:32648）
    例如：经度中心103°时，utm_zone = int((103+180)/6)+1 = 48 → EPSG:32648
"""

import os
import time
import math
import numpy as np
from typing import Tuple, List, Optional, Literal
from xml.etree import ElementTree as ET
from osgeo import gdal, osr, ogr
from pykrige.ok import OrdinaryKriging
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree

# ==================== 启用GDAL异常处理 ====================
gdal.UseExceptions()


class KmlToIaConverter:
    """
    KML文件转Ia(阿里亚斯强度)栅格文件转换器

    将地震局提供的KML格式PGA等值线文件，经过解析、坐标转换、
    插值计算后，输出Ia.tif栅格文件（可选输出PGA.tif）。

    属性:
        kml_path (str): 输入KML文件路径
        pga_output_path (str): 输出PGA栅格文件路径
        ia_output_path (str): 输出Ia栅格文件路径
        resolution (float): 目标分辨率(米)，默认30
        sample_interval (int): 等值线采样间隔，每隔多少个坐标点取一个，默认5
        n_closest_points (int): 克里金局部插值使用的最近点数，默认80
        export_pga (bool): 是否���出PGA.tif文件，默认True
        interp_method (str): 插值方法，可选 'kriging'、'rbf'、'idw'，默认'kriging'
        idw_power (float): IDW插值的幂次参数，默认2.0
        variogram_model (str): 克里金变异函数模型，默认'spherical'
        nlags (int): 克里金变异函数计算的滞后数，默认6

    用法示例:
        converter = KmlToIaConverter(
            kml_path="../../data/geology/kml/source.kml",
            pga_output_path="../../data/geology/ia/PGA.tif",
            ia_output_path="../../data/geology/ia/Ia.tif",
            resolution=30,
            export_pga=True,
            interp_method='kriging'
        )
        converter.run()
    """

    # ==================== 常量定义 ====================
    GRAVITY_ACCELERATION = 9.8   # 重力加速度 (m/s²)
    COEFFICIENT_A = 0.797        # Ia计算公式系数a
    COEFFICIENT_B = 1.837        # Ia计算公式系数b
    KML_NAMESPACE = {'kml': 'http://www.opengis.net/kml/2.2'}

    def __init__(
        self,
        kml_path: str,
        pga_output_path: str,
        ia_output_path: str,
        resolution: float = 30.0,
        sample_interval: int = 5,
        n_closest_points: int = 80,
        export_pga: bool = True,
        interp_method: Literal['kriging', 'rbf', 'idw'] = 'kriging',
        idw_power: float = 2.0,
        variogram_model: str = 'spherical',
        nlags: int = 6
    ):
        """
        初始化转换器

        参数:
            kml_path (str): 输入KML文件路径
            pga_output_path (str): 输出PGA栅格文件路径
            ia_output_path (str): 输出Ia栅格文件路径
            resolution (float): 目标分辨率(米)，默认30
            sample_interval (int): 等值线坐标采样间隔，默认5
            n_closest_points (int): 克里金/IDW局部最近点数，默认80
            export_pga (bool): 是否输出PGA.tif，默认True
            interp_method (str): 插值方法 'kriging'|'rbf'|'idw'，默认'kriging'
            idw_power (float): IDW幂次参数，默认2.0
            variogram_model (str): 克里金变异函数模型，默认'spherical'
            nlags (int): 克里金滞后数，默认6
        """
        self.kml_path = kml_path
        self.pga_output_path = pga_output_path
        self.ia_output_path = ia_output_path
        self.resolution = resolution
        self.sample_interval = sample_interval
        self.n_closest_points = n_closest_points
        self.export_pga = export_pga
        self.interp_method = interp_method
        self.idw_power = idw_power
        self.variogram_model = variogram_model
        self.nlags = nlags

        # 运行时数据（由 run() 过程填充）
        self._contours: List[dict] = []
        self._utm_epsg: int = 0
        self._utm_srs: Optional[osr.SpatialReference] = None
        self._transformer = None
        self._geo_transform: Optional[tuple] = None
        self._n_cols: int = 0
        self._n_rows: int = 0

    # ==================== KML 解析 ====================

    def parse_kml(self) -> List[dict]:
        """
        解析KML文件，提取所有PGA等值线数据

        KML中每个Placemark的name字段格式为 "0.01g"、"0.02g" 等，
        表示该等值线对应的PGA值（以重力加速度g为单位）。
        坐标存储在LineString/coordinates中，格式为 "lon,lat,alt lon,lat,alt ..."

        返回:
            list[dict]: 等值线数据列表，每项包含:
                - name (str): 原始名称，如 "0.01g"
                - pga_g (float): PGA值(g为单位)，如 0.01
                - pga_mps2 (float): PGA值(m/s²)，如 0.098
                - ia (float): 对应的Ia值
                - coordinates (list[tuple]): (经度, 纬度) 坐标点列表

        异常:
            FileNotFoundError: KML文件不存在
            ET.ParseError: KML文件格式错误
        """
        tree = ET.parse(self.kml_path)
        root = tree.getroot()
        ns = self.KML_NAMESPACE

        contours = []

        for placemark in root.findall('.//kml:Placemark', ns):
            name_elem = placemark.find('kml:name', ns)
            coords_elem = placemark.find('.//kml:coordinates', ns)

            if name_elem is None or coords_elem is None:
                continue

            name = name_elem.text.strip()

            # 解析PGA值：移除末尾的 'g' 字符
            try:
                pga_g = float(name.lower().replace('g', ''))
            except ValueError:
                print(f"  警告: 无法解析PGA值 '{name}'，跳过该等值线")
                continue

            if pga_g <= 0:
                print(f"  警告: PGA值 <= 0 '{name}'，跳过")
                continue

            # g → m/s²
            pga_mps2 = pga_g * self.GRAVITY_ACCELERATION

            # 计算Ia
            ia = self._calculate_ia(pga_mps2)

            # 解析坐标 "lon,lat,alt lon,lat,alt ..."
            coords_text = coords_elem.text.strip()
            coordinates = []
            for coord_str in coords_text.split():
                parts = coord_str.split(',')
                if len(parts) >= 2:
                    lon = float(parts[0])
                    lat = float(parts[1])
                    coordinates.append((lon, lat))

            if len(coordinates) < 2:
                print(f"  警告: 等值线 '{name}' 坐标点不足，跳过")
                continue

            contours.append({
                'name': name,
                'pga_g': pga_g,
                'pga_mps2': pga_mps2,
                'ia': ia,
                'coordinates': coordinates
            })

        # 按PGA值从大到小排序（内圈→外圈）
        contours.sort(key=lambda x: x['pga_g'], reverse=True)

        print(f"\n成功解析 {len(contours)} 条PGA等值线:")
        for c in contours:
            print(f"  {c['name']}: PGA={c['pga_mps2']:.4f} m/s², "
                  f"Ia={c['ia']:.6f} m/s, 坐标点数={len(c['coordinates'])}")

        self._contours = contours
        return contours

    # ==================== Ia 计算 ====================

    @staticmethod
    def _calculate_ia(pga: float) -> float:
        """
        根据PGA计算Ia(阿里亚斯强度)

        公式推导:
            log10(Ia) = a + b * log10(PGA)
            Ia = 10^(a + b * log10(PGA))

        参数:
            pga (float): 峰值地面加速度 (m/s²)，必须 > 0

        返回:
            float: 阿里亚斯强度 Ia (m/s)
        """
        if pga <= 0:
            return 0.0
        log_ia = KmlToIaConverter.COEFFICIENT_A + \
                 KmlToIaConverter.COEFFICIENT_B * math.log10(pga)
        return 10.0 ** log_ia

    # ==================== 投影与坐标转换 ====================

    def _determine_utm_projection(self, lon_min: float, lon_max: float):
        """
        根据数据经度范围自动确定UTM投影带

        投影选择规则（中国常用投影规范）：
            - 中国大陆经度范围：约73°E ~ 135°E
            - UTM 6°分带公式：zone = int((lon_center + 180) / 6) + 1
            - 北半球 EPSG = 32600 + zone
            例如：中心经度103° → zone=48 → EPSG:32648

        参数:
            lon_min (float): 经度最小值
            lon_max (float): 经度最大值
        """
        lon_center = (lon_min + lon_max) / 2.0
        utm_zone = int((lon_center + 180.0) / 6.0) + 1
        self._utm_epsg = 32600 + utm_zone  # 北半球

        print(f"\n投影信息:")
        print(f"  数据经度范围: {lon_min:.4f}° ~ {lon_max:.4f}°")
        print(f"  中心经度: {lon_center:.4f}°")
        print(f"  UTM带号: Zone {utm_zone}N")
        print(f"  EPSG代码: {self._utm_epsg}")

    def _create_transformer(self):
        """
        创建 WGS84(EPSG:4326) → UTM 坐标转换器

        兼容 GDAL 3.0+ 的轴顺序问题，
        强制使用传统GIS顺序（经度在前，纬度在后）
        """
        src_srs = osr.SpatialReference()
        src_srs.ImportFromEPSG(4326)
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

        dst_srs = osr.SpatialReference()
        dst_srs.ImportFromEPSG(self._utm_epsg)
        dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

        self._utm_srs = dst_srs
        self._transformer = osr.CoordinateTransformation(src_srs, dst_srs)

    def _transform_coords(
        self, lons: np.ndarray, lats: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        批量将WGS84经纬度坐标转换为UTM平面坐标

        参数:
            lons (np.ndarray): 经度数组
            lats (np.ndarray): 纬度数组

        返回:
            tuple: (x_utm数组, y_utm数组)，转换失败的点值为NaN
        """
        x_out = np.empty(len(lons), dtype=np.float64)
        y_out = np.empty(len(lats), dtype=np.float64)

        for i, (lon, lat) in enumerate(zip(lons, lats)):
            try:
                result = self._transformer.TransformPoint(float(lon), float(lat))
                x_out[i] = result[0]
                y_out[i] = result[1]
            except Exception:
                x_out[i] = np.nan
                y_out[i] = np.nan

        return x_out, y_out

    # ==================== 采样点准备 ====================

    def _prepare_sample_points(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        从等值线中提取并下采样坐标点，作为插值的输入采样点。
        同时对完全重叠的坐标点进行去重（保留首次出现的值），
        避免克里金插值出现奇异矩阵。

        返回:
            tuple: (x_utm, y_utm, ia_values, pga_values)
                所有数组长度相同，已去重
        """
        lons_all, lats_all = [], []
        ia_all, pga_all = [], []

        for contour in self._contours:
            coords = contour['coordinates']
            ia_val = contour['ia']
            pga_val = contour['pga_mps2']

            # 按间隔采样
            sampled = coords[::self.sample_interval]
            # 确保最后一个点被包含（闭合线的收尾点）
            if len(coords) > 1:
                last_pt = coords[-1]
                # 只有当最后一个点确实不在采样集中时才添加
                if len(sampled) == 0 or sampled[-1] != last_pt:
                    sampled.append(last_pt)

            for lon, lat in sampled:
                lons_all.append(lon)
                lats_all.append(lat)
                ia_all.append(ia_val)
                pga_all.append(pga_val)

        lons_arr = np.array(lons_all)
        lats_arr = np.array(lats_all)
        ia_arr = np.array(ia_all)
        pga_arr = np.array(pga_all)

        # 坐标转换到UTM
        x_utm, y_utm = self._transform_coords(lons_arr, lats_arr)

        # 去除转换失败的点
        valid = ~(np.isnan(x_utm) | np.isnan(y_utm))
        x_utm = x_utm[valid]
        y_utm = y_utm[valid]
        ia_arr = ia_arr[valid]
        pga_arr = pga_arr[valid]

        # -------- 去重处理 --------
        # 将坐标四舍五入到0.01米精度后去重，防止克里金奇异矩阵
        coords_rounded = np.round(np.column_stack([x_utm, y_utm]), decimals=2)
        _, unique_idx = np.unique(coords_rounded, axis=0, return_index=True)
        unique_idx.sort()  # 保持原始顺序

        x_utm = x_utm[unique_idx]
        y_utm = y_utm[unique_idx]
        ia_arr = ia_arr[unique_idx]
        pga_arr = pga_arr[unique_idx]

        print(f"\n采样点统计:")
        print(f"  采样间隔: 每 {self.sample_interval} 个点取1个")
        print(f"  去重后有效采样点数: {len(x_utm)}")
        print(f"  Ia值范围: {ia_arr.min():.6f} ~ {ia_arr.max():.6f} m/s")
        print(f"  PGA值范围: {pga_arr.min():.4f} ~ {pga_arr.max():.4f} m/s²")

        return x_utm, y_utm, ia_arr, pga_arr

    # ==================== 栅格网格构建 ====================

    def _build_grid(self, x_utm: np.ndarray, y_utm: np.ndarray):
        """
        根据采样点范围构建输出栅格网格参数

        在数据范围外扩展 10 个像素作为缓冲区

        参数:
            x_utm (np.ndarray): 采样点X坐标(UTM)
            y_utm (np.ndarray): 采样点Y坐标(UTM)
        """
        buffer = self.resolution * 10

        x_min = x_utm.min() - buffer
        x_max = x_utm.max() + buffer
        y_min = y_utm.min() - buffer
        y_max = y_utm.max() + buffer

        self._n_cols = int(np.ceil((x_max - x_min) / self.resolution))
        self._n_rows = int(np.ceil((y_max - y_min) / self.resolution))

        # GeoTIFF 仿射变换参数:
        # (左上角X, 像素宽度, 旋转, 左上角Y, 旋转, 像素高度负值)
        self._geo_transform = (x_min, self.resolution, 0.0,
                               y_max, 0.0, -self.resolution)

        # 记录网格坐标范围供插值使用
        self._x_min = x_min
        self._x_max = x_max
        self._y_min = y_min
        self._y_max = y_max

        print(f"\n栅格网格信息:")
        print(f"  分辨率: {self.resolution}m × {self.resolution}m")
        print(f"  网格大小: {self._n_cols} 列 × {self._n_rows} 行")
        print(f"  X范围: {x_min:.2f} ~ {x_max:.2f} m")
        print(f"  Y范围: {y_min:.2f} ~ {y_max:.2f} m")
        print(f"  总像素数: {self._n_cols * self._n_rows:,}")

    # ==================== 插值方法 ====================

    def _interpolate_kriging(
        self, x: np.ndarray, y: np.ndarray, values: np.ndarray
    ) -> np.ndarray:
        """
        普通克里金插值（Ordinary Kriging）

        优化策略:
            - 使用 vectorized 后端 + n_closest_points 进行局部插值，
              相比 loop 后端大幅提升速度
            - 采样点已去重，避免奇异矩阵

        参数:
            x (np.ndarray): 采样点X坐标(UTM)
            y (np.ndarray): 采样点Y坐标(UTM)
            values (np.ndarray): 采样点对应的值

        返回:
            np.ndarray: 插值结果栅格 (n_rows, n_cols)，行顺序为从上到下
        """
        ok = OrdinaryKriging(
            x.astype(np.float64),
            y.astype(np.float64),
            values.astype(np.float64),
            variogram_model=self.variogram_model,
            nlags=self.nlags,
            verbose=False,
            enable_plotting=False
        )

        # 构建网格坐标轴（从小到大）
        gridx = np.linspace(
            self._x_min + self.resolution / 2.0,
            self._x_min + (self._n_cols - 0.5) * self.resolution,
            self._n_cols
        )
        gridy = np.linspace(
            self._y_min + self.resolution / 2.0,
            self._y_min + (self._n_rows - 0.5) * self.resolution,
            self._n_rows
        )

        # 使用 vectorized 后端 + n_closest_points 局部搜索，比 loop 快很多
        z_grid, _ = ok.execute(
            'grid', gridx, gridy,
            n_closest_points=self.n_closest_points,
            backend='vectorized'
        )

        # PyKrige grid模式输出：z_grid[i,j] 中 i 对应 gridy（从小到大），
        # j 对应 gridx。GeoTIFF 第一行对应最大Y，所以需要翻转行顺序。
        z_grid = np.flipud(z_grid).astype(np.float32)

        return z_grid

    def _interpolate_rbf(
        self, x: np.ndarray, y: np.ndarray, values: np.ndarray
    ) -> np.ndarray:
        """
        径向基函数插值（RBF - Radial Basis Function）

        使用 scipy.interpolate.RBFInterpolator，默认核函数为 thin_plate_spline。
        该方法对大数据量较为友好，支持向量化计算。

        参数:
            x (np.ndarray): 采样点X坐标(UTM)
            y (np.ndarray): 采样点Y坐标(UTM)
            values (np.ndarray): 采样点对应的值

        返回:
            np.ndarray: 插值结果栅格 (n_rows, n_cols)
        """
        # 构建采样点矩阵 (N, 2)
        points = np.column_stack([x, y])

        # 创建RBF插值器，使用薄板样条核函数
        rbf = RBFInterpolator(
            points, values,
            kernel='thin_plate_spline',
            smoothing=0.0
        )

        # 构建网格查询点
        gridx = np.linspace(
            self._x_min + self.resolution / 2.0,
            self._x_min + (self._n_cols - 0.5) * self.resolution,
            self._n_cols
        )
        gridy = np.linspace(
            self._y_min + self.resolution / 2.0,
            self._y_min + (self._n_rows - 0.5) * self.resolution,
            self._n_rows
        )

        # 从大Y到小Y（GeoTIFF行序：第一行=最大Y）
        gridy_desc = gridy[::-1]

        gx, gy = np.meshgrid(gridx, gridy_desc)
        query_points = np.column_stack([gx.ravel(), gy.ravel()])

        # 批量插值
        z_flat = rbf(query_points)
        z_grid = z_flat.reshape(self._n_rows, self._n_cols).astype(np.float32)

        return z_grid

    def _interpolate_idw(
        self, x: np.ndarray, y: np.ndarray, values: np.ndarray
    ) -> np.ndarray:
        """
        反距离权重插值（IDW - Inverse Distance Weighting）

        使用 scipy.spatial.cKDTree 进行最近邻搜索以加速计算。
        对每个栅格点，取最近的 n_closest_points 个采样点，
        按距离的 idw_power 次方倒数进行加权平均。

        参数:
            x (np.ndarray): 采样点X坐标(UTM)
            y (np.ndarray): 采样点Y坐标(UTM)
            values (np.ndarray): 采样点对应的值

        返回:
            np.ndarray: 插值结果栅格 (n_rows, n_cols)
        """
        points = np.column_stack([x, y])
        tree = cKDTree(points)

        # 构建网格（GeoTIFF行序）
        gridx = np.linspace(
            self._x_min + self.resolution / 2.0,
            self._x_min + (self._n_cols - 0.5) * self.resolution,
            self._n_cols
        )
        gridy = np.linspace(
            self._y_min + self.resolution / 2.0,
            self._y_min + (self._n_rows - 0.5) * self.resolution,
            self._n_rows
        )
        gridy_desc = gridy[::-1]

        gx, gy = np.meshgrid(gridx, gridy_desc)
        query_points = np.column_stack([gx.ravel(), gy.ravel()])

        # KDTree 查询最近 k 个点
        k = min(self.n_closest_points, len(values))
        distances, indices = tree.query(query_points, k=k)

        # 处理距离为0的情况（查询点恰好在采样点上）
        # distances 和 indices 形状: (n_query, k)
        if k == 1:
            distances = distances.reshape(-1, 1)
            indices = indices.reshape(-1, 1)

        # 避免除以零：距离为0时权重设为极大值
        eps = 1e-12
        weights = 1.0 / np.power(np.maximum(distances, eps), self.idw_power)

        # 加权平均
        neighbor_values = values[indices]  # (n_query, k)
        z_flat = np.sum(weights * neighbor_values, axis=1) / np.sum(weights, axis=1)

        z_grid = z_flat.reshape(self._n_rows, self._n_cols).astype(np.float32)

        return z_grid

    def _interpolate(
        self, x: np.ndarray, y: np.ndarray, values: np.ndarray,
        method: str
    ) -> np.ndarray:
        """
        统一插值调度方法

        参数:
            x (np.ndarray): 采样点X坐标
            y (np.ndarray): 采样点Y坐标
            values (np.ndarray): 采样点值
            method (str): 插值方法名称 'kriging'|'rbf'|'idw'

        返回:
            np.ndarray: 插值结果栅格 (n_rows, n_cols)

        异常:
            ValueError: 不支持的插值方法
        """
        method = method.lower()
        if method == 'kriging':
            return self._interpolate_kriging(x, y, values)
        elif method == 'rbf':
            return self._interpolate_rbf(x, y, values)
        elif method == 'idw':
            return self._interpolate_idw(x, y, values)
        else:
            raise ValueError(
                f"不支持的插值方法: '{method}'，可选: 'kriging', 'rbf', 'idw'"
            )

    # ==================== PGA 栅格化 ====================

    def _rasterize_pga_contours(
        self, pga_values: np.ndarray, x_utm: np.ndarray, y_utm: np.ndarray
    ) -> np.ndarray:
        """
        将PGA等值线（闭合LineString）栅格化为PGA.tif

        实现逻辑:
            等值线按PGA值从小到大遍历（外圈到内圈），
            将每条等值线闭合为多边形，判断栅格像素中心是否在多边形内，
            若在则覆盖为该等值线的PGA值。
            这样内圈的值会覆盖外圈的值，最终结果正确。

        注意:
            KML中的等值线是LineString，不一定闭合。
            此处将首尾连接强制闭合，以构建多边形进行包含判断。
            使用GDAL/OGR矢量栅格化API代替逐像素判断以提升速度。

        参数:
            pga_values (np.ndarray): 采样点PGA值（未使用，仅用于统一接口）
            x_utm (np.ndarray): 采样点X坐标（未使用）
            y_utm (np.ndarray): 采样点Y坐标（未使用）

        返回:
            np.ndarray: PGA栅格数据 (n_rows, n_cols)
        """
        print("\n  PGA栅格化: 使用OGR矢量→栅格化...")

        # 使用最小PGA值作为背景值
        min_pga = self._contours[-1]['pga_mps2'] if self._contours else 0.0

        # 创建内存中的矢量数据源
        mem_driver = ogr.GetDriverByName('Memory')
        mem_ds = mem_driver.CreateDataSource('pga_contours')
        layer = mem_ds.CreateLayer('contours', srs=self._utm_srs, geom_type=ogr.wkbPolygon)

        # 添加 PGA 字段
        field_defn = ogr.FieldDefn('PGA', ogr.OFTReal)
        layer.CreateField(field_defn)

        # 从外圈到内圈（PGA从小到大）添加多边形要素
        for contour in reversed(self._contours):
            coords = contour['coordinates']
            if len(coords) < 3:
                continue

            ring = ogr.Geometry(ogr.wkbLinearRing)
            for lon, lat in coords:
                try:
                    result = self._transformer.TransformPoint(float(lon), float(lat))
                    ring.AddPoint(result[0], result[1])
                except Exception:
                    continue

            # 强制闭合
            if ring.GetPointCount() >= 3:
                first_pt = ring.GetPoint(0)
                last_pt = ring.GetPoint(ring.GetPointCount() - 1)
                if first_pt[0] != last_pt[0] or first_pt[1] != last_pt[1]:
                    ring.AddPoint(first_pt[0], first_pt[1])

            polygon = ogr.Geometry(ogr.wkbPolygon)
            polygon.AddGeometry(ring)

            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetField('PGA', contour['pga_mps2'])
            feature.SetGeometry(polygon)
            layer.CreateFeature(feature)
            feature = None

        # 创建输出栅格（内存）
        raster_driver = gdal.GetDriverByName('MEM')
        raster_ds = raster_driver.Create('', self._n_cols, self._n_rows, 1, gdal.GDT_Float32)
        raster_ds.SetGeoTransform(self._geo_transform)
        raster_ds.SetProjection(self._utm_srs.ExportToWkt())

        band = raster_ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.0)
        # 初始化为最小PGA值
        band.Fill(min_pga)

        # 矢量栅格化：按添加顺序渲染（外圈先画，内圈后画覆盖）
        gdal.RasterizeLayer(
            raster_ds, [1], layer,
            options=[f"ATTRIBUTE=PGA"]
        )

        pga_grid = band.ReadAsArray().astype(np.float32)

        mem_ds = None
        raster_ds = None

        print(f"  PGA栅格化完成，值范围: {np.nanmin(pga_grid):.4f} ~ "
              f"{np.nanmax(pga_grid):.4f} m/s²")

        return pga_grid

    # ==================== GeoTIFF 输出 ====================

    @staticmethod
    def _save_geotiff(
        data: np.ndarray, output_path: str,
        geo_transform: tuple, projection_wkt: str,
        nodata_value: float = -9999.0
    ):
        """
        将二维数组保存为GeoTIFF栅格文件

        参数:
            data (np.ndarray): 栅格数据，形状 (rows, cols)
            output_path (str): 输出文件路径
            geo_transform (tuple): GDAL仿射变换6参数
                (左上角X, 像素宽X, 旋转, 左上角Y, 旋转, 像素宽Y负值)
            projection_wkt (str): 坐标系WKT字符串
            nodata_value (float): 无数据标识值，默认-9999.0
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        rows, cols = data.shape
        data_clean = np.where(np.isnan(data), nodata_value, data)

        driver = gdal.GetDriverByName('GTiff')
        dataset = driver.Create(
            output_path, cols, rows, 1, gdal.GDT_Float32,
            options=['COMPRESS=LZW', 'TILED=YES']
        )

        if dataset is None:
            raise RuntimeError(f"无法创建GeoTIFF文件: {output_path}")

        dataset.SetGeoTransform(geo_transform)
        dataset.SetProjection(projection_wkt)

        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(nodata_value)
        band.WriteArray(data_clean)
        band.FlushCache()
        band.ComputeStatistics(False)

        dataset = None
        print(f"  已保存: {output_path}")

    # ==================== 主流程 ====================

    def run(self) -> bool:
        """
        执行完整的 KML → Ia.tif 转换流程

        流程:
            1. 解析KML文件
            2. 确定UTM投影并创建坐标转换器
            3. 准备采样点（下采样+去重+坐标转换）
            4. 构建输出栅格网格
            5. （可选）PGA等值线栅格化并输出PGA.tif
            6. Ia插值计算并输出Ia.tif
            7. 打印耗时统计

        返回:
            bool: 处理是否成功
        """
        print("=" * 60)
        print("KML → Ia 栅格处理程序")
        print(f"插值方法: {self.interp_method.upper()}")
        print(f"输出PGA.tif: {'是' if self.export_pga else '否'}")
        print("=" * 60)

        # 1. 检查输入文件
        if not os.path.exists(self.kml_path):
            print(f"错误: KML文件不存在 - {self.kml_path}")
            return False

        # 2. 解析KML
        contours = self.parse_kml()
        if len(contours) == 0:
            print("错误: 未找到有效的PGA等值线")
            return False

        # 3. 确定投影
        all_lons = []
        for c in contours:
            all_lons.extend([coord[0] for coord in c['coordinates']])
        self._determine_utm_projection(min(all_lons), max(all_lons))

        # 4. 创建坐标转换器
        self._create_transformer()

        # 5. 准备采样点
        x_utm, y_utm, ia_values, pga_values = self._prepare_sample_points()

        # 6. 构建栅格网格
        self._build_grid(x_utm, y_utm)

        projection_wkt = self._utm_srs.ExportToWkt()

        # 7. PGA栅格化（可选）
        if self.export_pga:
            print("\n" + "-" * 40)
            print("步骤: PGA等值线栅格化")
            print("-" * 40)
            pga_start = time.time()

            pga_grid = self._rasterize_pga_contours(pga_values, x_utm, y_utm)
            self._save_geotiff(
                pga_grid, self.pga_output_path,
                self._geo_transform, projection_wkt
            )

            pga_elapsed = time.time() - pga_start
            print(f"  PGA栅格化耗时: {pga_elapsed:.2f} 秒")

        # 8. Ia插值
        print("\n" + "-" * 40)
        print(f"步骤: Ia插值计算（{self.interp_method.upper()}）")
        print("-" * 40)

        interp_start = time.time()

        ia_grid = self._interpolate(x_utm, y_utm, ia_values, self.interp_method)

        self._save_geotiff(
            ia_grid, self.ia_output_path,
            self._geo_transform, projection_wkt
        )

        interp_elapsed = time.time() - interp_start
        print(f"\n✅ Ia插值计算到输出文件耗时: {interp_elapsed:.2f} 秒")

        # 9. 汇总
        print("\n" + "=" * 60)
        print("处理完成!")
        if self.export_pga:
            print(f"  PGA栅格: {self.pga_output_path}")
        print(f"  Ia栅格:  {self.ia_output_path}")
        print("=" * 60)

        return True


# ==================== 入口 ====================
if __name__ == "__main__":
    converter = KmlToIaConverter(
        kml_path="../../data/geology/kml/source.kml",       # 输入KML文件路径
        pga_output_path=None,     # PGA输出路径
        ia_output_path="../../data/geology/ia/Ia.tif",       # Ia输出路径
        resolution=30,                                        # 30m × 30m 分辨率
        sample_interval=5,                                    # 每5个点取1个
        n_closest_points=80,                                  # 局部搜索最近80个点
        export_pga=False,                                      # 输出PGA.tif
        interp_method='kriging',                              # 插值方法: kriging/rbf/idw
        idw_power=2.0,                                        # IDW幂次（仅idw时有效）
        variogram_model='spherical',                          # 克里金变异函数模型
        nlags=6                                               # 克里金滞后数
    )
    converter.run()