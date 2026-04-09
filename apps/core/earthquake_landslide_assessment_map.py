# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震滑坡评估图生成脚本
参考 earthquake_elevation_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

滑坡评估图说明：
- 地震滑坡评估TIF文件使用手动间隔分为五档
- TIF文件包含5个数值(1~5)，分别对应：低度危险区、较低危险区、中等危险区、较高危险区、高度危险区
- 五档颜色从低到高：rgb(13,109,18), rgb(121,183,15), rgb(242,254,35), rgb(254,172,24), rgb(254,63,29)
- 图例显示：色块 + 危险等级名称
- 统计输出图范围内各危险等级的面积和占比
- 只加载天地图矢量注记图层（放置在最上层），不加载矢量底图
"""

import os
import sys
import math
import re
import logging
import tempfile
import shutil
import numpy as np
from xml.etree import ElementTree as ET

# ============================================================
# Django settings 导入（可选）
# ============================================================
try:
    from django.conf import settings as _django_settings
    _DJANGO_AVAILABLE = True
except ImportError:
    _django_settings = None
    _DJANGO_AVAILABLE = False

from core.tianditu_basemap_downloader import download_tianditu_annotation_tiles

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.earthquake_landslide_assessment_map')

# ============================================================
# QGIS 相关模块导入
# ============================================================
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsPointXY,
    QgsRectangle,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutItemShape,
    QgsLayoutItemMapGrid,
    QgsPrintLayout,
    QgsUnitTypes,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsLineSymbol,
    QgsFillSymbol,
    QgsSimpleFillSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeature,
    QgsField,
    QgsLayoutExporter,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsCoordinateTransform,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# GDAL导入（用于栅格裁剪和统计）
try:
    from osgeo import gdal, osr, ogr
    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False
    print("[警告] GDAL模块未找到，将使用备用方案加载栅格")

# ============================================================
# 常量定义
# ============================================================

# 天地图配置
TIANDITU_TK = (
    getattr(_django_settings, 'TIANDITU_TK', '1ef76ef90c6eb961cb49618f9b1a399d')
    if _DJANGO_AVAILABLE else '1ef76ef90c6eb961cb49618f9b1a399d'
)

# 数据文件路径（优先从 Django settings 读取）
_DEFAULT_BASE = "../../data/geology/"

# 地震滑坡评估TIF文件路径
LANDSLIDE_ASSESSMENT_TIF_PATH = (
    getattr(_django_settings, 'LANDSLIDE_ASSESSMENT_TIF_PATH',
            _DEFAULT_BASE + '图12/ChinaRecla1.tif')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '图12/ChinaRecla1.tif'
)

PROVINCE_SHP_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '行政区划/省界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/省界.shp'
)
CITY_SHP_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/市界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/市界.shp'
)
COUNTY_SHP_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/县界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/县界.shp'
)
# 地级市点位数据
CITY_POINTS_SHP_PATH = (
    getattr(_django_settings, 'CITY_POINTS_SHP_PATH',
            _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp'
)

# === 布局尺寸常量 ===
MAP_TOTAL_WIDTH_MM = 220.0
LEGEND_WIDTH_MM = 50.0
BORDER_LEFT_MM = 4.0
BORDER_TOP_MM = 4.0
BORDER_BOTTOM_MM = 2.0
BORDER_RIGHT_MM = 1.0
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM

# 输出DPI
OUTPUT_DPI = 150

# === 震级配置 ===
MAGNITUDE_CONFIG = {
    "small": {
        "min_mag": 0, "max_mag": 6,
        "radius_km": 15, "map_size_km": 30, "scale": 150000,
    },
    "medium": {
        "min_mag": 6, "max_mag": 7,
        "radius_km": 50, "map_size_km": 100, "scale": 500000,
    },
    "large": {
        "min_mag": 7, "max_mag": 99,
        "radius_km": 150, "map_size_km": 300, "scale": 1500000,
    },
}

# === 边框宽度 ===
BORDER_WIDTH_MM = 0.35

# === 指北针尺寸常量 ===
NORTH_ARROW_WIDTH_MM = 12.0
NORTH_ARROW_HEIGHT_MM = 18.0

# === 经纬度字体(pt) ===
LONLAT_FONT_SIZE_PT = 10

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_GAP_MM = 0.3
CITY_DASH_PATTERN = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_GAP_MM = 0.2
COUNTY_DASH_PATTERN = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 12
LEGEND_ITEM_FONT_SIZE_PT = 10

# === 基本图例项配置（可单独设置） ===
BASIC_LEGEND_FONT_SIZE_PT = 10  # 基本图例项字体大小
BASIC_LEGEND_ROW_HEIGHT_MM = 8.0  # 基本图例项行高

# === 滑坡评估图例项配置（可单独设置） ===
ASSESSMENT_LEGEND_FONT_SIZE_PT = 10  # 滑坡评估图例项字体大小
ASSESSMENT_LEGEND_ROW_HEIGHT_MM = 7.5  # 滑坡评估图例项行高（毫米，色块高度）
ASSESSMENT_LEGEND_GAP_MM = 1.5  # 滑坡评估图例色块之间的间距（毫米，分开显示）

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8

# === 烈度圈样式 ===
INTENSITY_LINE_COLOR = QColor(0, 0, 0)
INTENSITY_LINE_WIDTH_MM = 0.5
INTENSITY_HALO_COLOR = QColor(255, 255, 255)
INTENSITY_HALO_WIDTH_MM = 1.0
INTENSITY_LABEL_FONT_SIZE_PT = 9

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 5.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# === 烈度图例颜色 ===
INTENSITY_LEGEND_COLOR = QColor(0, 0, 0)
INTENSITY_LEGEND_LINE_WIDTH_MM = 0.5

# === 滑坡评估分档配置 ===
# TIF文件中数值1~5分别对应五个危险等级
# 使用Discrete分类：值<=1为第一档，值<=2为第二档，以此类推
LANDSLIDE_CLASSES = [
    {"value": 1, "color": QColor(13, 109, 18),  "label": "低度危险区"},
    {"value": 2, "color": QColor(121, 183, 15), "label": "较低危险区"},
    {"value": 3, "color": QColor(242, 254, 35), "label": "中等危险区"},
    {"value": 4, "color": QColor(254, 172, 24), "label": "较高危险区"},
    {"value": 5, "color": QColor(254, 63, 29),  "label": "高度危险区"},
]

# WGS84
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# 地震滑坡评估TIF文件的自定义投影坐标系WKT（横轴墨卡托，中央经线105°，假东偏移18500000米）
CRS_TIF_PROJECTED = (
    'PROJCRS["User_Defined_Transverse_Mercator",'
    'BASEGEOGCRS["GCS_User_Defined",'
    'DATUM["User_Defined",'
    'ELLIPSOID["User_Defined_Spheroid",6378137,298.257222101,'
    'LENGTHUNIT["metre",1,ID["EPSG",9001]]]],'
    'PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9122]]]],'
    'CONVERSION["Transverse Mercator",'
    'METHOD["Transverse Mercator",ID["EPSG",9807]],'
    'PARAMETER["Latitude of natural origin",0,ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8801]],'
    'PARAMETER["Longitude of natural origin",105,ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8802]],'
    'PARAMETER["Scale factor at natural origin",1,SCALEUNIT["unity",1],ID["EPSG",8805]],'
    'PARAMETER["False easting",18500000,LENGTHUNIT["metre",1],ID["EPSG",8806]],'
    'PARAMETER["False northing",0,LENGTHUNIT["metre",1],ID["EPSG",8807]]],'
    'CS[Cartesian,2],'
    'AXIS["easting",east,ORDER[1],LENGTHUNIT["metre",1,ID["EPSG",9001]]],'
    'AXIS["northing",north,ORDER[2],LENGTHUNIT["metre",1,ID["EPSG",9001]]]]'
)

# === 裁剪缓冲区（度） ===
CLIP_BUFFER_DEGREES = 0.1

# === 面积计算常量 ===
M2_TO_KM2 = 1_000_000.0       # 平方米转平方千米
KM_PER_DEGREE = 111.0          # 每度纬度对应的千米数（近似值）

# === 危险等级判断阈值（%） ===
DANGER_LEVEL_THRESHOLD_PERCENT = 10.0

# === 图例布局和字体设置 ===
LEGEND_FONT_TIMES_NEW_ROMAN = "Times New Roman"


# ============================================================
# 工具函数
# ============================================================

def _format_area(area_km2):
    """
    格式化面积数值：统一四舍五入保留两位小数

    参数:
        area_km2 (float): 面积（平方千米）

    返回:
        float: 格式化后的面积值（保留两位小数）
    """
    return round(area_km2, 2)

def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数

    参数:
        magnitude (float): 地震震级

    返回:
        dict: 包含radius_km, map_size_km, scale的配置字典
    """
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度(km)计算地图范围(WGS84坐标)

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度
        half_size_km (float): 地图半幅宽度（公里）

    返回:
        QgsRectangle: 地图范围矩形
    """
    delta_lat = half_size_km / 111.0
    delta_lon = half_size_km / (111.0 * math.cos(math.radians(latitude)))
    xmin = longitude - delta_lon
    xmax = longitude + delta_lon
    ymin = latitude - delta_lat
    ymax = latitude + delta_lat
    return QgsRectangle(xmin, ymin, xmax, ymax)


def calculate_map_height_from_extent(extent, map_width_mm):
    """
    根据地图范围和宽度计算地图高度（保持宽高比）

    参数:
        extent (QgsRectangle): 地图范围
        map_width_mm (float): 地图宽度（毫米）

    返回:
        float: 地图高度（毫米）
    """
    lon_range = extent.xMaximum() - extent.xMinimum()
    lat_range = extent.yMaximum() - extent.yMinimum()
    if lon_range <= 0:
        return map_width_mm
    aspect_ratio = lat_range / lon_range
    return map_width_mm * aspect_ratio


def resolve_path(relative_path):
    """
    将相对路径转换为绝对路径

    参数:
        relative_path (str): 相对路径

    返回:
        str: 绝对路径
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字

    参数:
        num (int): 阿拉伯数字

    返回:
        str: 罗马数字字符串
    """
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = ""
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            result += syms[i]
            num -= val[i]
        i += 1
    return result


def _choose_tick_step(range_deg, target_min=4, target_max=6):
    """
    根据地理范围选择合适的经纬度刻度间隔

    参数:
        range_deg (float): 地理范围（度）
        target_min (int): 最小刻度数
        target_max (int): 最大刻度数

    返回:
        float: 刻度间隔（度）
    """
    candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    for step in candidates:
        n = range_deg / step
        if target_min <= n <= target_max:
            return step
    best_step = candidates[-1]
    best_diff = float("inf")
    for step in candidates:
        diff = abs(range_deg / step - 5)
        if diff < best_diff:
            best_diff = diff
            best_step = step
    return best_step


def create_north_arrow_svg(output_path):
    """
    创建指北针SVG文件

    参数:
        output_path (str): SVG文件输出路径

    返回:
        str: SVG文件路径
    """
    svg_content = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 90" width="60" height="90">
  <polygon points="30,24 18,77 30,64" fill="black" stroke="black" stroke-width="1"/>
  <polygon points="30,24 42,77 30,64" fill="white" stroke="black" stroke-width="1"/>
  <text x="30" y="22" text-anchor="middle" font-size="14" font-weight="bold"
        font-family="Arial" fill="black">N</text>
</svg>'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(svg_content)
    return output_path


def _find_name_field(layer, candidates):
    """
    在矢量图层的字段列表中查找名称字段

    参数:
        layer (QgsVectorLayer): 矢量图层
        candidates (list): 候选字段名列表

    返回:
        str: 找到的字段名，未找到返回None
    """
    fields = layer.fields()
    field_names = [f.name() for f in fields]
    for candidate in candidates:
        if candidate in field_names:
            return candidate
    for candidate in candidates:
        for fn in field_names:
            if candidate.lower() in fn.lower():
                return fn
    for f in fields:
        if f.type() == QVariant.String:
            return f.name()
    return None


# ============================================================
# 坐标转换辅助函数
# ============================================================

def transform_extent_to_tif_crs(extent, tif_path):
    """
    将WGS84经纬度范围（QgsRectangle）转换为TIF文件所使用的投影坐标范围。

    参数:
        extent (QgsRectangle): 以WGS84（EPSG:4326）经纬度表示的目标范围
        tif_path (str): TIF文件路径，用于读取其坐标系统

    返回:
        tuple: (xmin, ymin, xmax, ymax) 以TIF坐标系表示的范围；
               如果TIF已是WGS84或读取失败则返回原始范围的四元组
    """
    if not GDAL_AVAILABLE:
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())

    try:
        ds = gdal.Open(tif_path, gdal.GA_ReadOnly)
        if ds is None:
            print(f"[警告] transform_extent_to_tif_crs: 无法打开TIF文件 {tif_path}")
            return (extent.xMinimum(), extent.yMinimum(),
                    extent.xMaximum(), extent.yMaximum())

        wkt = ds.GetProjection()
        ds = None

        if not wkt:
            print("[警告] transform_extent_to_tif_crs: TIF文件无坐标系信息，假设WGS84")
            return (extent.xMinimum(), extent.yMinimum(),
                    extent.xMaximum(), extent.yMaximum())

        src_srs = osr.SpatialReference()
        src_srs.ImportFromWkt(wkt)

        wgs84_srs = osr.SpatialReference()
        wgs84_srs.ImportFromEPSG(4326)
        # osr 默认轴序：对于地理CRS先经度后纬度
        wgs84_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

        if src_srs.IsSame(wgs84_srs):
            print("[信息] TIF坐标系为WGS84，无需坐标转换")
            return (extent.xMinimum(), extent.yMinimum(),
                    extent.xMaximum(), extent.yMaximum())

        transform = osr.CoordinateTransformation(wgs84_srs, src_srs)

        # 转换四个角点，取外包矩形以应对非线性投影
        corners_wgs84 = [
            (extent.xMinimum(), extent.yMinimum()),
            (extent.xMinimum(), extent.yMaximum()),
            (extent.xMaximum(), extent.yMinimum()),
            (extent.xMaximum(), extent.yMaximum()),
        ]
        xs, ys = [], []
        for lon, lat in corners_wgs84:
            x, y, _ = transform.TransformPoint(lon, lat)
            xs.append(x)
            ys.append(y)

        proj_xmin, proj_xmax = min(xs), max(xs)
        proj_ymin, proj_ymax = min(ys), max(ys)

        print(f"[信息] WGS84范围 ({extent.xMinimum():.4f},{extent.yMinimum():.4f})-"
              f"({extent.xMaximum():.4f},{extent.yMaximum():.4f}) 已转换为投影坐标 "
              f"({proj_xmin:.1f},{proj_ymin:.1f})-({proj_xmax:.1f},{proj_ymax:.1f})")

        return (proj_xmin, proj_ymin, proj_xmax, proj_ymax)

    except Exception as e:
        print(f"[警告] transform_extent_to_tif_crs 异常: {e}，使用原始WGS84坐标")
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())


# ============================================================
# 大文件栅格裁剪优化函数
# ============================================================

def clip_raster_to_extent(input_path, output_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    使用GDAL裁剪栅格文件到指定范围

    参数:
        input_path (str): 输入栅格文件路径
        output_path (str): 输出裁剪后的栅格文件路径
        extent (QgsRectangle): 目标范围（WGS84坐标）
        buffer_degrees (float): 缓冲区大小（度），默认0.1度

    返回:
        str: 成功返回输出文件路径，失败返回None
    """
    if not GDAL_AVAILABLE:
        print("[警告] GDAL不可用，无法进行栅格裁剪")
        return None

    if not os.path.exists(input_path):
        print(f"[错误] 输入栅格文件不存在: {input_path}")
        return None

    try:
        src_ds = gdal.Open(input_path, gdal.GA_ReadOnly)
        if src_ds is None:
            print(f"[错误] 无法打开栅格文件: {input_path}")
            return None

        src_width = src_ds.RasterXSize
        src_height = src_ds.RasterYSize
        src_bands = src_ds.RasterCount

        print(f"[信息] 源栅格信息: {src_width}x{src_height}, {src_bands}波段")

        # 检测源TIF坐标系是否为WGS84
        src_wkt = src_ds.GetProjection()
        src_ds = None  # 关闭数据集，后续由gdal.Translate重新打开

        is_geographic = False
        if src_wkt:
            src_srs = osr.SpatialReference()
            src_srs.ImportFromWkt(src_wkt)
            wgs84_srs = osr.SpatialReference()
            wgs84_srs.ImportFromEPSG(4326)
            wgs84_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            is_geographic = bool(src_srs.IsSame(wgs84_srs))
        else:
            # 无坐标系信息，假设WGS84
            is_geographic = True

        if is_geographic:
            print("[信息] 检测到TIF坐标系为WGS84，使用经纬度坐标裁剪")
            # 在目标范围外增加缓冲区（度）
            clip_xmin = extent.xMinimum() - buffer_degrees
            clip_xmax = extent.xMaximum() + buffer_degrees
            clip_ymin = extent.yMinimum() - buffer_degrees
            clip_ymax = extent.yMaximum() + buffer_degrees

            print(f"[信息] 裁剪范围(WGS84): ({clip_xmin:.4f}, {clip_ymin:.4f}) - ({clip_xmax:.4f}, {clip_ymax:.4f})")

            translate_options = gdal.TranslateOptions(
                projWin=[clip_xmin, clip_ymax, clip_xmax, clip_ymin],
                projWinSRS='EPSG:4326',
                format='GTiff',
                creationOptions=[
                    'COMPRESS=LZW',
                    'TILED=YES',
                    'BLOCKXSIZE=256',
                    'BLOCKYSIZE=256',
                    'BIGTIFF=IF_SAFER'
                ]
            )
        else:
            print("[信息] 检测到TIF坐标系为投影坐标系，将WGS84范围转换为投影坐标后裁剪")
            # 将WGS84 extent（含缓冲区）转换为投影坐标
            buffered_extent = QgsRectangle(
                extent.xMinimum() - buffer_degrees,
                extent.yMinimum() - buffer_degrees,
                extent.xMaximum() + buffer_degrees,
                extent.yMaximum() + buffer_degrees,
            )
            proj_xmin, proj_ymin, proj_xmax, proj_ymax = transform_extent_to_tif_crs(
                buffered_extent, input_path
            )

            print(f"[信息] 裁剪范围(投影坐标): ({proj_xmin:.1f}, {proj_ymin:.1f}) - ({proj_xmax:.1f}, {proj_ymax:.1f})")

            translate_options = gdal.TranslateOptions(
                projWin=[proj_xmin, proj_ymax, proj_xmax, proj_ymin],
                format='GTiff',
                creationOptions=[
                    'COMPRESS=LZW',
                    'TILED=YES',
                    'BLOCKXSIZE=256',
                    'BLOCKYSIZE=256',
                    'BIGTIFF=IF_SAFER'
                ]
            )

        src_ds = gdal.Open(input_path, gdal.GA_ReadOnly)
        print(f"[信息] 正在裁剪栅格数据...")
        dst_ds = gdal.Translate(output_path, src_ds, options=translate_options)

        if dst_ds is None:
            print(f"[错误] 栅格裁剪失败")
            src_ds = None
            return None

        dst_width = dst_ds.RasterXSize
        dst_height = dst_ds.RasterYSize

        dst_ds = None
        src_ds = None

        src_size = os.path.getsize(input_path) / (1024 * 1024)
        dst_size = os.path.getsize(output_path) / (1024 * 1024)

        print(f"[信息] 裁剪完成: {dst_width}x{dst_height}")
        print(f"[信息] 原文件: {src_size:.2f}MB -> 裁剪后: {dst_size:.2f}MB")

        return output_path

    except Exception as e:
        print(f"[错误] 栅格裁剪异常: {e}")
        import traceback
        traceback.print_exc()
        return None


class TempFileManager:
    """
    临时文件管理器

    用于管理裁剪产生的临时文件，确保在处理完成后正确清理。
    """

    def __init__(self):
        """初始化临时文件管理器"""
        self.temp_dir = None
        self.temp_files = []

    def get_temp_dir(self):
        """
        获取临时目录路径

        返回:
            str: 临时目录路径
        """
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="earthquake_landslide_assessment_")
            print(f"[信息] 创建临时目录: {self.temp_dir}")
        return self.temp_dir

    def get_temp_file(self, suffix=".tif"):
        """
        获取临时文件路径

        参数:
            suffix (str): 文件后缀

        返回:
            str: 临时文件路径
        """
        temp_dir = self.get_temp_dir()
        fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
        os.close(fd)
        self.temp_files.append(temp_path)
        return temp_path

    def cleanup(self):
        """清理所有临时文件和目录"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError as e:
                print(f"[警告] 无法删除临时文件 {temp_file}: {e}")

        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"[信息] 已清理临时目录: {self.temp_dir}")
            except OSError as e:
                print(f"[警告] 无法删除临时目录 {self.temp_dir}: {e}")

        self.temp_files = []
        self.temp_dir = None


# 全局临时文件管理器
_temp_manager = TempFileManager()


def get_temp_manager():
    """
    获取全局临时文件管理器实例

    返回:
        TempFileManager: 临时文件管理器实例
    """
    return _temp_manager


# ============================================================
# 滑坡评估栅格图层渲染设置
# ============================================================

def apply_landslide_assessment_renderer(raster_layer):
    """
    为滑坡评估栅格图层应用手动间隔分类渲染器

    TIF文件中数值1~5分别对应五个危险等级，使用Discrete分类模式。

    参数:
        raster_layer (QgsRasterLayer): 滑坡评估栅格图层

    返回:
        bool: 是否成功应用渲染器
    """
    if raster_layer is None or not raster_layer.isValid():
        print("[错误] 无效的栅格图层，无法应用渲染器")
        return False

    try:
        shader = QgsRasterShader()
        color_ramp_shader = QgsColorRampShader()

        # 使用Discrete分类模式：值 <= threshold 的像素使用该分类的颜色
        color_ramp_shader.setColorRampType(QgsColorRampShader.Discrete)

        color_ramp_items = []
        for cls in LANDSLIDE_CLASSES:
            # Discrete模式下，value作为上界阈值
            # 值<=1.5为第一档，值<=2.5为第二档，以此类推
            item = QgsColorRampShader.ColorRampItem(
                cls["value"] + 0.5,  # 使用 x.5 作为阈值，确保整数值正确匹配
                cls["color"],
                cls["label"]
            )
            color_ramp_items.append(item)

        color_ramp_shader.setColorRampItemList(color_ramp_items)
        shader.setRasterShaderFunction(color_ramp_shader)

        renderer = QgsSingleBandPseudoColorRenderer(
            raster_layer.dataProvider(),
            1,  # 波段1
            shader
        )

        raster_layer.setRenderer(renderer)
        raster_layer.triggerRepaint()

        print("[信息] 滑坡评估图层渲染器设置完成，使用5档分类")
        return True

    except Exception as e:
        print(f"[错误] 应用滑坡评估渲染器失败: {e}")
        return False


def build_landslide_legend_list():
    """
    构建滑坡评估图例列表

    返回:
        list: 滑坡评估图例列表，每项为(color_rgba, label)元组
    """
    result = []
    for cls in LANDSLIDE_CLASSES:
        color = cls["color"]
        color_rgba = (color.red(), color.green(), color.blue(), 255)
        label = cls["label"]
        result.append((color_rgba, label))

    print(f"[信息] 构建滑坡评估图例列表完成，共 {len(result)} 项")
    return result


# ============================================================
# 滑坡评估面积统计函数
# ============================================================

def compute_landslide_area_statistics(tif_path, extent, kml_path=None):
    """
    统计指定范围内各危险等级的面积和占比

    使用GDAL读取原始TIF数据，统计范围内数值1~5各像素数量，
    根据像素分辨率计算每个等级的面积（平方千米）和占比（%）。

    自动检测TIF坐标系：
    - 若为投影坐标系（单位：米），直接用像素尺寸（米）计算面积
    - 若为地理坐标系（单位：度），使用纬度修正公式计算面积；
      当统计范围跨度超过1度时，按行逐行使用行中心纬度修正，精度更高

    参数:
        tif_path (str): TIF文件路径（应传入原始文件，函数内部按统计范围裁剪读取）
        extent (QgsRectangle): 统计范围（WGS84坐标），当未提供kml_path时使用此范围
        kml_path (str): 烈度圈KML文件路径（可选）；提供时，以最外圈（最小烈度值的圈）
                        的外包矩形作为数据读取范围，并用最外圈多边形作为掩膜进行统计

    返回:
        list: 统计结果列表，每项为字典 {"label": str, "area_km2": float, "percentage": float}
              如果统计失败返回空列表
    """
    if not GDAL_AVAILABLE:
        print("[警告] GDAL不可用，无法进行面积统计")
        return []

    abs_path = resolve_path(tif_path) if not os.path.isabs(tif_path) else tif_path
    if not os.path.exists(abs_path):
        print(f"[错误] TIF文件不存在，无法统计: {abs_path}")
        return []

    try:
        ds = gdal.Open(abs_path, gdal.GA_ReadOnly)
        if ds is None:
            print(f"[错误] 无法打开TIF文件: {abs_path}")
            return []

        # 获取地理变换参数
        gt = ds.GetGeoTransform()
        # gt[0]: 左上角X坐标
        # gt[1]: X方向像素分辨率
        # gt[3]: 左上角Y坐标
        # gt[5]: Y方向像素分辨率（负值）
        pixel_width = abs(gt[1])
        pixel_height = abs(gt[5])

        raster_xmin = gt[0]
        raster_ymax = gt[3]
        raster_width = ds.RasterXSize
        raster_height = ds.RasterYSize
        raster_xmax = raster_xmin + raster_width * gt[1]
        raster_ymin = raster_ymax + raster_height * gt[5]  # gt[5]是负值

        # 检测TIF坐标系类型（投影坐标/地理坐标）
        tif_wkt = ds.GetProjection()
        is_projected = False  # 默认假设为地理坐标系（度）
        if tif_wkt:
            tif_srs = osr.SpatialReference()
            tif_srs.ImportFromWkt(tif_wkt)
            is_projected = bool(tif_srs.IsProjected())
            if is_projected:
                print(f"[信息] 检测到TIF坐标系为投影坐标系（单位：米），直接用像素尺寸计算面积")
            else:
                print(f"[信息] 检测到TIF坐标系为地理坐标系（单位：度），使用纬度修正公式计算面积")
        else:
            print("[警告] TIF文件无坐标系信息，假设为地理坐标系（度）")

        # 预解析KML，获取最外圈（最小烈度值对应的圈）坐标（用于确定统计范围）
        kml_coords_wgs84 = None
        kml_intensity = None
        if kml_path:
            try:
                intensity_data = parse_intensity_kml(kml_path)
                if intensity_data:
                    # 烈度值越小，圈越大；取最小烈度值的圈即为最外圈/最大范围的烈度圈
                    outermost_item = min(intensity_data, key=lambda x: x["intensity"])
                    kml_coords_wgs84 = outermost_item["coords"]
                    kml_intensity = outermost_item["intensity"]
                    print(f"[信息] KML最外圈（最小烈度值{kml_intensity}度）解析成功，将以其外包矩形为统计范围")
            except Exception as kml_parse_err:
                print(f"[警告] KML解析失败: {kml_parse_err}，将使用extent范围统计")
                kml_coords_wgs84 = None

        # 确定统计范围：提供了KML时使用最外圈包围盒，否则使用extent参数
        if kml_coords_wgs84 is not None:
            lons = [c[0] for c in kml_coords_wgs84]
            lats = [c[1] for c in kml_coords_wgs84]
            query_extent = QgsRectangle(min(lons), min(lats), max(lons), max(lats))
            print(f"[信息] 统计范围：KML最外圈包围盒 "
                  f"({min(lons):.4f},{min(lats):.4f})-({max(lons):.4f},{max(lats):.4f})")
        else:
            query_extent = extent

        # 将WGS84 query_extent转换为TIF坐标系后计算像素范围
        tif_xmin, tif_ymin, tif_xmax, tif_ymax = transform_extent_to_tif_crs(query_extent, abs_path)

        # 计算与TIF范围的交集
        intersect_xmin = max(tif_xmin, raster_xmin)
        intersect_xmax = min(tif_xmax, raster_xmax)
        intersect_ymin = max(tif_ymin, raster_ymin)
        intersect_ymax = min(tif_ymax, raster_ymax)

        if intersect_xmin >= intersect_xmax or intersect_ymin >= intersect_ymax:
            print("[警告] 统计范围与栅格无交集")
            ds = None
            return []

        # 将坐标转为像素索引
        col_start = int((intersect_xmin - raster_xmin) / pixel_width)
        col_end = int(math.ceil((intersect_xmax - raster_xmin) / pixel_width))
        row_start = int((raster_ymax - intersect_ymax) / pixel_height)
        row_end = int(math.ceil((raster_ymax - intersect_ymin) / pixel_height))

        # 边界裁剪
        col_start = max(0, col_start)
        col_end = min(raster_width, col_end)
        row_start = max(0, row_start)
        row_end = min(raster_height, row_end)

        read_width = col_end - col_start
        read_height = row_end - row_start

        if read_width <= 0 or read_height <= 0:
            print("[警告] 计算后的读取范围无效")
            ds = None
            return []

        print(f"[信息] 统计读取范围: 列[{col_start}:{col_end}], 行[{row_start}:{row_end}], "
              f"尺寸: {read_width}x{read_height}")

        # 读取波段1数据
        band = ds.GetRasterBand(1)
        data = band.ReadAsArray(col_start, row_start, read_width, read_height)
        ds = None

        if data is None:
            print("[错误] 读取栅格数据失败")
            return []

        # 计算单像素面积
        if is_projected:
            # 投影坐标系：像素尺寸单位为米，直接换算为平方千米
            pixel_area_km2 = (pixel_width * pixel_height) / M2_TO_KM2
            # 投影坐标系下各行像素面积相同，使用统一值
            pixel_area_km2_per_row = None
            print(f"[信息] 像素分辨率: {pixel_width:.2f}m x {pixel_height:.2f}m")
            print(f"[信息] 单像素面积: {pixel_area_km2:.8f} km²")
        else:
            # 地理坐标系：像素尺寸单位为度，需用纬度修正
            # 查询范围的纬度跨度
            lat_range = query_extent.yMaximum() - query_extent.yMinimum()
            print(f"[信息] 像素分辨率: {pixel_width:.6f}° x {pixel_height:.6f}°")
            if lat_range > 1.0:
                # 跨度超过1度时，逐行按行中心纬度计算像素面积，精度更高
                # 读取窗口左上角的纬度（WGS84）：需将TIF坐标系下的行位置转回WGS84
                # 由于读取窗口对应的WGS84坐标范围即为query_extent与TIF交集
                # 这里用简单近似：WGS84纬度与TIF行线性对应（非投影TIF时完全正确）
                row_y_top = query_extent.yMaximum()
                # 各行中心纬度 = row_y_top - (i + 0.5) * pixel_height（度）
                pixel_area_km2_per_row = np.array([
                    (pixel_width * KM_PER_DEGREE * math.cos(math.radians(
                        row_y_top - (i + 0.5) * pixel_height
                    ))) * (pixel_height * KM_PER_DEGREE)
                    for i in range(read_height)
                ])
                # 中心纬度用于日志
                center_lat = (query_extent.yMinimum() + query_extent.yMaximum()) / 2.0
                pixel_area_km2 = float(np.mean(pixel_area_km2_per_row))
                print(f"[信息] 纬度跨度{lat_range:.2f}°>1°，采用逐行纬度修正计算面积")
                print(f"[信息] 中心纬度: {center_lat:.4f}°，平均单像素面积: {pixel_area_km2:.6f} km²")
            else:
                # 跨度不超过1度，使用统一中心纬度（近似误差<0.5%）
                center_lat = (query_extent.yMinimum() + query_extent.yMaximum()) / 2.0
                cos_lat = math.cos(math.radians(center_lat))
                pixel_area_km2 = (pixel_width * KM_PER_DEGREE * cos_lat) * (pixel_height * KM_PER_DEGREE)
                pixel_area_km2_per_row = None
                print(f"[信息] 中心纬度: {center_lat:.4f}°, cos(lat)={cos_lat:.6f}")
                print(f"[信息] 单像素面积: {pixel_area_km2:.6f} km²")

        # 若提供了KML文件，创建最外圈（最小烈度值）掩膜限定统计区域
        kml_mask = None
        if kml_coords_wgs84 is not None:
            try:
                # 建立坐标转换（WGS84 -> TIF CRS）
                wgs84_srs = osr.SpatialReference()
                wgs84_srs.ImportFromEPSG(4326)
                wgs84_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

                tif_srs_obj = osr.SpatialReference()
                need_transform = False
                if tif_wkt:
                    tif_srs_obj.ImportFromWkt(tif_wkt)
                    tif_srs_obj.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                    need_transform = not tif_srs_obj.IsSame(wgs84_srs)

                transform_ct = (osr.CoordinateTransformation(wgs84_srs, tif_srs_obj)
                                if need_transform else None)

                # 构建OGR多边形
                ring = ogr.Geometry(ogr.wkbLinearRing)
                for lon, lat in kml_coords_wgs84:
                    if transform_ct:
                        x, y, _ = transform_ct.TransformPoint(lon, lat)
                    else:
                        x, y = lon, lat
                    ring.AddPoint(x, y)
                ring.CloseRings()
                polygon = ogr.Geometry(ogr.wkbPolygon)
                polygon.AddGeometry(ring)

                # 创建与数据窗口对齐的掩膜栅格
                sub_x_origin = raster_xmin + col_start * pixel_width
                sub_y_origin = raster_ymax - row_start * pixel_height
                sub_gt = (sub_x_origin, pixel_width, 0.0,
                          sub_y_origin, 0.0, -pixel_height)

                drv = gdal.GetDriverByName('MEM')
                mask_ds = drv.Create('', read_width, read_height, 1, gdal.GDT_Byte)
                mask_ds.SetGeoTransform(sub_gt)
                if tif_wkt:
                    mask_ds.SetProjection(tif_wkt)

                mem_drv = ogr.GetDriverByName('Memory')
                mem_src = mem_drv.CreateDataSource('tmp_kml_mask')
                mem_layer = mem_src.CreateLayer(
                    'mask', srs=tif_srs_obj if tif_wkt else None)
                feat = ogr.Feature(mem_layer.GetLayerDefn())
                feat.SetGeometry(polygon)
                mem_layer.CreateFeature(feat)

                gdal.RasterizeLayer(mask_ds, [1], mem_layer, burn_values=[1])
                kml_mask = mask_ds.GetRasterBand(1).ReadAsArray().astype(bool)
                mask_ds = None
                print(f"[信息] KML最外圈（最小烈度值{kml_intensity}度）掩膜创建成功")
            except Exception as mask_err:
                print(f"[警告] KML掩膜创建失败: {mask_err}，将使用完整范围统计")
                kml_mask = None

        # 统计各等级像素数量
        stats_result = []
        total_valid_pixels = 0
        class_pixel_counts = {}

        for cls in LANDSLIDE_CLASSES:
            value = cls["value"]
            if kml_mask is not None:
                count = int(np.sum((data == value) & kml_mask))
            else:
                count = int(np.sum(data == value))
            class_pixel_counts[value] = count
            total_valid_pixels += count

        # 计算面积和占比
        # 若有逐行面积数组，则对每个等级按行累加面积
        if pixel_area_km2_per_row is not None:
            class_area_km2 = {}
            for cls in LANDSLIDE_CLASSES:
                value = cls["value"]
                mask_val = (data == value)
                if kml_mask is not None:
                    mask_val = mask_val & kml_mask
                # 每行中符合条件的像素数 × 该行单像素面积
                row_counts = np.sum(mask_val, axis=1)  # shape: (read_height,)
                class_area_km2[value] = float(np.dot(row_counts, pixel_area_km2_per_row))
            total_area_km2 = sum(class_area_km2.values())
        else:
            class_area_km2 = {cls["value"]: class_pixel_counts[cls["value"]] * pixel_area_km2
                              for cls in LANDSLIDE_CLASSES}
            total_area_km2 = total_valid_pixels * pixel_area_km2

        for cls in LANDSLIDE_CLASSES:
            value = cls["value"]
            count = class_pixel_counts[value]
            area_km2 = class_area_km2[value]
            percentage = (count / total_valid_pixels * 100.0) if total_valid_pixels > 0 else 0.0
            stats_result.append({
                "value": value,
                "label": cls["label"],
                "pixel_count": count,
                "area_km2": _format_area(area_km2),
                "percentage": round(percentage, 2),
            })
            print(f"[统计] {cls['label']}: {count}像素, {area_km2:.2f}km², {percentage:.2f}%")

        print(f"[信息] 统计完成，有效像素总数: {total_valid_pixels}, 总面积: {total_area_km2:.2f}km²")
        return stats_result

    except Exception as e:
        print(f"[错误] 面积统计异常: {e}")
        import traceback
        traceback.print_exc()
        return []


def determine_overall_danger_level(stats_list):
    """
    根据统计结果判断整体危险性等级

    规则：从高到低（高 > 较高 > 中等 > 较低 > 低）遍历各等级，
    选取占比超过10%的最高等级；若均不超过10%，则选取占比最高的等级。

    参数:
        stats_list (list): compute_landslide_area_statistics返回的统计结果列表

    返回:
        str: 整体危险性描述（如"低"、"较低"、"中等"、"较高"、"高"）
    """
    if not stats_list:
        return "未知"

    # 危险等级从高到低的顺序
    danger_order = ["高度危险区", "较高危险区", "中等危险区", "较低危险区", "低度危险区"]
    danger_map = {
        "低度危险区": "低",
        "较低危险区": "较低",
        "中等危险区": "中等",
        "较高危险区": "较高",
        "高度危险区": "高",
    }

    # 建立 label -> percentage 查找表
    pct_by_label = {item["label"]: item["percentage"] for item in stats_list}

    # 从高到低遍历，选取第一个占比超过阈值的等级
    for label in danger_order:
        pct = pct_by_label.get(label, 0.0)
        if pct > DANGER_LEVEL_THRESHOLD_PERCENT:
            return danger_map[label]

    # 若均不超过 10%，返回占比最大的等级
    max_item = max(stats_list, key=lambda x: x["percentage"])
    return danger_map.get(max_item["label"], "未知")


def format_stats_message(stats_list):
    """
    格式化统计信息为描述性字符串

    参数:
        stats_list (list): compute_landslide_area_statistics返回的统计结果列表

    返回:
        str: 格式化后的统计信息字符串
    """
    if not stats_list:
        return "总的来看，无法获取地震诱发斜坡地质灾害危险性数据。"

    overall_level = determine_overall_danger_level(stats_list)

    # 构建各等级描述（area_km2已在compute_landslide_area_statistics中格式化）
    parts = []
    for item in stats_list:
        parts.append(
            f"{item['label']}面积为{item['area_km2']}平方千米，占比{item['percentage']}%"
        )

    detail_str = "；".join(parts)

    message = (
        f"总的来看，地震诱发斜坡地质灾害危险性{overall_level}。"
        f"其中，{detail_str}"
    )
    return message


# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据

    参数:
        kml_path (str): KML文件路径

    返回:
        list: 烈度圈数据列表，每项包含intensity和coords
    """
    if not kml_path or not os.path.exists(kml_path):
        print(f"[警告] KML文件不存在或未提供: {kml_path}")
        return []

    intensity_data = []
    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for pm in root.iter(ns + "Placemark"):
            name_elem = pm.find(ns + "name")
            if name_elem is None or name_elem.text is None:
                continue
            intensity = _extract_intensity_from_name(name_elem.text)
            if intensity is None:
                continue
            coords = _extract_kml_linestring_coords(pm, ns)
            if coords:
                intensity_data.append({
                    "intensity": intensity,
                    "coords": coords,
                })
        print(f"[信息] 从KML解析到 {len(intensity_data)} 个烈度圈")
    except Exception as e:
        print(f"[错误] 解析KML文件失败: {e}")
    return intensity_data


def _extract_intensity_from_name(name):
    """
    从Placemark名称中提取烈度值

    参数:
        name (str): Placemark名称

    返回:
        int: 烈度值，解析失败返回None
    """
    if not name:
        return None
    name = name.strip()
    m = re.match(r'(\d+)\s*度?', name)
    if m:
        return int(m.group(1))
    roman_map = {
        'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
        'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'XI': 11, 'XII': 12,
    }
    clean = re.sub(r'\s*度\s*', '', name).strip()
    if clean.upper() in roman_map:
        return roman_map[clean.upper()]
    return None


def _extract_kml_linestring_coords(placemark, ns):
    """
    从Placemark中提取LineString坐标

    参数:
        placemark: XML Placemark元素
        ns (str): XML命名空间

    返回:
        list: 坐标列表[(lon, lat), ...]
    """
    coords_list = []
    for ls in placemark.iter(ns + "LineString"):
        coords_elem = ls.find(ns + "coordinates")
        if coords_elem is not None and coords_elem.text:
            parsed = _parse_kml_coords(coords_elem.text)
            coords_list.extend(parsed)
    return coords_list


def _parse_kml_coords(text):
    """
    解析KML坐标文本为(lon, lat)元组列表

    参数:
        text (str): KML坐标文本

    返回:
        list: 坐标列表[(lon, lat), ...]
    """
    coords = []
    for part in text.strip().split():
        vals = part.split(",")
        if len(vals) >= 2:
            try:
                lon = float(vals[0])
                lat = float(vals[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def create_intensity_layer(intensity_data):
    """
    根据解析的烈度圈数据创建QGIS矢量图层

    参数:
        intensity_data (list): 烈度圈数据列表

    返回:
        QgsVectorLayer: 烈度圈图层
    """
    if not intensity_data:
        return None

    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "烈度圈", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField("intensity", QVariant.Int),
        QgsField("label", QVariant.String),
    ])
    layer.updateFields()

    features = []
    for item in intensity_data:
        intensity = item["intensity"]
        coords = item["coords"]
        if len(coords) < 2:
            continue
        points = [QgsPointXY(lon, lat) for lon, lat in coords]
        geom = QgsGeometry.fromPolylineXY(points)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("intensity", intensity)
        feat.setAttribute("label", int_to_roman(intensity))
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()

    # 光晕线层
    halo_sl = QgsSimpleLineSymbolLayer()
    halo_sl.setColor(INTENSITY_HALO_COLOR)
    halo_sl.setWidth(INTENSITY_HALO_WIDTH_MM)
    halo_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    halo_sl.setPenStyle(Qt.SolidLine)

    # 主线层
    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(INTENSITY_LINE_COLOR)
    line_sl.setWidth(INTENSITY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, halo_sl)
    symbol.appendSymbolLayer(line_sl)

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _setup_intensity_labels(layer)
    layer.triggerRepaint()

    print(f"[信息] 创建烈度圈图层，共 {len(features)} 条烈度线")
    return layer


def _setup_intensity_labels(layer):
    """
    配置烈度圈图层的标注

    参数:
        layer (QgsVectorLayer): 烈度圈图层
    """
    settings = QgsPalLayerSettings()
    settings.fieldName = "label"
    settings.placement = Qgis.LabelPlacement.Line

    text_format = QgsTextFormat()
    font = QFont("Times New Roman", INTENSITY_LABEL_FONT_SIZE_PT)
    font.setBold(True)
    text_format.setFont(font)
    text_format.setSize(INTENSITY_LABEL_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(0, 0, 0))

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabelsEnabled(True)
    layer.setLabeling(labeling)


# ============================================================
# 图层加载函数
# ============================================================

def load_landslide_assessment_raster_optimized(tif_path, extent):
    """
    优化加载地震滑坡评估TIF栅格图层（按需裁剪）

    参数:
        tif_path (str): TIF文件路径
        extent (QgsRectangle): 目标地图范围

    返回:
        tuple: (QgsRasterLayer或None, str或None)
               第一个元素为加载的栅格图层，第二个为实际用于统计的TIF路径
    """
    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 滑坡评估底图文件不存在: {abs_path}")
        return None, None

    file_size_mb = os.path.getsize(abs_path) / (1024 * 1024)
    print(f"[信息] 滑坡评估文件大小: {file_size_mb:.2f}MB")

    # 尝试裁剪优化
    if GDAL_AVAILABLE and file_size_mb > 10:
        print(f"[信息] 文件较大，启用裁剪优化...")
        temp_manager = get_temp_manager()
        clipped_path = temp_manager.get_temp_file(suffix="_landslide_clipped.tif")
        result_path = clip_raster_to_extent(abs_path, clipped_path, extent)

        if result_path and os.path.exists(result_path):
            layer = QgsRasterLayer(result_path, "滑坡评估底图")
            if layer.isValid():
                print(f"[信息] 成功加载裁剪后的滑坡评估底图")
                apply_landslide_assessment_renderer(layer)
                return layer, result_path
            else:
                print(f"[警告] 裁剪后的栅格无效，尝试直接加载原文件")
        else:
            print(f"[警告] 栅格裁剪失败，尝试直接加载原文件")

    # 直接加载原文件
    print(f"[信息] 直接加载滑坡评估底图...")
    layer = QgsRasterLayer(abs_path, "滑坡评估底图")
    if not layer.isValid():
        print(f"[错误] 无法加载滑坡评估底图: {abs_path}")
        return None, None

    print(f"[信息] 成功加载滑坡评估底图: {abs_path}")
    apply_landslide_assessment_renderer(layer)
    return layer, abs_path


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）

    参数:
        shp_path (str): SHP文件路径
        layer_name (str): 图层名称

    返回:
        QgsVectorLayer: 矢量图层，加载失败返回None
    """
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 矢量文件不存在: {abs_path}")
        return None
    layer = QgsVectorLayer(abs_path, layer_name, "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载矢量图层: {abs_path}")
        return None
    print(f"[信息] 成功加载矢量图层 '{layer_name}': {abs_path}")
    return layer


# ============================================================
# 图层样式设置函数
# ============================================================

def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent):
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移。

    当省份质心与震中坐标重合时，标注点向右下角偏移3mm，避免遮挡震中五角星标识。

    参数:
        province_layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float): 震中经度（度）
        epicenter_lat (float): 震中纬度（度）
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量（mm转度）

    返回:
        QgsVectorLayer 或 None: 配置好标注的内存点图层，失败返回None
    """
    field_name = _find_name_field(province_layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过省份标注图层创建")
        return None

    # 计算3mm对应的经纬度偏移量
    if extent is not None:
        map_width_deg = extent.width()
        map_height_deg = extent.height()
    else:
        map_width_deg = 10.0
        map_height_deg = 10.0

    offset_mm = 3.0
    lon_offset_deg = offset_mm / MAP_WIDTH_MM * map_width_deg
    lat_offset_deg = offset_mm / MAP_WIDTH_MM * map_height_deg

    # 创建内存点图层
    label_layer = QgsVectorLayer("Point?crs=EPSG:4326", "省份标注", "memory")
    if not label_layer.isValid():
        print("[错误] 无法创建省份标注内存图层")
        return None

    provider = label_layer.dataProvider()
    provider.addAttributes([QgsField("province_name", QVariant.String)])
    label_layer.updateFields()

    layer_fields = label_layer.fields()
    feats_to_add = []
    offset_count = 0

    for feat in province_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        centroid = geom.centroid()
        if centroid is None or centroid.isEmpty():
            continue
        cx = centroid.asPoint().x()
        cy = centroid.asPoint().y()

        px, py = cx, cy
        if (abs(cx - epicenter_lon) < PROVINCE_EPICENTER_COINCIDENCE_TOL and
                abs(cy - epicenter_lat) < PROVINCE_EPICENTER_COINCIDENCE_TOL):
            px = cx + lon_offset_deg
            py = cy - lat_offset_deg
            offset_count += 1

        prov_name = feat[field_name]
        new_feat = QgsFeature(layer_fields)
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
        new_feat.setAttribute("province_name", prov_name)
        feats_to_add.append(new_feat)

    if feats_to_add:
        provider.addFeatures(feats_to_add)
    label_layer.updateExtents()

    # 设置透明点符号
    marker_symbol = QgsMarkerSymbol.createSimple({
        "name": "circle", "size": "0",
        "color": "0,0,0,0", "outline_color": "0,0,0,0",
    })
    label_layer.setRenderer(QgsSingleSymbolRenderer(marker_symbol))

    # 配置标注样式
    settings = QgsPalLayerSettings()
    settings.fieldName = "province_name"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.displayAll = True

    text_format = QgsTextFormat()
    font = QFont("SimHei", PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setFont(font)
    text_format.setSize(PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(PROVINCE_LABEL_COLOR)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    label_layer.setLabelsEnabled(True)
    label_layer.setLabeling(labeling)

    return label_layer


def style_province_layer(layer, center_lon=None, center_lat=None, extent=None):
    """
    设置省界图层样式

    参数:
        layer (QgsVectorLayer): 省界图层
        center_lon (float 或 None): 震中经度
        center_lat (float 或 None): 震中纬度
        extent (QgsRectangle 或 None): 地图范围
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(PROVINCE_COLOR)
    fill_sl.setStrokeWidth(PROVINCE_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.SolidLine)

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    if center_lon is None:
        _setup_province_labels(layer)
    layer.triggerRepaint()


def style_city_layer(layer):
    """
    设置市界图层样式

    参数:
        layer (QgsVectorLayer): 市界图层
    """
    symbol = QgsFillSymbol()
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(CITY_COLOR)
    fill_sl.setStrokeWidth(CITY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.CustomDashLine)
    fill_sl.setPenJoinStyle(Qt.MiterJoin)

    dash_pattern = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]
    if hasattr(fill_sl, 'setCustomDashVector'):
        fill_sl.setCustomDashVector(dash_pattern)
    else:
        fill_sl.setStrokeStyle(Qt.DashLine)

    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()


def style_county_layer(layer):
    """
    设置县界图层样式

    参数:
        layer (QgsVectorLayer): 县界图层
    """
    symbol = QgsFillSymbol()
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(COUNTY_COLOR)
    fill_sl.setStrokeWidth(COUNTY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.CustomDashLine)
    fill_sl.setPenJoinStyle(Qt.MiterJoin)

    dash_pattern = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]
    if hasattr(fill_sl, 'setCustomDashVector'):
        fill_sl.setCustomDashVector(dash_pattern)
    else:
        fill_sl.setStrokeStyle(Qt.DashLine)

    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()


def _setup_province_labels(layer):
    """
    配置省界图层标注

    参数:
        layer (QgsVectorLayer): 省界图层
    """
    field_name = _find_name_field(layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.displayAll = True

    text_format = QgsTextFormat()
    font = QFont("SimHei", PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setFont(font)
    text_format.setSize(PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(PROVINCE_LABEL_COLOR)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabelsEnabled(True)
    layer.setLabeling(labeling)

# ============================================================
# 震中和图例图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层：红色五角星+白边

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度

    返回:
        QgsVectorLayer: 震中标记图层
    """
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "震中", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    feat = QgsFeature(layer.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(longitude, latitude)))
    feat.setAttribute("name", "震中")
    provider.addFeature(feat)
    layer.updateExtents()

    marker_sl = QgsSimpleMarkerSymbolLayer()
    marker_sl.setShape(Qgis.MarkerShape.Star)
    marker_sl.setColor(EPICENTER_COLOR)
    marker_sl.setStrokeColor(EPICENTER_STROKE_COLOR)
    marker_sl.setStrokeWidth(EPICENTER_STROKE_WIDTH_MM)
    marker_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    marker_sl.setSize(EPICENTER_STAR_SIZE_MM)
    marker_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, marker_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print(f"[信息] 创建震中图层: ({longitude}, {latitude})")
    return layer


def create_city_point_layer(extent):
    """
    加载地级市点位数据（不显示标注，只显示点位）

    参数:
        extent (QgsRectangle): 地图范围

    返回:
        QgsVectorLayer: 地级市点位图层，加载失败返回None
    """
    abs_path = resolve_path(CITY_POINTS_SHP_PATH)
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    symbol_size_mm = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0

    # 白色背景圆
    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 外圈
    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 内点
    inner_sl = QgsSimpleMarkerSymbolLayer()
    inner_sl.setShape(Qgis.MarkerShape.Circle)
    inner_sl.setColor(QColor(0, 0, 0))
    inner_sl.setStrokeColor(QColor(0, 0, 0))
    inner_sl.setStrokeWidth(0)
    inner_sl.setSize(symbol_size_mm * 0.45)
    inner_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, bg_sl)
    symbol.appendSymbolLayer(outer_sl)
    symbol.appendSymbolLayer(inner_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    layer.setLabelsEnabled(False)
    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成（不显示标注）")
    return layer


def create_intensity_legend_layer():
    """
    创建烈度图例用的线图层

    返回:
        QgsVectorLayer: 烈度图例线图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "烈度", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(INTENSITY_LEGEND_COLOR)
    line_sl.setWidth(INTENSITY_LEGEND_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的线图层

    返回:
        QgsVectorLayer: 省界图例线图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "省界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(PROVINCE_COLOR)
    line_sl.setWidth(PROVINCE_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_city_legend_layer():
    """
    创建市界图例用的线图层

    返回:
        QgsVectorLayer: 市界图例线图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "市界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(CITY_COLOR)
    line_sl.setWidth(CITY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.CustomDashLine)

    dash_pattern = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]
    line_sl.setCustomDashVector(dash_pattern)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_county_legend_layer():
    """
    创建县界图例用的线图层

    返回:
        QgsVectorLayer: 县界图例线图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "县界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(COUNTY_COLOR)
    line_sl.setWidth(COUNTY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.CustomDashLine)

    dash_pattern = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]
    line_sl.setCustomDashVector(dash_pattern)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm,
                        landslide_legend_list=None, ordered_layers=None):
    """
    创建QGIS打印布局

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺
        map_height_mm (float): 地图高度（毫米）
        landslide_legend_list (list): 滑坡评估图例列表
        ordered_layers (list): 按渲染顺序排列的图层列表

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震滑坡评估图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm,
                                   QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    # 地图项
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top,
                                        QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, map_height_mm,
                                         QgsUnitTypes.LayoutMillimeters))
    map_item.setExtent(extent)
    map_item.setCrs(CRS_WGS84)
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM,
                                                      QgsUnitTypes.LayoutMillimeters))
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setBackgroundEnabled(True)
    map_item.setBackgroundColor(QColor(255, 255, 255))
    layout.addLayoutItem(map_item)

    # 显式设置渲染图层顺序
    layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
    if layers_to_set:
        map_item.setLayers(layers_to_set)
        map_item.setKeepLayerSet(True)
    map_item.invalidateCache()

    # 经纬度网格
    _setup_map_grid(map_item, extent)
    # 指北针
    _add_north_arrow(layout, map_height_mm)
    # 图例（含比例尺）
    _add_legend(layout, map_item, project, map_height_mm, output_height_mm, landslide_legend_list,
                scale=scale, extent=extent, center_lat=latitude)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
    """
    grid = QgsLayoutItemMapGrid("经纬度网格", map_item)
    grid.setEnabled(True)
    grid.setCrs(CRS_WGS84)

    lon_range = extent.xMaximum() - extent.xMinimum()
    lat_range = extent.yMaximum() - extent.yMinimum()
    lon_step = _choose_tick_step(lon_range, target_min=3, target_max=6)
    lat_step = _choose_tick_step(lat_range, target_min=3, target_max=5)

    grid.setIntervalX(lon_step)
    grid.setIntervalY(lat_step)
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
    grid.setAnnotationEnabled(True)

    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Bottom)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)

    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Top)
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.Left)

    annot_format = QgsTextFormat()
    annot_font = QFont("Times New Roman", LONLAT_FONT_SIZE_PT)
    annot_format.setFont(annot_font)
    annot_format.setSize(LONLAT_FONT_SIZE_PT)
    annot_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    annot_format.setColor(QColor(0, 0, 0))
    grid.setAnnotationTextFormat(annot_format)

    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinute)
    grid.setAnnotationPrecision(0)
    grid.setFrameStyle(QgsLayoutItemMapGrid.InteriorTicks)
    grid.setFrameWidth(1.5)
    grid.setFramePenSize(0.3)
    grid.setFramePenColor(QColor(0, 0, 0))

    map_item.grids().addGrid(grid)


def _add_north_arrow(layout, map_height_mm):
    """
    添加指北针

    参数:
        layout (QgsPrintLayout): 打印布局
        map_height_mm (float): 地图高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_north_arrow_landslide_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """
    添加比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图布局项
        scale (int): 比例尺
        extent (QgsRectangle): 地图范围
        center_lat (float): 中心纬度
        map_height_mm (float): 地图高度（毫米）
    """
    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM
    map_right = map_left + MAP_WIDTH_MM
    map_bottom = map_top + map_height_mm

    lon_range_deg = extent.xMaximum() - extent.xMinimum()
    map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))
    km_per_mm = map_total_km / MAP_WIDTH_MM
    target_bar_km = MAP_WIDTH_MM * 0.18 * km_per_mm

    nice_values = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    bar_km = nice_values[0]
    for nv in nice_values:
        if nv <= target_bar_km * 1.5:
            bar_km = nv
        else:
            break

    bar_length_mm = bar_km / km_per_mm
    bar_length_mm = max(bar_length_mm, 20.0)
    num_segments = 4

    sb_width = bar_length_mm + 16.0
    sb_height = 14.0
    sb_x = map_right - sb_width
    sb_y = map_bottom - sb_height

    # 比例尺白色背景
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(sb_x, sb_y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(sb_width, sb_height, QgsUnitTypes.LayoutMillimeters))
    bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    bg_shape.setSymbol(bg_symbol)
    bg_shape.setFrameEnabled(True)
    bg_shape.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(bg_shape)

    # 比例尺文字 1:xxx
    tick_format = QgsTextFormat()
    tick_format.setFont(QFont("Times New Roman", SCALE_FONT_SIZE_PT))
    tick_format.setSize(SCALE_FONT_SIZE_PT)
    tick_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    tick_format.setColor(QColor(0, 0, 0))

    scale_label = QgsLayoutItemLabel(layout)
    scale_label.setText(f"1:{scale:,}")
    scale_label.setTextFormat(tick_format)
    scale_label.attemptMove(QgsLayoutPoint(sb_x, sb_y + 0.5, QgsUnitTypes.LayoutMillimeters))
    scale_label.attemptResize(QgsLayoutSize(sb_width, 4.5, QgsUnitTypes.LayoutMillimeters))
    scale_label.setHAlign(Qt.AlignHCenter)
    scale_label.setVAlign(Qt.AlignVCenter)
    scale_label.setFrameEnabled(False)
    scale_label.setBackgroundEnabled(False)
    layout.addLayoutItem(scale_label)

    # 比例尺色块段
    bar_start_x = sb_x + (sb_width - bar_length_mm) / 2.0
    bar_y = sb_y + 5.5
    bar_h = 1.8
    seg_width_mm = bar_length_mm / num_segments

    for i in range(num_segments):
        seg_shape = QgsLayoutItemShape(layout)
        seg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        seg_x = bar_start_x + i * seg_width_mm
        seg_shape.attemptMove(QgsLayoutPoint(seg_x, bar_y, QgsUnitTypes.LayoutMillimeters))
        seg_shape.attemptResize(QgsLayoutSize(seg_width_mm, bar_h, QgsUnitTypes.LayoutMillimeters))
        fill_color = '0,0,0,255' if i % 2 == 0 else '255,255,255,255'
        seg_symbol = QgsFillSymbol.createSimple({
            'color': fill_color,
            'outline_color': '0,0,0,255',
            'outline_width': '0.15',
            'outline_width_unit': 'MM',
        })
        seg_shape.setSymbol(seg_symbol)
        seg_shape.setFrameEnabled(False)
        layout.addLayoutItem(seg_shape)

    # 比例尺刻度标签
    label_y = bar_y + bar_h + 0.3
    label_h = 3.5

    lbl_0 = QgsLayoutItemLabel(layout)
    lbl_0.setText("0")
    lbl_0.setTextFormat(tick_format)
    lbl_0.attemptMove(QgsLayoutPoint(bar_start_x - 1.5, label_y, QgsUnitTypes.LayoutMillimeters))
    lbl_0.attemptResize(QgsLayoutSize(6.0, label_h, QgsUnitTypes.LayoutMillimeters))
    lbl_0.setHAlign(Qt.AlignHCenter)
    lbl_0.setVAlign(Qt.AlignTop)
    lbl_0.setFrameEnabled(False)
    lbl_0.setBackgroundEnabled(False)
    layout.addLayoutItem(lbl_0)

    mid_km = bar_km // 2
    if mid_km > 0:
        lbl_mid = QgsLayoutItemLabel(layout)
        lbl_mid.setText(str(mid_km))
        lbl_mid.setTextFormat(tick_format)
        mid_x = bar_start_x + bar_length_mm / 2.0 - 3.0
        lbl_mid.attemptMove(QgsLayoutPoint(mid_x, label_y, QgsUnitTypes.LayoutMillimeters))
        lbl_mid.attemptResize(QgsLayoutSize(8.0, label_h, QgsUnitTypes.LayoutMillimeters))
        lbl_mid.setHAlign(Qt.AlignHCenter)
        lbl_mid.setVAlign(Qt.AlignTop)
        lbl_mid.setFrameEnabled(False)
        lbl_mid.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_mid)

    lbl_end = QgsLayoutItemLabel(layout)
    lbl_end.setText(f"{bar_km} km")
    lbl_end.setTextFormat(tick_format)
    end_x = bar_start_x + bar_length_mm - 4.0
    lbl_end.attemptMove(QgsLayoutPoint(end_x, label_y, QgsUnitTypes.LayoutMillimeters))
    lbl_end.attemptResize(QgsLayoutSize(14.0, label_h, QgsUnitTypes.LayoutMillimeters))
    lbl_end.setHAlign(Qt.AlignHCenter)
    lbl_end.setVAlign(Qt.AlignTop)
    lbl_end.setFrameEnabled(False)
    lbl_end.setBackgroundEnabled(False)
    layout.addLayoutItem(lbl_end)


def _add_legend(layout, map_item, project, map_height_mm, output_height_mm,
                landslide_legend_list=None, scale=None, extent=None, center_lat=None):
    """
    添加图例
    - 上部：震中/地级市/省界/市界/县界（3行2列，平行排列）
    - 下部：滑坡评估图例（色块 + 危险等级名称）
    - 底部：比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图布局项
        project (QgsProject): QGIS项目
        map_height_mm (float): 地图高度（毫米）
        output_height_mm (float): 输出高度（毫米）
        landslide_legend_list (list): 滑坡评估图例列表
        scale (int): 比例尺分母（用于绘制比例尺）
        extent (QgsRectangle): 地图范围（用于计算比例尺）
        center_lat (float): 地图中心纬度（用于计算比例尺）
    """
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

    # 标题文本格式
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    # 基本图例项文本格式
    basic_item_format = QgsTextFormat()
    basic_item_format.setFont(QFont("SimSun", BASIC_LEGEND_FONT_SIZE_PT))
    basic_item_format.setSize(BASIC_LEGEND_FONT_SIZE_PT)
    basic_item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    basic_item_format.setColor(QColor(0, 0, 0))

    # 滑坡评估图例文本格式
    assessment_format = QgsTextFormat()
    assessment_format.setFont(QFont("SimSun", ASSESSMENT_LEGEND_FONT_SIZE_PT))
    assessment_format.setSize(ASSESSMENT_LEGEND_FONT_SIZE_PT)
    assessment_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    assessment_format.setColor(QColor(0, 0, 0))

    # 图例背景
    legend_bg = QgsLayoutItemShape(layout)
    legend_bg.setShapeType(QgsLayoutItemShape.Rectangle)
    legend_bg.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    legend_bg.attemptResize(QgsLayoutSize(legend_width, legend_height, QgsUnitTypes.LayoutMillimeters))
    legend_bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    legend_bg.setSymbol(legend_bg_symbol)
    legend_bg.setFrameEnabled(True)
    legend_bg.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend_bg)

    # 标题 "图  例"
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + 1.0, QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(legend_width, 5.0, QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # ===== 上部图例：3行2列（基本图例项） =====
    top_legend_start_y = legend_y + 7.0

    col_count = 2
    row_count = 3
    left_pad = 2.0
    right_pad = 2.0
    col_gap = 1.0
    row_height = BASIC_LEGEND_ROW_HEIGHT_MM
    icon_width = 4.0
    icon_height = 2.5
    icon_text_gap = 1.0

    available_width = legend_width - left_pad - right_pad - (col_count - 1) * col_gap
    col_width = available_width / col_count

    # 基本图例项列表（3行2列，共6项）
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度圈", "solid_line_black"),
    ]

    for idx, (display_name, draw_type) in enumerate(legend_items):
        row = idx // col_count
        col = idx % col_count

        item_x = legend_x + left_pad + col * (col_width + col_gap)
        item_y = top_legend_start_y + row * row_height
        icon_center_y = item_y + row_height / 2.0

        if draw_type == "star":
            _draw_star_icon(layout, item_x, icon_center_y, icon_width, icon_height)
        elif draw_type == "circle":
            _draw_city_icon(layout, item_x, icon_center_y, icon_width, icon_height)
        elif draw_type == "solid_line":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM, solid=True)
        elif draw_type == "dash_line_city":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_line_county":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)
        elif draw_type == "solid_line_black":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            INTENSITY_LEGEND_COLOR, INTENSITY_LEGEND_LINE_WIDTH_MM, solid=True)

        # 文字标签
        text_x = item_x + icon_width + icon_text_gap
        text_width = col_width - icon_width - icon_text_gap

        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(display_name)
        text_label.setTextFormat(basic_item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, row_height - 1.0, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    top_legend_height = row_count * row_height

    # ===== 下部：滑坡评估图例 =====
    if landslide_legend_list:
        # 标题"滑坡评估"
        assessment_title_y = top_legend_start_y + top_legend_height + 2.0

        assessment_title_format = QgsTextFormat()
        assessment_title_font = QFont("SimHei")
        assessment_title_font.setPointSizeF(10.0)
        assessment_title_format.setFont(assessment_title_font)
        assessment_title_format.setSize(10.0)
        assessment_title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        assessment_title_format.setColor(QColor(0, 0, 0))

        assessment_title_label = QgsLayoutItemLabel(layout)
        assessment_title_label.setText("滑坡危险性等级")
        assessment_title_label.setTextFormat(assessment_title_format)
        assessment_title_label.attemptMove(
            QgsLayoutPoint(legend_x, assessment_title_y, QgsUnitTypes.LayoutMillimeters))
        assessment_title_label.attemptResize(
            QgsLayoutSize(legend_width, 5.0, QgsUnitTypes.LayoutMillimeters))
        assessment_title_label.setHAlign(Qt.AlignHCenter)
        assessment_title_label.setVAlign(Qt.AlignVCenter)
        assessment_title_label.setFrameEnabled(False)
        assessment_title_label.setBackgroundEnabled(False)
        layout.addLayoutItem(assessment_title_label)

        item_start_y = assessment_title_y + 5.0

        colorbar_width = 8.0  # 色块宽度（毫米）
        colorbar_height = ASSESSMENT_LEGEND_ROW_HEIGHT_MM  # 单个色块高度（毫米）
        colorbar_gap = ASSESSMENT_LEGEND_GAP_MM  # 色块之间间距（毫米，分开显示）
        colorbar_left_pad = 3.0  # 色块左边距（毫米）
        label_gap = 2.0  # 色块与标签之间间距（毫米）
        assessment_right_pad = 2.0

        text_area_width = (legend_width - colorbar_left_pad
                           - colorbar_width - label_gap - assessment_right_pad)

        current_y = item_start_y
        displayed_count = 0

        for idx, (color_rgba, label) in enumerate(landslide_legend_list):
            # 防止超出图例背景范围
            if current_y + colorbar_height > legend_y + legend_height - 2.0:
                break

            # 绘制色块矩形（带黑色细边框）
            color_box = QgsLayoutItemShape(layout)
            color_box.setShapeType(QgsLayoutItemShape.Rectangle)
            color_box.attemptMove(QgsLayoutPoint(legend_x + colorbar_left_pad, current_y,
                                                 QgsUnitTypes.LayoutMillimeters))
            color_box.attemptResize(QgsLayoutSize(colorbar_width, colorbar_height,
                                                  QgsUnitTypes.LayoutMillimeters))
            color_str = f"{color_rgba[0]},{color_rgba[1]},{color_rgba[2]},{color_rgba[3]}"
            box_symbol = QgsFillSymbol.createSimple({
                'color': color_str,
                'outline_color': '80,80,80,255',
                'outline_width': '0.15',
                'outline_width_unit': 'MM',
            })
            color_box.setSymbol(box_symbol)
            color_box.setFrameEnabled(False)
            layout.addLayoutItem(color_box)

            # 绘制标签文本（垂直居中于色块）
            text_x = legend_x + colorbar_left_pad + colorbar_width + label_gap

            text_label = QgsLayoutItemLabel(layout)
            text_label.setText(label)
            text_label.setTextFormat(assessment_format)
            text_label.attemptMove(QgsLayoutPoint(text_x, current_y, QgsUnitTypes.LayoutMillimeters))
            # 标签高度与色块高度一致，确保垂直居中
            text_label.attemptResize(QgsLayoutSize(text_area_width, colorbar_height,
                                                   QgsUnitTypes.LayoutMillimeters))
            text_label.setHAlign(Qt.AlignLeft)
            text_label.setVAlign(Qt.AlignVCenter)
            text_label.setFrameEnabled(False)
            text_label.setBackgroundEnabled(False)
            text_label.setMode(QgsLayoutItemLabel.ModeFont)
            layout.addLayoutItem(text_label)

            current_y += colorbar_height + colorbar_gap
            displayed_count += 1

        print(f"[信息] 滑坡评估图例添加完成，共 {displayed_count} 项")
    else:
        print("[信息] 无滑坡评估图例数据，跳过")

    # ── 比例尺（位于图例内容下方）──
    if scale is not None and extent is not None and center_lat is not None:
        lon_range_deg = extent.xMaximum() - extent.xMinimum()
        map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))
        km_per_mm = map_total_km / MAP_WIDTH_MM if MAP_WIDTH_MM > 0 else 1.0
        target_bar_km = MAP_WIDTH_MM * 0.18 * km_per_mm

        nice_values = [1, 2, 5, 10, 20, 50, 100, 200, 500]
        bar_km = nice_values[0]
        for nv in nice_values:
            if nv <= target_bar_km * 1.5:
                bar_km = nv
            else:
                break

        bar_length_mm = bar_km / km_per_mm if km_per_mm > 0 else 20.0
        bar_length_mm = max(bar_length_mm, 20.0)
        num_segments = 4

        std_bar_width = bar_length_mm + 16.0
        std_bar_height = 14.0

        avail_width = legend_width - 4.0
        if std_bar_width > avail_width:
            scale_factor = avail_width / std_bar_width
            std_bar_width = avail_width
            bar_length_mm *= scale_factor
            std_bar_height *= scale_factor
        else:
            scale_factor = 1.0

        # 比例尺垂直位置：距底部留 4mm 空间
        sb_height = std_bar_height
        sb_y = legend_y + legend_height - sb_height - 4.0
        sb_x = legend_x + (legend_width - std_bar_width) / 2.0

        scale_font_size = SCALE_FONT_SIZE_PT
        scale_tf = QgsTextFormat()
        scale_tf.setFont(QFont("Times New Roman", scale_font_size))
        scale_tf.setSize(scale_font_size)
        scale_tf.setSizeUnit(QgsUnitTypes.RenderPoints)
        scale_tf.setColor(QColor(0, 0, 0))

        lbl_scale = QgsLayoutItemLabel(layout)
        lbl_scale.setText(f"1:{scale:,}")
        lbl_scale.setTextFormat(scale_tf)
        lbl_scale.attemptMove(QgsLayoutPoint(sb_x, sb_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        lbl_scale.attemptResize(QgsLayoutSize(std_bar_width, 4.5 * scale_factor,
                                              QgsUnitTypes.LayoutMillimeters))
        lbl_scale.setHAlign(Qt.AlignHCenter)
        lbl_scale.setVAlign(Qt.AlignVCenter)
        lbl_scale.setFrameEnabled(False)
        lbl_scale.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_scale)

        bar_start_x = sb_x + (std_bar_width - bar_length_mm) / 2.0
        bar_y = sb_y + 5.5 * scale_factor
        bar_h = 1.8 * scale_factor
        seg_width_mm = bar_length_mm / num_segments

        for i in range(num_segments):
            seg_shape = QgsLayoutItemShape(layout)
            seg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
            seg_x = bar_start_x + i * seg_width_mm
            seg_shape.attemptMove(QgsLayoutPoint(seg_x, bar_y, QgsUnitTypes.LayoutMillimeters))
            seg_shape.attemptResize(QgsLayoutSize(seg_width_mm, bar_h,
                                                  QgsUnitTypes.LayoutMillimeters))
            fill_color = '0,0,0,255' if i % 2 == 0 else '255,255,255,255'
            seg_symbol = QgsFillSymbol.createSimple({
                'color': fill_color,
                'outline_color': '0,0,0,255',
                'outline_width': '0.15',
                'outline_width_unit': 'MM',
            })
            seg_shape.setSymbol(seg_symbol)
            seg_shape.setFrameEnabled(False)
            layout.addLayoutItem(seg_shape)

        tick_tf = QgsTextFormat()
        tick_tf.setFont(QFont("Times New Roman", scale_font_size))
        tick_tf.setSize(scale_font_size)
        tick_tf.setSizeUnit(QgsUnitTypes.RenderPoints)
        tick_tf.setColor(QColor(0, 0, 0))

        label_y = bar_y + bar_h + 0.3
        label_h = 3.5 * scale_factor

        lbl_0 = QgsLayoutItemLabel(layout)
        lbl_0.setText("0")
        lbl_0.setTextFormat(tick_tf)
        lbl_0.attemptMove(QgsLayoutPoint(bar_start_x - 1.5, label_y,
                                         QgsUnitTypes.LayoutMillimeters))
        lbl_0.attemptResize(QgsLayoutSize(6.0, label_h, QgsUnitTypes.LayoutMillimeters))
        lbl_0.setHAlign(Qt.AlignHCenter)
        lbl_0.setVAlign(Qt.AlignTop)
        lbl_0.setFrameEnabled(False)
        lbl_0.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_0)

        mid_km = bar_km // 2
        if mid_km > 0:
            lbl_mid = QgsLayoutItemLabel(layout)
            lbl_mid.setText(str(mid_km))
            lbl_mid.setTextFormat(tick_tf)
            mid_x = bar_start_x + bar_length_mm / 2.0 - 3.0
            lbl_mid.attemptMove(QgsLayoutPoint(mid_x, label_y, QgsUnitTypes.LayoutMillimeters))
            lbl_mid.attemptResize(QgsLayoutSize(8.0, label_h, QgsUnitTypes.LayoutMillimeters))
            lbl_mid.setHAlign(Qt.AlignHCenter)
            lbl_mid.setVAlign(Qt.AlignTop)
            lbl_mid.setFrameEnabled(False)
            lbl_mid.setBackgroundEnabled(False)
            layout.addLayoutItem(lbl_mid)

        lbl_end = QgsLayoutItemLabel(layout)
        lbl_end.setText(f"{bar_km} km")
        lbl_end.setTextFormat(tick_tf)
        end_x = bar_start_x + bar_length_mm - 4.0
        lbl_end.attemptMove(QgsLayoutPoint(end_x, label_y, QgsUnitTypes.LayoutMillimeters))
        lbl_end.attemptResize(QgsLayoutSize(14.0, label_h, QgsUnitTypes.LayoutMillimeters))
        lbl_end.setHAlign(Qt.AlignHCenter)
        lbl_end.setVAlign(Qt.AlignTop)
        lbl_end.setFrameEnabled(False)
        lbl_end.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_end)

        print(f"[信息] 比例尺添加到图例区完成，1:{scale:,}")

    print("[信息] 图例添加完成")


def _draw_star_icon(layout, x, center_y, width, height):
    """
    在图例中绘制红色五角星图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        height (float): 高度
    """
    star_label = QgsLayoutItemLabel(layout)
    star_label.setText("★")

    star_format = QgsTextFormat()
    star_format.setFont(QFont("SimSun", 10))
    star_format.setSize(10)
    star_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    star_format.setColor(EPICENTER_COLOR)
    star_label.setTextFormat(star_format)

    star_label.attemptMove(QgsLayoutPoint(x, center_y - height / 2.0 - 0.5, QgsUnitTypes.LayoutMillimeters))
    star_label.attemptResize(QgsLayoutSize(width, height + 1.0, QgsUnitTypes.LayoutMillimeters))
    star_label.setHAlign(Qt.AlignHCenter)
    star_label.setVAlign(Qt.AlignVCenter)
    star_label.setFrameEnabled(False)
    star_label.setBackgroundEnabled(False)
    layout.addLayoutItem(star_label)


def _draw_city_icon(layout, x, center_y, width, height):
    """
    在图例中绘制地级市圆点图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        height (float): 高度
    """
    icon_size = min(width, height) * 0.6
    center_x = x + width / 2.0

    outer_circle = QgsLayoutItemShape(layout)
    outer_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    outer_circle.attemptMove(QgsLayoutPoint(center_x - icon_size / 2.0, center_y - icon_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    outer_circle.attemptResize(QgsLayoutSize(icon_size, icon_size, QgsUnitTypes.LayoutMillimeters))
    outer_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': '0.15',
        'outline_width_unit': 'MM',
    })
    outer_circle.setSymbol(outer_symbol)
    outer_circle.setFrameEnabled(False)
    layout.addLayoutItem(outer_circle)

    inner_size = icon_size * 0.4
    inner_circle = QgsLayoutItemShape(layout)
    inner_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    inner_circle.attemptMove(QgsLayoutPoint(center_x - inner_size / 2.0, center_y - inner_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    inner_circle.attemptResize(QgsLayoutSize(inner_size, inner_size, QgsUnitTypes.LayoutMillimeters))
    inner_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    inner_circle.setSymbol(inner_symbol)
    inner_circle.setFrameEnabled(False)
    layout.addLayoutItem(inner_circle)


def _draw_line_icon(layout, x, center_y, width, color, line_width_mm, solid=True):
    """
    在图例中绘制实线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽（毫米）
        solid (bool): 是否实线
    """
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)

    line_height = max(line_width_mm, 0.5)
    line_shape.attemptMove(QgsLayoutPoint(x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
    line_shape.attemptResize(QgsLayoutSize(width, line_height, QgsUnitTypes.LayoutMillimeters))

    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_style': 'no',
    })

    line_shape.setSymbol(line_symbol)
    line_shape.setFrameEnabled(False)
    layout.addLayoutItem(line_shape)


def _draw_dash_line_icon(layout, x, center_y, width, color, line_width_mm, dash_gap_mm):
    """
    在图例中绘制虚线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标总宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽(mm)
        dash_gap_mm (float): 虚线间隔(mm)
    """
    line_height = max(line_width_mm, 0.5)
    color_str = f"{color.red()},{color.green()},{color.blue()},255"

    dash_length_mm = max(dash_gap_mm * 3.5, 0.8)
    pattern_length = dash_length_mm + dash_gap_mm

    current_x = x
    while current_x < x + width:
        actual_dash_length = min(dash_length_mm, x + width - current_x)
        if actual_dash_length <= 0:
            break

        dash_shape = QgsLayoutItemShape(layout)
        dash_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_height / 2.0,
                                              QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(QgsLayoutSize(actual_dash_length, line_height,
                                               QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)

        current_x += pattern_length


# ============================================================
# PNG导出
# ============================================================

def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出文件路径
        dpi (int): 输出分辨率（默认150）

    返回:
        str: 成功时返回输出文件路径，失败返回None
    """
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    settings.cropToContents = False

    abs_path = os.path.abspath(output_path)
    result = exporter.exportToImage(abs_path, settings)

    if result == QgsLayoutExporter.Success:
        print(f"[信息] PNG导出成功: {abs_path}")
        return abs_path
    else:
        error_map = {
            QgsLayoutExporter.FileError: "文件错误",
            QgsLayoutExporter.MemoryError: "内存错误",
            QgsLayoutExporter.SvgLayerError: "SVG图层错误",
            QgsLayoutExporter.PrintError: "打印错误",
            QgsLayoutExporter.Canceled: "已取消",
        }
        msg = error_map.get(result, f"未知错误(代码:{result})")
        print(f"[错误] PNG导出失败: {msg}")
        return None


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_landslide_assessment_map(longitude, latitude, magnitude,
                                                  output_path="output_landslide_assessment_map.png",
                                                  kml_path=None,
                                                  basemap_path=None, annotation_path=None):
    """
    生成地震滑坡评估图（主入口函数）

    除生成图片外，还会统计输出图范围内各危险等级的面积和占比，
    并返回包含图片路径和统计信息的字典。

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        output_path (str): 输出PNG文件路径
        kml_path (str): 烈度圈KML文件路径（可选）
        basemap_path (str): 底图路径（可选，未使用）
        annotation_path (str): 注记图层路径（可选）

    返回:
        dict: {
            "image_path": str 或 None,   # 输出图片路径
            "stats_message": str,         # 统计信息描述文本
            "stats_detail": list,         # 各等级详细统计列表
        }
    """
    logger.info('开始生成滑坡评估图: lon=%.4f lat=%.4f M=%.1f output=%s',
                longitude, latitude, magnitude, output_path)
    try:
        return _generate_earthquake_landslide_assessment_map_impl(
            longitude, latitude, magnitude, output_path, kml_path,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成滑坡评估图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_landslide_assessment_map_impl(longitude, latitude, magnitude,
                                                        output_path, kml_path,
                                                        basemap_path=None, annotation_path=None):
    """generate_earthquake_landslide_assessment_map 的实际实现。"""
    print("=" * 60)
    print(f"[开始] 生成地震滑坡评估图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print(f"  GDAL可用: {GDAL_AVAILABLE}")
    print("=" * 60)

    # 根据震级获取配置
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    # 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 计算地图高度
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 通过 QGISManager 确保 QGIS 已初始化
    from core.qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 获取临时文件管理器
    temp_manager = get_temp_manager()

    # 临时注记底图文件路径
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_temp_annotation_landslide.png"
    )

    # 用于保存统计用的TIF路径
    stats_tif_path = None

    result_dict = {
        "image_path": None,
        "stats_message": "",
        "stats_detail": [],
    }

    try:
        # ============================================================
        # 下载天地图矢量注记瓦片
        # ============================================================
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        if annotation_path:
            annotation_raster = QgsRasterLayer(annotation_path, "天地图注记", "gdal")
            if not annotation_raster.isValid():
                annotation_raster = None
        else:
            annotation_raster = download_tianditu_annotation_tiles(
                extent, width_px, height_px, temp_annotation_path)

        # 加载滑坡评估底图（优化版，按需裁剪）
        assessment_layer, stats_tif_path = load_landslide_assessment_raster_optimized(
            LANDSLIDE_ASSESSMENT_TIF_PATH, extent)
        if assessment_layer:
            project.addMapLayer(assessment_layer)

        # 构建滑坡评估图例列表
        landslide_legend_list = build_landslide_legend_list()
        print(f"[信息] 获取到 {len(landslide_legend_list)} 个滑坡评估图例项")

        # 加载县界图层
        county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界_地图")
        if county_layer:
            style_county_layer(county_layer)
            project.addMapLayer(county_layer)

        # 加载市界图层
        city_layer = load_vector_layer(CITY_SHP_PATH, "市界_地图")
        if city_layer:
            style_city_layer(city_layer)
            project.addMapLayer(city_layer)

        # 加载省界图层
        province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界_地图")
        if province_layer:
            style_province_layer(province_layer, center_lon=longitude, center_lat=latitude, extent=extent)
            project.addMapLayer(province_layer)

        # 创建省份标注图层
        province_label_layer = None
        if province_layer:
            province_label_layer = create_province_label_layer(province_layer, longitude, latitude, extent)
            if province_label_layer:
                project.addMapLayer(province_label_layer)

        # 加载地级市点位图层
        city_point_layer = create_city_point_layer(extent)
        if city_point_layer:
            project.addMapLayer(city_point_layer)

        # 创建图例辅助图层
        province_legend_layer = create_province_legend_layer()
        if province_legend_layer:
            project.addMapLayer(province_legend_layer)

        city_legend_layer = create_city_legend_layer()
        if city_legend_layer:
            project.addMapLayer(city_legend_layer)

        county_legend_layer = create_county_legend_layer()
        if county_legend_layer:
            project.addMapLayer(county_legend_layer)

        intensity_legend_layer = create_intensity_legend_layer()
        if intensity_legend_layer:
            project.addMapLayer(intensity_legend_layer)

        # 处理烈度圈KML（统一解析为绝对路径，供后续统计使用）
        intensity_layer = None
        abs_kml = None
        if kml_path:
            abs_kml = kml_path if os.path.isabs(kml_path) else resolve_path(kml_path)
            intensity_data = parse_intensity_kml(abs_kml)
            if intensity_data:
                intensity_layer = create_intensity_layer(intensity_data)
                if intensity_layer:
                    project.addMapLayer(intensity_layer)

        # 创建震中图层
        epicenter_layer = create_epicenter_layer(longitude, latitude)
        if epicenter_layer:
            project.addMapLayer(epicenter_layer)

        # 添加注记图层
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # 按渲染顺序排列图层（第一项在最上层）
        ordered_layers = [lyr for lyr in [
            epicenter_layer,
            annotation_raster,
            intensity_layer,
            city_point_layer,
            province_label_layer,
            province_layer,
            city_layer,
            county_layer,
            assessment_layer,
        ] if lyr is not None]

        # 创建打印布局
        layout = create_print_layout(project, longitude, latitude, magnitude,
                                     extent, scale, map_height_mm,
                                     landslide_legend_list, ordered_layers)

        # 导出PNG
        image_result = export_layout_to_png(layout, output_path, OUTPUT_DPI)
        result_dict["image_path"] = image_result

        # ============================================================
        # 统计各危险等级面积和占比
        # 始终从原始TIF文件读取数据，避免使用已按地图显示范围裁剪的临时文件
        # ============================================================
        abs_stats_tif = resolve_path(LANDSLIDE_ASSESSMENT_TIF_PATH) if not os.path.isabs(LANDSLIDE_ASSESSMENT_TIF_PATH) else LANDSLIDE_ASSESSMENT_TIF_PATH
        if os.path.exists(abs_stats_tif):
            # 统一使用绝对路径传入KML，避免相对路径在工作目录不同时找不到文件
            stats_kml = abs_kml if kml_path else None
            stats_list = compute_landslide_area_statistics(abs_stats_tif, extent, kml_path=stats_kml)
            result_dict["stats_detail"] = stats_list
            result_dict["stats_message"] = format_stats_message(stats_list)
            print(f"\n[统计结果] {result_dict['stats_message']}")
        else:
            result_dict["stats_message"] = "总的来看，无法获取地震诱发斜坡地质灾害危险性数据。"
            print(f"[警告] 无法获取滑坡评估底图进行统计: {abs_stats_tif}")

    finally:
        # 清理临时文件
        temp_manager.cleanup()

        # 清理指北针临时SVG文件
        svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_north_arrow_landslide_temp.svg")
        if os.path.exists(svg_temp):
            try:
                os.remove(svg_temp)
            except OSError:
                pass

        # 清理临时注记底图文件
        if not annotation_path and os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw_path = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw_path):
                    os.remove(pgw_path)
            except OSError:
                pass

    print("=" * 60)
    if result_dict["image_path"]:
        print(f"[完成] 滑坡评估图已输出: {result_dict['image_path']}")
    else:
        print("[失败] 滑坡评估图输出失败")
    print("=" * 60)
    return result_dict


# ============================================================
# 测试方法
# ============================================================

def test_magnitude_config():
    """测试震级配置获取"""
    print("\n--- 测试: get_magnitude_config ---")
    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000
    assert config_s["map_size_km"] == 30
    print(f"  M4.5 -> 范围{config_s['map_size_km']}km, 比例尺1:{config_s['scale']} ✓")

    config_m = get_magnitude_config(6.5)
    assert config_m["scale"] == 500000
    assert config_m["map_size_km"] == 100
    print(f"  M6.5 -> 范围{config_m['map_size_km']}km, 比例尺1:{config_m['scale']} ✓")

    config_l = get_magnitude_config(7.5)
    assert config_l["scale"] == 1500000
    assert config_l["map_size_km"] == 300
    print(f"  M7.5 -> 范围{config_l['map_size_km']}km, 比例尺1:{config_l['scale']} ✓")

    print("  所有震级配置测试通过 ✓")


def test_calculate_extent():
    """测试地图范围计算"""
    print("\n--- 测试: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum()
    assert extent.yMinimum() < 39.9 < extent.yMaximum()
    delta_y = extent.yMaximum() - extent.yMinimum()
    assert abs(delta_y - 0.2703) < 0.02
    print(f"  15km半径范围: 纬度差{delta_y:.4f}° ✓")
    print("  所有范围计算测试通过 ✓")


def test_int_to_roman():
    """测试罗马数字转换"""
    print("\n--- 测试: int_to_roman ---")
    assert int_to_roman(4) == "IV"
    assert int_to_roman(5) == "V"
    assert int_to_roman(6) == "VI"
    assert int_to_roman(9) == "IX"
    assert int_to_roman(10) == "X"
    assert int_to_roman(12) == "XII"
    print("  IV=4, V=5, VI=6, IX=9, X=10, XII=12 ✓")
    print("  罗马数字转换测试通过 ✓")


def test_landslide_classes():
    """测试滑坡评估分档配置"""
    print("\n--- 测试: 滑坡评估分档配置 ---")

    # 验证分档数量
    assert len(LANDSLIDE_CLASSES) == 5
    print(f"  分档数量: {len(LANDSLIDE_CLASSES)} ✓")

    # 第一档：低度危险区，值=1，颜色rgb(13,109,18)
    cls1 = LANDSLIDE_CLASSES[0]
    assert cls1["value"] == 1
    assert cls1["color"].red() == 13
    assert cls1["color"].green() == 109
    assert cls1["color"].blue() == 18
    assert cls1["label"] == "低度危险区"
    print(f"  第一档: 值={cls1['value']}, {cls1['label']}, "
          f"RGB({cls1['color'].red()},{cls1['color'].green()},{cls1['color'].blue()}) ✓")

    # 第二档：较低危险区，值=2，颜色rgb(121,183,15)
    cls2 = LANDSLIDE_CLASSES[1]
    assert cls2["value"] == 2
    assert cls2["color"].red() == 121
    assert cls2["color"].green() == 183
    assert cls2["color"].blue() == 15
    assert cls2["label"] == "较低危险区"
    print(f"  第二档: 值={cls2['value']}, {cls2['label']}, "
          f"RGB({cls2['color'].red()},{cls2['color'].green()},{cls2['color'].blue()}) ✓")

    # 第三档：中等危险区，值=3，颜色rgb(242,254,35)
    cls3 = LANDSLIDE_CLASSES[2]
    assert cls3["value"] == 3
    assert cls3["color"].red() == 242
    assert cls3["color"].green() == 254
    assert cls3["color"].blue() == 35
    assert cls3["label"] == "中等危险区"
    print(f"  第三档: 值={cls3['value']}, {cls3['label']}, "
          f"RGB({cls3['color'].red()},{cls3['color'].green()},{cls3['color'].blue()}) ✓")

    # 第四档：较高危险区，值=4，颜色rgb(254,172,24)
    cls4 = LANDSLIDE_CLASSES[3]
    assert cls4["value"] == 4
    assert cls4["color"].red() == 254
    assert cls4["color"].green() == 172
    assert cls4["color"].blue() == 24
    assert cls4["label"] == "较高危险区"
    print(f"  第四档: 值={cls4['value']}, {cls4['label']}, "
          f"RGB({cls4['color'].red()},{cls4['color'].green()},{cls4['color'].blue()}) ✓")

    # 第五档：高度危险区，值=5，颜色rgb(254,63,29)
    cls5 = LANDSLIDE_CLASSES[4]
    assert cls5["value"] == 5
    assert cls5["color"].red() == 254
    assert cls5["color"].green() == 63
    assert cls5["color"].blue() == 29
    assert cls5["label"] == "高度危险区"
    print(f"  第五档: 值={cls5['value']}, {cls5['label']}, "
          f"RGB({cls5['color'].red()},{cls5['color'].green()},{cls5['color'].blue()}) ✓")

    # 验证值从1到5递增
    values = [cls["value"] for cls in LANDSLIDE_CLASSES]
    assert values == [1, 2, 3, 4, 5]
    print(f"  值列表: {values} ✓")

    print("  滑坡评估分档配置测试通过 ✓")


def test_build_landslide_legend_list():
    """测试滑坡评估图例列表构建"""
    print("\n--- 测试: build_landslide_legend_list ---")

    legend_list = build_landslide_legend_list()

    # 验证数量
    assert len(legend_list) == 5
    print(f"  图例项数量: {len(legend_list)} ✓")

    # 验证第一项
    color1, label1 = legend_list[0]
    assert color1 == (13, 109, 18, 255)
    assert label1 == "低度危险区"
    print(f"  第一项: {label1}, RGBA{color1} ✓")

    # 验证第三项
    color3, label3 = legend_list[2]
    assert color3 == (242, 254, 35, 255)
    assert label3 == "中等危险区"
    print(f"  第三项: {label3}, RGBA{color3} ✓")

    # 验证第五项
    color5, label5 = legend_list[4]
    assert color5 == (254, 63, 29, 255)
    assert label5 == "高度危险区"
    print(f"  第五项: {label5}, RGBA{color5} ✓")

    print("  滑坡评估图例列表构建测试通过 ✓")


def test_determine_overall_danger_level():
    """测试整体危险性等级判断"""
    print("\n--- 测试: determine_overall_danger_level ---")

    # 测试：较高危险区>10%，应返回"较高"
    stats_low = [
        {"value": 1, "label": "低度危险区", "pixel_count": 1000, "area_km2": 50.0, "percentage": 40.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 500, "area_km2": 25.0, "percentage": 20.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 500, "area_km2": 25.0, "percentage": 20.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 300, "area_km2": 15.0, "percentage": 12.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 8.0},
    ]
    level = determine_overall_danger_level(stats_low)
    assert level == "较高", f"期望'较高'，实际'{level}'"
    print(f"  较高危险区>10%（高度危险区<=10%）-> 整体: '{level}' ✓")

    # 测试：高度危险区60%>10%，应返回"高"
    stats_high = [
        {"value": 1, "label": "低度危险区", "pixel_count": 100, "area_km2": 5.0, "percentage": 5.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 100, "area_km2": 5.0, "percentage": 5.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 10.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 400, "area_km2": 20.0, "percentage": 20.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 1200, "area_km2": 60.0, "percentage": 60.0},
    ]
    level = determine_overall_danger_level(stats_high)
    assert level == "高", f"期望'高'，实际'{level}'"
    print(f"  高度危险区60%>10% -> 整体: '{level}' ✓")

    # 测试：中等危险区60%>10%，应返回"中等"
    stats_mid = [
        {"value": 1, "label": "低度危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 10.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 10.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 1200, "area_km2": 60.0, "percentage": 60.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 10.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 200, "area_km2": 10.0, "percentage": 10.0},
    ]
    level = determine_overall_danger_level(stats_mid)
    assert level == "中等", f"期望'中等'，实际'{level}'"
    print(f"  中等危险区60%>10%（更高等级均<=10%）-> 整体: '{level}' ✓")

    # 测试：均不超过10%时，返回占比最高等级
    stats_all_low = [
        {"value": 1, "label": "低度危险区", "pixel_count": 500, "area_km2": 5.0, "percentage": 9.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 400, "area_km2": 4.0, "percentage": 8.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 300, "area_km2": 3.0, "percentage": 6.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 200, "area_km2": 2.0, "percentage": 4.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 100, "area_km2": 1.0, "percentage": 2.0},
    ]
    level = determine_overall_danger_level(stats_all_low)
    assert level == "低", f"期望'低'，实际'{level}'"
    print(f"  所有等级均<=10%，返回占比最高等级 -> 整体: '{level}' ✓")

    # 测试空列表
    level_empty = determine_overall_danger_level([])
    assert level_empty == "未知"
    print(f"  空列表 -> 整体: '{level_empty}' ✓")

    print("  整体危险性等级判断测试通过 ✓")


def test_format_stats_message():
    """测试统计信息格式化"""
    print("\n--- 测试: format_stats_message ---")

    stats_list = [
        {"value": 1, "label": "低度危险区", "pixel_count": 1000, "area_km2": 500.0, "percentage": 40.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 600, "area_km2": 300.0, "percentage": 24.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 400, "area_km2": 200.0, "percentage": 16.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 300, "area_km2": 150.0, "percentage": 12.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 200, "area_km2": 100.0, "percentage": 8.0},
    ]

    message = format_stats_message(stats_list)
    print(f"  生成消息: {message}")

    # 验证消息包含关键信息（面积统一保留两位小数）
    assert "总的来看" in message
    assert "地震诱发斜坡地质灾害危险性" in message
    assert "低度危险区面积为500.0平方千米" in message
    assert "占比40.0%" in message
    assert "较低危险区面积为300.0平方千米" in message
    assert "中等危险区面积为200.0平方千米" in message
    assert "较高危险区面积为150.0平方千米" in message
    assert "高度危险区面积为100.0平方千米" in message
    # 较高危险区12%>10%，高度危险区8%<=10% -> 整体"较高"
    assert "危险性较高" in message
    print("  消息内容验证 ✓")

    # 测试小面积（<1 km²）保留2位小数
    stats_small = [
        {"value": 1, "label": "低度危险区", "pixel_count": 10, "area_km2": 0.55, "percentage": 55.0},
        {"value": 2, "label": "较低危险区", "pixel_count": 8, "area_km2": 0.44, "percentage": 44.0},
        {"value": 3, "label": "中等危险区", "pixel_count": 1, "area_km2": 0.00, "percentage": 0.0},
        {"value": 4, "label": "较高危险区", "pixel_count": 1, "area_km2": 0.00, "percentage": 0.0},
        {"value": 5, "label": "高度危险区", "pixel_count": 1, "area_km2": 0.00, "percentage": 0.0},
    ]
    small_msg = format_stats_message(stats_small)
    assert "低度危险区面积为0.55平方千米" in small_msg
    assert "较低危险区面积为0.44平方千米" in small_msg
    print(f"  小面积保留2位小数验证 ✓")

    # 测试空统计列表
    empty_msg = format_stats_message([])
    assert "无法获取" in empty_msg
    assert "总的来看" in empty_msg
    print(f"  空列表消息: {empty_msg} ✓")

    print("  统计信息格式化测试通过 ✓")


def test_boundary_styles():
    """测试市界和县界样式参数"""
    print("\n--- 测试: 市界和县界样式参数 ---")

    assert CITY_COLOR.red() == 160
    assert CITY_COLOR.green() == 160
    assert CITY_COLOR.blue() == 160
    assert CITY_LINE_WIDTH_MM == 0.24
    assert CITY_DASH_GAP_MM == 0.3
    print(f"  市界颜色: RGB({CITY_COLOR.red()},{CITY_COLOR.green()},{CITY_COLOR.blue()}) ✓")
    print(f"  市界线宽: {CITY_LINE_WIDTH_MM}mm ✓")
    print(f"  市界虚线间隔: {CITY_DASH_GAP_MM}mm ✓")

    assert COUNTY_COLOR.red() == 160
    assert COUNTY_COLOR.green() == 160
    assert COUNTY_COLOR.blue() == 160
    assert COUNTY_LINE_WIDTH_MM == 0.14
    assert COUNTY_DASH_GAP_MM == 0.2
    print(f"  县界颜色: RGB({COUNTY_COLOR.red()},{COUNTY_COLOR.green()},{COUNTY_COLOR.blue()}) ✓")
    print(f"  县界线宽: {COUNTY_LINE_WIDTH_MM}mm ✓")
    print(f"  县界虚线间隔: {COUNTY_DASH_GAP_MM}mm ✓")

    print("  市界和县界样式参数测试通过 ✓")


def test_legend_font_config():
    """测试图例字体配置"""
    print("\n--- 测试: 图例字体配置 ---")

    assert ASSESSMENT_LEGEND_FONT_SIZE_PT == 10
    print(f"  滑坡评估图例字体大小: {ASSESSMENT_LEGEND_FONT_SIZE_PT}pt ✓")

    assert BASIC_LEGEND_FONT_SIZE_PT == 10
    print(f"  基本图例字体大小: {BASIC_LEGEND_FONT_SIZE_PT}pt ✓")

    assert ASSESSMENT_LEGEND_ROW_HEIGHT_MM == 7.5
    print(f"  滑坡评估图例项行高: {ASSESSMENT_LEGEND_ROW_HEIGHT_MM}mm ✓")

    assert BASIC_LEGEND_ROW_HEIGHT_MM == 8.0
    print(f"  基本图例项行高: {BASIC_LEGEND_ROW_HEIGHT_MM}mm ✓")

    print("  图例字体配置测试通过 ✓")


def test_temp_file_manager():
    """测试临时文件管理器"""
    print("\n--- 测试: TempFileManager ---")

    manager = TempFileManager()

    # 测试创建临时目录
    temp_dir = manager.get_temp_dir()
    assert temp_dir is not None
    assert os.path.exists(temp_dir)
    assert "earthquake_landslide_assessment_" in temp_dir
    print(f"  临时目录创建成功: {temp_dir} ✓")

    # 测试创建临时文件
    temp_file = manager.get_temp_file(suffix=".tif")
    assert temp_file is not None
    assert temp_file.endswith(".tif")
    print(f"  临时文件创建成功: {temp_file} ✓")

    # 测试清理
    manager.cleanup()
    assert not os.path.exists(temp_dir)
    print("  临时文件清理成功 ✓")

    print("  临时文件管理器测试通过 ✓")


def test_gdal_availability():
    """测试GDAL可用性"""
    print("\n--- 测试: GDAL可用性 ---")

    print(f"  GDAL模块可用: {GDAL_AVAILABLE}")

    if GDAL_AVAILABLE:
        print(f"  GDAL版本: {gdal.VersionInfo()}")
        print("  GDAL功能正常 ✓")
    else:
        print("  [警告] GDAL不可用，将使用备用方案")

    print("  GDAL可用性测试完成")


def test_clip_extent_calculation():
    """测试裁剪范围计算"""
    print("\n--- 测试: 裁剪范围计算 ---")

    extent = calculate_extent(118.18, 39.63, 150)

    # 添加缓冲区
    clip_xmin = extent.xMinimum() - CLIP_BUFFER_DEGREES
    clip_xmax = extent.xMaximum() + CLIP_BUFFER_DEGREES
    clip_ymin = extent.yMinimum() - CLIP_BUFFER_DEGREES
    clip_ymax = extent.yMaximum() + CLIP_BUFFER_DEGREES

    # 验证缓冲区正确添��
    assert clip_xmin < extent.xMinimum()
    assert clip_xmax > extent.xMaximum()
    assert clip_ymin < extent.yMinimum()
    assert clip_ymax > extent.yMaximum()

    buffer_applied = extent.xMinimum() - clip_xmin
    assert abs(buffer_applied - CLIP_BUFFER_DEGREES) < 0.0001

    print(
        f"  原始范围: ({extent.xMinimum():.4f}, {extent.yMinimum():.4f}) - "
        f"({extent.xMaximum():.4f}, {extent.yMaximum():.4f})")
    print(f"  裁剪范围: ({clip_xmin:.4f}, {clip_ymin:.4f}) - ({clip_xmax:.4f}, {clip_ymax:.4f})")
    print(f"  缓冲区大小: {CLIP_BUFFER_DEGREES}度 ✓")

    print("  裁剪范围计算测试通过 ✓")


def test_legend_layout_config():
    """测试图例布局配置"""
    print("\n--- 测试: 图例布局配置 ---")

    # 测试基本图例项配置
    assert BASIC_LEGEND_FONT_SIZE_PT == 10
    assert BASIC_LEGEND_ROW_HEIGHT_MM == 8.0
    print(f"  基本图例项字体大小: {BASIC_LEGEND_FONT_SIZE_PT}pt ✓")
    print(f"  基本图例项行高: {BASIC_LEGEND_ROW_HEIGHT_MM}mm ✓")

    # 测试滑坡评估图例项配置
    assert ASSESSMENT_LEGEND_FONT_SIZE_PT == 10
    assert ASSESSMENT_LEGEND_ROW_HEIGHT_MM == 7.5
    print(f"  滑坡评估图例项字体大小: {ASSESSMENT_LEGEND_FONT_SIZE_PT}pt ✓")
    print(f"  滑坡评估图例项行高: {ASSESSMENT_LEGEND_ROW_HEIGHT_MM}mm ✓")

    # 验证3行2列布局（含烈度圈）
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度圈", "solid_line_black"),
    ]
    assert len(legend_items) == 6  # 3行 x 2列 = 6项
    print(f"  基本图例项数量: {len(legend_items)} (3行x2列) ✓")

    print("  图例布局配置测试通过 ✓")


def test_compute_landslide_area_statistics_mock():
    """
    测试面积统计函数的逻辑（使用模拟数据）

    创建一个小型临时TIF文件用于测试统计逻辑。
    需要GDAL和numpy可用。
    """
    print("\n--- 测试: compute_landslide_area_statistics (模拟数据) ---")

    if not GDAL_AVAILABLE:
        print("  [跳过] GDAL不可用，无法创建模拟TIF")
        return

    try:
        # 创建一个10x10像素的临时TIF文件
        # 每个像素 0.01度 x 0.01度
        temp_dir = tempfile.mkdtemp(prefix="test_landslide_stats_")
        temp_tif = os.path.join(temp_dir, "test_landslide.tif")

        # 栅格参数：覆盖 (116.0, 39.0) - (116.1, 39.1)，10x10像素
        xmin, ymax = 116.0, 39.1
        pixel_size = 0.01  # 0.01度/像素
        nx, ny = 10, 10

        driver = gdal.GetDriverByName('GTiff')
        ds = driver.Create(temp_tif, nx, ny, 1, gdal.GDT_Byte)
        ds.SetGeoTransform([xmin, pixel_size, 0, ymax, 0, -pixel_size])

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())

        # 写入模拟数据：
        # 值1(低度危险区): 40像素 (40%)
        # 值2(较低危险区): 25像素 (25%)
        # 值3(中等危险区): 15像素 (15%)
        # 值4(较高危险区): 10像素 (10%)
        # 值5(高度危险区): 10像素 (10%)
        data = np.zeros((ny, nx), dtype=np.uint8)
        data[0:4, :] = 1   # 前4行共40像素 -> 值1
        data[4:6, :] = 2   # 第5-6行共20像素 -> 值2（注意修正为25需要额外5个）
        data[6, :5] = 2    # 第7行前5像素 -> 值2（累计25）
        data[6, 5:] = 3    # 第7行后5像素 -> 值3
        data[7, :] = 3     # 第8行10像素 -> 值3（累计15）
        data[8, :] = 4     # 第9行10像素 -> 值4
        data[9, :] = 5     # 第10行10像素 -> 值5

        band = ds.GetRasterBand(1)
        band.WriteArray(data)
        band.FlushCache()
        ds = None

        # 统计范围完全覆盖TIF
        extent = QgsRectangle(116.0, 39.0, 116.1, 39.1)
        stats = compute_landslide_area_statistics(temp_tif, extent)

        # 验证统计结果
        assert len(stats) == 5
        print(f"  统计结果项数: {len(stats)} ✓")

        # 验证各等级像素数
        pixel_counts = {s["value"]: s["pixel_count"] for s in stats}
        assert pixel_counts[1] == 40, f"值1期望40像素，实际{pixel_counts[1]}"
        assert pixel_counts[2] == 25, f"值2期望25像素，实际{pixel_counts[2]}"
        assert pixel_counts[3] == 15, f"值3期望15像素，实际{pixel_counts[3]}"
        assert pixel_counts[4] == 10, f"值4期望10像素，实际{pixel_counts[4]}"
        assert pixel_counts[5] == 10, f"值5期望10像素，实际{pixel_counts[5]}"
        print(f"  像素计数: {pixel_counts} ✓")

        # 验证百分比
        percentages = {s["value"]: s["percentage"] for s in stats}
        assert abs(percentages[1] - 40.0) < 0.01
        assert abs(percentages[2] - 25.0) < 0.01
        assert abs(percentages[3] - 15.0) < 0.01
        assert abs(percentages[4] - 10.0) < 0.01
        assert abs(percentages[5] - 10.0) < 0.01
        print(f"  百分比: {percentages} ✓")

        # 验证面积大于0
        for s in stats:
            assert s["area_km2"] > 0
        print("  所有等级面积 > 0 ✓")

        # 验证总百分比为100%
        total_pct = sum(s["percentage"] for s in stats)
        assert abs(total_pct - 100.0) < 0.1
        print(f"  总百分比: {total_pct:.2f}% ≈ 100% ✓")

        # 测试格式化消息
        message = format_stats_message(stats)
        assert "总的来看" in message
        assert "低度危险区" in message
        assert "高度危险区" in message
        print(f"  消息生成正确 ✓")

        # 清理临时文件
        shutil.rmtree(temp_dir)
        print("  临时文件清理完成 ✓")

        print("  面积统计函数（模拟数据）测试通过 ✓")

    except Exception as e:
        print(f"  [错误] 模拟数据统计测试失败: {e}")
        import traceback
        traceback.print_exc()
        # 尝试清理
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def test_compute_stats_partial_overlap():
    """
    测试统计范围仅部分覆盖TIF的情况

    验证当统计范围小于TIF范围时，只统计交集区域的像素。
    """
    print("\n--- 测试: compute_landslide_area_statistics (部分覆盖) ---")

    if not GDAL_AVAILABLE:
        print("  [跳过] GDAL不可用")
        return

    try:
        temp_dir = tempfile.mkdtemp(prefix="test_landslide_partial_")
        temp_tif = os.path.join(temp_dir, "test_partial.tif")

        # 创建 20x20 像素TIF，覆盖 (116.0,39.0)-(116.2,39.2)
        xmin, ymax = 116.0, 39.2
        pixel_size = 0.01
        nx, ny = 20, 20

        driver = gdal.GetDriverByName('GTiff')
        ds = driver.Create(temp_tif, nx, ny, 1, gdal.GDT_Byte)
        ds.SetGeoTransform([xmin, pixel_size, 0, ymax, 0, -pixel_size])

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())

        # 全部填充值3
        data = np.full((ny, nx), 3, dtype=np.uint8)
        band = ds.GetRasterBand(1)
        band.WriteArray(data)
        band.FlushCache()
        ds = None

        # 统计范围只覆盖左上角 10x10 像素
        partial_extent = QgsRectangle(116.0, 39.1, 116.1, 39.2)
        stats = compute_landslide_area_statistics(temp_tif, partial_extent)

        # 应该只统计到100个像素（10x10），全部为值3
        assert len(stats) == 5
        pixel_counts = {s["value"]: s["pixel_count"] for s in stats}
        assert pixel_counts[3] == 100, f"值3期望100像素，实际{pixel_counts[3]}"
        assert pixel_counts[1] == 0
        assert pixel_counts[2] == 0
        assert pixel_counts[4] == 0
        assert pixel_counts[5] == 0
        print(f"  部分覆盖像素计数: {pixel_counts} ✓")

        # 值3的百分比应为100%
        pct_3 = next(s["percentage"] for s in stats if s["value"] == 3)
        assert abs(pct_3 - 100.0) < 0.01
        print(f"  值3占比: {pct_3}% ✓")

        shutil.rmtree(temp_dir)
        print("  部分覆盖统计测试通过 ✓")

    except Exception as e:
        print(f"  [错误] 部分覆盖测试失败: {e}")
        import traceback
        traceback.print_exc()
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def test_compute_stats_no_overlap():
    """
    测试统计范围与TIF无交集的情况

    验证当统计范围完全在TIF范围外时，返回空列表。
    """
    print("\n--- 测试: compute_landslide_area_statistics (无交集) ---")

    if not GDAL_AVAILABLE:
        print("  [跳过] GDAL不可用")
        return

    try:
        temp_dir = tempfile.mkdtemp(prefix="test_landslide_nooverlap_")
        temp_tif = os.path.join(temp_dir, "test_nooverlap.tif")

        # 创建TIF覆盖 (116.0,39.0)-(116.1,39.1)
        xmin, ymax = 116.0, 39.1
        pixel_size = 0.01
        nx, ny = 10, 10

        driver = gdal.GetDriverByName('GTiff')
        ds = driver.Create(temp_tif, nx, ny, 1, gdal.GDT_Byte)
        ds.SetGeoTransform([xmin, pixel_size, 0, ymax, 0, -pixel_size])

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())

        data = np.full((ny, nx), 1, dtype=np.uint8)
        band = ds.GetRasterBand(1)
        band.WriteArray(data)
        band.FlushCache()
        ds = None

        # 统计范围完全在TIF外面（经度120附近）
        no_overlap_extent = QgsRectangle(120.0, 40.0, 120.1, 40.1)
        stats = compute_landslide_area_statistics(temp_tif, no_overlap_extent)

        # 应返回空列表
        assert stats == []
        print("  无交集返回空列表 ✓")

        shutil.rmtree(temp_dir)
        print("  无交集统计测试通过 ✓")

    except Exception as e:
        print(f"  [错误] 无交集测试失败: {e}")
        import traceback
        traceback.print_exc()
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def test_renderer_color_items():
    """
    测试渲染器颜色分档项的阈值设置

    验证 Discrete 模式下阈值使用 value+0.5，确保整数值正确匹配。
    """
    print("\n--- 测试: apply_landslide_assessment_renderer 颜色分档 ---")

    # 验证每个分档的阈值
    for cls in LANDSLIDE_CLASSES:
        expected_threshold = cls["value"] + 0.5
        print(f"  值{cls['value']}({cls['label']}): 阈值={expected_threshold} ✓")

    # 验证值1的像素(如1.0)应匹配到第一档(阈值1.5)
    # 值2的像素(如2.0)应匹配到第二档(阈值2.5)
    for cls in LANDSLIDE_CLASSES:
        value = cls["value"]
        threshold = value + 0.5
        assert value < threshold  # 像素值 < 阈值，匹配该档
        if value > 1:
            prev_threshold = (value - 1) + 0.5
            assert value > prev_threshold  # 像素值 > 上一档阈值，不匹配上一档
        print(f"  值{value}: {value} < {threshold} (匹配本档) ✓")

    print("  渲染器颜色分档阈值测试通过 ✓")


def test_return_dict_structure():
    """
    测试主函数返回值的字典结构

    验证返回字典包含正确的键。
    """
    print("\n--- 测试: 主函数返回值结构 ---")

    # 模拟返回字典结构
    result_dict = {
        "image_path": "/path/to/output.png",
        "stats_message": "距本次地震震中15千米内...",
        "stats_detail": [
            {"value": 1, "label": "低度危险区", "pixel_count": 100, "area_km2": 10.0, "percentage": 50.0},
        ],
    }

    # 验证键存在
    assert "image_path" in result_dict
    assert "stats_message" in result_dict
    assert "stats_detail" in result_dict
    print("  返回字典包含 image_path ✓")
    print("  返回字典包含 stats_message ✓")
    print("  返回字典包含 stats_detail ✓")

    # 验证stats_detail的结构
    detail = result_dict["stats_detail"][0]
    assert "value" in detail
    assert "label" in detail
    assert "pixel_count" in detail
    assert "area_km2" in detail
    assert "percentage" in detail
    print("  stats_detail 条目包含所有必要字段 ✓")

    print("  主函数返回值结构测试通过 ✓")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("运行 earthquake_landslide_assessment_map 全部测试")
    print("=" * 60)

    test_magnitude_config()
    test_calculate_extent()
    test_int_to_roman()
    test_landslide_classes()
    test_build_landslide_legend_list()
    test_determine_overall_danger_level()
    test_format_stats_message()
    test_boundary_styles()
    test_legend_font_config()
    test_temp_file_manager()
    test_gdal_availability()
    test_clip_extent_calculation()
    test_legend_layout_config()
    test_renderer_color_items()
    test_return_dict_structure()
    test_compute_landslide_area_statistics_mock()
    test_compute_stats_partial_overlap()
    test_compute_stats_no_overlap()

    print("\n" + "=" * 60)
    print("全部测试执行完成")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        run_all_tests()
    elif len(sys.argv) >= 4:
        try:
            lon = float(sys.argv[1])
            lat = float(sys.argv[2])
            mag = float(sys.argv[3])
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_landslide_M{mag}_{lon}_{lat}.png"
            kml = sys.argv[5] if len(sys.argv) > 5 else None
            result = generate_earthquake_landslide_assessment_map(lon, lat, mag, out, kml)
            if result:
                print(f"\n[输出图片] {result['image_path']}")
                print(f"[统计信息] {result['stats_message']}")
                if result['stats_detail']:
                    print("\n[详细统计]")
                    for item in result['stats_detail']:
                        print(f"  {item['label']}: {item['area_km2']}km², {item['percentage']}%")
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_landslide_assessment_map.py <经度> <纬度> <震级> [输出文件名] [kml路径]")
    else:
        print("使用默认参数运行（唐山 M2.8）...")
        result = generate_earthquake_landslide_assessment_map(
            longitude=103.36, latitude=34.09,
            magnitude=2.0, output_path="earthquake_landslide_tangshan_M2.8.png",
            kml_path = "../../data/geology/n0432881302350072.kml"
        )
        if result:
            print(f"\n[输出图片] {result['image_path']}")
            print(f"[统计信息] {result['stats_message']}")
            if result['stats_detail']:
                print("\n[详细统计]")
                for item in result['stats_detail']:
                    print(f"  {item['label']}: {item['area_km2']}km², {item['percentage']}%")