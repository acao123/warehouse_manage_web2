# -*- coding: utf-8 -*-
"""
KML格式PGA等值线转换为Ia栅格文件工具（重构版 v3.3）
基于QGIS 3.40.15 Python环境

功能：
    1. 解析KML文件获取PGA等值线（LineString）
    2. 将PGA值(g单位)转换为实际加速度值(m/s²)
    3. 根据公式 log10(Ia) = 0.797 + 1.837 * log10(PGA) 计算Ia(阿里亚斯强度)值
    4. 使用插值算法对Ia进行插值计算（支持6种插值方法）
    5. 只输出Ia.tif；如需PGA.tif，使用矢量栅格化方式（非插值）
    6. 分辨率固定为30米×30米

主要改进（v3.3 相较 v3.2）：
    1. 移除未使用的 import traceback
    2. _interpolate_ia_to_file 添加 try-except 异常处理并向上抛出
    3. cleanup 方法添加异常保护（不掩盖原始异常）
    4. scipy_idw/scipy_tin 分块循环预分配 pts 数组，避免 meshgrid 中间拷贝
    5. radial 距离合并改用 numpy 向量化（np.diff + np.bincount），替代 Python 逐点循环
    6. 修正 __main__ 块注释（kriging 方法注释误写为 'scipy TIN'）

主要改进（v3.2 相较 v3.1）：
    1. 修正误导性变量命名：_utm_epsg/_utm_srs → _output_epsg/_output_srs
    2. 移除 _run_impl 中冗余的文件存在性检查（parse_kml 已抛出 FileNotFoundError）
    3. 移除 _run_impl 中永远不可到达的 return False 路径
    4. 移除从未使用的 coord_batch_size 参数
    5. 径向插值中心改用最高PGA等值线重心（替代全采样点均值，几何更准确）
    6. 修复日志中断码字符：合并后控制点数
    7. scipy_tin NaN填充改用 NearestNDInterpolator（比 RBF neighbors=1 更准确）
    8. QGIS插值改用 QgsGridFileWriter（C++实现，绕过逐像素Python循环，速度显著提升）
    9. 克里金 backend 改为 'vectorized'（比 'loop' 快数倍）
    10. gc.collect() 改为每N块调用一次（减少GC开销）
    11. 内存估算乘数注释说明（4×float32 = 16 bytes/pixel）
    12. driver.Create()、band.WriteArray()、gdal.RasterizeLayer() 返回值均加检查
    13. _ensure_file_writable 增加父目录可写性检查
    14. _build_qgs_vector_layer 增加 layer.isValid() 检查

主要改进（v3.1 相较 v3.0）：
    1. 修复经度方向分辨率未考虑纬度余弦的Bug（像素非正方形问题）
    2. 移除无用的EPSG:4326→EPSG:4326恒等坐标变换，直接使用经纬度坐标
    3. 修复KML name解析过于宽松的Bug（'g'替换可能误伤其他字符）
    4. 所有关键方法添加try-except + logger日志 + 异常向上抛出
    5. 全部print替换为logger日志，保持生产环境可控
    6. QgsFeature设置fields定义，确保属性值不丢失
    7. 方法名重命名消除误导（_determine_utm_projection → _setup_output_crs）

作者: Copilot (重构版 v3.3)
日期: 2026-03-31
版本: 3.3
QGIS版本: 3.40.15

支持插值方法:
    - 'scipy_tin' : scipy Delaunay三角网插值（默认，平滑无突变，推荐）
    - 'radial'    : 径向距离1D插值（专为同心圈优化，完美单调递增）
    - 'scipy_idw' : scipy RBFInterpolator（速度快，支持邻近点限制）
    - 'kriging'   : pykrige普通克里金插值（统计精度最高，需安装pykrige）
    - 'qgis_idw'  : QGIS自带反距离权重插值（无需额外依赖）
    - 'qgis_tin'  : QGIS自带三角网插值（无需额外依赖）

插值范围:
    最大范围：300km × 300km
    固定分辨率：30米 × 30米

投影说明：
    输出坐标系为 EPSG:4326 (WGS 84) 经纬度坐标。
    经度方向分辨率根据数据中心纬度修正：res_lon = resolution / (111000 * cos(center_lat))
    纬度方向分辨率：res_lat = resolution / 111000

内存优化说明（运行环境 32G，占用不超过 10G）：
    - 使用生成器迭代采样点，避免一次性构建大型中间列表
    - 超过 max_sample_points 时随机抽样，严格控制采样点总数
    - scipy方法：按 chunk_size 行分块处理，逐块释放临时数组
    - 各阶段处理后及时 del 临时数组并调用 gc.collect()
    - 所有GDAL/OGR对象使用后立即置None释放
    - run()方法使用try-finally确保异常时也能释放资源
"""

import gc
import logging
import math
import os
import random
import re
import time
import warnings
from typing import Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

import numpy as np
from osgeo import gdal, ogr, osr

# ============================================================
# Django settings 导入（可选）
# ============================================================
try:
    from django.conf import settings as _django_settings
    _DJANGO_AVAILABLE = True
except ImportError:
    _django_settings = None
    _DJANGO_AVAILABLE = False

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.kml_to_Ia')

# ==================== QGIS 插值相关模块 ====================
# 以下模块在 QGIS 3.40.15 Python 环境中内置
from qgis.analysis import (
    QgsGridFileWriter,
    QgsIDWInterpolator,
    QgsInterpolator,
    QgsTinInterpolator,
)
from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsRectangle,
    QgsVectorLayer,
)

# QGIS 3.40 使用 QMetaType.Type.Double 代替旧版 QVariant.Double
from PyQt5.QtCore import QMetaType

# ==================== 可选第三方库（scipy / pykrige）====================
try:
    warnings.filterwarnings(
        "ignore",
        message=r"A NumPy version .* is required for this version of SciPy",
        category=UserWarning,
        module=r"scipy",
    )
    from scipy.interpolate import (
        RBFInterpolator as _RBFInterpolator,
        LinearNDInterpolator as _LinearNDInterpolator,
        CloughTocher2DInterpolator as _CloughTocher2DInterpolator,
        NearestNDInterpolator as _NearestNDInterpolator,
        interp1d as _interp1d,
    )
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

try:
    from pykrige.ok import OrdinaryKriging as _OrdinaryKriging
    _HAS_PYKRIGE = True
except ImportError:
    _HAS_PYKRIGE = False

# ==================== 启用GDAL异常处理 ====================
gdal.UseExceptions()

# ==================== KML PGA值解析正则 ====================
# 匹配格式如 "0.01g"、"0.05G"、"0.10 g" 等
_PGA_NAME_PATTERN = re.compile(r'^([0-9]*\.?[0-9]+)\s*[gG]$')


class KmlToIaConverter:
    """
    KML转Ia栅格文件转换器（QGIS 3.40.15，内存优化版 v3.3）

    将地震局提供的KML格式PGA等值线文件，经过解析、插值计算后，
    输出Ia.tif栅格文件（可选输出PGA.tif，使用矢量栅格化非插值）。

    输出坐标系: EPSG:4326 (WGS 84)

    主要特性:
        - 只对Ia进行插值，PGA.tif使用等值线矢量栅格化生成
        - 经纬度分辨率区分方向，修正纬度对经度距离的影响
        - 采样点数量可控（sample_interval + max_sample_points），避免内存溢出
        - 支持6种插值方法（scipy_tin推荐，平滑无突变）
        - 严格内存控制（<10GB）：生成器、分批转换、分块写入、及时释放
        - 异常安全：run()使用try-finally，异常时也能释放资源
        - 所有关键方法添加try-except + logger日志 + 异常向上抛出

    支持的插值方法:
        - 'scipy_tin'（默认/推荐）：scipy Delaunay三角网插值，C1/C0连续，平滑无突变
        - 'radial'     ：径向距离1D插值，专为同心圈优化，完美单调递增
        - 'scipy_idw'  ：scipy RBF插值，速度快，支持邻近点限制，需安装scipy
        - 'kriging'    ：pykrige普通克里金插值，统计精度最高，需安装pykrige
        - 'qgis_idw'   ：QGIS反距离权重，适合稀疏/不均匀数据，无需额外依赖
        - 'qgis_tin'   ：QGIS三角网插值，基于Delaunay三角剖分，无需额外依赖

    用法示例:
        converter = KmlToIaConverter(
            kml_path="path/to/source.kml",
            ia_output_path="path/to/Ia.tif",
            interp_method='scipy_tin',
            scipy_tin_smooth=True,
            sample_interval=5,
            max_sample_points=50000,
        )
        converter.run()
    """

    # ==================== 常量定义 ====================
    GRAVITY_ACCELERATION = 9.8   # 重力加速度 (m/s²)
    COEFFICIENT_A = 0.797        # Ia计算公式系数a: log10(Ia) = a + b*log10(PGA)
    COEFFICIENT_B = 1.837        # Ia计算公式系数b
    KML_NAMESPACE = {'kml': 'http://www.opengis.net/kml/2.2'}
    # 每度对应的地面距离(米)，用于纬度方向的分辨率换算
    METERS_PER_DEGREE = 111_000.0

    def __init__(
        self,
        kml_path: str,
        ia_output_path: str,
        pga_output_path: Optional[str] = None,
        resolution: float = 30.0,           # 目标分辨率(米)，推荐范围 10~100 m

        # 采样参数
        sample_interval: int = 5,           # 等值线坐标采样间隔，推荐 3~10
        max_sample_points: int = 50000,     # 最大采样点数

        export_pga: bool = False,           # 是否同时输出 PGA.tif（矢量栅格化）
        interp_method: str = 'scipy_tin',   # 插值方法

        # ---- QGIS IDW 参数 ----
        qgis_idw_power: float = 2.0,

        # ---- QGIS TIN 参数 ----
        qgis_tin_method: int = 0,

        # ---- scipy IDW/RBF 参数 ----
        scipy_kernel: str = 'thin_plate_spline',
        scipy_neighbors: int = 100,

        # ---- scipy TIN 参数 ----
        scipy_tin_smooth: bool = True,

        # ---- 径向插值参数 ----
        radial_kind: str = 'cubic',

        # ---- 克里金参数 ----
        kriging_variogram: str = 'linear',
        kriging_nlags: int = 6,
        kriging_neighbors: int = 50,

        # ---- 内存优化参数 ----
        chunk_size: int = 1000,
        max_memory_gb: float = 10.0,
    ):
        self.kml_path = kml_path
        self.ia_output_path = ia_output_path
        self.pga_output_path = pga_output_path
        self.resolution = resolution
        self.sample_interval = sample_interval
        self.max_sample_points = max_sample_points
        self.export_pga = export_pga

        # 处理插值方法名（向后兼容）
        _method = interp_method.lower().strip()
        if _method == 'idw':
            _method = 'qgis_idw'
        elif _method == 'tin':
            _method = 'qgis_tin'
        self.interp_method = _method

        # QGIS IDW 参数
        self.qgis_idw_power = qgis_idw_power
        # QGIS TIN 参数
        self.qgis_tin_method = qgis_tin_method
        # scipy IDW/RBF 参数
        self.scipy_kernel = scipy_kernel
        self.scipy_neighbors = scipy_neighbors
        # scipy TIN 参数
        self.scipy_tin_smooth = scipy_tin_smooth
        # 径向插值参数
        self.radial_kind = radial_kind
        # 克里金参数
        self.kriging_variogram = kriging_variogram
        self.kriging_nlags = kriging_nlags
        self.kriging_neighbors = kriging_neighbors
        # 内存优化参数
        self.chunk_size = chunk_size
        self.max_memory_gb = max_memory_gb

        # 运行时数据（由 run() 过程填充）
        self._contours: List[dict] = []
        self._output_epsg: int = 0
        self._output_srs: Optional[osr.SpatialReference] = None
        self._geo_transform: Optional[tuple] = None
        self._n_cols: int = 0
        self._n_rows: int = 0
        self._x_min: float = 0.0
        self._x_max: float = 0.0
        self._y_min: float = 0.0
        self._y_max: float = 0.0
        self._res_lon: float = 0.0   # 经度方向分辨率（度），考虑纬度余弦
        self._res_lat: float = 0.0   # 纬度方向分辨率（度）

    # ==================== KML 解析 ====================

    def parse_kml(self) -> List[dict]:
        """
        解析KML文件，提取所有PGA等值线数据（内存优化版）

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
            ValueError: KML文件中无有效等值线
        """
        try:
            if not os.path.exists(self.kml_path):
                raise FileNotFoundError(f"KML文件不存在: {self.kml_path}")

            tree = ET.parse(self.kml_path)
            root = tree.getroot()
            ns = self.KML_NAMESPACE

            contours = []

            for placemark in root.findall('.//kml:Placemark', ns):
                name_elem = placemark.find('kml:name', ns)
                coords_elem = placemark.find('.//kml:coordinates', ns)

                if name_elem is None or coords_elem is None:
                    continue

                name_text = (name_elem.text or '').strip()
                if not name_text:
                    continue

                # 使用正则安全解析PGA值（匹配 "0.01g"、"0.05G"、"0.10 g" 等）
                match = _PGA_NAME_PATTERN.match(name_text)
                if not match:
                    logger.warning("无法解析PGA值 '%s'，跳过该等值线", name_text)
                    continue

                try:
                    pga_g = float(match.group(1))
                except ValueError:
                    logger.warning("PGA数值转换失败 '%s'，跳过", name_text)
                    continue

                if pga_g <= 0:
                    logger.warning("PGA值 <= 0 '%s'，跳过", name_text)
                    continue

                # g → m/s²
                pga_mps2 = pga_g * self.GRAVITY_ACCELERATION

                # 计算Ia
                ia = self._calculate_ia(pga_mps2)

                # 解析坐标 "lon,lat,alt lon,lat,alt ..."
                coords_text = (coords_elem.text or '').strip()
                if not coords_text:
                    logger.warning("等值线 '%s' 坐标为空，跳过", name_text)
                    continue

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
                    logger.warning("等值线 '%s' 坐标点不足(%d)，跳过",
                                   name_text, len(coordinates))
                    continue

                contours.append({
                    'name': name_text,
                    'pga_g': pga_g,
                    'pga_mps2': pga_mps2,
                    'ia': ia,
                    'coordinates': coordinates
                })

            # 解析完成后立即释放 XML 树，节省内存
            del tree, root
            gc.collect()

            if not contours:
                raise ValueError("KML文件中未找到有效的PGA等值线")

            # 按PGA值从大到小排序（内圈→外圈）
            contours.sort(key=lambda x: x['pga_g'], reverse=True)

            logger.info("成功解析 %d 条PGA等值线", len(contours))
            for c in contours:
                logger.info("  %s: PGA=%.4f m/s², Ia=%.6f m/s, 坐标点数=%d",
                            c['name'], c['pga_mps2'], c['ia'], len(c['coordinates']))

            self._contours = contours
            return contours

        except (FileNotFoundError, ValueError):
            raise
        except ET.ParseError as exc:
            logger.error("KML文件解析失败: %s", exc, exc_info=True)
            raise ValueError(f"KML文件格式错误: {exc}") from exc
        except Exception as exc:
            logger.error("parse_kml 异常: %s", exc, exc_info=True)
            raise

    # ==================== Ia 计算 ====================

    @staticmethod
    def _calculate_ia(pga: float) -> float:
        """
        根据PGA计算Ia(阿里亚斯强度)

        公式: log10(Ia) = a + b * log10(PGA)
              Ia = 10^(a + b * log10(PGA))

        参数:
            pga (float): 峰值地面加速度 (m/s²)，必须 > 0

        返回:
            float: 阿里亚斯强度 Ia (m/s)
        """
        if pga <= 0:
            return 0.0
        try:
            log_ia = KmlToIaConverter.COEFFICIENT_A + \
                     KmlToIaConverter.COEFFICIENT_B * math.log10(pga)
            return 10.0 ** log_ia
        except (ValueError, OverflowError) as exc:
            logger.error("Ia计算异常: pga=%.6f, error=%s", pga, exc)
            raise

    # ==================== 投影与坐标系设置 ====================

    def _setup_output_crs(self):
        """
        设置输出坐标系为 EPSG:4326 (WGS 84) 并创建空间参考对象。

        输出直接使用经纬度坐标系，无需坐标转换。
        """
        try:
            self._output_epsg = 4326

            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            self._output_srs = srs

            logger.info("输出坐标系: EPSG:4326 (WGS 84)")
        except Exception as exc:
            logger.error("设置输出坐标系失败: %s", exc, exc_info=True)
            raise

    # ==================== 采样点准备 ====================

    def _iter_sample_points(self) -> Iterator[Tuple[float, float, float]]:
        """
        生成器：逐条遍历等值线并按间隔采样，逐点输出（内存优化）

        生成:
            (lon, lat, ia_val): 每个采样点的经纬度和对应的 Ia 值
        """
        for contour in self._contours:
            coords = contour['coordinates']
            ia_val = contour['ia']

            sampled: List[tuple] = list(coords[::self.sample_interval])
            if len(coords) > 1:
                last_pt = coords[-1]
                if not sampled or sampled[-1] != last_pt:
                    sampled.append(last_pt)

            for lon, lat in sampled:
                yield lon, lat, ia_val

    def _prepare_sample_points(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        从等值线中提取并下采样坐标点，作为插值的输入采样点（内存优化版）

        处理流程:
            1. 使用生成器按 sample_interval 间隔采样
            2. 若采样点数超过 max_sample_points，随机抽样到该数量
            3. 去除完全重叠的坐标点（防止插值奇异矩阵）

        返回:
            tuple: (x_arr, y_arr, ia_values)，经纬度坐标和Ia值数组

        异常:
            ValueError: 没有有效的采样点
        """
        try:
            rows = list(self._iter_sample_points())
            if not rows:
                raise ValueError("没有有效的采样点")

            total_before = len(rows)
            logger.info("采样点统计: 采样间隔=%d, 原始采样点数=%d",
                        self.sample_interval, total_before)

            # 若采样点数超过 max_sample_points，随机抽样
            if total_before > self.max_sample_points:
                rows = random.sample(rows, self.max_sample_points)
                logger.info("超过最大采样点数限制(%d)，随机抽样至 %d 个点",
                            self.max_sample_points, len(rows))

            lons_arr = np.array([r[0] for r in rows], dtype=np.float64)
            lats_arr = np.array([r[1] for r in rows], dtype=np.float64)
            ia_arr   = np.array([r[2] for r in rows], dtype=np.float32)
            del rows
            gc.collect()

            # EPSG:4326 无需坐标转换，直接使用经纬度作为x/y
            x_out = lons_arr
            y_out = lats_arr

            # -------- 去重处理 --------
            # 四舍五入到0.00001度精度（约1m）后去重
            coords_rounded = np.round(np.column_stack([x_out, y_out]), decimals=5)
            _, unique_idx = np.unique(coords_rounded, axis=0, return_index=True)
            del coords_rounded
            unique_idx.sort()

            x_out  = x_out[unique_idx]
            y_out  = y_out[unique_idx]
            ia_arr = ia_arr[unique_idx]
            del unique_idx
            gc.collect()

            logger.info("去重后有效采样点数: %d", len(x_out))
            logger.info("Ia值范围: %.6f ~ %.6f m/s", ia_arr.min(), ia_arr.max())

            return x_out, y_out, ia_arr

        except ValueError:
            raise
        except Exception as exc:
            logger.error("_prepare_sample_points 异常: %s", exc, exc_info=True)
            raise

    # ==================== 栅格网格构建 ====================

    def _build_grid(self, x_arr: np.ndarray, y_arr: np.ndarray):
        """
        根据采样点范围构建输出栅格网格参数（EPSG:4326，经纬度坐标）

        经纬度分辨率区分方向:
            - 纬度方向: res_lat = resolution / 111000
            - 经度方向: res_lon = resolution / (111000 * cos(center_lat))

        在数据范围外扩展 10 个像素作为缓冲区。

        参数:
            x_arr (np.ndarray): 采样点经度坐标（度）
            y_arr (np.ndarray): 采样点纬度坐标（度）
        """
        try:
            # 计算数据中心纬度，用于修正经度方向分辨率
            center_lat = float(np.mean(y_arr))
            cos_lat = math.cos(math.radians(center_lat))
            # 避免极端情况（极地附近cos接近0）
            if cos_lat < 0.01:
                cos_lat = 0.01
                logger.warning("数据中心纬度 %.4f° 接近极地，经度分辨率修正受限", center_lat)

            # 纬度方向分辨率（度）
            self._res_lat = self.resolution / self.METERS_PER_DEGREE
            # 经度方向分辨率（度），考虑纬度对经度距离的影响
            self._res_lon = self.resolution / (self.METERS_PER_DEGREE * cos_lat)

            buffer_x = self._res_lon * 10
            buffer_y = self._res_lat * 10

            x_min = float(x_arr.min()) - buffer_x
            x_max = float(x_arr.max()) + buffer_x
            y_min = float(y_arr.min()) - buffer_y
            y_max = float(y_arr.max()) + buffer_y

            self._n_cols = int(np.ceil((x_max - x_min) / self._res_lon))
            self._n_rows = int(np.ceil((y_max - y_min) / self._res_lat))

            # 防止栅格尺寸为0
            if self._n_cols <= 0 or self._n_rows <= 0:
                raise ValueError(
                    f"计算得到的栅格尺寸无效: {self._n_cols} 列 × {self._n_rows} 行，"
                    f"数据范围: X[{x_min:.6f}, {x_max:.6f}] Y[{y_min:.6f}, {y_max:.6f}]"
                )

            # GeoTIFF 仿射变换参数:
            # (左上角X, 像素宽度, 旋转, 左上角Y, 旋转, 像素高度负值)
            self._geo_transform = (x_min, self._res_lon, 0.0,
                                   y_max, 0.0, -self._res_lat)

            self._x_min = x_min
            self._x_max = x_max
            self._y_min = y_min
            self._y_max = y_max

            logger.info("栅格网格信息:")
            logger.info("  中心纬度: %.4f°, cos(lat)=%.6f", center_lat, cos_lat)
            logger.info("  经度方向分辨率: %.1fm ≈ %.6f°", self.resolution, self._res_lon)
            logger.info("  纬度方向分辨率: %.1fm ≈ %.6f°", self.resolution, self._res_lat)
            logger.info("  网格大小: %d 列 × %d 行", self._n_cols, self._n_rows)
            logger.info("  经度范围: %.6f° ~ %.6f°", x_min, x_max)
            logger.info("  纬度范围: %.6f° ~ %.6f°", y_min, y_max)
            logger.info("  总像素数: %s", f"{self._n_cols * self._n_rows:,}")

            # 估算峰值内存：4 个 float32 中间数组（输入网格、插值结果、NaN掩码、输出缓冲）
            # 每像素 4 bytes × 4 数组 = 16 bytes/pixel
            memory_bytes = self._n_cols * self._n_rows * 4 * 4
            memory_gb = memory_bytes / (1024 ** 3)
            logger.info("  估算峰值内存: %.2f GB", memory_gb)
            if memory_gb > self.max_memory_gb:
                logger.warning("估算内存(%.2fGB)超过限制(%.2fGB)，"
                               "请减小插值范围或增大sample_interval/max_sample_points",
                               memory_gb, self.max_memory_gb)

        except ValueError:
            raise
        except Exception as exc:
            logger.error("_build_grid 异常: %s", exc, exc_info=True)
            raise

    # ==================== QGIS 插值方法 ====================

    def _build_qgs_vector_layer(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
        values: np.ndarray,
        field_name: str = 'value',
    ) -> QgsVectorLayer:
        """
        将采样点数组构建为 QGIS 内存矢量图层，供 QGIS 插值算法使用。

        参数:
            x_arr (np.ndarray): 采样点经度坐标
            y_arr (np.ndarray): 采样点纬度坐标
            values (np.ndarray): 采样点对应值
            field_name (str): 值字段名，默认'value'

        返回:
            QgsVectorLayer: QGIS 内存点图层
        """
        try:
            crs_auth_id = f"EPSG:{self._output_epsg}"
            layer = QgsVectorLayer(
                f"Point?crs={crs_auth_id}", "sample_points", "memory"
            )
            if not layer.isValid():
                raise RuntimeError(f"QGIS内存矢量图层创建失败，CRS: {crs_auth_id}")

            provider = layer.dataProvider()
            provider.addAttributes([QgsField(field_name, QMetaType.Type.Double)])
            layer.updateFields()

            # 获取字段定义，确保每个 QgsFeature 属性正确关联
            fields = layer.fields()

            batch_size = 5000
            batch: List[QgsFeature] = []
            for xi, yi, vi in zip(x_arr, y_arr, values):
                feat = QgsFeature(fields)
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

        except Exception as exc:
            logger.error("构建QGIS矢量图层失败: %s", exc, exc_info=True)
            raise

    def _run_qgis_interpolation(
            self,
            x_arr: np.ndarray,
            y_arr: np.ndarray,
            values: np.ndarray,
            output_tif_path: str,
    ) -> None:
        """
        使用 QGIS 插值器进行插值，通过 QgsGridFileWriter 调用 C++ 内部循环
        代替逐像素的 Python 调用，大幅提升性能。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        layer = None
        out_ds = None
        src_ds = None
        band = None
        interpolator = None
        tmp_grid_path = None

        try:
            layer = self._build_qgs_vector_layer(x_arr, y_arr, values)

            layer_data = QgsInterpolator.LayerData()
            layer_data.source = layer
            layer_data.valueSource = QgsInterpolator.ValueSource.ValueAttribute
            layer_data.interpolationAttribute = 0
            layer_data.sourceType = QgsInterpolator.SourceType.SourcePoints

            method = self.interp_method
            if method == 'qgis_idw':
                interpolator = QgsIDWInterpolator([layer_data])
                interpolator.setDistanceCoefficient(self.qgis_idw_power)
                logger.info("使用 QGIS IDW 插值，幂次=%.1f", self.qgis_idw_power)
            elif method == 'qgis_tin':
                tin_enum = (
                    QgsTinInterpolator.TinInterpolation.Linear
                    if self.qgis_tin_method == 0
                    else QgsTinInterpolator.TinInterpolation.CloughTocher
                )
                interpolator = QgsTinInterpolator([layer_data], tin_enum)
                logger.info("使用 QGIS TIN 插值，方法=%d", self.qgis_tin_method)
            else:
                raise ValueError(f"不支持的QGIS插值方法: '{method}'")

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)

            start_time = time.time()
            logger.info("使用 QgsGridFileWriter 插值（C++内部循环），共 %d 列 × %d 行 = %s 像素...",
                        self._n_cols, self._n_rows, f"{self._n_cols * self._n_rows:,}")

            # QgsGridFileWriter 在 C++ 层面完成插值循环，避免逐像素 Python 调用
            extent = QgsRectangle(self._x_min, self._y_min, self._x_max, self._y_max)
            tmp_grid_path = output_tif_path + '.tmp_grid.asc'
            self._ensure_file_writable(tmp_grid_path)
            writer = QgsGridFileWriter(
                interpolator,
                tmp_grid_path,
                extent,
                self._n_cols,
                self._n_rows,
                self._res_lon,
                self._res_lat,
            )
            ret = writer.writeFile()
            if ret != 0:
                raise RuntimeError(f"QgsGridFileWriter.writeFile() 失败，返回值: {ret}")

            # 将生成的 ASCII 网格文件转为 GeoTIFF，并修正坐标系/地理变换
            src_ds = gdal.Open(tmp_grid_path)
            if src_ds is None:
                raise RuntimeError(f"无法打开临时网格文件: {tmp_grid_path}")

            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.CreateCopy(
                output_tif_path, src_ds,
                options=['COMPRESS=LZW', 'TILED=YES'],
            )
            src_ds = None  # 关闭源数据集（GDAL临时文件可释放）
            if out_ds is None:
                raise RuntimeError(f"GeoTIFF文件创建失败: {output_tif_path}")

            # 强制设置正确的仿射变换和坐标系，覆盖 CreateCopy 可能推断出的值
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._output_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            # 将有效像素中负值裁剪为 0（插值器可能返回微小负值）
            data = band.ReadAsArray()
            nodata_mask = data == -9999.0
            np.maximum(data, 0.0, out=data)
            data[nodata_mask] = -9999.0  # 恢复 nodata 像素
            ret = band.WriteArray(data)
            if ret != 0:
                raise RuntimeError("band.WriteArray() 写入失败")
            del data, nodata_mask

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("QGIS插值完成，总耗时: %.1fs, 已保存: %s", total_time, output_tif_path)

        except Exception as exc:
            logger.error("QGIS插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            src_ds = None
            band = None
            if tmp_grid_path and os.path.exists(tmp_grid_path):
                try:
                    os.remove(tmp_grid_path)
                except OSError as e:
                    logger.warning("删除临时网格文件失败: %s", e)
            del interpolator, layer
            gc.collect()

    # ==================== scipy 插值方法 ====================

    def _run_scipy_interpolation(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
        values: np.ndarray,
        output_tif_path: str,
    ) -> None:
        """
        使用 scipy RBFInterpolator 进行IDW近似插值，分块写入 GeoTIFF。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'scipy_idw' 方法。"
                "请在QGIS Python环境中运行: pip install scipy"
            )

        rbf = None
        out_ds = None
        band = None

        try:
            X_train = np.column_stack([x_arr, y_arr])
            rbf = _RBFInterpolator(
                X_train,
                values.astype(np.float64),
                kernel=self.scipy_kernel,
                neighbors=self.scipy_neighbors,
            )
            del X_train
            gc.collect()
            logger.info("scipy RBFInterpolator 已建立模型，核函数=%s，邻近点数=%d",
                        self.scipy_kernel, self.scipy_neighbors)

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            if out_ds is None:
                raise RuntimeError(f"创建GeoTIFF文件失败: {output_tif_path}")
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._output_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            # 预先计算全部行的 Y 坐标（中心点），按块切片复用
            full_grid_y = self._y_max - (np.arange(n_rows) + 0.5) * self._res_lat

            # 预分配 pts 缓冲区（最大块大小），循环中复用，避免每次重新分配
            # X 坐标在所有块中相同（tile），预先填充到 pts_buf 中
            max_chunk_pts = chunk_rows * self._n_cols
            pts_buf = np.empty((max_chunk_pts, 2), dtype=np.float64)
            pts_buf[:, 0] = np.tile(grid_x, chunk_rows)

            start_time = time.time()
            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start
                actual_pts = actual_rows * self._n_cols

                grid_y = full_grid_y[row_start:row_end]
                pts = pts_buf[:actual_pts]
                # 直接广播赋值，避免 meshgrid + ravel 的中间拷贝
                pts_y_view = pts[:, 1].reshape(actual_rows, self._n_cols)
                pts_y_view[:] = grid_y[:, np.newaxis]

                try:
                    chunk_vals = rbf(pts).reshape(actual_rows, self._n_cols)
                except Exception as exc:
                    logger.error("scipy RBF 插值第 %d 块失败: %s", chunk_idx, exc)
                    raise

                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                ret = band.WriteArray(chunk_vals.astype(np.float32), 0, row_start)
                if ret != 0:
                    raise RuntimeError(f"band.WriteArray() 写入失败（第 {chunk_idx} 块）")
                del chunk_vals
                # 每 10 块清理一次 GC，避免过于频繁的 GC 开销
                if (chunk_idx + 1) % 10 == 0:
                    gc.collect()

                if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                    elapsed = time.time() - start_time
                    logger.info("scipy_idw 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("scipy_idw 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except Exception as exc:
            logger.error("scipy_idw 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del rbf
            gc.collect()

    # ==================== scipy TIN 插值方法 ====================

    def _run_scipy_tin_interpolation(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
        values: np.ndarray,
        output_tif_path: str,
    ) -> None:
        """
        使用 scipy Delaunay 三角网插值，分块写入 GeoTIFF。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'scipy_tin' 方法。"
                "请在QGIS Python环境中运行: pip install scipy"
            )

        interp = None
        nn_interp = None  # NearestNDInterpolator for NaN fill outside convex hull
        out_ds = None
        band = None

        try:
            points = np.column_stack([x_arr, y_arr])

            if self.scipy_tin_smooth:
                interp = _CloughTocher2DInterpolator(points, values.astype(np.float64))
                logger.info("使用 scipy CloughTocher TIN 插值（C1连续，最平滑）")
            else:
                interp = _LinearNDInterpolator(points, values.astype(np.float64))
                logger.info("使用 scipy Linear TIN 插值（C0连续，更快）")

            del points
            gc.collect()

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            if out_ds is None:
                raise RuntimeError(f"创建GeoTIFF文件失败: {output_tif_path}")
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._output_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            # 预先计算全部行的 Y 坐标（中心点），按块切片复用
            full_grid_y = self._y_max - (np.arange(n_rows) + 0.5) * self._res_lat
            start_time = time.time()

            # 使用 NearestNDInterpolator 填充三角网凸包外的 NaN 像素
            # （比 RBFInterpolator neighbors=1 更准确、更快）
            nn_interp = _NearestNDInterpolator(
                np.column_stack([x_arr, y_arr]),
                values.astype(np.float64),
            )

            # 预分配 pts 缓冲区（最大块大小），循环中复用，避免每次重新分配
            # X 坐标在所有块中相同（tile），预先填充到 pts_buf 中
            max_chunk_pts = chunk_rows * self._n_cols
            pts_buf = np.empty((max_chunk_pts, 2), dtype=np.float64)
            pts_buf[:, 0] = np.tile(grid_x, chunk_rows)

            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start
                actual_pts = actual_rows * self._n_cols

                grid_y = full_grid_y[row_start:row_end]
                pts = pts_buf[:actual_pts]
                # 直接广播赋值，避免 meshgrid + ravel 的中间拷贝
                pts_y_view = pts[:, 1].reshape(actual_rows, self._n_cols)
                pts_y_view[:] = grid_y[:, np.newaxis]

                try:
                    chunk_vals = interp(pts)
                except Exception as exc:
                    logger.error("scipy_tin 插值第 %d 块失败: %s", chunk_idx, exc)
                    raise

                # 用最近邻值填充三角网外部的 NaN 像素（凸包以外区域）
                nan_mask = np.isnan(chunk_vals)
                if nan_mask.any():
                    try:
                        chunk_vals[nan_mask] = nn_interp(pts[nan_mask])
                    except Exception as exc:
                        logger.warning("NaN填充失败（第%d块），使用0填充: %s", chunk_idx, exc)
                        chunk_vals[nan_mask] = 0.0

                chunk_vals = chunk_vals.reshape(actual_rows, self._n_cols)
                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                ret = band.WriteArray(chunk_vals.astype(np.float32), 0, row_start)
                if ret != 0:
                    raise RuntimeError(f"band.WriteArray() 写入失败（第 {chunk_idx} 块）")
                del chunk_vals
                # 每 10 块清理一次 GC，避免过于频繁的 GC 开销
                if (chunk_idx + 1) % 10 == 0:
                    gc.collect()

                if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                    elapsed = time.time() - start_time
                    logger.info("scipy_tin 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("scipy_tin 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except Exception as exc:
            logger.error("scipy_tin 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del interp, nn_interp
            gc.collect()

    # ==================== 径向距离插值方法 ====================

    def _run_radial_interpolation(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
        values: np.ndarray,
        output_tif_path: str,
    ) -> None:
        """
        径向距离插值 —— 专为同心环状等值线优化。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'radial' 方法。"
                "请在QGIS Python环境中运行: pip install scipy"
            )

        interp_func = None
        out_ds = None
        band = None

        try:
            # 使用最高PGA等值线（self._contours 已按PGA降序排列，首项即最高值）的重心
            # 作为径向插值的震中，比全采样点均值在非均匀采样时更几何准确
            highest_coords = np.array(self._contours[0]['coordinates'], dtype=np.float64)
            center_x = float(np.mean(highest_coords[:, 0]))
            center_y = float(np.mean(highest_coords[:, 1]))
            del highest_coords
            logger.info("震中坐标（最高PGA等值线重心）: (%.6f°, %.6f°)", center_x, center_y)

            # 计算距离时考虑经纬度的不等距性
            # 使用加权欧氏距离：经度方向乘以 cos(center_lat)
            cos_lat = math.cos(math.radians(center_y))
            dx = (x_arr - center_x) * cos_lat
            dy = y_arr - center_y
            distances = np.sqrt(dx ** 2 + dy ** 2)

            sorted_idx = np.argsort(distances)
            dist_sorted = distances[sorted_idx]
            val_sorted = values[sorted_idx]
            del distances, sorted_idx, dx, dy
            gc.collect()

            # 对距离去重：将距离相差不超过 _res_lat/2 的点合并取均值（向量化实现）
            # 使用 np.diff + np.cumsum 划分组，再用 np.bincount 聚合统计
            tol = self._res_lat / 2.0
            breaks = np.concatenate([[True], np.diff(dist_sorted) >= tol])
            group_ids = np.cumsum(breaks) - 1  # 0-indexed 组编号

            group_sums = np.bincount(group_ids, weights=val_sorted)
            group_cnts = np.bincount(group_ids)
            val_arr = group_sums / group_cnts

            # 取每组第一个距离值作为代表距离
            _, first_idx = np.unique(group_ids, return_index=True)
            dist_arr = dist_sorted[first_idx]

            del dist_sorted, val_sorted, breaks, group_ids, group_sums, group_cnts, first_idx
            gc.collect()

            logger.info("距离范围: %.6f° ~ %.6f°，合并后控制点数: %d",
                        dist_arr[0], dist_arr[-1], len(dist_arr))

            interp_func = _interp1d(
                dist_arr, val_arr,
                kind=self.radial_kind,
                bounds_error=False,
                fill_value=(float(val_arr[0]), float(val_arr[-1])),
            )
            del dist_arr, val_arr
            gc.collect()

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            if out_ds is None:
                raise RuntimeError(f"创建GeoTIFF文件失败: {output_tif_path}")
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._output_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            # 预先计算全部行的 Y 坐标（中心点），按块切片复用
            full_grid_y = self._y_max - (np.arange(n_rows) + 0.5) * self._res_lat
            start_time = time.time()

            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start

                grid_y = full_grid_y[row_start:row_end]
                xx, yy = np.meshgrid(grid_x, grid_y)

                # 计算距离时同样考虑经纬度不等距性
                pixel_dx = (xx - center_x) * cos_lat
                pixel_dy = yy - center_y
                pixel_dists = np.sqrt(pixel_dx ** 2 + pixel_dy ** 2)
                del xx, yy, pixel_dx, pixel_dy

                try:
                    chunk_vals = interp_func(pixel_dists).reshape(actual_rows, self._n_cols)
                except Exception as exc:
                    logger.error("radial 插值第 %d 块失败: %s", chunk_idx, exc)
                    raise
                del pixel_dists
                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                ret = band.WriteArray(chunk_vals.astype(np.float32), 0, row_start)
                if ret != 0:
                    raise RuntimeError(f"band.WriteArray() 写入失败（第 {chunk_idx} 块）")
                del chunk_vals
                # 每 10 块清理一次 GC，避免过于频繁的 GC 开销
                if (chunk_idx + 1) % 10 == 0:
                    gc.collect()

                if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                    elapsed = time.time() - start_time
                    logger.info("radial 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("radial 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except Exception as exc:
            logger.error("radial 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del interp_func
            gc.collect()

    # ==================== 克里金插值方法 ====================

    def _run_kriging_interpolation(
        self,
        x_arr: np.ndarray,
        y_arr: np.ndarray,
        values: np.ndarray,
        output_tif_path: str,
    ) -> None:
        """
        使用 pykrige 普通克里金（Ordinary Kriging）插值，分块写入 GeoTIFF。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_PYKRIGE:
            raise ImportError(
                "pykrige 未安装，无法使用 'kriging' 方法。"
                "请在QGIS Python环境中运行: pip install pykrige"
            )

        ok = None
        out_ds = None
        band = None

        try:
            logger.info("使用 pykrige 普通克里金，变差函数=%s，滞后数=%d，邻近点数=%d",
                        self.kriging_variogram, self.kriging_nlags, self.kriging_neighbors)

            ok = _OrdinaryKriging(
                x_arr.astype(np.float64),
                y_arr.astype(np.float64),
                values.astype(np.float64),
                variogram_model=self.kriging_variogram,
                nlags=self.kriging_nlags,
                verbose=False,
                enable_plotting=False,
            )
            gc.collect()

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            if out_ds is None:
                raise RuntimeError(f"创建GeoTIFF文件失败: {output_tif_path}")
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._output_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            # 预先计算全部行的 Y 坐标（中心点），按块切片复用
            full_grid_y = self._y_max - (np.arange(n_rows) + 0.5) * self._res_lat
            start_time = time.time()

            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                row_end = min(row_start + chunk_rows, n_rows)

                grid_y = full_grid_y[row_start:row_end]

                try:
                    z, _ss = ok.execute(
                        'grid',
                        grid_x.astype(np.float64),
                        grid_y.astype(np.float64),
                        n_closest_points=self.kriging_neighbors,
                        backend='vectorized',   # vectorized 比 loop 快数倍
                    )
                except Exception as exc:
                    logger.error("kriging 插值第 %d 块失败: %s", chunk_idx, exc)
                    raise
                del _ss

                chunk_vals = np.array(z, dtype=np.float32)
                del z
                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                ret = band.WriteArray(chunk_vals, 0, row_start)
                if ret != 0:
                    raise RuntimeError(f"band.WriteArray() 写入失败（第 {chunk_idx} 块）")
                del chunk_vals
                # 每 10 块清理一次 GC，避免过于频繁的 GC 开销
                if (chunk_idx + 1) % 10 == 0:
                    gc.collect()

                if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                    elapsed = time.time() - start_time
                    logger.info("kriging 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("kriging 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except Exception as exc:
            logger.error("kriging 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del ok
            gc.collect()

    # ==================== 统一插值入口 ====================

    def _interpolate_ia_to_file(
            self,
            x_arr: np.ndarray,
            y_arr: np.ndarray,
            ia_values: np.ndarray,
            output_tif_path: str,
    ) -> None:
        """
        统一插值入口：只对Ia进行插值，根据 self.interp_method 路由到对应方法。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            ia_values: 采样点对应的Ia值
            output_tif_path: 输出Ia GeoTIFF 文件路径
        """
        try:
            method = self.interp_method

            if method in ('qgis_idw', 'qgis_tin'):
                self._run_qgis_interpolation(x_arr, y_arr, ia_values, output_tif_path)
            elif method == 'scipy_idw':
                self._run_scipy_interpolation(x_arr, y_arr, ia_values, output_tif_path)
            elif method == 'scipy_tin':
                self._run_scipy_tin_interpolation(x_arr, y_arr, ia_values, output_tif_path)
            elif method == 'radial':
                self._run_radial_interpolation(x_arr, y_arr, ia_values, output_tif_path)
            elif method == 'kriging':
                self._run_kriging_interpolation(x_arr, y_arr, ia_values, output_tif_path)
            else:
                raise ValueError(
                    f"不支持的插值方法: '{method}'，"
                    f"可选: 'scipy_tin', 'radial', 'scipy_idw', 'kriging', 'qgis_idw', 'qgis_tin'"
                )
        except Exception as exc:
            logger.error("插值失败 (method=%s): %s", self.interp_method, exc, exc_info=True)
            raise

    # ==================== PGA 矢量栅格化 ====================

    def _rasterize_pga_contours(self) -> None:
        """
        将PGA等值线（闭合LineString）矢量栅格化为PGA.tif（内存优化版）

        此方法使用矢量栅格化（非插值）生成PGA.tif。
        等值线按PGA值从小到大遍历（外圈到内圈），
        内圈覆盖外圈，最终结果正确。
        """
        mem_ds = None
        raster_ds = None
        band = None

        try:
            logger.info("PGA栅格化: 使用OGR矢量→栅格化（非插值）...")

            if not self.pga_output_path:
                logger.warning("PGA输出路径未设置，跳过PGA栅格化")
                return

            min_pga = self._contours[-1]['pga_mps2'] if self._contours else 0.0

            mem_driver = ogr.GetDriverByName('MEM')
            mem_ds = mem_driver.CreateDataSource('pga_contours')
            layer = mem_ds.CreateLayer(
                'contours', srs=self._output_srs, geom_type=ogr.wkbPolygon
            )

            field_defn = ogr.FieldDefn('PGA', ogr.OFTReal)
            layer.CreateField(field_defn)

            for contour in reversed(self._contours):
                coords = contour['coordinates']
                if len(coords) < 3:
                    continue

                ring = ogr.Geometry(ogr.wkbLinearRing)
                for lon, lat in coords:
                    ring.AddPoint(float(lon), float(lat))

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

            raster_driver = gdal.GetDriverByName('MEM')
            raster_ds = raster_driver.Create(
                '', self._n_cols, self._n_rows, 1, gdal.GDT_Float32
            )
            if raster_ds is None:
                raise RuntimeError("内存栅格数据集创建失败")
            raster_ds.SetGeoTransform(self._geo_transform)
            raster_ds.SetProjection(self._output_srs.ExportToWkt())

            band = raster_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)
            band.Fill(min_pga)

            err = gdal.RasterizeLayer(
                raster_ds, [1], layer,
                options=["ATTRIBUTE=PGA"]
            )
            if err != 0:
                raise RuntimeError(f"gdal.RasterizeLayer 失败，错误码: {err}")
            band.ComputeStatistics(False)
            band.FlushCache()

            pga_min = band.GetMinimum()
            pga_max = band.GetMaximum()

            os.makedirs(
                os.path.dirname(os.path.abspath(self.pga_output_path)), exist_ok=True
            )
            self._ensure_file_writable(self.pga_output_path)
            gdal.Translate(
                self.pga_output_path, raster_ds,
                format='GTiff',
                creationOptions=['COMPRESS=LZW', 'TILED=YES'],
                outputType=gdal.GDT_Float32,
            )

            logger.info("PGA栅格化完成，值范围: %.4f ~ %.4f m/s², 已保存: %s",
                        pga_min or 0.0, pga_max or 0.0, self.pga_output_path)

        except Exception as exc:
            logger.error("PGA栅格化失败: %s", exc, exc_info=True)
            raise
        finally:
            mem_ds = None
            raster_ds = None
            band = None
            gc.collect()

    # ==================== 辅助方法 ====================

    def _ensure_file_writable(
        self, file_path: str, max_retries: int = 3, retry_delay: float = 1.0
    ) -> None:
        """
        确保输出文件可写：先检查父目录可写性，再尝试删除已存在的文件。

        参数:
            file_path: 输出文件路径
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        异常:
            RuntimeError: 父目录不可写，或文件无法删除
        """
        # 检查父目录是否可写，防止后续 driver.Create() 因权限问题返回 None
        parent_dir = os.path.dirname(os.path.abspath(file_path))
        if not os.path.isdir(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        if not os.access(parent_dir, os.W_OK):
            raise RuntimeError(
                f"输出目录不可写，请检查权限：\n"
                f"  目录路径: {parent_dir}\n"
                f"  文件路径: {file_path}"
            )

        if not os.path.exists(file_path):
            return

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                os.remove(file_path)
                return
            except OSError as exc:
                last_error = exc
                if attempt < max_retries:
                    logger.warning("删除文件失败（第%d次），%.1f秒后重试: %s",
                                   attempt, retry_delay, file_path)
                    time.sleep(retry_delay)

        raise RuntimeError(
            f"无法删除已存在的输出文件，请检查以下内容后重试：\n"
            f"  文件路径: {file_path}\n"
            f"  错误信息: {last_error}\n"
            f"  可能原因: 文件正被其他程序占用或权限不足\n"
            f"  解决建议: 请关闭所有可能打开该文件的程序，或检查文件/目录的读写权限"
        )

    # ==================== 资源清理 ====================

    def cleanup(self) -> None:
        """
        清理所有运行时资源，释放内存。
        """
        try:
            logger.info("清理临时资源...")
            self._contours.clear()
            self._output_srs = None
            gc.collect()
            logger.info("资源清理完成")
        except Exception as exc:
            logger.warning("资源清理异常（已忽略）: %s", exc)

    # ==================== 主流程 ====================

    def run(self) -> bool:
        """
        执行完整的 KML → Ia.tif 转换流程

        流程:
            1. 解析KML文件
            2. 设置 EPSG:4326 输出坐标系
            3. 准备采样点（生成器下采样 + 随机抽样 + 去重）
            4. 构建输出栅格网格（经纬度坐标，经度分辨率修正纬度余弦）
            5. （可选）PGA等值线矢量栅格化并输出PGA.tif
            6. 使用选定的插值方法计算并输出Ia.tif
            7. 打印耗时统计

        返回:
            bool: 处理是否成功
        """
        logger.info('KmlToIaConverter.run() 开始: kml=%s method=%s',
                    self.kml_path, self.interp_method)
        try:
            result = self._run_impl()
            if result:
                logger.info('KmlToIaConverter.run() 成功: ia=%s', self.ia_output_path)
            else:
                logger.error('KmlToIaConverter.run() 返回 False')
            return result
        except Exception as exc:
            logger.error('KmlToIaConverter.run() 失败: %s', exc, exc_info=True)
            raise

    def _run_impl(self) -> bool:
        """
        run() 的实际实现。

        成功时返回 True；所有失败路径均通过异常向上传播（不返回 False）。
        """
        logger.info("=" * 60)
        logger.info("KML → Ia 栅格处理程序（QGIS 3.40.15，v3.3）")
        logger.info("插值方法: %s", self.interp_method)
        logger.info("采样间隔: %d，最大采样点数: %d",
                     self.sample_interval, self.max_sample_points)
        logger.info("输出PGA.tif: %s",
                     '是（矢量栅格化）' if self.export_pga else '否')
        logger.info("=" * 60)

        try:
            # 1. 解析KML（parse_kml 内部已检查文件存在性，不存在时抛出 FileNotFoundError；
            #    解析结果同时存入 self._contours 供后续步骤使用）
            self.parse_kml()

            # 2. 设置 EPSG:4326 输出坐标系
            self._setup_output_crs()

            # 3. 准备采样点（坐标保持经纬度）
            x_arr, y_arr, ia_values = self._prepare_sample_points()

            # 4. 构建栅格网格（经纬度坐标，分辨率按度计算，经度修正纬度余弦）
            self._build_grid(x_arr, y_arr)

            # 5. PGA矢量栅格化（可选）
            if self.export_pga and self.pga_output_path:
                logger.info("-" * 40)
                logger.info("步骤: PGA等值线矢量栅格化（非插值）")
                logger.info("-" * 40)
                pga_start = time.time()
                self._rasterize_pga_contours()
                pga_elapsed = time.time() - pga_start
                logger.info("PGA栅格化耗时: %.2f 秒", pga_elapsed)

            # 6. Ia插值
            logger.info("-" * 40)
            logger.info("步骤: Ia插值计算（%s）", self.interp_method)
            logger.info("-" * 40)

            interp_start = time.time()
            self._interpolate_ia_to_file(x_arr, y_arr, ia_values, self.ia_output_path)

            del x_arr, y_arr, ia_values
            gc.collect()

            interp_elapsed = time.time() - interp_start
            logger.info("✅ Ia插值计算到输出文件耗时: %.2f 秒", interp_elapsed)

            # 7. 汇总
            logger.info("=" * 60)
            logger.info("处理完成!")
            if self.export_pga and self.pga_output_path:
                logger.info("  PGA栅格: %s", self.pga_output_path)
            logger.info("  Ia栅格:  %s", self.ia_output_path)
            logger.info("=" * 60)

            return True

        except Exception as e:
            logger.error("转换失败: %s", e, exc_info=True)
            raise

        finally:
            self.cleanup()


# ==================== 入口 ====================
if __name__ == "__main__":
    QgsApplication.setPrefixPath('', True)
    _qgs_app = QgsApplication([], False)
    _qgs_app.initQgis()
    try:
        converter = KmlToIaConverter(
        kml_path="../../data/geology/kml/source.kml",  # 输入KML文件路径
        ia_output_path="../../data/geology/ia/Ia.tif",  # Ia输出路径

        # PGA输出（可选，使用矢量栅格化非插值）
        pga_output_path="../../data/geology/kml/PGA.tif",  # 不需要PGA时设为None
        export_pga=True,  # 是否同时输出PGA.tif（矢量栅格化）

        # 基础参数
        resolution=30,  # 输出分辨率(米)；推荐10~100

        # 采样参数
        sample_interval=5,  # 等值线采样间隔；推荐3~10，越小采样越密
        max_sample_points=10000,  # 最大采样点数；超过时随机抽样，避免内存溢出

        # ========== 选择插值方法 ==========
        # 推荐方法（平滑，无突变）
        interp_method='kriging',  # 普通克里金 - 统计精度最高（需安装pykrige）
        # interp_method='radial',   # 径向插值 - 专为同心圈，完美单调递增

        # 其他可用方法
        # interp_method='scipy_idw',  # scipy RBF - 速度快（可能有边界突变）
        # interp_method='scipy_tin',  # scipy TIN - 无需pykrige，比kriging快
        # interp_method='qgis_idw',   # QGIS IDW - 无需额外依赖
        # interp_method='qgis_tin',   # QGIS TIN - 无需额外依赖

        # QGIS IDW 参数（仅 interp_method='qgis_idw' 时有效）
        qgis_idw_power=2.0,  # IDW幂次；推荐1.0~4.0，越大近点主导

        # QGIS TIN 参数（仅 interp_method='qgis_tin' 时有效）
        qgis_tin_method=0,  # TIN子方法: 0=线性（快）, 1=Clough-Tocher（平滑）

        # scipy IDW/RBF 参数（仅 interp_method='scipy_idw' 时有效，需安装scipy）
        scipy_kernel='thin_plate_spline',  # RBF核函数；推荐'thin_plate_spline'
        scipy_neighbors=100,  # 邻近点数；越小越快内存越低，推荐50~200

        # scipy TIN 参数（仅 interp_method='scipy_tin' 时有效，需安装scipy）
        scipy_tin_smooth=True,  # True=CloughTocher(C1最平滑), False=Linear(C0更快)

        # 径向插值参数（仅 interp_method='radial' 时有效，需安装scipy）
        radial_kind='cubic',  # 1D插值类型: 'linear'(快), 'cubic'(更平滑)

        # 克里金参数（仅 interp_method='kriging' 时有效，需安装pykrige）
        kriging_variogram='linear',  # 变差函数: 'linear','power','gaussian','spherical'
        kriging_nlags=6,  # 半变差函数滞后数
        kriging_neighbors=50,  # 克里金最近邻点数

        # 内存优化参数
        chunk_size=1000,  # 栅格分块行数；推荐500~2000
        max_memory_gb=10.0,  # 最大内存使用限制(GB)；参考值，实际由上方参数控制
        )
        converter.run()
    finally:
        _qgs_app.exitQgis()