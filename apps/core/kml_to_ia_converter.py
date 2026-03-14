#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
KML转Ia(阿里亚斯强度)栅格文件处理模块

功能：将地震局提供的KML格式PGA等值线文件转换为Ia.tif栅格文件
处理流程：
    1. 解析KML文件获取PGA等值线（LineString）
    2. 将PGA值(g单位)转换为实际加速度值(m/s²)
    3. 根据公式 log10(Ia) = a + b * log10(PGA) 计算Ia值
    4. 使用QGIS自带IDW/TIN插值算法生成栅格（替换第三方pykrige/scipy）
    5. 可选输出PGA.tif和Ia.tif
    6. 打印插值计算到输出文件的耗时

作者：Copilot
日期：2026-03-14
QGIS版本：3.40.15

投影说明：
    根据数据经度范围的中心经度，自动选择对应的UTM投影带。
    中国大陆经度范围约73°E~135°E，对应UTM带号13N~53N。
    计算方式：utm_zone = int((lon_center + 180) / 6) + 1
    北半球EPSG编码：326xx（如Zone 48N → EPSG:32648）
    例如：经度中心103°时，utm_zone = int((103+180)/6)+1 = 48 → EPSG:32648

内存优化说明（运行环境 32G）：
    - 使用生成器迭代采样点，避免一次性构建大型中间列表
    - 坐标转换分批进行（默认每批 10 000 个点）
    - QgsGridFileWriter 逐行流式写入 ASC 文件，不在内存中保留完整栅格
    - 各阶段处理后及时 del 临时数组并调用 gc.collect()
"""

import gc
import os
import time
import math
import tempfile
import numpy as np
from typing import Iterator, List, Literal, Optional, Tuple
from xml.etree import ElementTree as ET
from osgeo import gdal, osr, ogr

# ==================== QGIS 插值相关模块 ====================
# 以下模块在 QGIS 3.40.15 Python 环境中内置，无需安装第三方库
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.analysis import (
    QgsGridFileWriter,
    QgsIDWInterpolator,
    QgsInterpolator,
    QgsTinInterpolator,
)
from PyQt5.QtCore import QMetaType

# ==================== 启用GDAL异常处理 ====================
gdal.UseExceptions()


class KmlToIaConverter:
    """
    KML文件转Ia(阿里亚斯强度)栅格文件转换器（QGIS 3.40.15 优化版）

    将地震局提供的KML格式PGA等值线文件，经过解析、坐标转换、
    插值计算后，输出Ia.tif栅格文件（可选输出PGA.tif）。

    本版本使用 QGIS 3.40.15 自带的 IDW / TIN 插值算法，替换了
    原有的第三方库 pykrige（克里金）和 scipy（RBF/IDW），并针对
    32G 内存运行环境进行了全面的内存优化。

    支持的插值方法对比：
        - 'idw'（反距离权重）：
            速度快，适合稀疏/不均匀数据；
            idw_power 值越大，近点影响越强，结果越局部化；
            idw_power 值越小，远点影响增大，结果越平滑。
        - 'tin'（三角网线性插值）：
            基于 Delaunay 三角剖分的保值插值，不外推超出采样值范围；
            适合分布较均匀的数据，边缘精度优于 IDW；
            tin_method=1（Clough-Tocher）可得到更平滑的曲面，但计算较慢。

    属性:
        kml_path (str): 输入KML文件路径
        pga_output_path (str): 输出PGA栅格文件路径（export_pga=False 时可为 None）
        ia_output_path (str): 输出Ia栅格文件路径
        resolution (float): 目标分辨率(米)，默认30
        sample_interval (int): 等值线采样间隔，每隔多少个坐标点取一个，默认5
        export_pga (bool): 是否输出PGA.tif文件，默认True
        interp_method (str): 插值方法，可选 'idw'、'tin'，默认'idw'
        idw_power (float): IDW距离衰减幂次，默认2.0（仅 idw 方法有效）
        tin_method (int): TIN子方法，0=线性(Linear)，1=Clough-Tocher，默认0（仅 tin 方法有效）

    用法示例:
        converter = KmlToIaConverter(
            kml_path="../../data/geology/kml/source.kml",
            pga_output_path="../../data/geology/ia/PGA.tif",
            ia_output_path="../../data/geology/ia/Ia.tif",
            resolution=30,
            export_pga=True,
            interp_method='idw',
            idw_power=2.0,
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
        pga_output_path: Optional[str],
        ia_output_path: str,
        resolution: float = 30.0,
        sample_interval: int = 5,
        export_pga: bool = True,
        interp_method: Literal['idw', 'tin'] = 'idw',
        idw_power: float = 2.0,
        tin_method: int = 0,
    ):
        """
        初始化转换器

        参数:
            kml_path (str): 输入KML文件路径
            pga_output_path (str | None): 输出PGA栅格文件路径；
                export_pga=False 时可传 None
            ia_output_path (str): 输出Ia栅格文件路径
            resolution (float): 目标分辨率(米)，默认30
                调参建议：分辨率越小精度越高但计算量越大；
                         推荐范围 10~100 m，地震烈度图通常用 30~90 m
            sample_interval (int): 等值线坐标采样间隔，默认5
                调参建议：值越小采样点越密，插值精度越高但内存占用越大；
                         推荐范围 3~10；数据点稀少时可设为 1
            export_pga (bool): 是否同时输出 PGA.tif，默认 True
            interp_method (str): 插值方法，'idw' 或 'tin'，默认 'idw'
                'idw' — 反距离权重：适合稀疏/不均匀数据，速度快
                'tin' — 三角网线性插值：适合均匀分布数据，边缘精度好
            idw_power (float): IDW 距离衰减幂次，默认 2.0
                调参建议：推荐范围 1.0~4.0；
                         值越大近点主导，结果越局部化；
                         值越小远点影响增大，结果越平滑；
                         仅在 interp_method='idw' 时有效
            tin_method (int): TIN 插值子方法，默认 0
                0 = Linear（线性）：在三角形内线性插值，速度快，无振荡
                1 = Clough-Tocher：曲线插值，结果更平滑，计算量较大
                仅在 interp_method='tin' 时有效
        """
        self.kml_path = kml_path
        self.pga_output_path = pga_output_path
        self.ia_output_path = ia_output_path
        self.resolution = resolution
        self.sample_interval = sample_interval
        self.export_pga = export_pga
        self.interp_method = interp_method
        self.idw_power = idw_power
        self.tin_method = tin_method

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
        解析KML文件，提取所有PGA等值线数据（内存优化版）

        KML中每个Placemark的name字段格式为 "0.01g"、"0.02g" 等，
        表示该等值线对应的PGA值（以重力加速度g为单位）。
        坐标存储在LineString/coordinates中，格式为 "lon,lat,alt lon,lat,alt ..."
        解析完成后立即释放 XML 树对象，节省内存。

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
                    try:
                        lon = float(parts[0])
                        lat = float(parts[1])
                        coordinates.append((lon, lat))
                    except ValueError:
                        continue

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

        # 解析完成后立即释放 XML 树，节省内存
        del tree, root
        gc.collect()

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

    def _transform_coords_batch(
        self, lons: np.ndarray, lats: np.ndarray, chunk_size: int = 10000
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        分批批量将WGS84经纬度坐标转换为UTM平面坐标（内存优化版）

        将坐标数组分成多批次处理，避免一次性分配过大的临时缓冲区。

        参数:
            lons (np.ndarray): 经度数组
            lats (np.ndarray): 纬度数组
            chunk_size (int): 每批处理的点数，默认10000

        返回:
            tuple: (x_utm数组, y_utm数组)，转换失败的点值为NaN
        """
        n = len(lons)
        x_out = np.empty(n, dtype=np.float64)
        y_out = np.empty(n, dtype=np.float64)

        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            for i in range(start, end):
                try:
                    result = self._transformer.TransformPoint(
                        float(lons[i]), float(lats[i])
                    )
                    x_out[i] = result[0]
                    y_out[i] = result[1]
                except Exception:
                    x_out[i] = np.nan
                    y_out[i] = np.nan

        return x_out, y_out

    # ==================== 采样点准备 ====================

    def _iter_sample_points(self) -> Iterator[Tuple[float, float, float, float]]:
        """
        生成器：逐条遍历等值线并按间隔采样，逐点输出（内存优化）

        使用生成器替代列表，避免一次性将所有坐标载入内存。

        生成:
            (lon, lat, ia_val, pga_val): 每个采样点的经纬度和对应的 Ia/PGA 值
        """
        for contour in self._contours:
            coords = contour['coordinates']
            ia_val = contour['ia']
            pga_val = contour['pga_mps2']

            # 按间隔采样，构建可变列表以便追加末尾点
            sampled: List[tuple] = list(coords[::self.sample_interval])
            # 确保最后一个点被包含（闭合线的收尾点）
            if len(coords) > 1:
                last_pt = coords[-1]
                if not sampled or sampled[-1] != last_pt:
                    sampled.append(last_pt)

            for lon, lat in sampled:
                yield lon, lat, ia_val, pga_val

    def _prepare_sample_points(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        从等值线中提取并下采样坐标点，作为插值的输入采样点（内存优化版）

        使用生成器迭代采样点，避免大量中间列表占用内存。
        对完全重叠的坐标点进行去重，避免插值出现奇异矩阵。
        各阶段处理完成后立即释放临时数组。

        返回:
            tuple: (x_utm, y_utm, ia_values, pga_values)
                所有数组长度相同，已去重
        """
        # 通过生成器一次性收集数据，避免多次 append 开销
        rows = list(self._iter_sample_points())
        if not rows:
            raise ValueError("没有有效的采样点")

        lons_arr = np.array([r[0] for r in rows], dtype=np.float64)
        lats_arr = np.array([r[1] for r in rows], dtype=np.float64)
        ia_arr   = np.array([r[2] for r in rows], dtype=np.float32)
        pga_arr  = np.array([r[3] for r in rows], dtype=np.float32)
        del rows   # 立即释放中间列表
        gc.collect()

        # 坐标转换到UTM（分批处理，控制每批内存峰值）
        x_utm, y_utm = self._transform_coords_batch(lons_arr, lats_arr)
        del lons_arr, lats_arr   # 释放经纬度数组
        gc.collect()

        # 去除转换失败的点
        valid = ~(np.isnan(x_utm) | np.isnan(y_utm))
        x_utm   = x_utm[valid]
        y_utm   = y_utm[valid]
        ia_arr  = ia_arr[valid]
        pga_arr = pga_arr[valid]

        # -------- 去重处理 --------
        # 将坐标四舍五入到0.01米精度后去重，防止插值奇异矩阵
        coords_rounded = np.round(np.column_stack([x_utm, y_utm]), decimals=2)
        _, unique_idx = np.unique(coords_rounded, axis=0, return_index=True)
        del coords_rounded   # 释放临时数组
        unique_idx.sort()    # 保持原始顺序

        x_utm   = x_utm[unique_idx]
        y_utm   = y_utm[unique_idx]
        ia_arr  = ia_arr[unique_idx]
        pga_arr = pga_arr[unique_idx]
        del unique_idx
        gc.collect()

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

    # ==================== QGIS 插值方法 ====================

    def _build_qgs_vector_layer(
        self,
        x_utm: np.ndarray,
        y_utm: np.ndarray,
        values: np.ndarray,
        field_name: str = 'value',
    ) -> QgsVectorLayer:
        """
        将采样点数组构建为 QGIS 内存矢量图层，供 QGIS 插值算法使用。

        要素以批次方式添加（每批 5000 个），控制每次写入的内存峰值。

        参数:
            x_utm (np.ndarray): 采样点X坐标(UTM)
            y_utm (np.ndarray): 采样点Y坐标(UTM)
            values (np.ndarray): 采样点对应值（Ia 或 PGA）
            field_name (str): 值字段名，默认'value'

        返回:
            QgsVectorLayer: QGIS 内存点图层（含一个 Double 型值字段）
        """
        crs_auth_id = f"EPSG:{self._utm_epsg}"
        layer = QgsVectorLayer(
            f"Point?crs={crs_auth_id}", "sample_points", "memory"
        )

        provider = layer.dataProvider()
        provider.addAttributes([QgsField(field_name, QMetaType.Type.Double)])
        layer.updateFields()

        # 每批次添加要素，防止单次写入占用过多内存
        batch_size = 5000
        batch: List[QgsFeature] = []
        for xi, yi, vi in zip(x_utm, y_utm, values):
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(xi), float(yi))))
            feat.setAttributes([float(vi)])
            batch.append(feat)
            if len(batch) >= batch_size:
                provider.addFeatures(batch)
                batch.clear()
        if batch:
            provider.addFeatures(batch)

        layer.updateExtents()
        return layer

    def _run_qgis_interpolation(
        self,
        x_utm: np.ndarray,
        y_utm: np.ndarray,
        values: np.ndarray,
        tmp_asc_path: str,
    ) -> None:
        """
        使用 QGIS 3.40.15 自带的插值算法将采样点插值到 ASC 栅格文件。

        根据 self.interp_method 选择 IDW 或 TIN 插值器，
        通过 QgsGridFileWriter 逐行流式写入 ASC 文件，
        不在内存中构建完整的栅格数组，内存占用低。

        IDW（反距离权重）调参说明：
            self.idw_power（默认2.0，推荐1.0~4.0）
                — 距离衰减幂次，值越大近点主导，结果越局部化；
                  值越小远点影响增大，结果越平滑；
                  QGIS IDW 使用所有采样点，通过空间索引加速

        TIN（三角网线性插值）调参说明：
            self.tin_method（默认0）
                — 0=Linear（线性插值，速度快，无振荡）
                — 1=Clough-Tocher（曲线插值，更平滑，计算量较大）

        参数:
            x_utm: 采样点X坐标(UTM)
            y_utm: 采样点Y坐标(UTM)
            values: 采样点对应值
            tmp_asc_path: 输出临时 ASC 文件路径
        """
        # ---- 构建 QGIS 内存矢量图层 ----
        layer = self._build_qgs_vector_layer(x_utm, y_utm, values)

        # ---- 构建 QgsInterpolator.LayerData ----
        layer_data = QgsInterpolator.LayerData()
        layer_data.source = layer                                         # 数据源（矢量图层）
        layer_data.valueSource = QgsInterpolator.ValueSource.ValueAttribute  # 使用属性字段中的值
        layer_data.interpolationAttribute = 0                             # 第0个字段（'value'）
        layer_data.sourceType = QgsInterpolator.SourceType.SourcePoints   # 点要素类型

        # ---- 创建插值器 ----
        method = self.interp_method.lower()
        if method == 'idw':
            # IDW：反距离权重插值
            # setDistanceCoefficient 设置距离衰减幂次；
            # QGIS IDW 使用全部采样点（通过空间索引加速），不限制搜索半径
            interpolator = QgsIDWInterpolator([layer_data])
            interpolator.setDistanceCoefficient(self.idw_power)
        elif method == 'tin':
            # TIN：三角网线性插值 / Clough-Tocher 曲线插值
            # tin_method=0 → Linear；tin_method=1 → CloughTocher
            tin_enum = (
                QgsTinInterpolator.TinInterpolation.Linear
                if self.tin_method == 0
                else QgsTinInterpolator.TinInterpolation.CloughTocher
            )
            interpolator = QgsTinInterpolator([layer_data], tin_enum)
        else:
            raise ValueError(
                f"不支持的插值方法: '{method}'，可选: 'idw', 'tin'"
            )

        # ---- 构建栅格空间范围 ----
        extent = QgsRectangle(
            self._x_min, self._y_min,
            self._x_max, self._y_max
        )

        # ---- 流式写入 ASC 文件（逐行，内存友好）----
        # QgsGridFileWriter 不在内存中保留完整栅格，直接按行写入文件
        writer = QgsGridFileWriter(
            interpolator,
            tmp_asc_path,
            extent,
            self._n_cols,
            self._n_rows,
            self.resolution,    # cellSizeX
            self.resolution,    # cellSizeY
        )
        error_code = writer.writeFile()
        if error_code != 0:
            raise RuntimeError(
                f"QgsGridFileWriter 写入失败，错误码: {error_code}"
            )

        # 显式释放 QGIS 对象，加快内存回收
        del writer, interpolator, layer_data, layer
        gc.collect()

    def _asc_to_geotiff(self, asc_path: str, tif_path: str) -> None:
        """
        将 ESRI ASCII 栅格文件转换为带投影信息的压缩 GeoTIFF 文件。

        使用 GDAL Translate 完成格式转换，同时写入 UTM 投影信息、
        LZW 压缩和 Tiled 存储，减小输出文件体积。

        参数:
            asc_path (str): 输入 ASC 文件路径
            tif_path (str): 输出 GeoTIFF 文件路径
        """
        os.makedirs(os.path.dirname(os.path.abspath(tif_path)), exist_ok=True)

        src_ds = gdal.Open(asc_path, gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError(f"无法读取ASC插值结果: {asc_path}")

        translate_options = gdal.TranslateOptions(
            format='GTiff',
            outputType=gdal.GDT_Float32,
            creationOptions=['COMPRESS=LZW', 'TILED=YES'],
            outputSRS=self._utm_srs.ExportToWkt(),
            noData=-9999.0,
        )
        dst_ds = gdal.Translate(tif_path, src_ds, options=translate_options)
        if dst_ds is None:
            src_ds = None
            raise RuntimeError(f"无法创建GeoTIFF文件: {tif_path}")

        band = dst_ds.GetRasterBand(1)
        band.ComputeStatistics(False)
        band.FlushCache()

        # 显式关闭数据集，释放文件句柄
        src_ds = None
        dst_ds = None
        band = None
        gc.collect()

        print(f"  已保存: {tif_path}")

    def _interpolate_to_file(
        self,
        x_utm: np.ndarray,
        y_utm: np.ndarray,
        values: np.ndarray,
        output_tif_path: str,
    ) -> None:
        """
        统一插值入口：调用 QGIS 插值算法写入临时 ASC 文件，
        再转换为最终 GeoTIFF 格式，处理完成后清理临时文件。

        参数:
            x_utm: 采样点X坐标(UTM)
            y_utm: 采样点Y坐标(UTM)
            values: 采样点值
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        # 临时 ASC 文件：QGIS GridFileWriter 的中间输出格式
        with tempfile.NamedTemporaryFile(suffix='.asc', delete=False) as tmp:
            tmp_asc_path = tmp.name

        try:
            self._run_qgis_interpolation(x_utm, y_utm, values, tmp_asc_path)
            self._asc_to_geotiff(tmp_asc_path, output_tif_path)
        finally:
            # 使用 os.path.splitext 安全地替换扩展名，避免路径中含多个 '.asc' 时误替换
            base, _ = os.path.splitext(tmp_asc_path)
            for ext in ('.asc', '.prj'):
                candidate = base + ext
                if os.path.exists(candidate):
                    os.remove(candidate)

    # ==================== PGA 栅格化 ====================

    def _rasterize_pga_contours(self) -> None:
        """
        将PGA等值线（闭合LineString）矢量栅格化为PGA.tif（内存优化版）

        实现逻辑:
            等值线按PGA值从小到大遍历（外圈到内圈），
            将每条等值线闭合为多边形，利用 GDAL/OGR 矢量栅格化 API
            批量处理，内圈覆盖外圈，最终结果正确。

        优化措施:
            - 矢量数据和内存栅格处理完成后立即显式释放
            - 使用 GDAL Translate 直接写出压缩 GeoTIFF，避免二次读取
            - 每个 OGR Feature 创建后立即置 None 释放

        注意:
            KML中的等值线是LineString，不一定闭合，
            此处强制首尾连接以构建多边形进行包含判断。
        """
        print("\n  PGA栅格化: 使用OGR矢量→栅格化...")

        if not self.pga_output_path:
            print("  警告: PGA输出路径未设置，跳过PGA栅格化")
            return

        # 使用最小PGA值作为背景值
        min_pga = self._contours[-1]['pga_mps2'] if self._contours else 0.0

        # 创建内存矢量数据源
        mem_driver = ogr.GetDriverByName('Memory')
        mem_ds = mem_driver.CreateDataSource('pga_contours')
        layer = mem_ds.CreateLayer(
            'contours', srs=self._utm_srs, geom_type=ogr.wkbPolygon
        )

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
            feature = None   # 立即释放要素对象

        # 创建内存临时栅格
        raster_driver = gdal.GetDriverByName('MEM')
        raster_ds = raster_driver.Create(
            '', self._n_cols, self._n_rows, 1, gdal.GDT_Float32
        )
        raster_ds.SetGeoTransform(self._geo_transform)
        raster_ds.SetProjection(self._utm_srs.ExportToWkt())

        band = raster_ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.0)
        band.Fill(min_pga)

        # 矢量栅格化：外圈先画，内圈后画（覆盖）
        gdal.RasterizeLayer(
            raster_ds, [1], layer,
            options=["ATTRIBUTE=PGA"]
        )
        band.ComputeStatistics(False)
        band.FlushCache()

        pga_min = band.GetMinimum()
        pga_max = band.GetMaximum()

        # 直接从内存栅格写出压缩 GeoTIFF，无需先保存再读取
        os.makedirs(
            os.path.dirname(os.path.abspath(self.pga_output_path)), exist_ok=True
        )
        gdal.Translate(
            self.pga_output_path, raster_ds,
            format='GTiff',
            creationOptions=['COMPRESS=LZW', 'TILED=YES'],
            outputType=gdal.GDT_Float32,
        )

        # 释放所有 GDAL/OGR 对象
        mem_ds = None
        raster_ds = None
        band = None
        gc.collect()

        print(f"  PGA栅格化完成，值范围: {pga_min:.4f} ~ {pga_max:.4f} m/s²")
        print(f"  已保存: {self.pga_output_path}")

    # ==================== 主流程 ====================

    def run(self) -> bool:
        """
        执行完整的 KML → Ia.tif 转换流程

        流程:
            1. 解析KML文件
            2. 确定UTM投影并创建坐标转换器
            3. 准备采样点（生成器下采样 + 去重 + 批量坐标转换）
            4. 构建输出栅格网格
            5. （可选）PGA等值线矢量栅格化并输出PGA.tif
            6. 使用 QGIS IDW/TIN 插值计算并输出Ia.tif
            7. 打印耗时统计

        返回:
            bool: 处理是否成功
        """
        print("=" * 60)
        print("KML → Ia 栅格处理程序（QGIS 3.40.15 插值）")
        print(f"插值方法: {self.interp_method.upper()}")
        print(f"输出PGA.tif: {'是' if self.export_pga else '否'}")
        print("=" * 60)

        # 1. 检查输入文件
        if not os.path.exists(self.kml_path):
            print(f"错误: KML文件不存在 - {self.kml_path}")
            return False

        # 2. 解析KML（解析后 XML 树已被释放）
        contours = self.parse_kml()
        if len(contours) == 0:
            print("错误: 未找到有效的PGA等值线")
            return False

        # 3. 确定投影
        all_lons = [coord[0] for c in contours for coord in c['coordinates']]
        self._determine_utm_projection(min(all_lons), max(all_lons))
        del all_lons
        gc.collect()

        # 4. 创建坐标转换器
        self._create_transformer()

        # 5. 准备采样点（生成器 + 批量转换，内存友好）
        x_utm, y_utm, ia_values, pga_values = self._prepare_sample_points()

        # 6. 构建栅格网格
        self._build_grid(x_utm, y_utm)

        # 7. PGA栅格化（可选）
        if self.export_pga and self.pga_output_path:
            print("\n" + "-" * 40)
            print("步骤: PGA等值线栅格化")
            print("-" * 40)
            pga_start = time.time()
            self._rasterize_pga_contours()
            pga_elapsed = time.time() - pga_start
            print(f"  PGA栅格化耗时: {pga_elapsed:.2f} 秒")

        # 8. Ia插值（QGIS IDW/TIN，流式写入文件）
        print("\n" + "-" * 40)
        print(f"步骤: Ia插值计算（QGIS {self.interp_method.upper()}）")
        print("-" * 40)

        interp_start = time.time()
        self._interpolate_to_file(x_utm, y_utm, ia_values, self.ia_output_path)

        # 释放大型数组
        del x_utm, y_utm, ia_values, pga_values
        gc.collect()

        interp_elapsed = time.time() - interp_start
        print(f"\n✅ Ia插值计算到输出文件耗时: {interp_elapsed:.2f} 秒")

        # 9. 汇总
        print("\n" + "=" * 60)
        print("处理完成!")
        if self.export_pga and self.pga_output_path:
            print(f"  PGA栅格: {self.pga_output_path}")
        print(f"  Ia栅格:  {self.ia_output_path}")
        print("=" * 60)

        return True


# ==================== 入口 ====================
if __name__ == "__main__":
    converter = KmlToIaConverter(
        kml_path="../../data/geology/kml/source.kml",        # 输入KML文件路径
        pga_output_path="../../data/geology/kml/PGA.tif",                                 # PGA输出路径（不需要可设为None）
        ia_output_path="../../data/geology/ia/Ia.tif",        # Ia输出路径
        resolution=30,          # 输出分辨率(米)；推荐10~100，越小精度越高但越慢
        sample_interval=5,      # 等值线采样间隔；推荐3~10，越小采样越密
        export_pga=False,       # 是否同时输出PGA.tif
        interp_method='idw',    # 插值方法: 'idw'(反距离权重) 或 'tin'(三角网)
        idw_power=2.0,          # IDW幂次；推荐1.0~4.0，越大近点主导（仅idw有效）
        tin_method=0,           # TIN子方法: 0=线性, 1=Clough-Tocher（仅tin有效）
    )
    converter.run()