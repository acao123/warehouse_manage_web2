# -*- coding: utf-8 -*-
"""
KML格式PGA等值线转换为Ia栅格文件工具（重构版 v4.0）
基于QGIS 3.40.15 Python环境

功能：
    1. 解析KML文件获取PGA等值线（LineString）
    2. 将PGA值(g单位)转换为实际加速度值(m/s²)
    3. 根据公式 log10(Ia) = 0.797 + 1.837 * log10(PGA) 计算Ia(阿里亚斯强度)值
    4. 使用插值算法对Ia进行插值计算（支持6种插值方法）
    5. 只输出Ia.tif；如需PGA.tif，使用矢量栅格化方式（非插值）
    6. 分辨率固定为30米×30米

主要改进（v4.0 相较 v3.9）：
    1. qgis_idw 方法引入**椭圆径向辅助场重塑**，彻底消除同心环带（banding）：
       - 新增参数：arcgis_idw_radial_assist（默认 True；False=退回 v3.9 行为）。
       - 通过协方差矩阵特征分解确定椭圆主轴方向和轴比，使用椭圆距离（而非纯圆形）
         让结果形状与 PGA 等值线（椭圆形）保持一致。
       - 拟合 1D 径向趋势 f_radial(r_ellipse) 捕获主体单调梯度；
         IDW 仅对残差做插值（残差在等值线间变化平缓，不产生环带）；
         最终值 = f_radial(r_ellipse_pixel) + IDW_residual(pixel)。
       - 降级路径（安全）：采样点不足 3 个、协方差奇异、控制点 < 2、
         PchipInterpolator 构建失败或 scipy 不可用时，自动退回原始 IDW 行为。

主要改进（v3.9 相较 v3.8）：
    1. scipy_tin 插值方法完全移除 KD-Tree IDW 填充逻辑：
       - 凸包外部 NaN 像素改用 NearestNDInterpolator（scipy 自带最近邻插值，
         属于 TIN/Voronoi 体系，非 IDW），保持纯 scipy TIN 体系内实现。
       - sigma 自适应改用 Delaunay 三角网边长中位数（无需 cKDTree 用于数值计算）：
         构建 Delaunay 三角网，取所有边长的中位数作为 d_typical，
         用于 sigma_pixels = max(1, factor * d_typical / resolution)。
       - 沿用 v3.8 的径向辅助场重塑（scipy_tin_radial_assist，默认 True）。
       - 沿用 v3.7 的全图高斯平滑后处理（gaussian_filter + mask 归一化）。
       - 废弃参数新增：scipy_tin_idw_neighbors、scipy_tin_idw_power
         （已改用 NearestNDInterpolator，传入非默认值输出 deprecation warning）。

主要改进（v3.8 相较 v3.7）：
    1. scipy_tin 插值方法引入**径向距离场辅助重塑**，从根本上消除同心环带：
       根因分析：v3.7 依靠 sigma 极小的高斯平滑（约 0.5~1 像素）无法跨越等值线
       之间的距离（几十至上百像素），TIN 在等值线间产生"平台"导致阶梯状环带。
       新策略（方案 A —— 径向辅助场重塑）：
         a. 计算所有采样点的几何中心 (cx, cy)（UTM 米坐标）。
         b. 对每个采样点计算径向距离 r_i = sqrt((x_i-cx)^2 + (y_i-cy)^2)。
         c. 将 (r_i, ia_i) 按距离分 bin（容差 = resolution/2）合并均值，
            用 PchipInterpolator 拟合 1D 径向趋势曲线 f_radial(r)。
         d. 对采样点计算残差 aux_i = ia_i - f_radial(r_i)（残差小且变化平缓）。
         e. 用 TIN 对残差 aux_i 做 2D 插值 → 残差场；
            TIN 外部 NaN 像素同样用 NearestNDInterpolator（对残差）填充。
         f. 像素最终值 = f_radial(r_pixel) + 残差场(pixel)；
            r_pixel 是该像素到中心的径向距离。
         g. 最后做一次轻度高斯平滑（保留现有 gaussian_filter 流程）。
       效果：径向方向由 f_radial 保证平滑（无环带），横向局部细节由 TIN 残差保留。
       新增参数：scipy_tin_radial_assist（默认 True；设为 False 可退回 v3.7 行为）。

主要改进（v3.7 相较 v3.6）：
    1. scipy_tin 插值方法深度重构，彻底消除同心环带（banding/concentric rings）：
       - 关键优化：仅对 TIN 返回 NaN 的像素执行 KD-Tree 查询和 IDW 计算。
         v3.6 对全部像素都执行 KD-Tree 查询和 IDW，浪费严重；v3.7 只有 TIN
         外部的少量 NaN 像素才触发 IDW，分块计算量大幅下降，性能提升 2-5 倍。
       - 移除 smoothstep 混合带逻辑（d_safe/d_blend）：TIN 内部不再做混合，
         避免重复 KD-Tree 查询；废弃 scipy_tin_blend_safe_dist、
         scipy_tin_blend_far_dist、scipy_tin_density_safe_factor、
         scipy_tin_density_far_factor 四个参数（向后兼容保留，内部不使用）。
       - 空间高斯平滑后处理（新增 scipy_tin_smooth_sigma_factor 参数）：
         所有分块累积到完整 (n_rows, n_cols) float32 数组后，用
         scipy.ndimage.gaussian_filter 做一次可分离 2D 高斯卷积（C 实现，极快）。
         sigma 自适应：sigma_pixels = max(1.0, k_sigma * d_typical / resolution)，
         其中 d_typical 为采样点 KD-Tree k-近邻距离中位数，k_sigma 默认 0.5。
         平滑使用 mask 归一化技术（num/den），避免 NoData 边界污染有效像素。
         此步对消除 TIN 三角面片棱边折线条带和同心环带极其有效，
         性能代价仅 O(W·H)，远低于全图 KD-Tree 查询。
       新增参数：scipy_tin_smooth_sigma_factor（默认 0.5）。

主要改进（v3.6 相较 v3.5）：
    1. scipy_tin 插值方法平滑效果深度优化，消除同心环状条带（banding）：
       - 自适应混合带距离阈值：当 scipy_tin_blend_safe_dist / scipy_tin_blend_far_dist
         为 None 时，不再用固定像素倍数（2×/10×resolution），而是基于 KD-Tree
         k-近邻距离中位数（d_typical）自适应计算；d_safe = density_safe_factor *
         d_typical，d_blend = density_far_factor * d_typical，能随采样密度自动
         缩放混合带宽度，彻底解决 30m 分辨率下混合带过窄问题。
         在日志中输出 d_typical、d_safe、d_blend 实际米数与计算模式。
       - 混合权重由线性插值改为 smoothstep（S 曲线）：
         t = clip((d − d_safe) / (d_blend − d_safe), 0, 1)，alpha = t²(3 − 2t)；
         过渡更柔和，彻底消除混合带边界的视觉棱线。
       - 强制对所有 TIN 有效像素执行混合（移除 alpha > 0 门槛），smoothstep
         在 t=0 时精确为 0，不影响极近像素，代码更简洁。
       - IDW 默认邻域数从 12 增大到 24，幂次从 2.0 降为 1.5，让趋势面更平滑。
       新增参数：scipy_tin_density_safe_factor（默认 0.25）、
                scipy_tin_density_far_factor（默认 1.5）。

主要改进（v3.5 相较 v3.4）：
    1. scipy_tin 插值方法全面升级：三角网外部 NaN 填充由 k=1 最近邻改为
       IDW（k=scipy_tin_idw_neighbors 个邻居，默认 12）平滑插值；新增三角网
       边界混合带——在 TIN 有效但距最近采样点较远的像素上，按距离在 TIN 与 IDW
       结果之间线性融合（d_safe~d_blend 可由参数控制），消除三角面片棱边突变；
       扩展 NaN 统计日志（纯 TIN / 混合带 / 纯 IDW 像素计数与占比）。
       新增参数：scipy_tin_blend_safe_dist、scipy_tin_blend_far_dist、
       scipy_tin_idw_neighbors、scipy_tin_idw_power。
    2. kriging (EBK) 方法对齐 ArcGIS EBK 原理：
       - ebk_n_simulations 默认改为 100，真正实现多次模拟——对每个子集重复
         n_simulations 次（每次对 sill/range 加入 ±ebk_simulation_noise 高斯
         扰动），预测时取所有模拟均值；n_simulations=1 退化为原有行为。
       - overlap_factor 显式钳制到 [1.0, 3.0] 区间并给 warning。
       - 预测权重改为二次平滑核 w = max(0,(1-(d/r)²))²（r 为子集 KNN 半径），
         与 ArcGIS EBK 论文一致；全为 0 时退回 1/(1+d²)。
       - k_predict（每像素参与加权的子集数）由新参数 ebk_predict_neighbors
         控制，默认 4（与 ArcGIS EBK 重叠区平均覆盖数一致）。
       - 子集建模阶段加入 ThreadPoolExecutor 并行（并行度 = max_interp_workers）。
       新增参数：ebk_predict_neighbors、ebk_simulation_noise。

主要改进（v3.4 相较 v3.3）：
    1. 使用 UTM 投影替代 EPSG:4326 经纬度投影，使插值算法在等距米制坐标系下进行，
       结果更精确；根据数据中心经度自动选择 UTM 带号（北半球 WGS84 UTM 43N–53N，
       EPSG:32643–32653），超出范围时按通用公式计算并记录 warning 日志。
    2. 优化 scipy TIN 插值：CloughTocher/Linear 插值器启用 rescale=True 和显式
       fill_value=np.nan；CloughTocher 增加 tol=1e-6, maxiter=400；
       NaN 填充由 RBFInterpolator(neighbors=1) 改为真正的 cKDTree 最近邻，
       并在日志中汇报 NaN 像素统计（数量、占比、到最近采样点最大/平均距离）。

主要改进（v3.3 相较 v3.2）：
    1. qgis_idw 改为 KD-Tree 局部 IDW，与 ArcGIS IDW 默认参数对齐
       （最近 12 点搜索邻域，反距离权重幂次=2），性能较全局 IDW 大幅提升
    2. kriging 改为子集化 EBK（简化版 Empirical Bayesian Kriging），
       与 ArcGIS EBK 默认参数（子集大小 100，power 变差函数）对齐；
       模型构建复杂度从全局 O(n³) 降为 N×O(K³)，当 n_samples > 500 时显著加速
    3. 新增 idw_num_neighbors、idw_max_distance、ebk_subset_size、
       ebk_overlap_factor、ebk_variogram、ebk_n_simulations 参数

主要改进（v3.2 相较 v3.1）：
    1. QGIS插值优化：使用 QgsGridFileWriter 替代逐像素 Python 循环，
       改为 C++ 批量插值，大幅提升 qgis_idw/qgis_tin 速度
    2. scipy插值并行优化：使用 ThreadPoolExecutor 并行处理分块，
       scipy C扩展在插值时释放GIL，多线程可有效提升吞吐量
    3. 新增 max_interp_workers 参数控制并行线程数（默认2）

主要改进（v3.1 相较 v3.0）：
    1. 修复经度方向分辨率未考虑纬度余弦的Bug（像素非正方形问题）
    2. 移除无用的EPSG:4326→EPSG:4326恒等坐标变换，直接使用经纬度坐标
    3. 修复KML name解析过于宽松的Bug（'g'替换可能误伤其他字符）
    4. 所有关键方法添加try-except + logger日志 + 异常向上抛出
    5. 全部print替换为logger日志，保持生产环境可控
    6. QgsFeature设置fields定义，确保属性值不丢失
    7. 方法名重命名消除误导（_determine_utm_projection → _setup_output_crs）

作者: acao (重构版 v4.0)
日期: 2026-05-28
版本: 4.0
QGIS版本: 3.40.15

支持插值方法:
    - 'scipy_tin' : scipy Delaunay三角网插值（默认，平滑无突变，推荐）
    - 'radial'    : 径向距离1D插值（专为同心圈优化，完美单调递增）
    - 'scipy_idw' : scipy RBFInterpolator（速度快，支持邻近点限制）
    - 'kriging'   : 简化版 Empirical Bayesian Kriging（与 ArcGIS EBK 对齐，需 scipy + pykrige）
    - 'qgis_idw'  : KD-Tree 局部反距离权重插值（与 ArcGIS IDW 对齐，需 scipy）
    - 'qgis_tin'  : QGIS自带三角网插值（无需额外依赖）

插值范围:
    最大范围：300km × 300km
    固定分辨率：30米 × 30米

投影说明：
    输出坐标系根据数据中心经度自动选择对应的 UTM 投影带（北半球 WGS84 UTM）。
    中国区域使用 EPSG:32643–32653（UTM 43N–53N，覆盖东经 72°–138°）。
    所有采样点坐标从 EPSG:4326 (WGS84) 转换到 UTM 投影后再做插值，
    像素宽高均等于 resolution（米），无需 cos(lat) 修正。
    UTM 带号通用公式：zone = int((center_lon + 180) / 6) + 1，
    EPSG = 32600 + zone（北半球）。

内存优化说明（运行环境 32G，占用不超过 10G）：
    - 使用生成器迭代采样点，避免一次性构建大型中间列表
    - 超过 max_sample_points 时随机抽样，严格控制采样点总数
    - scipy方法：按 chunk_size 行分块处理，逐块释放临时数组
    - 各阶段处理后及时 del 临时数组并调用 gc.collect()
    - 所有GDAL/OGR对象使用后立即置None释放
    - run()方法使用try-finally确保异常时也能释放资源
"""

import concurrent.futures
import gc
import logging
import math
import os
import random
import re
import tempfile
import threading
import time
import traceback
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
        PchipInterpolator as _PchipInterpolator,
    )
    from scipy.spatial import cKDTree as _cKDTree, Delaunay as _Delaunay
    from scipy.ndimage import gaussian_filter as _gaussian_filter
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


class TaskCancelledException(Exception):
    """任务取消异常：由 KmlToIaConverter 检测到取消信号时抛出。"""
    pass


class KmlToIaConverter:
    """
    KML转Ia栅格文件转换器（QGIS 3.40.15，内存优化版 v4.0）

    将地震局提供的KML格式PGA等值线文件，经过解析、插值计算后，
    输出Ia.tif栅格文件（可选输出PGA.tif，使用矢量栅格化非插值）。

    输出坐标系: 根据数据中心经度自动选择的 UTM 投影（北半球 WGS84 UTM）。

    主要特性:
        - 只对Ia进行插值，PGA.tif使用等值线矢量栅格化生成
        - 所有插值在 UTM 米制坐标系下进行，距离权重更准确
        - 采样点数量可控（sample_interval + max_sample_points），避免内存溢出
        - 支持6种插值方法（scipy_tin推荐，平滑无突变）
        - 严格内存控制（<10GB）：生成器、分批转换、分块写入、及时释放
        - 异常安全：run()使用try-finally，异常时也能释放资源
        - 所有关键方法添加try-except + logger日志 + 异常向上抛出

    支持的插值方法:
        - 'scipy_tin'（默认/推荐）：scipy Delaunay三角网插值，C1/C0连续；
          TIN 外部用 NearestNDInterpolator（纯 scipy TIN/Voronoi 体系）填充；
          全图一次高斯平滑后处理（sigma 由 scipy_tin_smooth_sigma_factor 控制），
          彻底消除三角面片棱线条带和同心环带（v3.9 重构，完全移除 IDW 逻辑）
        - 'radial'     ：径向距离1D插值，专为同心圈优化，完美单调递增
        - 'scipy_idw'  ：scipy RBF插值，速度快，支持邻近点限制，需安装scipy
        - 'kriging'    ：真正对齐 ArcGIS EBK 的子集化克里金，支持多次模拟
                        （ebk_n_simulations 默认 100），需安装 scipy + pykrige
        - 'qgis_idw'   ：KD-Tree 局部反距离权重插值，与 ArcGIS IDW 对齐，需安装 scipy
        - 'qgis_tin'   ：QGIS三角网插值，基于Delaunay三角剖分，无需额外依赖

    用法示例:
        converter = KmlToIaConverter(
            kml_path="path/to/source.kml",
            ia_output_path="path/to/Ia.tif",
            interp_method='scipy_tin',
            scipy_tin_smooth=True,
            scipy_tin_smooth_sigma_factor=0.5,  # v3.7 新增：高斯平滑 sigma 倍率
            scipy_tin_radial_assist=True,       # v3.8 新增：径向辅助场重塑（消除同心环带，推荐）
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

        # ---- scipy TIN 混合平滑参数（v3.5 / v3.6，v3.7 部分废弃）----
        # 【已废弃，v3.7+ 不再使用，保留以向后兼容调用方】
        # 若传入非默认值，_run_scipy_tin_interpolation 开头会记录 logger.warning
        scipy_tin_blend_safe_dist: Optional[float] = None,  # 废弃：TIN 纯用区距离阈值（米）
        scipy_tin_blend_far_dist: Optional[float] = None,   # 废弃：IDW 纯用区距离阈值（米）
        scipy_tin_density_safe_factor: float = 0.25,        # 废弃：自适应 d_safe 倍率
        scipy_tin_density_far_factor: float = 1.5,          # 废弃：自适应 d_blend 倍率
        # 【v3.7 新增】高斯平滑 sigma 倍率：sigma_pixels = max(1, factor * d_typical / resolution)
        # 设为 0 完全禁用平滑后处理（仅用于调试）；推荐范围 0.3 ~ 1.0
        scipy_tin_smooth_sigma_factor: float = 0.5,
        scipy_tin_idw_neighbors: int = 24,                  # TIN 外部 IDW 邻近点数，默认 24
        scipy_tin_idw_power: float = 1.5,                   # TIN 外部 IDW 幂次，默认 1.5
        # 【v3.8 新增】True=启用径向辅助场重塑（消除同心环带，推荐）；False=退回 v3.7 行为
        scipy_tin_radial_assist: bool = True,

        # ---- 径向插值参数 ----
        radial_kind: str = 'cubic',

        # ---- 克里金参数 ----
        kriging_variogram: str = 'linear',
        kriging_nlags: int = 6,
        kriging_neighbors: int = 50,

        # ---- 内存优化参数 ----
        chunk_size: int = 1000,
        coord_batch_size: int = 10000,
        max_memory_gb: float = 10.0,

        # ---- 并行插值参数 ----
        max_interp_workers: int = 4,        # scipy插值并行线程数，推荐 1~4

        # ---- 取消信号参数 ----
        cancel_event: Optional[threading.Event] = None,  # 取消事件，set()后立即停止插值

        # ---- ArcGIS IDW 对齐参数（qgis_idw 方法专用）----
        idw_num_neighbors: int = 12,          # KD-Tree 局部搜索邻近点数，与 ArcGIS 默认一致
        idw_max_distance: Optional[float] = None,  # 最大搜索距离（米，UTM坐标），None 表示不限制

        # ---- ArcGIS EBK 对齐参数（kriging 方法专用）----
        ebk_subset_size: int = 100,           # 每个子集的采样点数，与 ArcGIS EBK 默认一致
        ebk_overlap_factor: float = 1.0,      # 子集重叠因子（钳制到[1.0,3.0]），越大子集越多
        ebk_variogram: str = 'power',         # 变差函数，与 ArcGIS EBK 默认一致
        ebk_n_simulations: int = 100,         # 每个子集的模拟次数，与 ArcGIS EBK 默认一致
        ebk_predict_neighbors: int = 4,       # 每像素参与加权的子集数，默认4（v3.5 新增）
        ebk_simulation_noise: float = 0.1,    # 变差函数参数扰动幅度，n_simulations>1 时生效（v3.5 新增）

        # ---- ArcGIS IDW 椭圆径向辅助场（v4.0 新增）----
        # True=启用椭圆径向辅助场重塑（消除同心环带，推荐）；False=退回原始 IDW 行为（向后兼容/调试）
        arcgis_idw_radial_assist: bool = True,
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
        # scipy TIN 混合平滑参数（v3.5 / v3.6，v3.7 废弃，保留向后兼容）
        self.scipy_tin_blend_safe_dist = scipy_tin_blend_safe_dist
        self.scipy_tin_blend_far_dist = scipy_tin_blend_far_dist
        self.scipy_tin_idw_neighbors = scipy_tin_idw_neighbors
        self.scipy_tin_idw_power = scipy_tin_idw_power
        self.scipy_tin_density_safe_factor = scipy_tin_density_safe_factor  # v3.6 废弃
        self.scipy_tin_density_far_factor = scipy_tin_density_far_factor    # v3.6 废弃
        self.scipy_tin_smooth_sigma_factor = scipy_tin_smooth_sigma_factor  # v3.7 新增
        self.scipy_tin_radial_assist = scipy_tin_radial_assist              # v3.8 新增
        # 径向插值参数
        self.radial_kind = radial_kind
        # 克里金参数
        self.kriging_variogram = kriging_variogram
        self.kriging_nlags = kriging_nlags
        self.kriging_neighbors = kriging_neighbors
        # 内存优化参数
        self.chunk_size = chunk_size
        self.coord_batch_size = coord_batch_size
        self.max_memory_gb = max_memory_gb
        # 并行插值参数
        self.max_interp_workers = max(1, max_interp_workers)
        # 取消信号
        self._cancel_event: Optional[threading.Event] = cancel_event
        # ArcGIS IDW 参数
        self.idw_num_neighbors = idw_num_neighbors
        self.idw_max_distance = idw_max_distance
        # ArcGIS EBK 参数
        self.ebk_subset_size = ebk_subset_size
        self.ebk_overlap_factor = ebk_overlap_factor
        self.ebk_variogram = ebk_variogram
        self.ebk_n_simulations = ebk_n_simulations
        self.ebk_predict_neighbors = ebk_predict_neighbors
        self.ebk_simulation_noise = ebk_simulation_noise
        # ArcGIS IDW 椭圆径向辅助场（v4.0 新增）
        self.arcgis_idw_radial_assist = arcgis_idw_radial_assist

        # 运行时数据（由 run() 过程填充）
        self._contours: List[dict] = []
        self._utm_epsg: int = 0
        self._utm_srs: Optional[osr.SpatialReference] = None
        self._wgs84_srs: Optional[osr.SpatialReference] = None
        self._coord_transform = None
        self._geo_transform: Optional[tuple] = None
        self._n_cols: int = 0
        self._n_rows: int = 0
        self._x_min: float = 0.0
        self._x_max: float = 0.0
        self._y_min: float = 0.0
        self._y_max: float = 0.0
        self._res_lon: float = 0.0   # X 方向像素大小（米）
        self._res_lat: float = 0.0   # Y 方向像素大小（米）
        self._pixel_size: float = 0.0  # 像素大小（米）

    # ==================== KML 解析 ====================

    def _check_cancelled(self) -> None:
        """检查取消信号，若已设置则抛出 TaskCancelledException（线程安全，极低开销）。"""
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise TaskCancelledException("任务已被取消")

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

    def _setup_output_crs(self, center_lon: float):
        """
        根据数据中心经度自动选择 UTM 投影带，设置输出坐标系。

        UTM 带号通用公式：zone = int((center_lon + 180) / 6) + 1
        EPSG = 32600 + zone（北半球 WGS84 UTM）

        中国区域覆盖带号 43–53（东经 72°–138°），超出范围时仍按通用公式计算
        并记录 warning 日志，而不是失败。

        参数:
            center_lon (float): 数据中心经度（度，WGS84）

        副作用:
            - self._utm_epsg：UTM EPSG 代码
            - self._utm_srs：UTM 空间参考对象
            - self._wgs84_srs：WGS84 空间参考对象
            - self._coord_transform：WGS84 → UTM 坐标变换对象
        """
        try:
            zone = int((center_lon + 180) / 6) + 1
            if not (43 <= zone <= 53):
                logger.warning(
                    "中心经度 %.4f° 超出中国区域 UTM 43N–53N 范围（zone=%d），"
                    "仍按通用公式计算 EPSG=%d",
                    center_lon, zone, 32600 + zone,
                )
            self._utm_epsg = 32600 + zone
            central_meridian = -180 + (zone - 1) * 6 + 3

            # UTM 空间参考
            utm_srs = osr.SpatialReference()
            utm_srs.ImportFromEPSG(self._utm_epsg)
            utm_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            self._utm_srs = utm_srs

            # WGS84 空间参考
            wgs84_srs = osr.SpatialReference()
            wgs84_srs.ImportFromEPSG(4326)
            wgs84_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            self._wgs84_srs = wgs84_srs

            # 坐标变换：WGS84 → UTM（输入 (lon, lat)，输出 (easting, northing)）
            self._coord_transform = osr.CoordinateTransformation(wgs84_srs, utm_srs)

            logger.info(
                "UTM 投影: 带号=%d, EPSG=%d, 中央经线=%.0f°E",
                zone, self._utm_epsg, central_meridian,
            )
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
            3. 将 (lon, lat) 坐标批量转换为 UTM (x, y) 米坐标
            4. 去除完全重叠的坐标点（防止插值奇异矩阵）

        返回:
            tuple: (x_arr, y_arr, ia_values)，UTM 米坐标和Ia值数组

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

            # 使用 coord_transform 将 (lon, lat) 批量转换为 UTM (x, y) 米坐标
            pts_lonlat = list(zip(lons_arr.tolist(), lats_arr.tolist()))
            transformed = self._coord_transform.TransformPoints(pts_lonlat)
            x_out = np.array([p[0] for p in transformed], dtype=np.float64)
            y_out = np.array([p[1] for p in transformed], dtype=np.float64)
            del lons_arr, lats_arr, transformed, pts_lonlat
            gc.collect()

            # -------- 去重处理 --------
            # UTM 是米单位，四舍五入到整米（decimals=0）后去重
            coords_rounded = np.round(np.column_stack([x_out, y_out]), decimals=0)
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
        根据采样点范围构建输出栅格网格参数（UTM 米坐标）

        UTM 是等距米坐标，直接使用 resolution（米）作为像素宽高，
        无需 cos(lat) 修正。在数据范围外扩展 10 个像素作为缓冲区。

        参数:
            x_arr (np.ndarray): 采样点 UTM X 坐标（米，easting）
            y_arr (np.ndarray): 采样点 UTM Y 坐标（米，northing）
        """
        try:
            self._pixel_size = self.resolution
            self._res_lon = self.resolution   # X 方向像素大小（米）
            self._res_lat = self.resolution   # Y 方向像素大小（米）

            buffer_x = self.resolution * 10
            buffer_y = self.resolution * 10

            x_min = float(x_arr.min()) - buffer_x
            x_max = float(x_arr.max()) + buffer_x
            y_min = float(y_arr.min()) - buffer_y
            y_max = float(y_arr.max()) + buffer_y

            self._n_cols = int(np.ceil((x_max - x_min) / self.resolution))
            self._n_rows = int(np.ceil((y_max - y_min) / self.resolution))

            # 防止栅格尺寸为0
            if self._n_cols <= 0 or self._n_rows <= 0:
                raise ValueError(
                    f"计算得到的栅格尺寸无效: {self._n_cols} 列 × {self._n_rows} 行，"
                    f"数据范围: X[{x_min:.1f}, {x_max:.1f}] Y[{y_min:.1f}, {y_max:.1f}]"
                )

            # GeoTIFF 仿射变换参数:
            # (左上角X, 像素宽度, 旋转, 左上角Y, 旋转, 像素高度负值)
            self._geo_transform = (x_min, self.resolution, 0.0,
                                   y_max, 0.0, -self.resolution)

            self._x_min = x_min
            self._x_max = x_max
            self._y_min = y_min
            self._y_max = y_max

            logger.info("栅格网格信息:")
            logger.info("  像素大小: %.1f m", self.resolution)
            logger.info("  网格大小: %d 列 × %d 行", self._n_cols, self._n_rows)
            logger.info("  X 范围: %.1f m ~ %.1f m", x_min, x_max)
            logger.info("  Y 范围: %.1f m ~ %.1f m", y_min, y_max)
            logger.info("  总像素数: %s", f"{self._n_cols * self._n_rows:,}")

            # 估算内存使用
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
            crs_auth_id = f"EPSG:{self._utm_epsg}"
            layer = QgsVectorLayer(
                f"Point?crs={crs_auth_id}", "sample_points", "memory"
            )

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
        使用 QGIS 插值器进行插值（优化版：按行批量处理）

        由于 QGIS 3.40 的 QgsGridFileWriter API 存在兼容性问题，
        改用 interpolatePoint 逐点插值，但按行批量处理以提高效率。

        参数:
            x_arr: 采样点X坐标(经度)
            y_arr: 采样点Y坐标(纬度)
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        layer = None
        out_ds = None
        band = None
        interpolator = None

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
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            # 预计算列坐标（经度）
            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            logger.info("开始 QGIS 插值，共 %d 行 × %d 列 = %s 像素...",
                        n_rows, self._n_cols, f"{n_rows * self._n_cols:,}")

            start_time = time.time()
            report_interval = max(1, n_rows // 20)

            for row_idx in range(n_rows):
                # 每 100 行检查一次取消信号（减少检查开销）
                if row_idx % 100 == 0:
                    self._check_cancelled()
                y = self._y_max - (row_idx + 0.5) * self._res_lat

                row_data = np.full(self._n_cols, -9999.0, dtype=np.float32)
                for col_idx in range(self._n_cols):
                    x = grid_x[col_idx]
                    success, value = interpolator.interpolatePoint(x, y)
                    if success == 0:
                        row_data[col_idx] = max(0.0, value)

                band.WriteArray(row_data.reshape(1, -1), 0, row_idx)

                if (row_idx + 1) % report_interval == 0 or row_idx == n_rows - 1:
                    elapsed = time.time() - start_time
                    progress = 100.0 * (row_idx + 1) / n_rows
                    eta = elapsed / (row_idx + 1) * (n_rows - row_idx - 1) if row_idx > 0 else 0
                    logger.info("进度: %d/%d 行 (%.1f%%), 已用时: %.1fs, 预计剩余: %.1fs",
                                row_idx + 1, n_rows, progress, elapsed, eta)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("QGIS插值完成，总耗时: %.1fs, 已保存: %s", total_time, output_tif_path)

        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error("QGIS插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del interpolator, layer
            gc.collect()

    # ==================== ArcGIS IDW 插值方法（KD-Tree 局部 IDW）====================

    def _run_arcgis_idw_interpolation(
            self,
            x_arr: np.ndarray,
            y_arr: np.ndarray,
            values: np.ndarray,
            output_tif_path: str,
    ) -> None:
        """
        ArcGIS IDW 风格的局部反距离权重插值（KD-Tree 加速）。

        与 ArcGIS IDW 工具原理一致：
            - 使用 scipy.spatial.cKDTree 对每个像素查询最近的 N 个采样点
              （默认 N=12，与 ArcGIS IDW 默认 Search Neighborhood 一致）。
            - 反距离权重 w_i = 1 / d_i^power；当 d_i = 0 时直接取该点的值。
            - 支持可选最大搜索距离 idw_max_distance（单位：米，UTM坐标）。

        性能：cKDTree.query 在 C 扩展层释放 GIL，可通过 ThreadPoolExecutor 多线程加速；
              局部搜索复杂度 O(n_pixels × log(n_samples) × N)，
              远优于全局 IDW 的 O(n_pixels × n_samples)。

        参数:
            x_arr: 采样点X坐标（UTM easting，米）
            y_arr: 采样点Y坐标（UTM northing，米）
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'qgis_idw' (ArcGIS IDW) 方法。"
                "请在 QGIS Python 环境中运行: pip install scipy"
            )

        tree = None
        out_ds = None
        band = None

        try:
            # 建立 KD-Tree（在所有分块插值时共享）
            pts_train = np.column_stack([x_arr, y_arr]).astype(np.float64)
            tree = _cKDTree(pts_train)
            del pts_train
            vals_f64 = values.astype(np.float64)

            n_neighbors = min(self.idw_num_neighbors, len(x_arr))
            power = self.qgis_idw_power
            max_dist = self.idw_max_distance

            logger.info(
                "ArcGIS IDW (KD-Tree) 插值: 邻近点数=%d, 幂次=%.1f, 最大距离=%s",
                n_neighbors, power,
                f"{max_dist:.1f} m" if max_dist is not None else "无限制",
            )

            # ==================================================================
            # v4.0 椭圆径向辅助场重塑（arcgis_idw_radial_assist）：消除同心环带
            # 当 arcgis_idw_radial_assist=True 时：
            #   1. 通过协方差矩阵特征分解得到椭圆主轴方向和轴比；
            #   2. 拟合 1D 椭圆径向趋势 f_radial(r_ellipse)；
            #   3. IDW 仅对残差做插值（残差平缓，无明显环带）；
            #   4. 最终值 = f_radial(r_ellipse_pixel) + IDW_residual。
            # 当 arcgis_idw_radial_assist=False 时：退回原始 IDW 行为。
            # ==================================================================
            use_radial_assist = False
            f_radial = None
            cx = cy = 0.0
            _rot_mat = None    # 旋转矩阵 (2,2)：行为特征向量
            _ratio = 1.0       # 次轴→主轴放大比例 = sqrt(λ_max / λ_min)

            if self.arcgis_idw_radial_assist and _HAS_SCIPY and len(x_arr) >= 3:
                try:
                    # 1. 几何中心
                    cx = float(np.mean(x_arr))
                    cy = float(np.mean(y_arr))

                    # 2. 协方差矩阵特征分解 → 椭圆主轴方向和轴比
                    dx = (x_arr - cx).astype(np.float64)
                    dy = (y_arr - cy).astype(np.float64)
                    cov = np.cov(np.vstack([dx, dy]))  # shape (2,2)
                    eigvals, eigvecs = np.linalg.eigh(cov)  # eigvals 升序
                    lam_min, lam_max = float(eigvals[0]), float(eigvals[1])

                    if lam_min <= 0.0:
                        # 奇异（如所有点在一条线上）：退回圆形欧氏距离
                        logger.warning(
                            "椭圆径向辅助场：协方差矩阵奇异（λ_min=%.6g），退回圆形距离",
                            lam_min,
                        )
                        _rot_mat = np.eye(2, dtype=np.float64)
                        _ratio = 1.0
                    else:
                        _ratio = float(np.sqrt(lam_max / lam_min))
                        # eigvecs[:,i] 是第 i 个特征向量；转置后行是特征向量
                        _rot_mat = eigvecs.T.astype(np.float64)  # (2,2)

                    # 3. 椭圆径向距离（样本点）
                    coords = np.column_stack([dx, dy])      # (n, 2)
                    rot_coords = coords @ _rot_mat.T         # (n, 2)
                    # rot_coords[:,0] = 次轴分量，rot_coords[:,1] = 主轴分量
                    u_major = rot_coords[:, 1]               # 主轴方向分量
                    v_minor = rot_coords[:, 0]               # 次轴方向分量
                    r_ellipse_arr = np.sqrt(u_major ** 2 + (_ratio * v_minor) ** 2)
                    del dx, dy, coords, rot_coords, u_major, v_minor

                    # 4. 按距离分 bin（容差 = resolution/2），合并均值
                    # 容差取 resolution/2：比采样间距小一个量级，避免同等值线上的采样点被
                    # 分入多个 bin，同时足以合并极接近的重复距离点（数值稳健）。
                    sorted_idx = np.argsort(r_ellipse_arr)
                    r_sorted = r_ellipse_arr[sorted_idx]
                    v_sorted = values[sorted_idx].astype(np.float64)

                    tol_bin = self.resolution / 2.0
                    merged_r = [float(r_sorted[0])]
                    merged_v = [float(v_sorted[0])]
                    running_sum = float(v_sorted[0])
                    running_cnt = 1
                    last_r = merged_r[0]
                    for _i in range(1, len(r_sorted)):
                        _r = float(r_sorted[_i])
                        _v = float(v_sorted[_i])
                        if _r - last_r < tol_bin:
                            running_sum += _v
                            running_cnt += 1
                        else:
                            merged_v[-1] = running_sum / running_cnt
                            merged_r.append(_r)
                            merged_v.append(_v)
                            last_r = _r
                            running_sum = _v
                            running_cnt = 1
                    merged_v[-1] = running_sum / running_cnt
                    del r_sorted, v_sorted, sorted_idx

                    r_knots = np.array(merged_r, dtype=np.float64)
                    v_knots = np.array(merged_v, dtype=np.float64)
                    del merged_r, merged_v

                    if len(r_knots) < 2:
                        logger.warning(
                            "椭圆径向辅助场：合并后控制点数 %d < 2，退回原始 IDW",
                            len(r_knots),
                        )
                        del r_knots, v_knots, r_ellipse_arr
                        gc.collect()
                    else:
                        # 5. 拟合 1D 径向趋势（单调三次 Hermite，无振荡）
                        f_radial = _PchipInterpolator(r_knots, v_knots, extrapolate=True)
                        n_knots = len(r_knots)

                        # 6. 计算残差：vals_f64 = ia - f_radial(r_ellipse)
                        radial_at_samples = f_radial(r_ellipse_arr)
                        vals_f64 = values.astype(np.float64) - radial_at_samples
                        del radial_at_samples, r_knots, v_knots, r_ellipse_arr
                        gc.collect()

                        use_radial_assist = True

                        # 主轴角度（度），仅用于日志
                        _axis_angle = float(
                            np.degrees(np.arctan2(eigvecs[1, 1], eigvecs[0, 1]))
                        ) if lam_min > 0.0 else 0.0
                        logger.info(
                            "椭圆径向辅助场（v4.0）: 中心=(%.1f, %.1f), "
                            "ratio=%.3f, 主轴角度=%.1f°, 控制点数=%d, "
                            "残差范围=[%.4f, %.4f]",
                            cx, cy, _ratio, _axis_angle, n_knots,
                            float(vals_f64.min()), float(vals_f64.max()),
                        )

                except Exception as _e:
                    logger.warning(
                        "椭圆径向辅助场构建失败（%s），退回原始 IDW 行为", _e,
                    )
                    vals_f64 = values.astype(np.float64)   # 重置为原始值
                    use_radial_assist = False
                    f_radial = None

            if not use_radial_assist:
                if self.arcgis_idw_radial_assist:
                    logger.info("ArcGIS IDW：椭圆径向辅助场不可用，退回原始 IDW")
                else:
                    logger.info("ArcGIS IDW：arcgis_idw_radial_assist=False，使用原始 IDW")

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon
            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            chunk_starts = list(range(0, n_rows, chunk_rows))

            # 内层函数：在线程中计算单个分块的 ArcGIS IDW 插值结果
            def _compute_chunk_arcgis_idw(row_start: int) -> Tuple[int, np.ndarray]:
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start
                grid_y = self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat
                xx, yy = np.meshgrid(grid_x, grid_y)
                pts_query = np.column_stack([xx.ravel(), yy.ravel()])
                del xx, yy
                n_pts = pts_query.shape[0]

                # 查询最近的 n_neighbors 个采样点
                dists, idxs = tree.query(pts_query, k=n_neighbors)
                # 保证形状为 (n_pts, n_neighbors)，即使 n_neighbors==1 也统一
                if n_neighbors == 1:
                    dists = dists.reshape(-1, 1)
                    idxs = idxs.reshape(-1, 1)

                # 精确匹配（d=0）的像素：直接取该采样点的值
                exact_mask = dists[:, 0] == 0.0

                # 反距离权重（d=0 处设为 0.0，避免除零；精确匹配单独处理）
                with np.errstate(divide='ignore', invalid='ignore'):
                    weights = np.where(dists > 0.0, 1.0 / (dists ** power), 0.0)

                # 可选：超出最大搜索距离的邻居权重置 0
                if max_dist is not None:
                    weights[dists > max_dist] = 0.0

                weight_sum = weights.sum(axis=1)  # (n_pts,)
                chunk_vals = np.full(n_pts, -9999.0, dtype=np.float64)

                # 非精确匹配且权重和 > 0 的像素：加权平均（vals_f64 可能是残差）
                valid_mask = (~exact_mask) & (weight_sum > 0.0)
                if valid_mask.any():
                    w = weights[valid_mask]           # (n_valid, n_neighbors)
                    v = vals_f64[idxs[valid_mask]]    # (n_valid, n_neighbors)
                    chunk_vals[valid_mask] = (w * v).sum(axis=1) / weight_sum[valid_mask]

                # 精确匹配：直接赋值
                if exact_mask.any():
                    chunk_vals[exact_mask] = vals_f64[idxs[exact_mask, 0]]

                del dists, idxs, weights, weight_sum

                # v4.0 椭圆径向辅助场：将径向趋势加回残差场
                if use_radial_assist and f_radial is not None:
                    _dx_p = pts_query[:, 0] - cx
                    _dy_p = pts_query[:, 1] - cy
                    _pcoords = np.column_stack([_dx_p, _dy_p])
                    _prot = _pcoords @ _rot_mat.T       # (n_pts, 2)
                    _pu = _prot[:, 1]                   # 主轴分量
                    _pv = _prot[:, 0]                   # 次轴分量
                    _r_px = np.sqrt(_pu ** 2 + (_ratio * _pv) ** 2)
                    _trend = f_radial(_r_px)
                    # 仅对有效像素（非 NoData）加回趋势；NoData 标记值为 -9999.0，
                    # 用 -9998.0 作为阈值以避免浮点精度问题
                    _valid_for_trend = chunk_vals > -9998.0
                    chunk_vals[_valid_for_trend] += _trend[_valid_for_trend]
                    del _dx_p, _dy_p, _pcoords, _prot, _pu, _pv, _r_px, _trend, _valid_for_trend

                del pts_query

                result = chunk_vals.reshape(actual_rows, self._n_cols).astype(np.float32)
                del chunk_vals
                # nodata 标记值为 -9999.0；用 -9998.0 作阈值以容忍浮点误差
                nodata_mask = result < -9998.0
                np.maximum(result, 0.0, out=result)
                result[nodata_mask] = -9999.0
                return row_start, result

            start_time = time.time()
            logger.info("ArcGIS IDW 插值开始，分块数=%d，并行线程数=%d",
                        len(chunk_starts), self.max_interp_workers)

            # 滑动窗口式并行提交与消费（与 scipy_idw 保持相同模式）
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_interp_workers
            ) as executor:
                pending: dict = {}
                submit_ptr = 0

                while submit_ptr < len(chunk_starts) and submit_ptr < self.max_interp_workers:
                    rs = chunk_starts[submit_ptr]
                    pending[rs] = executor.submit(_compute_chunk_arcgis_idw, rs)
                    submit_ptr += 1

                for chunk_idx, rs in enumerate(chunk_starts):
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise TaskCancelledException("任务已被取消")
                    try:
                        row_start_res, chunk_vals = pending.pop(rs).result()
                    except Exception as exc:
                        logger.error("ArcGIS IDW 分块 row_start=%d 失败: %s", rs, exc)
                        raise

                    band.WriteArray(chunk_vals, 0, row_start_res)
                    del chunk_vals
                    gc.collect()

                    if submit_ptr < len(chunk_starts):
                        next_rs = chunk_starts[submit_ptr]
                        pending[next_rs] = executor.submit(_compute_chunk_arcgis_idw, next_rs)
                        submit_ptr += 1

                    row_end = min(rs + chunk_rows, n_rows)
                    if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                        elapsed = time.time() - start_time
                        logger.info("ArcGIS IDW 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                    row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("ArcGIS IDW 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error("ArcGIS IDW 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            if tree is not None:
                del tree
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
        使用 scipy RBFInterpolator 进行IDW近似插值，分块并行写入 GeoTIFF。

        优化说明：使用 ThreadPoolExecutor（max_interp_workers 线程）并行处理分块。
        scipy 的 RBFInterpolator.__call__ 在 C 扩展层释放 GIL，多线程可有效提升吞吐量。
        每次最多有 max_interp_workers 个分块的结果同时在内存中，按顺序写盘后立即释放。

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
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            chunk_starts = list(range(0, n_rows, chunk_rows))

            # 辅助函数：在线程中计算单个分块的插值结果
            def _compute_chunk_idw(row_start: int) -> Tuple[int, np.ndarray]:
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start
                grid_y = self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat
                xx, yy = np.meshgrid(grid_x, grid_y)
                pts = np.column_stack([xx.ravel(), yy.ravel()])
                del xx, yy
                result = rbf(pts).reshape(actual_rows, self._n_cols).astype(np.float32)
                del pts
                np.maximum(result, 0.0, out=result)
                return row_start, result

            start_time = time.time()
            logger.info("scipy_idw 插值开始，分块数=%d，并行线程数=%d",
                        len(chunk_starts), self.max_interp_workers)

            # 使用字典映射 row_start → Future，按顺序消费、并行计算
            # 每次最多保留 max_interp_workers 个未消费的 Future 在内存中
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_interp_workers
            ) as executor:
                # pending: row_start → Future（只包含已提交但未消费的分块）
                pending: dict = {}
                submit_ptr = 0  # 下一个待提交分块的索引

                # 预先提交前 max_interp_workers 个分块
                while submit_ptr < len(chunk_starts) and submit_ptr < self.max_interp_workers:
                    rs = chunk_starts[submit_ptr]
                    pending[rs] = executor.submit(_compute_chunk_idw, rs)
                    submit_ptr += 1

                for chunk_idx, rs in enumerate(chunk_starts):
                    # 检查取消信号；若已取消，立即取消未提交分块并退出
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise TaskCancelledException("任务已被取消")
                    # 等待当前分块的计算结果（一定在 pending 中）
                    try:
                        row_start_res, chunk_vals = pending.pop(rs).result()
                    except Exception as exc:
                        logger.error("scipy_idw 插值分块 row_start=%d 失败: %s", rs, exc)
                        raise

                    band.WriteArray(chunk_vals, 0, row_start_res)
                    del chunk_vals
                    gc.collect()

                    # 提交下一个分块（维持滑动窗口大小）
                    if submit_ptr < len(chunk_starts):
                        next_rs = chunk_starts[submit_ptr]
                        pending[next_rs] = executor.submit(_compute_chunk_idw, next_rs)
                        submit_ptr += 1

                    row_end = min(rs + chunk_rows, n_rows)
                    if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                        elapsed = time.time() - start_time
                        logger.info("scipy_idw 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                    row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("scipy_idw 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except TaskCancelledException:
            raise
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
        使用 scipy Delaunay 三角网插值，分块并行写入 GeoTIFF（v3.9 重构版）。

        重构说明（v3.9）：
            - 完全移除 KD-Tree IDW 填充逻辑：凸包外部 NaN 像素改用
              NearestNDInterpolator（scipy 自带最近邻插值，属于 TIN/Voronoi 体系，
              非 IDW），保持纯 scipy TIN 体系内实现。
            - sigma 自适应改用 Delaunay 边长中位数（无需 cKDTree 用于数值计算）：
              从 Delaunay 三角网提取所有边，取边长中位数作为 d_typical，
              计算 sigma_pixels = max(1, factor * d_typical / resolution)。
            - 沿用 v3.8 的径向辅助场重塑（scipy_tin_radial_assist，默认 True）。
            - 沿用 v3.7 的全图高斯平滑后处理（gaussian_filter + mask 归一化）。
            - 废弃参数新增：scipy_tin_idw_neighbors、scipy_tin_idw_power
              （已改用 NearestNDInterpolator，传入非默认值输出 deprecation warning）。

        重构说明（v3.8）：
            - 新增径向辅助场重塑（scipy_tin_radial_assist，默认 True）：
              ① 计算所有采样点的几何中心 (cx, cy)，对每个采样点计算径向距离 r_i。
              ② 将 (r_i, ia_i) 按距离分 bin 合并均值，用 PchipInterpolator 拟合
                 1D 径向趋势曲线 f_radial(r)。
              ③ 计算残差 aux_i = ia_i - f_radial(r_i)，对残差做 2D TIN 插值。
              ④ 最终像素值 = f_radial(r_pixel) + TIN_residual(pixel)。
              此策略从根本上消除同心环带：径向方向由 f_radial 保证平滑，
              横向局部细节由 TIN 残差场保留。

        参数:
            x_arr: 采样点X坐标（UTM easting，米）
            y_arr: 采样点Y坐标（UTM northing，米）
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'scipy_tin' 方法。"
                "请在QGIS Python环境中运行: pip install scipy"
            )

        # 检查废弃参数，若用户显式传入非默认值则记录 warning（v3.7）
        _deprecated_nondefault = []
        if self.scipy_tin_blend_safe_dist is not None:
            _deprecated_nondefault.append("scipy_tin_blend_safe_dist")
        if self.scipy_tin_blend_far_dist is not None:
            _deprecated_nondefault.append("scipy_tin_blend_far_dist")
        if self.scipy_tin_density_safe_factor != 0.25:
            _deprecated_nondefault.append("scipy_tin_density_safe_factor")
        if self.scipy_tin_density_far_factor != 1.5:
            _deprecated_nondefault.append("scipy_tin_density_far_factor")
        if _deprecated_nondefault:
            logger.warning(
                "v3.7+ scipy_tin 已弃用以下参数，改用 scipy_tin_smooth_sigma_factor；"
                "传入的值将被忽略: %s",
                ", ".join(_deprecated_nondefault),
            )

        # 检查 v3.9 新废弃的 IDW 参数
        _deprecated_idw = []
        if self.scipy_tin_idw_neighbors != 24:
            _deprecated_idw.append("scipy_tin_idw_neighbors")
        if self.scipy_tin_idw_power != 1.5:
            _deprecated_idw.append("scipy_tin_idw_power")
        if _deprecated_idw:
            logger.warning(
                "v3.9+ scipy_tin 已弃用 IDW 相关参数（凸包外部填充已改用 NearestNDInterpolator）；"
                "传入的值将被忽略: %s",
                ", ".join(_deprecated_idw),
            )

        interp = None
        nn_interp = None     # NearestNDInterpolator（v3.9：凸包外部填充）
        f_radial = None
        out_ds = None
        band = None

        try:
            points = np.column_stack([x_arr, y_arr])

            # ==================================================================
            # v3.8 径向辅助场重塑（方案 A）：消除同心环带
            # 当 scipy_tin_radial_assist=True 时：
            #   1. 拟合 1D 径向趋势 f_radial(r)，再对残差做 2D TIN 插值；
            #   2. 最终值 = f_radial(r_pixel) + TIN_residual(pixel)。
            # 当 scipy_tin_radial_assist=False 时：退回 v3.7 行为（直接对原始值 TIN）。
            # ==================================================================
            use_radial_assist = False
            cx = cy = 0.0
            tin_values = values.astype(np.float64)  # TIN 使用的值（原始值或残差）

            if self.scipy_tin_radial_assist and len(x_arr) >= 3:
                try:
                    # 1. 计算几何中心
                    cx = float(np.mean(x_arr))
                    cy = float(np.mean(y_arr))

                    # 2. 计算各采样点到中心的径向距离
                    r_arr = np.sqrt((x_arr - cx) ** 2 + (y_arr - cy) ** 2)

                    # 3. 按距离分 bin（容差 = resolution/2），合并均值
                    sorted_idx = np.argsort(r_arr)
                    r_sorted = r_arr[sorted_idx]
                    v_sorted = values[sorted_idx].astype(np.float64)

                    tol_bin = self.resolution / 2.0
                    merged_r = [float(r_sorted[0])]
                    merged_v = [float(v_sorted[0])]
                    running_sum = float(v_sorted[0])
                    running_cnt = 1
                    last_r = merged_r[0]
                    for i in range(1, len(r_sorted)):
                        _r = float(r_sorted[i])
                        _v = float(v_sorted[i])
                        if _r - last_r < tol_bin:
                            # Accumulate within bin; average written on bin close
                            running_sum += _v
                            running_cnt += 1
                        else:
                            # Close current bin: write final average once
                            merged_v[-1] = running_sum / running_cnt
                            # Start new bin
                            merged_r.append(_r)
                            merged_v.append(_v)
                            last_r = _r
                            running_sum = _v
                            running_cnt = 1
                    # Close the last bin
                    merged_v[-1] = running_sum / running_cnt
                    del r_sorted, v_sorted, sorted_idx
                    gc.collect()

                    r_knots = np.array(merged_r, dtype=np.float64)
                    v_knots = np.array(merged_v, dtype=np.float64)
                    del merged_r, merged_v

                    if len(r_knots) >= 2:
                        # 4. 用 PchipInterpolator 拟合径向趋势（单调三次 Hermite，无振荡）
                        f_radial = _PchipInterpolator(r_knots, v_knots, extrapolate=True)
                        n_knots = len(r_knots)

                        # 5. 计算残差：tin_values = ia - f_radial(r)
                        radial_at_samples = f_radial(r_arr)
                        tin_values = values.astype(np.float64) - radial_at_samples
                        del radial_at_samples, r_arr, r_knots, v_knots
                        gc.collect()

                        use_radial_assist = True
                        logger.info(
                            "径向辅助场（v3.8）: 中心=(%.1f, %.1f), 控制点数=%d, "
                            "残差范围=[%.4f, %.4f]",
                            cx, cy, n_knots,
                            float(tin_values.min()), float(tin_values.max()),
                        )
                    else:
                        logger.warning(
                            "径向辅助场：合并后控制点数 %d < 2，回退到 v3.7 行为",
                            len(r_knots),
                        )
                        del r_knots, v_knots, r_arr
                        gc.collect()
                except Exception as _e:
                    logger.warning(
                        "径向辅助场构建失败（%s），回退到 v3.7 行为", _e
                    )
                    # 异常时回退：使用原始值，use_radial_assist 已为 False
                    tin_values = values.astype(np.float64)
                    use_radial_assist = False

            if use_radial_assist:
                logger.info("scipy_tin v3.9 模式：径向辅助场重塑（消除同心环带）已启用")
            else:
                logger.info("scipy_tin v3.9 模式：直接对原始值 TIN 插值（无径向辅助）")

            # 构建 TIN 主插值器（使用残差 tin_values 或原始值）
            if self.scipy_tin_smooth:
                interp = _CloughTocher2DInterpolator(
                    points, tin_values,
                    fill_value=np.nan,
                    tol=1e-6,
                    maxiter=400,
                    rescale=True,
                )
                logger.info("使用 scipy CloughTocher TIN 插值（C1连续，最平滑，rescale=True）")
            else:
                interp = _LinearNDInterpolator(
                    points, tin_values,
                    fill_value=np.nan,
                    rescale=True,
                )
                logger.info("使用 scipy Linear TIN 插值（C0连续，更快，rescale=True）")

            # 构建 NearestNDInterpolator（v3.9：凸包外部 NaN 像素填充，替代 IDW）
            # 与 TIN 使用相同的采样点和目标值（残差或原始值），保持一致性
            nn_interp = _NearestNDInterpolator(
                np.column_stack([x_arr, y_arr]), tin_values
            )

            del points
            gc.collect()

            # 计算 d_typical：使用 Delaunay 三角网边长中位数（v3.9，不使用 cKDTree）
            pts_xy = np.column_stack([x_arr, y_arr])
            tri_for_sigma = _Delaunay(pts_xy)
            simplices = tri_for_sigma.simplices  # shape (n_simplices, 3)
            # 提取所有唯一边（numpy 向量化，避免 Python 循环）
            edges_01 = np.sort(simplices[:, :2], axis=1)
            edges_02 = np.sort(simplices[:, [0, 2]], axis=1)
            edges_12 = np.sort(simplices[:, 1:], axis=1)
            all_edges = np.unique(
                np.vstack([edges_01, edges_02, edges_12]), axis=0
            )
            del edges_01, edges_02, edges_12, simplices, tri_for_sigma
            edge_vecs = pts_xy[all_edges[:, 0]] - pts_xy[all_edges[:, 1]]
            edge_lengths = np.sqrt((edge_vecs ** 2).sum(axis=1))
            d_typical = float(np.median(edge_lengths))
            del all_edges, edge_vecs, edge_lengths, pts_xy
            gc.collect()

            # 高斯平滑 sigma（像素数）
            sigma_factor = float(self.scipy_tin_smooth_sigma_factor)
            if sigma_factor < 0.0:
                logger.warning(
                    "scipy_tin_smooth_sigma_factor=%.3f 为负数，已重置为 0（禁用平滑）",
                    sigma_factor,
                )
                sigma_factor = 0.0
            if sigma_factor > 0.0:
                sigma_pixels = max(1.0, sigma_factor * d_typical / self.resolution)
            else:
                sigma_pixels = 0.0

            logger.info(
                "scipy_tin v3.9 参数: d_typical=%.1f m (Delaunay边长中位数), "
                "sigma_factor=%.2f, sigma_pixels=%.2f px, radial_assist=%s",
                d_typical, sigma_factor, sigma_pixels, use_radial_assist,
            )

            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            n_cols = self._n_cols
            chunk_rows = self.chunk_size
            chunk_starts = list(range(0, n_rows, chunk_rows))

            # 检查是否有足够内存累积完整结果数组用于高斯平滑
            # 共需 5 个 (n_rows×n_cols) float32 数组：full_arr, mask, arr_safe, num, den
            _SMOOTH_ARRAY_COUNT = 5
            full_arr_bytes = n_rows * n_cols * 4
            smooth_mem_bytes = full_arr_bytes * _SMOOTH_ARRAY_COUNT
            do_smooth = (
                sigma_pixels > 0.0
                and smooth_mem_bytes <= self.max_memory_gb * 1e9
            )
            if sigma_pixels > 0.0 and not do_smooth:
                logger.warning(
                    "跳过高斯平滑后处理：所需内存 %.2f GB 超过 max_memory_gb=%.1f GB",
                    smooth_mem_bytes / 1e9, self.max_memory_gb,
                )

            # 完整结果数组（NoData 填 -9999.0），用于高斯平滑后统一写盘
            if do_smooth:
                full_arr = np.full((n_rows, n_cols), -9999.0, dtype=np.float32)
            else:
                full_arr = None

            # 内层函数：计算单个分块（TIN + NearestNDInterpolator 填充 NaN）
            # 返回 (row_start, result_2d, n_pure_tin, n_nn_filled)
            def _compute_chunk_tin(
                row_start: int,
            ) -> Tuple[int, np.ndarray, int, int]:
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start
                grid_y = self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat
                xx, yy = np.meshgrid(grid_x, grid_y)
                pts = np.column_stack([xx.ravel(), yy.ravel()])
                del xx, yy

                # TIN 插值（三角网外部像素返回 NaN；当 radial_assist 时为残差场）
                chunk_vals_tin = interp(pts)

                nan_mask = np.isnan(chunk_vals_tin)
                n_pure_tin = int((~nan_mask).sum())
                n_nn_filled = int(nan_mask.sum())

                # v3.9：仅对 NaN 像素执行 NearestNDInterpolator 填充（替代 IDW）
                if nan_mask.any():
                    nan_pts = pts[nan_mask]
                    chunk_vals_tin[nan_mask] = nn_interp(nan_pts)
                    del nan_pts

                # v3.8 径向辅助场重塑：将径向趋势加回残差场，得到最终 Ia 值
                if use_radial_assist and f_radial is not None:
                    r_pixels = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
                    chunk_vals_tin += f_radial(r_pixels)
                    del r_pixels

                del pts

                chunk_vals_tin = chunk_vals_tin.reshape(actual_rows, n_cols).astype(np.float32)
                np.maximum(chunk_vals_tin, 0.0, out=chunk_vals_tin)
                return (row_start, chunk_vals_tin, n_pure_tin, n_nn_filled)

            start_time = time.time()
            logger.info("scipy_tin 插值开始，分块数=%d，并行线程数=%d",
                        len(chunk_starts), self.max_interp_workers)

            total_pure_tin = 0
            total_nn_filled = 0

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_interp_workers
            ) as executor:
                pending: dict = {}
                submit_ptr = 0

                while submit_ptr < len(chunk_starts) and submit_ptr < self.max_interp_workers:
                    rs = chunk_starts[submit_ptr]
                    pending[rs] = executor.submit(_compute_chunk_tin, rs)
                    submit_ptr += 1

                for chunk_idx, rs in enumerate(chunk_starts):
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise TaskCancelledException("任务已被取消")
                    try:
                        (row_start_res, chunk_vals,
                         n_pure_tin_chunk, n_nn_filled_chunk) = pending.pop(rs).result()
                    except Exception as exc:
                        logger.error("scipy_tin 插值分块 row_start=%d 失败: %s", rs, exc)
                        raise

                    # 若需平滑，先累积到 full_arr；否则直接写盘
                    if do_smooth:
                        full_arr[row_start_res: row_start_res + chunk_vals.shape[0], :] = chunk_vals
                    else:
                        band.WriteArray(chunk_vals, 0, row_start_res)
                    del chunk_vals

                    # 累计统计
                    total_pure_tin += n_pure_tin_chunk
                    total_nn_filled += n_nn_filled_chunk

                    gc.collect()

                    if submit_ptr < len(chunk_starts):
                        next_rs = chunk_starts[submit_ptr]
                        pending[next_rs] = executor.submit(_compute_chunk_tin, next_rs)
                        submit_ptr += 1

                    row_end = min(rs + chunk_rows, n_rows)
                    if (chunk_idx + 1) % 5 == 0 or row_end == n_rows:
                        elapsed = time.time() - start_time
                        logger.info("scipy_tin 进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                                    row_end, n_rows, 100.0 * row_end / n_rows, elapsed)

            # 高斯平滑后处理（v3.7）：对完整栅格做可分离 2D 高斯卷积，消除环带/折线条带
            if do_smooth and full_arr is not None:
                logger.info("scipy_tin 高斯平滑后处理: sigma=%.2f px ...", sigma_pixels)
                mask = (full_arr != -9999.0).astype(np.float32)
                arr_safe = np.where(mask > 0, full_arr, 0.0).astype(np.float32)
                num = _gaussian_filter(arr_safe, sigma=sigma_pixels)
                den = _gaussian_filter(mask, sigma=sigma_pixels)
                smoothed = np.where(den > 1e-6, num / den, -9999.0).astype(np.float32)
                del arr_safe, num, den, mask
                gc.collect()
                band.WriteArray(smoothed, 0, 0)
                del smoothed
                gc.collect()
                logger.info("scipy_tin 高斯平滑完成")

            band.ComputeStatistics(False)
            band.FlushCache()

            # 统计日志（v3.9：NN最近邻填充替代 IDW）
            total_pixels = self._n_rows * self._n_cols
            logger.info(
                "scipy_tin 像素统计: 纯TIN=%d (%.2f%%), NN最近邻填充=%d (%.2f%%)",
                total_pure_tin, 100.0 * total_pure_tin / total_pixels,
                total_nn_filled, 100.0 * total_nn_filled / total_pixels,
            )

            total_time = time.time() - start_time
            logger.info("scipy_tin 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error("scipy_tin 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del interp
            if nn_interp is not None:
                del nn_interp
            if f_radial is not None:
                del f_radial
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

        UTM 是等距米坐标，直接使用欧氏距离 sqrt(dx² + dy²)，无需 cos(lat) 修正。

        参数:
            x_arr: 采样点X坐标（UTM easting，米）
            y_arr: 采样点Y坐标（UTM northing，米）
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
            # 计算震中（几何中心，UTM 米坐标）
            center_x = float(np.mean(x_arr))
            center_y = float(np.mean(y_arr))
            logger.info("震中坐标（UTM m）: (%.1f, %.1f)", center_x, center_y)

            # UTM 等距坐标，直接使用欧氏距离
            dx = x_arr - center_x
            dy = y_arr - center_y
            distances = np.sqrt(dx ** 2 + dy ** 2)

            sorted_idx = np.argsort(distances)
            dist_sorted = distances[sorted_idx]
            val_sorted = values[sorted_idx]
            del distances, sorted_idx, dx, dy
            gc.collect()

            # 对距离去重：将距离相差不超过 resolution/2 的点合并取均值
            tol = self.resolution / 2.0
            merged_dists = [float(dist_sorted[0])]
            merged_vals = [float(val_sorted[0])]
            running_sum = float(val_sorted[0])
            running_cnt = 1

            for d, v in zip(dist_sorted[1:], val_sorted[1:]):
                d = float(d)
                v = float(v)
                if d - merged_dists[-1] < tol:
                    running_sum += v
                    running_cnt += 1
                    merged_vals[-1] = running_sum / running_cnt
                else:
                    merged_dists.append(d)
                    merged_vals.append(v)
                    running_sum = v
                    running_cnt = 1

            del dist_sorted, val_sorted
            gc.collect()

            dist_arr = np.array(merged_dists, dtype=np.float64)
            val_arr = np.array(merged_vals, dtype=np.float64)
            del merged_dists, merged_vals

            logger.info("距离范围: %.1f m ~ %.1f m，合并控制点数: %d",
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
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            start_time = time.time()

            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                self._check_cancelled()
                row_end = min(row_start + chunk_rows, n_rows)
                actual_rows = row_end - row_start

                grid_y = self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat
                xx, yy = np.meshgrid(grid_x, grid_y)

                # UTM 等距坐标，直接使用欧氏距离
                pixel_dx = xx - center_x
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
                band.WriteArray(chunk_vals.astype(np.float32), 0, row_start)
                del chunk_vals
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

        except TaskCancelledException:
            raise
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
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon

            n_rows = self._n_rows
            chunk_rows = self.chunk_size
            start_time = time.time()

            for chunk_idx, row_start in enumerate(range(0, n_rows, chunk_rows)):
                self._check_cancelled()
                row_end = min(row_start + chunk_rows, n_rows)

                grid_y = self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat

                try:
                    z, _ss = ok.execute(
                        'grid',
                        grid_x.astype(np.float64),
                        grid_y.astype(np.float64),
                        n_closest_points=self.kriging_neighbors,
                        backend='loop',
                    )
                except Exception as exc:
                    logger.error("kriging 插值第 %d 块失败: %s", chunk_idx, exc)
                    raise
                del _ss

                chunk_vals = np.array(z, dtype=np.float32)
                del z
                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                band.WriteArray(chunk_vals, 0, row_start)
                del chunk_vals
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

        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error("kriging 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
            del ok
            gc.collect()

    # ==================== EBK 插值方法（子集化克里金）====================

    def _run_ebk_interpolation(
            self,
            x_arr: np.ndarray,
            y_arr: np.ndarray,
            values: np.ndarray,
            output_tif_path: str,
    ) -> None:
        """
        对齐 ArcGIS EBK 原理的子集化克里金插值（v3.5 升级版）。

        算法说明（v3.5 EBK）：
            1. 子集划分：使用 cKDTree 将采样点划分为多个重叠子集
               （每个子集约 ebk_subset_size 个点）。overlap_factor 钳制到 [1.0, 3.0]。
            2. 子集建模（并行）：ThreadPoolExecutor 并行构建局部 OrdinaryKriging 模型。
               每个子集执行 ebk_n_simulations 次模拟：每次对变差函数 sill/range 参数
               加入 ±ebk_simulation_noise 高斯扰动，建立 OK 模型；
               ebk_n_simulations=1 时退化为无扰动的原有行为。
            3. 逐分块预测：每个像素查询最近的 ebk_predict_neighbors 个子集，
               权重使用二次平滑核 w = max(0, (1-(d/r)²))²（r 为子集KNN半径），
               全为 0 时退回 1/(1+d²)；各模拟的预测值取均值后再做子集加权。

        参数:
            x_arr: 采样点X坐标（UTM easting，米）
            y_arr: 采样点Y坐标（UTM northing，米）
            values: 采样点对应值（Ia）
            output_tif_path: 输出 GeoTIFF 文件路径
        """
        if not _HAS_PYKRIGE:
            raise ImportError(
                "pykrige 未安装，无法使用 'kriging' (EBK) 方法。"
                "请在 QGIS Python 环境中运行: pip install pykrige"
            )
        if not _HAS_SCIPY:
            raise ImportError(
                "scipy 未安装，无法使用 'kriging' (EBK) 方法。"
                "请在 QGIS Python 环境中运行: pip install scipy"
            )

        out_ds = None
        band = None

        try:
            n_samples = len(x_arr)
            subset_size = self.ebk_subset_size
            n_simulations = max(1, self.ebk_n_simulations)
            sim_noise = self.ebk_simulation_noise
            k_predict_cfg = self.ebk_predict_neighbors

            # overlap_factor 钳制到 [1.0, 3.0]
            overlap_factor = self.ebk_overlap_factor
            if overlap_factor < 1.0:
                logger.warning(
                    "ebk_overlap_factor=%.2f 小于 1.0，已钳制为 1.0", overlap_factor
                )
                overlap_factor = 1.0
            elif overlap_factor > 3.0:
                logger.warning(
                    "ebk_overlap_factor=%.2f 大于 3.0，已钳制为 3.0", overlap_factor
                )
                overlap_factor = 3.0

            # 向后兼容：若 ebk_variogram 仍是默认值 'power' 且
            # 用户设置了非默认的 kriging_variogram（!= 'linear'），则使用旧值
            variogram = self.ebk_variogram
            if variogram == 'power' and self.kriging_variogram != 'linear':
                variogram = self.kriging_variogram
                logger.info("EBK 向后兼容：使用 kriging_variogram='%s'", variogram)

            # ArcGIS EBK 默认 n_closest_points=15；保持向后兼容不改 __init__ 默认值
            n_closest = min(15, self.kriging_neighbors, subset_size)
            if self.kriging_neighbors < 15:
                logger.info(
                    "EBK 搜索邻域: 使用 kriging_neighbors=%d（ArcGIS EBK 默认 15）",
                    self.kriging_neighbors,
                )

            logger.info(
                "EBK 插值: 采样点数=%d, 子集大小=%d, 重叠因子=%.1f, 变差函数=%s, "
                "模拟次数=%d, 扰动幅度=%.2f, 预测邻近子集数=%d",
                n_samples, subset_size, overlap_factor, variogram,
                n_simulations, sim_noise if n_simulations > 1 else 0.0,
                k_predict_cfg,
            )

            # ---- 1. 子集划分 ----
            pts_train = np.column_stack([x_arr, y_arr]).astype(np.float64)
            tree_train = _cKDTree(pts_train)
            vals_f64 = values.astype(np.float64)
            k_per_subset = min(subset_size, n_samples)

            n_subsets = max(1, int(np.ceil(n_samples / subset_size * overlap_factor)))
            n_subsets = min(n_subsets, n_samples)

            rng = np.random.default_rng(seed=42)
            seed_indices = rng.choice(n_samples, size=n_subsets, replace=False)

            logger.info("EBK 子集划分: 计划建立 %d 个子集（每个约 %d 点），每个子集 %d 次模拟",
                        n_subsets, k_per_subset, n_simulations)

            # ---- 2. 并行建立局部克里金模型（每个子集含 n_simulations 个模型）----
            # local_models: list of (models_list, center_x, center_y, radius)
            # models_list: list of OrdinaryKriging 对象（长度 = n_simulations）
            local_models = []
            report_interval = max(1, n_subsets // 10)

            def _build_subset(args):
                """在线程中构建单个子集的所有模拟模型。"""
                i, seed_idx = args
                # 每 50 个子集在主线程外检查取消（直接访问 event 是线程安全的）
                if i % 50 == 0 and self._cancel_event is not None and self._cancel_event.is_set():
                    raise TaskCancelledException("任务已被取消")

                seed_pt = pts_train[seed_idx]
                dists_k, idxs_k = tree_train.query(seed_pt.reshape(1, -1), k=k_per_subset)
                dists_k = dists_k[0]
                idxs_k  = idxs_k[0]

                sub_x = x_arr[idxs_k].astype(np.float64)
                sub_y = y_arr[idxs_k].astype(np.float64)
                sub_v = vals_f64[idxs_k]

                if len(idxs_k) < 3:
                    return None
                if np.std(sub_v) < 1e-10:
                    return None

                radius = float(np.max(dists_k))
                center_x = float(seed_pt[0])
                center_y = float(seed_pt[1])

                sim_models = []
                # 以子集为基础建立基础模型，获取变差函数参数
                try:
                    ok_base = _OrdinaryKriging(
                        sub_x, sub_y, sub_v,
                        variogram_model=variogram,
                        nlags=self.kriging_nlags,
                        verbose=False,
                        enable_plotting=False,
                    )
                except Exception as exc:
                    logger.debug("EBK 子集 %d 基础建模失败: %s", i + 1, exc)
                    return None

                if n_simulations == 1:
                    # 退化为原有行为：单模型无扰动
                    sim_models.append(ok_base)
                else:
                    # 从基础模型读取变差函数参数（pykrige 存储在 variogram_model_parameters）
                    try:
                        base_params = ok_base.variogram_model_parameters
                    except AttributeError:
                        base_params = None

                    sim_rng = np.random.default_rng(seed=seed_idx + 1000)
                    for _s in range(n_simulations):
                        if base_params is not None and sim_noise > 0.0:
                            try:
                                # 对所有参数加入高斯扰动（±sim_noise 相对幅度）
                                perturbed = [
                                    max(p * (1.0 + sim_rng.normal(0.0, sim_noise)), 1e-12)
                                    for p in base_params
                                ]
                                ok_sim = _OrdinaryKriging(
                                    sub_x, sub_y, sub_v,
                                    variogram_model=variogram,
                                    variogram_parameters=perturbed,
                                    nlags=self.kriging_nlags,
                                    verbose=False,
                                    enable_plotting=False,
                                )
                                sim_models.append(ok_sim)
                            except Exception:
                                # 扰动建模失败时使用基础模型
                                sim_models.append(ok_base)
                        else:
                            sim_models.append(ok_base)

                return (sim_models, center_x, center_y, radius)

            logger.info("EBK 开始并行建模（线程数=%d）...", self.max_interp_workers)
            build_start = time.time()

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.max_interp_workers
            ) as executor:
                futures = {
                    executor.submit(_build_subset, (i, seed_idx)): i
                    for i, seed_idx in enumerate(seed_indices)
                }
                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    # 取消检查
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise TaskCancelledException("任务已被取消")
                    i = futures[future]
                    try:
                        result = future.result()
                    except TaskCancelledException:
                        raise
                    except Exception as exc:
                        logger.warning("EBK 子集 %d 建模失败: %s", i + 1, exc)
                        result = None
                    completed += 1
                    if result is not None:
                        local_models.append(result)
                    if completed % report_interval == 0 or completed == n_subsets:
                        logger.info("EBK 建模进度: %d/%d，已建立 %d 个模型",
                                    completed, n_subsets, len(local_models))

            build_elapsed = time.time() - build_start
            logger.info("EBK 建模完成，耗时 %.1fs，共 %d 个局部子集模型", build_elapsed, len(local_models))

            if not local_models:
                raise RuntimeError(
                    "EBK 没有可用的子集克里金模型，"
                    "请检查采样点数量（≥3）和 ebk_subset_size 参数"
                )

            n_models = len(local_models)

            # 子集中心的 KD-Tree（用于快速查找每个像素的最近子集）
            centers = np.array(
                [[m[1], m[2]] for m in local_models], dtype=np.float64
            )
            radii = np.array([m[3] for m in local_models], dtype=np.float64)
            tree_centers = _cKDTree(centers)
            k_predict = min(k_predict_cfg, n_models)

            # ---- 3. 输出栅格准备 ----
            os.makedirs(os.path.dirname(os.path.abspath(output_tif_path)), exist_ok=True)
            self._ensure_file_writable(output_tif_path)
            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_tif_path,
                self._n_cols, self._n_rows, 1, gdal.GDT_Float32,
                ['COMPRESS=LZW', 'TILED=YES'],
            )
            out_ds.SetGeoTransform(self._geo_transform)
            out_ds.SetProjection(self._utm_srs.ExportToWkt())
            band = out_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)

            grid_x = self._x_min + (np.arange(self._n_cols) + 0.5) * self._res_lon
            n_rows_total = self._n_rows
            chunk_rows = self.chunk_size
            start_time = time.time()

            # ---- 4. 逐分块预测（串行，进度完整报告）----
            for chunk_idx, row_start in enumerate(range(0, n_rows_total, chunk_rows)):
                self._check_cancelled()
                row_end = min(row_start + chunk_rows, n_rows_total)
                actual_rows = row_end - row_start

                grid_y = (
                    self._y_max - (np.arange(row_start, row_end) + 0.5) * self._res_lat
                )
                xx, yy = np.meshgrid(grid_x, grid_y)
                pts_query = np.column_stack([xx.ravel(), yy.ravel()])
                del xx, yy
                n_pts = pts_query.shape[0]

                # 查询每个像素最近的 k_predict 个子集
                dists_to_centers, subset_idxs = tree_centers.query(pts_query, k=k_predict)
                if k_predict == 1:
                    dists_to_centers = dists_to_centers.reshape(-1, 1)
                    subset_idxs      = subset_idxs.reshape(-1, 1)

                # 二次平滑核权重 w = max(0, (1-(d/r)²))²（r 为该子集 KNN 半径）
                radii_per_pixel = radii[subset_idxs]  # (n_pts, k_predict)
                # r=0 时用 1.0 代替（防除零；此时 d/r≥1 使权重=0，自动触发后续 fallback）
                safe_radii = np.where(radii_per_pixel > 0.0, radii_per_pixel, 1.0)
                d_over_r = dists_to_centers / safe_radii
                weights_pred = np.maximum(0.0, 1.0 - d_over_r ** 2) ** 2  # (n_pts, k_predict)

                # 若所有权重为 0（像素在所有子集 KNN 半径之外），退回 1/(1+d²)
                weight_row_sum = weights_pred.sum(axis=1)  # (n_pts,)
                fallback_mask = weight_row_sum == 0.0
                if fallback_mask.any():
                    fallback_weights = 1.0 / (1.0 + dists_to_centers[fallback_mask] ** 2)
                    weights_pred[fallback_mask] = fallback_weights

                # 展平，方便按模型分组批量预测
                all_model_idxs = subset_idxs.ravel()                     # (n_pts * k_predict,)
                all_pixel_idxs = np.repeat(np.arange(n_pts), k_predict)  # (n_pts * k_predict,)
                all_weights    = weights_pred.ravel()                     # (n_pts * k_predict,)

                weighted_sum = np.zeros(n_pts, dtype=np.float64)
                weight_total = np.zeros(n_pts, dtype=np.float64)

                # 按模型批量预测（每个唯一子集只调用一次 ok.execute 或均值预测）
                for m_idx in np.unique(all_model_idxs):
                    sel         = all_model_idxs == m_idx
                    px_idxs_sel = all_pixel_idxs[sel]
                    w_sel       = all_weights[sel]
                    px_coords   = pts_query[px_idxs_sel, 0].astype(np.float64)
                    py_coords   = pts_query[px_idxs_sel, 1].astype(np.float64)

                    sim_models_list = local_models[m_idx][0]  # list of OrdinaryKriging

                    # 对每个模拟的预测值累加，最终取均值
                    sim_sum = np.zeros(len(px_idxs_sel), dtype=np.float64)
                    sim_cnt = 0
                    for ok_sim in sim_models_list:
                        try:
                            z_sim, _ = ok_sim.execute(
                                'points', px_coords, py_coords,
                                n_closest_points=n_closest,
                                backend='loop',
                            )
                            sim_sum += np.asarray(z_sim, dtype=np.float64)
                            sim_cnt += 1
                        except Exception as exc:
                            logger.debug(
                                "EBK 子集 %d 块 %d 模拟预测失败，跳过: %s",
                                m_idx, chunk_idx, exc,
                            )
                            continue

                    if sim_cnt == 0:
                        continue
                    z_mean = sim_sum / sim_cnt

                    np.add.at(weighted_sum, px_idxs_sel, w_sel * z_mean)
                    np.add.at(weight_total, px_idxs_sel, w_sel)

                del pts_query, dists_to_centers, subset_idxs, weights_pred
                del all_model_idxs, all_pixel_idxs, all_weights

                # 归一化
                with np.errstate(divide='ignore', invalid='ignore'):
                    chunk_vals = np.where(
                        weight_total > 0.0,
                        weighted_sum / weight_total,
                        -9999.0,
                    )
                del weighted_sum, weight_total

                chunk_vals = chunk_vals.reshape(actual_rows, self._n_cols).astype(np.float32)
                nodata_mask = chunk_vals < -9998.0
                np.maximum(chunk_vals, 0.0, out=chunk_vals)
                chunk_vals[nodata_mask] = -9999.0

                band.WriteArray(chunk_vals, 0, row_start)
                del chunk_vals
                gc.collect()

                if (chunk_idx + 1) % 5 == 0 or row_end == n_rows_total:
                    elapsed = time.time() - start_time
                    logger.info(
                        "EBK 预测进度: %d/%d 行 (%.1f%%), 已用时: %.1fs",
                        row_end, n_rows_total,
                        100.0 * row_end / n_rows_total, elapsed,
                    )

            band.ComputeStatistics(False)
            band.FlushCache()

            total_time = time.time() - start_time
            logger.info("EBK 插值完成，总耗时: %.1fs, 已保存: %s",
                        total_time, output_tif_path)

        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error("EBK 插值失败: %s", exc, exc_info=True)
            raise
        finally:
            out_ds = None
            band = None
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
        method = self.interp_method

        if method == 'qgis_tin':
            self._run_qgis_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        elif method == 'qgis_idw':
            self._run_arcgis_idw_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        elif method == 'scipy_idw':
            self._run_scipy_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        elif method == 'scipy_tin':
            self._run_scipy_tin_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        elif method == 'radial':
            self._run_radial_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        elif method == 'kriging':
            self._run_ebk_interpolation(x_arr, y_arr, ia_values, output_tif_path)
        else:
            raise ValueError(
                f"不支持的插值方法: '{method}'，"
                f"可选: 'scipy_tin', 'radial', 'scipy_idw', 'kriging', 'qgis_idw', 'qgis_tin'"
            )

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

            mem_driver = ogr.GetDriverByName('Memory')
            mem_ds = mem_driver.CreateDataSource('pga_contours')
            layer = mem_ds.CreateLayer(
                'contours', srs=self._utm_srs, geom_type=ogr.wkbPolygon
            )

            field_defn = ogr.FieldDefn('PGA', ogr.OFTReal)
            layer.CreateField(field_defn)

            for contour in reversed(self._contours):
                coords = contour['coordinates']
                if len(coords) < 3:
                    continue

                ring = ogr.Geometry(ogr.wkbLinearRing)
                for lon, lat in coords:
                    x, y, _ = self._coord_transform.TransformPoint(
                        float(lon), float(lat)
                    )
                    ring.AddPoint(x, y)

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
            raster_ds.SetGeoTransform(self._geo_transform)
            raster_ds.SetProjection(self._utm_srs.ExportToWkt())

            band = raster_ds.GetRasterBand(1)
            band.SetNoDataValue(-9999.0)
            band.Fill(min_pga)

            gdal.RasterizeLayer(
                raster_ds, [1], layer,
                options=["ATTRIBUTE=PGA"]
            )
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
        确保输出文件可写，如果文件已存在则尝试删除。

        参数:
            file_path: 输出文件路径
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        异常:
            RuntimeError: 文件无法删除或写入
        """
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
        logger.info("清理临时资源...")
        self._contours.clear()
        self._utm_srs = None
        self._wgs84_srs = None
        self._coord_transform = None
        gc.collect()
        logger.info("资源清理完成")

    # ==================== 主流程 ====================

    def run(self) -> bool:
        """
        执行完整的 KML → Ia.tif 转换流程

        流程:
            1. 解析KML文件
            2. 计算数据中心经度，自动选择 UTM 投影带
            3. 准备采样点（生成器下采样 + 随机抽样 + UTM坐标转换 + 去重）
            4. 构建输出栅格网格（UTM 米坐标，直接使用 resolution 作为像素大小）
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
        except TaskCancelledException:
            raise
        except Exception as exc:
            logger.error('KmlToIaConverter.run() 失败: %s', exc, exc_info=True)
            raise

    def _run_impl(self) -> bool:
        """run() 的实际实现。"""
        logger.info("=" * 60)
        logger.info("KML → Ia 栅格处理程序（QGIS 3.40.15，v3.9）")
        logger.info("插值方法: %s", self.interp_method)
        logger.info("采样间隔: %d，最大采样点数: %d",
                     self.sample_interval, self.max_sample_points)
        logger.info("输出PGA.tif: %s",
                     '是（矢量栅格化）' if self.export_pga else '否')
        logger.info("=" * 60)

        try:
            # 0. 检查取消信号（在任何耗时操作之前）
            self._check_cancelled()

            # 1. 检查输入文件
            if not os.path.exists(self.kml_path):
                logger.error("KML文件不存在: %s", self.kml_path)
                return False

            # 2. 解析KML
            contours = self.parse_kml()
            if len(contours) == 0:
                logger.error("未找到有效的PGA等值线")
                return False

            # 3. 计算数据中心经度，自动选择 UTM 投影带
            all_lons_iter = (
                lon for c in self._contours for lon, lat in c['coordinates']
            )
            all_lons_arr = np.fromiter(all_lons_iter, dtype=np.float64)
            center_lon = float(all_lons_arr.mean()) if all_lons_arr.size > 0 else 105.0
            del all_lons_arr
            self._setup_output_crs(center_lon)

            # 4. 准备采样点（坐标转换为 UTM 米坐标）
            x_arr, y_arr, ia_values = self._prepare_sample_points()

            # 5. 构建栅格网格（UTM 米坐标，直接使用 resolution 作为像素大小）
            self._build_grid(x_arr, y_arr)

            # 6. PGA矢量栅格化（可选）
            if self.export_pga and self.pga_output_path:
                logger.info("-" * 40)
                logger.info("步骤: PGA等值线矢量栅格化（非插值）")
                logger.info("-" * 40)
                pga_start = time.time()
                self._rasterize_pga_contours()
                pga_elapsed = time.time() - pga_start
                logger.info("PGA栅格化耗时: %.2f 秒", pga_elapsed)

            # 7. Ia插值
            logger.info("-" * 40)
            logger.info("步骤: Ia插值计算（%s）", self.interp_method)
            logger.info("-" * 40)

            interp_start = time.time()
            self._interpolate_ia_to_file(x_arr, y_arr, ia_values, self.ia_output_path)

            del x_arr, y_arr, ia_values
            gc.collect()

            interp_elapsed = time.time() - interp_start
            logger.info("✅ Ia插值计算到输出文件耗时: %.2f 秒", interp_elapsed)

            # 8. 汇总
            logger.info("=" * 60)
            logger.info("处理完成!")
            if self.export_pga and self.pga_output_path:
                logger.info("  PGA栅格: %s", self.pga_output_path)
            logger.info("  Ia栅格:  %s", self.ia_output_path)
            logger.info("=" * 60)

            return True

        except TaskCancelledException:
            raise
        except Exception as e:
            logger.error("转换失败: %s", e, exc_info=True)
            raise

        finally:
            self.cleanup()


# ==================== 入口 ====================
if __name__ == "__main__":
    converter = KmlToIaConverter(
        kml_path="E:\\code\\python\\地质\\PGA666.kml",  # 输入KML文件路径
        ia_output_path="E:\\code\\python\\地质\\Ia123.tif",  # Ia输出路径

        # PGA输出（可选，使用矢量栅格化非插值）
        pga_output_path="../../data/geology/kml/PGA.tif",  # 不需要PGA时设为None
        export_pga=False,  # 是否同时输出PGA.tif（矢量栅格化）

        # 基础参数
        resolution=30,  # 输出分辨率(米)；推荐10~100

        # 采样参数
        sample_interval=1,  # 等值线采样间隔；推荐3~10，越小采样越密
        max_sample_points=10000,  # 最大采样点数；超过时随机抽样，避免内存溢出

        # ========== 选择插值方法 ==========
        # 推荐方法（平滑，无突变）
        interp_method='qgis_idw',  # ArcGIS IDW (KD-Tree局部IDW，与ArcGIS默认对齐，需scipy)
        # interp_method='radial',   # 径向插值 - 专为同心圈，完美单调递增

        # 其他可用方法
        # interp_method='scipy_idw',  # scipy RBF - 速度快（可能有边界突变）
        # interp_method='kriging',    # EBK 子集化克里金 - 与ArcGIS EBK对齐，需scipy+pykrige
        # interp_method='qgis_tin',   # QGIS TIN - 无需额外依赖

        # ArcGIS IDW 参数（仅 interp_method='qgis_idw' 时有效，需安装scipy）
        qgis_idw_power=2.0,         # IDW幂次；推荐1.0~4.0，越大近点主导（与ArcGIS默认一致）
        idw_num_neighbors=12,        # 局部搜索邻近点数；默认12与ArcGIS一致，越大结果越平滑
        # idw_max_distance=50000,    # 最大搜索距离（米，UTM坐标），None表示不限制（与ArcGIS默认一致）
        #                            # 例如 50000 表示 50 km
        # (v4.0 新增) 椭圆径向辅助场重塑，消除同心环带，形状与PGA等值线吻合（推荐 True）
        # False=退回原始 IDW 行为（向后兼容/调试对比用）
        arcgis_idw_radial_assist=True,

        # QGIS TIN 参数（仅 interp_method='qgis_tin' 时有效）
        qgis_tin_method=0,  # TIN子方法: 0=线性（快）, 1=Clough-Tocher（平滑）

        # scipy IDW/RBF 参数（仅 interp_method='scipy_idw' 时有效，需安装scipy）
        scipy_kernel='thin_plate_spline',  # RBF核函数；推荐'thin_plate_spline'
        scipy_neighbors=100,  # 邻近点数；越小越快内存越低，推荐50~200

        # scipy TIN 参数（仅 interp_method='scipy_tin' 时有效，需安装scipy）
        scipy_tin_smooth=True,  # True=CloughTocher(C1最平滑), False=Linear(C0更快)
        scipy_tin_smooth_sigma_factor=0.5,   # (v3.7 新增) 高斯平滑 sigma 倍率；推荐 0.3~1.0；0=禁用平滑
        # (v3.8 新增) 径向辅助场重塑，消除同心环带（推荐 True）；False=退回 v3.7 行为
        scipy_tin_radial_assist=True,
        # 以下参数已废弃（向后兼容保留，内部不使用）：
        # scipy_tin_idw_neighbors=24,        # 废弃：v3.9+ 已改用 NearestNDInterpolator
        # scipy_tin_idw_power=1.5,           # 废弃：v3.9+ 已改用 NearestNDInterpolator
        # scipy_tin_blend_safe_dist=None,    # 废弃：v3.5/v3.6 TIN 纯用距离阈值（米）
        # scipy_tin_blend_far_dist=None,     # 废弃：v3.5/v3.6 IDW 纯用距离阈值（米）
        # scipy_tin_density_safe_factor=0.25,# 废弃：v3.6 自适应 d_safe 倍率
        # scipy_tin_density_far_factor=1.5,  # 废弃：v3.6 自适应 d_blend 倍率

        # 径向插值参数（仅 interp_method='radial' 时有效，需安装scipy）
        radial_kind='cubic',  # 1D插值类型: 'linear'(快), 'cubic'(更平滑)

        # EBK 参数（仅 interp_method='kriging' 时有效，需安装scipy+pykrige）
        ebk_subset_size=100,         # 子集大小；默认100与ArcGIS EBK一致
        ebk_overlap_factor=1.0,      # 子集重叠因子；[1.0, 3.0]，越大子集数越多
        ebk_variogram='power',       # 变差函数；默认'power'与ArcGIS EBK一致
        ebk_n_simulations=100,       # (v3.5) 每个子集的模拟次数；默认100与ArcGIS EBK一致
        ebk_predict_neighbors=4,     # (v3.5) 每像素参与加权的子集数；默认4
        ebk_simulation_noise=0.1,    # (v3.5) 变差函数参数扰动幅度；n_simulations>1 时生效
        # 旧版 kriging 参数（保留向后兼容）
        kriging_variogram='linear',  # 变差函数: 旧参数，ebk_variogram优先生效
        kriging_nlags=6,             # 半变差函数滞后数
        kriging_neighbors=50,        # 克里金最近邻点数（每次预测使用的训练点数）

        # 内存优化参数
        chunk_size=1000,  # 栅格分块行数；推荐500~2000
        coord_batch_size=10000,  # 坐标转换批次大小；推荐5000~50000
        max_memory_gb=10.0,  # 最大内存使用限制(GB)；参考值，实际由上方参数控制

        # 并行插值参数（scipy/ArcGIS IDW 方法适用）
        max_interp_workers=2,  # 插值并行线程数；推荐1~4，越多速度越快但内存消耗越大
    )
    converter.run()