# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震滑坡危险性评估图生成脚本
参考 earthquake_newmark_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

危险性评估图说明：
- 读取Dn.tif文件获取Newmark位移值
- 对每个栅格像素按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算危险性概率
- Dn <= 0.1cm 的栅格直接判定为不危险（P=0）
- 使用自然断点法（Jenks）将概率值分为5类：低度危险区、较低危险区、中等危险区、较高危险区、高度危险区
- 颜色从绿色向红色过渡
- 图例色块分开显示
- 统计各危险等级面积和占比并返回（基于烈度圈最外圈范围）

优化说明：
- 针对大文件TIF进行优化，只裁剪加载需要范围内的数据
- 先用GDAL计算危险性概率栅格，再加载到QGIS中渲染
- 显著减少内存占用和处理时间
- 只加载天地图矢量注记图层（放置在最上层），不加载矢量底图
- 支持通过烈度.kml文件定义统计范围（最外圈烈度圈）
- 烈度圈掩膜改用 GDAL RasterizeLayer 实现，亿级像素场景从分钟级降到秒级

CRS支持说明：
- 支持任意投影的 Dn.tif（地理坐标 EPSG:4326 或投影坐标 EPSG:326xx UTM 等）
- 动态获取输入栅格 CRS，不再硬编码假定 EPSG:4326
- 面积统计方式根据 CRS 类型自动切换：
  - 投影坐标（米）：直接用像素物理面积 abs(pixel_width * pixel_height)
  - 地理坐标（度）：按 cos(纬度) 换算为平方千米
- 烈度多边形坐标（EPSG:4326）在投影栅格下自动 reproject 到栅格 CRS
- 保持向后兼容：当 Dn.tif 仍是 EPSG:4326 时，所有功能行为不变
"""

import os
import sys
import math
import time
import logging
import tempfile
import shutil
import requests
import re
import xml.etree.ElementTree as ET
from PIL import Image
from io import BytesIO

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
logger = logging.getLogger('report.core.earthquake_hazard_map')

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
    QgsFeatureRequest,
    QgsField,
    QgsLayoutExporter,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsCoordinateTransform,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# GDAL导入（用于栅格裁剪、读取数据和危险性概率计算）
try:
    from osgeo import gdal, osr, ogr
    import numpy as np
    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False
    print("[警告] GDAL模块未找到，将使用备用方案加载栅格")

# ============================================================
# 常量定义
# ============================================================

# 天地图配置
TIANDITU_TK = '1ef76ef90c6eb961cb49618f9b1a399d'

# 数据文件路径（优先从 Django settings 读取）
_DEFAULT_BASE = "../../data/geology/"

DN_TIF_PATH =""
# DN_TIF_PATH ='../../data/geology/ia/Dn.tif'
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
MAP_TOTAL_WIDTH_MM = 220.0          # 布局总宽度（毫米）
LEGEND_WIDTH_MM = 50.0              # 图例区域宽度（毫米）
BORDER_LEFT_MM = 4.0                # 左边框宽度（毫米）
BORDER_TOP_MM = 4.0                 # 上边框宽度（毫米）
BORDER_BOTTOM_MM = 2.0              # 下边框宽度（毫米）
BORDER_RIGHT_MM = 1.0               # 右边框宽度（毫米）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM  # 地图区域宽度

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
BORDER_WIDTH_MM = 0.35              # 图框线宽（毫米）

# === 指北针尺寸常量 ===
NORTH_ARROW_WIDTH_MM = 12.0         # 指北针宽度（毫米）
NORTH_ARROW_HEIGHT_MM = 18.0        # 指北针高度（毫米）

# === 经纬度字体(pt) ===
LONLAT_FONT_SIZE_PT = 8             # 经纬度注记字体大小（磅）

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)      # 省界颜色
PROVINCE_LINE_WIDTH_MM = 0.4                # 省界线宽（毫米）
PROVINCE_LABEL_FONT_SIZE_PT = 8             # 省名标注字体大小（磅）
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)   # 省名标注颜色
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6   # 省份质心与震中坐标重合判断容差

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)          # 市界颜色
CITY_LINE_WIDTH_MM = 0.24                   # 市界线宽（毫米）
CITY_DASH_GAP_MM = 0.3                      # 市界虚线间距（毫米）
CITY_DASH_PATTERN = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]  # 市界虚线样式

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)        # 县界颜色
COUNTY_LINE_WIDTH_MM = 0.14                 # 县界线宽（毫米）
COUNTY_DASH_GAP_MM = 0.2                    # 县界虚线间距（毫米）
COUNTY_DASH_PATTERN = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]  # 县界虚线样式

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9                 # 地级市名称标注字体大小（磅）
CITY_LABEL_COLOR = QColor(0, 0, 0)          # 地级市名称颜色

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 12              # 图例标题字体大小（磅）
LEGEND_ITEM_FONT_SIZE_PT = 10               # 图例项字体大小（磅）

# === 基本图例项配置 ===
BASIC_LEGEND_FONT_SIZE_PT = 10              # 基本图例项字体大小（磅）
BASIC_LEGEND_ROW_HEIGHT_MM = 8.0            # 基本图例项行高（毫米）

# === 危险性图例项配置 ===
HAZARD_LEGEND_ITEM_FONT_SIZE_PT = 10        # 危险性图例项字体大小（磅）
HAZARD_LEGEND_ROW_HEIGHT_MM = 7.5           # 危险性图例项行高（毫米，色块高度）
HAZARD_LEGEND_GAP_MM = 1.5                  # 危险性图例色块之间的间距（毫米，分开显示）

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8                      # 比例尺字体大小（磅）

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 5.0                # 震中五角星大小（毫米）
EPICENTER_COLOR = QColor(255, 0, 0)         # 震中五角星颜色
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)  # 震中五角星描边颜色
EPICENTER_STROKE_WIDTH_MM = 0.4             # 震中五角星描边宽度（毫米）

# === 危险性等级配置 ===
# 5类危险性等级名称（从低到高）
HAZARD_LEVEL_NAMES = [
    "低度危险区",
    "较低危险区",
    "中等危险区",
    "较高危险区",
    "高度危险区",
]

# 5类危险性等级颜色（从绿色向红色过渡）
HAZARD_COLORS = [
    QColor(0, 168, 0),      # 第1档 - 深绿色（低度危险）
    QColor(140, 210, 0),    # 第2档 - 黄绿色（较低危险）
    QColor(255, 210, 0),    # 第3档 - 黄色（中等危险）
    QColor(255, 100, 0),    # 第4档 - 橙色（较高危险）
    QColor(200, 0, 0),      # 第5档 - 深红色（高度危险）
]

# Dn阈值：Dn <= 此值时直接判定为不危险（不参与危险性等级判定）
DN_SAFE_THRESHOLD = 0.1     # 单位：cm

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# === 裁剪缓冲区（度） ===
CLIP_BUFFER_DEGREES = 0.1   # 在目标范围外增加缓冲区（度），确保边缘数据完整

# 1度对应的米数（近似），用于将度数缓冲区转换为投影坐标系（米）下的缓冲区
METERS_PER_DEGREE = 111000.0

# === 图例字体 ===
LEGEND_FONT_TIMES_NEW_ROMAN = "Times New Roman"  # 数字标签字体

# EPSG:4326 空间参考（模块级常量，避免重复创建）
if GDAL_AVAILABLE:
    _SRS_EPSG4326 = osr.SpatialReference()
    _SRS_EPSG4326.ImportFromEPSG(4326)
else:
    _SRS_EPSG4326 = None


# ============================================================
# 栅格 CRS 辅助函数（支持任意投影的 Dn.tif）
# ============================================================

def get_raster_srs(tif_path):
    """
    动态获取栅格文件的空间参考系统（SRS）。

    参数:
        tif_path (str): TIF文件路径（绝对路径）

    返回:
        osr.SpatialReference: 栅格的空间参考，若无投影信息则fallback到EPSG:4326
    """
    srs = osr.SpatialReference()
    if not GDAL_AVAILABLE:
        srs.ImportFromEPSG(4326)
        return srs

    try:
        ds = gdal.Open(tif_path, gdal.GA_ReadOnly)
        if ds is None:
            logger.warning('get_raster_srs: 无法打开栅格文件 %s，fallback到EPSG:4326', tif_path)
            srs.ImportFromEPSG(4326)
            return srs

        wkt = ds.GetProjection()
        ds = None

        if wkt:
            srs.ImportFromWkt(wkt)
            srs.AutoIdentifyEPSG()
            auth = srs.GetAuthorityCode(None)
            logger.info('get_raster_srs: 栅格 %s CRS = %s:%s', tif_path,
                        srs.GetAuthorityName(None), auth)
        else:
            logger.warning('get_raster_srs: 栅格 %s 无投影信息，fallback到EPSG:4326', tif_path)
            srs.ImportFromEPSG(4326)
    except Exception as exc:
        logger.warning('get_raster_srs: 读取CRS异常 %s，fallback到EPSG:4326: %s', tif_path, exc)
        srs.ImportFromEPSG(4326)

    return srs


def transform_extent_to_raster_crs(extent_4326, raster_srs):
    """
    将 EPSG:4326 下的地图范围（QgsRectangle）转换为栅格自身 CRS 下的范围。

    当栅格已是 EPSG:4326 时直接返回原始 extent；
    当栅格为投影坐标系（UTM 等）时，对四个角点及边中点进行坐标变换并取外接矩形。

    参数:
        extent_4326 (QgsRectangle): WGS84 地图范围
        raster_srs (osr.SpatialReference): 栅格的空间参考

    返回:
        QgsRectangle: 栅格 CRS 下的范围
    """
    if _SRS_EPSG4326 is not None and raster_srs.IsSame(_SRS_EPSG4326):
        return extent_4326
    if _SRS_EPSG4326 is None:
        return extent_4326

    raster_crs_qgs = QgsCoordinateReferenceSystem()
    wkt = raster_srs.ExportToWkt()
    raster_crs_qgs.createFromWkt(wkt)

    transform = QgsCoordinateTransform(CRS_WGS84, raster_crs_qgs, QgsProject.instance())

    xmin = extent_4326.xMinimum()
    xmax = extent_4326.xMaximum()
    ymin = extent_4326.yMinimum()
    ymax = extent_4326.yMaximum()
    xmid = (xmin + xmax) / 2.0
    ymid = (ymin + ymax) / 2.0

    sample_points = [
        QgsPointXY(xmin, ymin), QgsPointXY(xmin, ymax),
        QgsPointXY(xmax, ymin), QgsPointXY(xmax, ymax),
        QgsPointXY(xmin, ymid), QgsPointXY(xmax, ymid),
        QgsPointXY(xmid, ymin), QgsPointXY(xmid, ymax),
    ]

    xs = []
    ys = []
    for pt in sample_points:
        try:
            pt_transformed = transform.transform(pt)
            xs.append(pt_transformed.x())
            ys.append(pt_transformed.y())
        except Exception as exc:
            logger.warning('transform_extent_to_raster_crs: 坐标变换失败 %s: %s', pt, exc)

    if not xs:
        logger.warning('transform_extent_to_raster_crs: 所有点变换失败，返回原始extent')
        return extent_4326

    result = QgsRectangle(min(xs), min(ys), max(xs), max(ys))
    logger.info('transform_extent_to_raster_crs: WGS84 %s -> 栅格CRS %s',
                extent_4326.toString(), result.toString())
    return result


def transform_polygon_coords_to_raster_crs(coords_lonlat, raster_srs):
    """
    将 EPSG:4326 经纬度多边形坐标列表转换为栅格 CRS 下的坐标列表。

    当栅格已是 EPSG:4326 时直接返回原始坐标列表；
    当栅格为投影坐标系时，逐点进行坐标变换。

    参数:
        coords_lonlat (list): EPSG:4326 坐标列表 [(lon, lat), ...]
        raster_srs (osr.SpatialReference): 栅格的空间参考

    返回:
        list: 栅格 CRS 下的坐标列表 [(x, y), ...]
    """
    if not coords_lonlat:
        return coords_lonlat

    srs_4326_ref = _SRS_EPSG4326
    if srs_4326_ref is None:
        return coords_lonlat
    if raster_srs.IsSame(srs_4326_ref):
        return coords_lonlat

    raster_crs_qgs = QgsCoordinateReferenceSystem()
    wkt = raster_srs.ExportToWkt()
    raster_crs_qgs.createFromWkt(wkt)

    transform = QgsCoordinateTransform(CRS_WGS84, raster_crs_qgs, QgsProject.instance())

    transformed_coords = []
    for lon, lat in coords_lonlat:
        try:
            pt = transform.transform(QgsPointXY(lon, lat))
            transformed_coords.append((pt.x(), pt.y()))
        except Exception as exc:
            logger.warning('transform_polygon_coords_to_raster_crs: 坐标 (%s,%s) 变换失败: %s',
                           lon, lat, exc)
            transformed_coords.append((lon, lat))  # fallback到原始坐标

    logger.debug('transform_polygon_coords_to_raster_crs: %d个坐标点已转换到栅格CRS',
                 len(transformed_coords))
    return transformed_coords


# ============================================================
# KML 烈度圈解析函数
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析烈度.kml文件，提取各烈度圈的多边形坐标

    KML文件格式示例：
    <?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
    <Document>
    <Placemark><name>4度</name>
    <LineString><coordinates>lon1,lat1,0 lon2,lat2,0 ...</coordinates></LineString>
    </Placemark>
    ...
    </Document>
    </kml>

    参数:
        kml_path (str): KML文件路径

    返回:
        dict: 烈度等级到坐标列表的字典，格式为 {intensity_value: [(lon, lat), ...]}
              烈度值为整数，坐标为闭合多边形的顶点列表
              如果解析失败返回空字典
    """
    if not kml_path or not os.path.exists(kml_path):
        print(f"[警告] KML文件不存在: {kml_path}")
        return {}

    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()

        # 处理KML命名空间
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}

        # 尝试带命名空间查找
        placemarks = root.findall('.//kml:Placemark', ns)
        if not placemarks:
            # 尝试不带命名空间查找
            placemarks = root.findall('.//{http://www.opengis.net/kml/2.2}Placemark')
        if not placemarks:
            # 尝试直接查找
            placemarks = root.findall('.//Placemark')

        intensity_polygons = {}

        for placemark in placemarks:
            # 获取烈度名称（例如 "4度"、"5度"）
            name_elem = placemark.find('kml:name', ns)
            if name_elem is None:
                name_elem = placemark.find('{http://www.opengis.net/kml/2.2}name')
            if name_elem is None:
                name_elem = placemark.find('name')

            if name_elem is None or name_elem.text is None:
                continue

            name = name_elem.text.strip()
            # 提取烈度数值（支持 "4度"、"IV度" 等格式）
            intensity_value = _extract_intensity_value(name)
            if intensity_value is None:
                continue

            # 获取坐标字符串（支持LineString和Polygon）
            coords_elem = None
            for tag in ['LineString', 'Polygon', 'LinearRing']:
                coords_elem = placemark.find(f'.//kml:{tag}/kml:coordinates', ns)
                if coords_elem is None:
                    coords_elem = placemark.find(f'.//{{{ns["kml"]}}}{tag}/{{{ns["kml"]}}}coordinates')
                if coords_elem is None:
                    coords_elem = placemark.find(f'.//{tag}/coordinates')
                if coords_elem is not None:
                    break

            if coords_elem is None or coords_elem.text is None:
                continue

            # 解析坐标字符串
            coords_text = coords_elem.text.strip()
            coords = _parse_kml_coordinates(coords_text)

            if coords and len(coords) >= 3:
                # 确保多边形闭合
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                intensity_polygons[intensity_value] = coords
                print(f"[信息] 解析烈度圈: {name} (烈度值={intensity_value})，顶点数={len(coords)}")

        print(f"[信息] KML解析完成，共解析 {len(intensity_polygons)} 个烈度圈")
        return intensity_polygons

    except ET.ParseError as e:
        print(f"[错误] KML解析失败: {e}")
        return {}
    except Exception as e:
        logger.error('解析KML文件失败: %s', e, exc_info=True)
        print(f"[错误] 解析KML文件失败: {e}")
        return {}


def _extract_intensity_value(name_str):
    """
    从烈度名称字符串中提取烈度数值

    支持格式：
    - "4度"、"5度" 等阿拉伯数字格式
    - "IV度"、"V度" 等罗马数字格式
    - 纯数字 "4"、"5" 等

    参数:
        name_str (str): 烈度名称字符串

    返回:
        int 或 None: 烈度数值，无法解析返回None
    """
    if not name_str:
        return None

    # 尝试提取阿拉伯数字
    match = re.search(r'(\d+)', name_str)
    if match:
        return int(match.group(1))

    # 尝试解析罗马数字
    roman_map = {
        'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
        'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'XI': 11, 'XII': 12
    }
    upper_name = name_str.upper().replace('度', '').strip()
    if upper_name in roman_map:
        return roman_map[upper_name]

    return None


def _parse_kml_coordinates(coords_text):
    """
    解析KML坐标字符串为坐标列表

    坐标格式：lon1,lat1,alt1 lon2,lat2,alt2 ...
    （坐标点之间用空格或换行分隔，每个坐标内用逗号分隔经度、纬度、高度）

    参数:
        coords_text (str): KML坐标字符串

    返回:
        list: [(lon, lat), ...] 坐标元组列表
    """
    coords = []
    # 按空格或换行分割坐标点
    points = coords_text.split()
    for point in points:
        point = point.strip()
        if not point:
            continue
        parts = point.split(',')
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def get_outermost_intensity_polygon(intensity_polygons):
    """
    获取最外圈（烈度值最小）的烈度圈多边形

    参数:
        intensity_polygons (dict): parse_intensity_kml 返回的烈度圈字典

    返回:
        tuple: (intensity_value, coords_list) 或 (None, None)
               - intensity_value: 最小烈度值
               - coords_list: 多边形坐标列表 [(lon, lat), ...]
    """
    if not intensity_polygons:
        return None, None

    # 找到烈度值最小的圈（最外圈）
    min_intensity = min(intensity_polygons.keys())
    return min_intensity, intensity_polygons[min_intensity]


def create_polygon_geometry_from_coords(coords_list):
    """
    从坐标列表创建 QgsGeometry 多边形对象

    参数:
        coords_list (list): [(lon, lat), ...] 坐标元组列表

    返回:
        QgsGeometry 或 None: 多边形几何对象
    """
    if not coords_list or len(coords_list) < 3:
        return None

    # 构建 WKT 字符串
    wkt_coords = ', '.join([f'{lon} {lat}' for lon, lat in coords_list])
    wkt = f'POLYGON(({wkt_coords}))'

    try:
        geom = QgsGeometry.fromWkt(wkt)
        if geom.isGeosValid():
            return geom
        else:
            # 尝试修复无效几何
            geom = geom.makeValid()
            return geom if geom.isGeosValid() else None
    except Exception as e:
        print(f"[警告] 创建多边形几何失败: {e}")
        return None


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数（地图范围、比例尺等）

    参数:
        magnitude (float): 地震震级

    返回:
        dict: 包含 radius_km、map_size_km、scale 的配置字典
    """
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度（km）计算地图范围（WGS84坐标）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
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
    将相对路径转换为以当前脚本为基准的绝对路径

    参数:
        relative_path (str): 相对路径字符串

    返回:
        str: 绝对路径字符串
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字字符串

    参数:
        num (int): 阿拉伯数字（正整数）

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
        target_min (int): 期望最小刻度数
        target_max (int): 期望最大刻度数

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
        layer (QgsVectorLayer): 矢量图层对象
        candidates (list): 候选字段名列表（按优先级排列）

    返回:
        str: 找到的字段名，未找到返回None
    """
    fields = layer.fields()
    field_names = [f.name() for f in fields]
    # 精确匹配
    for candidate in candidates:
        if candidate in field_names:
            return candidate
    # 模糊匹配（不区分大小写）
    for candidate in candidates:
        for fn in field_names:
            if candidate.lower() in fn.lower():
                return fn
    # 回退：返回第一个字符串字段
    for f in fields:
        if f.type() == QVariant.String:
            return f.name()
    return None


# ============================================================
# 临时文件管理器
# ============================================================

class TempFileManager:
    """
    临时文件管理器

    用于管理处理过程中产生的临时文件，确保在处理完成后正确清理，避免磁盘空间浪费。
    """

    def __init__(self):
        """初始化临时文件管理器，创建空的文件列表和目录变量"""
        self.temp_dir = None
        self.temp_files = []

    def get_temp_dir(self):
        """
        获取临时目录路径，如不存在则创建

        返回:
            str: 临时目录绝对路径
        """
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="earthquake_hazard_")
            print(f"[信息] 创建临时目录: {self.temp_dir}")
        return self.temp_dir

    def get_temp_file(self, suffix=".tif"):
        """
        在临时目录中创建一个新的临时文件并返回其路径

        参数:
            suffix (str): 临时文件后缀，默认为 .tif

        返回:
            str: 临时文件绝对路径
        """
        temp_dir = self.get_temp_dir()
        fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
        os.close(fd)
        self.temp_files.append(temp_path)
        return temp_path

    def cleanup(self):
        """清理所有已登记的临时文件和临时目录"""
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


# 全局临时文件管理器实例
_temp_manager = TempFileManager()


def get_temp_manager():
    """
    获取全局临时文件管理器实例

    返回:
        TempFileManager: 全局唯一的临时文件管理器实例
    """
    return _temp_manager


# ============================================================
# 自然断点法（Jenks）工具函数
# ============================================================

# jenkspy 导入（优先使用，性能最优；不可用时自动降级为 numpy 向量化实现）
try:
    import jenkspy
    JENKSPY_AVAILABLE = True
except ImportError:
    JENKSPY_AVAILABLE = False
    print("[警告] jenkspy未安装，将使用内置numpy向量化实现。"
          "建议执行 pip install jenkspy 以获得最佳性能。")


def compute_jenks_breaks(data_flat, num_classes):
    """
    使用自然断点法（Jenks Natural Breaks）计算分类边界值

    优先使用 jenkspy 库（C/Rust底层实现，速度最快）；
    若 jenkspy 不可用则降级为内置 numpy 向量化 Fisher-Jenks 实现；
    若 numpy 也不可用则退化为等间距分类。

    jenkspy 说明：
        - 安装：pip install jenkspy
        - API：jenkspy.jenks_breaks(data, nb_class=n) -> list，长度为 nb_class+1
        - 底层为 C/Rust，对百万级数据也能在秒级内完成
        - 输入支持 list、numpy.ndarray、pandas.Series

    降采样策略（jenkspy 和 numpy 实现均适用）：
        - 超过 MAX_SAMPLES 时采用分层采样（按分位数分20层，每层等比例采样）
        - 分层采样比随机采样更好地保留数据分布的极端值和形态特征

    参数:
        data_flat (numpy.ndarray 或 list): 一维数组，包含所有有效像素的概率值
        num_classes (int): 分类数目（危险性等级数，通常为5）

    返回:
        list: 长度为 num_classes+1 的边界值列表（包含最小值和最大值）
              例如 [0.0, v1, v2, v3, v4, max_val]
    """
    # ----------------------------------------------------------------
    # 步骤1：基础校验与快速退出
    # ----------------------------------------------------------------
    if not GDAL_AVAILABLE:
        # numpy 不可用时退化为等间距分类
        print("[警告] numpy不可用，使用等间距分类代替自然断点法")
        try:
            min_val = float(min(data_flat))
            max_val = float(max(data_flat))
        except (TypeError, ValueError):
            return [0.0] * (num_classes + 1)
        if max_val <= min_val:
            return [min_val] * (num_classes + 1)
        step = (max_val - min_val) / num_classes
        return [min_val + i * step for i in range(num_classes + 1)]

    # 转为 numpy 数组以便后续统一处理
    # float32 既能满足概率值精度需求，又可减少内存占用（相比 float64 节省一半）
    if not isinstance(data_flat, np.ndarray):
        data_flat = np.asarray(data_flat, dtype=np.float32)
    elif data_flat.dtype != np.float32:
        # 显式拷贝为 float32，避免后续降采样修改原始数组
        data_flat = data_flat.astype(np.float32)

    n = len(data_flat)

    if n == 0:
        print("[警告] 输入数据为空，返回全零边界")
        return [0.0] * (num_classes + 1)

    # 先于排序计算真实极值（O(n) 扫描，比全量排序快得多）
    min_val = float(np.min(data_flat))
    max_val = float(np.max(data_flat))

    # 数据完全相同，无需分类
    if max_val <= min_val:
        print(f"[信息] 数据无变化（均为 {min_val:.6f}），返回相同边界")
        return [min_val] * (num_classes + 1)

    # 唯一值数量不足时直接用唯一值作边界（仅对小数组执行，避免大数组 unique 代价）
    MAX_UNIQUE_CHECK = 5000  # 超过此规模跳过 unique 检测，依赖后续 Jenks 自然处理
    if n <= MAX_UNIQUE_CHECK:
        unique_vals = np.unique(data_flat)
        if len(unique_vals) <= num_classes:
            print(f"[信息] 唯一值数({len(unique_vals)})<=分类数({num_classes})，直接使用唯一值作边界")
            breaks = [min_val]
            idx_step = max(1, len(unique_vals) // num_classes)
            for i in range(idx_step, len(unique_vals), idx_step):
                if len(breaks) < num_classes:
                    breaks.append(float(unique_vals[i]))
            while len(breaks) < num_classes:
                breaks.append(breaks[-1])
            breaks.append(max_val)
            return breaks

    # ----------------------------------------------------------------
    # 步骤2：降采样（在排序前执行，关键性能优化）
    #   原来流程：先排序 O(n log n)，再降采样；
    #   优化后：先均匀步长下采样 O(n)，再对小样本排序 O(m log m)，m << n，
    #   对百万像素级数据可提升性能一个数量级以上。
    # ----------------------------------------------------------------
    MAX_SAMPLES = 10000  # 降低到10000；jenkspy 在此规模下已足够稳定，进一步减少排序开销

    original_n = n
    if n > MAX_SAMPLES:
        # 均匀步长下采样（可重现，无随机性）：跳过排序直接在原始数据上采样
        step = max(1, n // MAX_SAMPLES)
        data_flat = data_flat[::step][:MAX_SAMPLES]
        n = len(data_flat)
        logger.debug('compute_jenks_breaks: 降采样 %d -> %d（步长 %d）', original_n, n, step)
        print(f"[信息] 降采样完成，样本数: {n}（原始: {original_n}）")

    # 只对降采样后的小数组排序（O(m log m)，m <= MAX_SAMPLES）
    if JENKSPY_AVAILABLE:
        data_sorted = np.sort(data_flat)  # 已是 float32
    else:
        data_sorted = np.sort(data_flat.astype(np.float64))
    n = len(data_sorted)

    # ----------------------------------------------------------------
    # 步骤3：调用 jenkspy 或内置实现计算自然断点
    # ----------------------------------------------------------------
    if JENKSPY_AVAILABLE:
        breaks = _compute_jenks_with_jenkspy(data_sorted, num_classes, min_val, max_val)
    else:
        breaks = _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    print(f"[信息] 自然断点法计算完成，边界值: {[f'{v:.4f}' for v in breaks]}")
    return breaks


def _compute_jenks_with_jenkspy(data_sorted, num_classes, min_val, max_val):
    """
    使用 jenkspy 库计算自然断点（C/Rust底层，性能最优）

    兼容性说明：
        jenkspy 各版本参数名不一致，通过运行时自动探测正确参数名来规避版本差异：
        - 优先尝试位置参数调用（所有版本均支持，最稳妥）
        - 若失败则依次尝试 n_classes=、nb_class= 关键字参数

    参数:
        data_sorted (numpy.ndarray): 已排序的一维 float32 数组
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的边界值列表
    """
    breaks = None
    last_exc = None

    # ---- 策略1：位置参数（所有版本均支持，最优先）----
    try:
        breaks = jenkspy.jenks_breaks(data_sorted, num_classes)
    except TypeError as exc:
        last_exc = exc

    # ---- 策略2：关键字参数 n_classes=（jenkspy 0.4.x 部分版本）----
    if breaks is None:
        try:
            breaks = jenkspy.jenks_breaks(data_sorted, n_classes=num_classes)
        except TypeError as exc:
            last_exc = exc

    # ---- 策略3：关键字参数 nb_class=（jenkspy 旧版本）----
    if breaks is None:
        try:
            breaks = jenkspy.jenks_breaks(data_sorted, nb_class=num_classes)
        except TypeError as exc:
            last_exc = exc

    # ---- 三种策略均失败时降级为 numpy 实现 ----
    if breaks is None:
        print(f"[警告] jenkspy 所有调用方式均失败，降级为 numpy 实现。最后异常: {last_exc}")
        return _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    # ---- 后处理：统一格式化结果 ----
    breaks = [float(b) for b in breaks]

    # jenkspy 返回长度应为 num_classes+1，校验一下
    if len(breaks) != num_classes + 1:
        print(f"[警告] jenkspy 返回边界数({len(breaks)})与期望({num_classes + 1})不符，降级为 numpy 实现")
        return _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    # 确保首尾与实际数据范围严格一致（规避浮点转换误差）
    breaks[0] = min_val
    breaks[-1] = max_val

    print(f"[信息] 使用 jenkspy 计算自然断点成功（样本数: {len(data_sorted)}）")
    return _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val)


def _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val):
    """
    使用 numpy 向量化 Fisher-Jenks 动态规划计算自然断点（jenkspy 不可用时的降级实现）

    核心优化：通过前缀和在 O(1) 内计算任意区间的加权组内平方差（SSD），
    将算法整体复杂度从 O(n²·k) 降至 O(n·k)。

    SSD(i, j) = Σx²[i..j] - (Σx[i..j])² / count(i,j)

    参数:
        data_sorted (numpy.ndarray): 已排序的一维 float32 数组
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的边界值列表
    """
    # numpy 降级路径的激进下采样上限（O(n²·k) 复杂度，n 大时极慢）
    # 即使上层已降采样到 MAX_SAMPLES，这里仍做二次保护，确保 numpy 路径在合理时间完成
    MAX_NUMPY_SAMPLES = 2000
    if len(data_sorted) > MAX_NUMPY_SAMPLES:
        step = max(1, len(data_sorted) // MAX_NUMPY_SAMPLES)
        data_sorted = data_sorted[::step][:MAX_NUMPY_SAMPLES]
        min_val = float(data_sorted[0])
        max_val = float(data_sorted[-1])
        print(f"[信息] numpy降级路径：进一步下采样至 {len(data_sorted)} 个样本")

    n = len(data_sorted)
    x = data_sorted.astype(np.float64)

    # 前缀和预计算（长度 n+1，首元素为0）
    cum_x = np.zeros(n + 1, dtype=np.float64)
    cum_x2 = np.zeros(n + 1, dtype=np.float64)
    np.cumsum(x, out=cum_x[1:])
    np.cumsum(x * x, out=cum_x2[1:])

    def interval_ssd(i_arr, j_scalar):
        """
        向量化计算多个区间 [i_arr[t], j_scalar] 的加权组内平方差

        参数:
            i_arr (numpy.ndarray): 区间起始位置数组（0-indexed）
            j_scalar (int): 区间终止位置（0-indexed，含）

        返回:
            numpy.ndarray: 与 i_arr 等长的 SSD 值数组
        """
        counts = j_scalar - i_arr + 1
        sum_x = cum_x[j_scalar + 1] - cum_x[i_arr]
        sum_x2 = cum_x2[j_scalar + 1] - cum_x2[i_arr]
        ssd = sum_x2 - (sum_x * sum_x) / counts
        return np.maximum(ssd, 0.0)

    # 初始化 DP 表
    dp = np.full((num_classes + 1, n), np.inf, dtype=np.float64)
    back = np.zeros((num_classes + 1, n), dtype=np.int32)

    # k=1 时：dp[1][j] = SSD(0, j)
    for j in range(n):
        dp[1, j] = interval_ssd(np.array([0], dtype=np.int32), j)[0]
    back[1, :] = 0

    # k=2..num_classes 递推
    for k in range(2, num_classes + 1):
        for j in range(k - 1, n):
            m_arr = np.arange(k - 1, j + 1, dtype=np.int32)
            ssd_k = interval_ssd(m_arr, j)
            prev_dp = dp[k - 1, m_arr - 1]
            total_ssd = prev_dp + ssd_k
            best_idx = int(np.argmin(total_ssd))
            dp[k, j] = total_ssd[best_idx]
            back[k, j] = m_arr[best_idx]

    # 反向追踪分割点
    split_points = []
    k = num_classes
    j = n - 1
    while k >= 2:
        m = int(back[k, j])
        split_points.append(m)
        j = m - 1
        k -= 1
    split_points.reverse()

    # 构建边界值（使用相邻元素均值作为边界）
    breaks = [min_val]
    for m in split_points:
        boundary = float((x[m - 1] + x[m]) / 2.0)
        breaks.append(boundary)
    breaks.append(max_val)

    return _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val)


def _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val):
    """
    后处理：确保边界值列表严格单调递增，长度为 num_classes+1

    处理规则：
    - 去除重复或逆序边界（在上一边界基础上微小递增）
    - 若清理后边界数不足，在间距最大处插入中间值
    - 确保首尾为原始数据的最小值和最大值

    参数:
        breaks (list): 原始边界值列表
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的严格单调递增边界值列表
    """
    # 去除重复或逆序值
    cleaned = [breaks[0]]
    for b in breaks[1:]:
        if b > cleaned[-1]:
            cleaned.append(b)
        else:
            cleaned.append(cleaned[-1] + 1e-9)

    # 补充不足的边界（在间距最大处插入中间值）
    while len(cleaned) < num_classes + 1:
        gaps = [cleaned[i + 1] - cleaned[i] for i in range(len(cleaned) - 1)]
        max_gap_idx = int(np.argmax(gaps))
        mid = (cleaned[max_gap_idx] + cleaned[max_gap_idx + 1]) / 2.0
        cleaned.insert(max_gap_idx + 1, mid)

    # 确保首尾正确
    cleaned[0] = min_val
    cleaned[-1] = max_val

    return cleaned


# ============================================================
# 危险性概率计算核心函数
# ============================================================

def calculate_hazard_probability(dn_value, a, b, c):
    """
    按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算单个像素的滑坡危险性概率

    注意：Dn <= DN_SAFE_THRESHOLD（0.1cm）的像素直接返回0（不危险）。

    参数:
        dn_value (float): Newmark位移值（cm），单个像素值
        a (float): 公式参数 a
        b (float): 公式参数 b（通常为负值，使概率随Dn增大而增大）
        c (float): 公式参数 c（指数参数）

    返回:
        float: 危险性概率值，范围 [0, 1]
    """
    if dn_value <= DN_SAFE_THRESHOLD:
        return 0.0
    try:
        prob = a * (1.0 - math.exp(b * (dn_value ** c)))
        # 将概率值限制在 [0, 1] 范围内
        return max(0.0, min(1.0, prob))
    except (OverflowError, ValueError):
        return 0.0


def compute_hazard_raster(dn_array, nodata_value, a, b, c):
    """
    对整个Dn栅格数组逐像素计算危险性概率（向量化计算，效率高）

    处理规则：
    - 无效值（NoData）直接判定为不危险，概率为 0
    - Dn <= DN_SAFE_THRESHOLD 的像素概率为 0
    - 其余像素按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算

    参数:
        dn_array (numpy.ndarray): Dn值二维数组（从TIF文件读取）
        nodata_value (float 或 None): 无效值标记，None表示无NoData设置
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c

    返回:
        numpy.ndarray: 与输入同形状的危险性概率二维浮点数组
                       所有值为 [0, 1]，NoData和Dn<=0.1cm的位置值为0
    """
    # 创建输出数组，初始化为0（所有像素默认为不危险）
    prob_array = np.zeros(dn_array.shape, dtype=np.float32)

    # 构建有效像素掩膜（排除NoData值）
    if nodata_value is not None:
        # 使用容差比较，防止浮点精度问题
        valid_mask = np.abs(dn_array - nodata_value) > 1e-6
    else:
        valid_mask = ~np.isnan(dn_array)

    # NoData位置概率已经是0（初始化值），无需额外处理
    # Dn <= 阈值 的有效像素概率也是0（初始化值），无需额外处理

    # 只需处理危险区：Dn > 阈值 的有效像素进行公式计算
    hazard_mask = valid_mask & (dn_array > DN_SAFE_THRESHOLD)

    if np.any(hazard_mask):
        # 提取危险区像素（已是 float32，无需类型转换）
        dn_hazard = dn_array[hazard_mask]
        if dn_hazard.dtype != np.float32:
            dn_hazard = dn_hazard.astype(np.float32)

        # 原地 float32 计算：P(f) = a * (1 - exp(b * dn^c))
        # 使用 out= 参数复用同一缓冲区，减少中间数组分配
        buf = np.empty(dn_hazard.shape, dtype=np.float32)
        np.power(dn_hazard, np.float32(c), out=buf)         # buf = dn^c
        buf *= np.float32(b)                                 # buf = b * dn^c
        np.clip(buf, -500.0, 500.0, out=buf)                # 防止指数运算溢出
        np.exp(buf, out=buf)                                 # buf = exp(b * dn^c)
        buf *= np.float32(-1.0)                              # buf = -exp(b * dn^c)
        buf += np.float32(1.0)                               # buf = 1 - exp(b * dn^c)
        buf *= np.float32(a)                                 # buf = a * (1 - exp(b * dn^c))
        np.clip(buf, 0.0, 1.0, out=buf)                     # 限制在 [0, 1]
        prob_array[hazard_mask] = buf

    return prob_array


def generate_hazard_tif(dn_tif_path, output_tif_path, extent, a, b, c,
                        buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    读取Dn.tif，裁剪到目标范围，计算危险性概率并保存为新的GeoTIFF文件

    该函数是危险性评估图的核心处理函数：
    1. 动态获取 Dn.tif 的 CRS，将 EPSG:4326 的 extent 转换为栅格 CRS 下的范围
    2. 使用GDAL裁剪Dn.tif到目标范围（带缓冲区）
    3. 逐像素按公式计算危险性概率
    4. 将结果保存为GeoTIFF（Float32格式），保留原始 CRS 和 GeoTransform

    支持任意投影的 Dn.tif（地理坐标 EPSG:4326 或投影坐标 EPSG:326xx UTM 等）。

    参数:
        dn_tif_path (str): Dn.tif文件绝对路径
        output_tif_path (str): 输出危险性概率TIF文件路径
        extent (QgsRectangle): 目标地图范围（WGS84坐标）
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c
        buffer_degrees (float): 裁剪缓冲区大小（度），默认0.1度

    返回:
        tuple: (output_tif_path, max_dn_value, prob_array_2d, geotransform)
               - output_tif_path: 成功返回输出文件路径，失败返回None
               - max_dn_value: 范围内Dn最大值（float），失败返回None
               - prob_array_2d: 二维概率数组，用于后续统计
               - geotransform: 地理变换参数元组（栅格CRS坐标系下）
    """
    if not GDAL_AVAILABLE:
        print("[错误] GDAL不可用，无法生成危险性栅格")
        return None, None, None, None

    if not os.path.exists(dn_tif_path):
        print(f"[错误] Dn.tif文件不存在: {dn_tif_path}")
        return None, None, None, None

    try:
        # 打开源栅格文件
        src_ds = gdal.Open(dn_tif_path, gdal.GA_ReadOnly)
        if src_ds is None:
            print(f"[错误] 无法打开Dn.tif: {dn_tif_path}")
            return None, None, None, None

        # 获取地理变换参数和基本信息
        gt = src_ds.GetGeoTransform()
        src_proj = src_ds.GetProjection()
        src_width = src_ds.RasterXSize
        src_height = src_ds.RasterYSize

        # 动态获取栅格 CRS，将 WGS84 extent 转换为栅格 CRS 下的范围
        raster_srs = get_raster_srs(dn_tif_path)
        extent_in_raster_crs = transform_extent_to_raster_crs(extent, raster_srs)
        logger.info('generate_hazard_tif: 栅格CRS extent=%s', extent_in_raster_crs.toString())

        if raster_srs.IsProjected():
            buffer_units = buffer_degrees * METERS_PER_DEGREE
        else:
            buffer_units = buffer_degrees

        # 计算带缓冲区的裁剪范围（在栅格CRS坐标下）
        clip_xmin = extent_in_raster_crs.xMinimum() - buffer_units
        clip_xmax = extent_in_raster_crs.xMaximum() + buffer_units
        clip_ymin = extent_in_raster_crs.yMinimum() - buffer_units
        clip_ymax = extent_in_raster_crs.yMaximum() + buffer_units

        # 将栅格CRS坐标转换为像素坐标
        px_xmin = int((clip_xmin - gt[0]) / gt[1])
        px_xmax = int((clip_xmax - gt[0]) / gt[1]) + 1

        # 根据 y 分辨率符号区分北上（gt[5]<0）和南上（gt[5]>0）影像，
        # 正确推导行索引（避免当 gt[5]>0 时 ymin/ymax 计算反转）
        if gt[5] < 0:
            # 北上影像（常规）：y_origin 在左上角，行号向下递增
            px_ymin = int((clip_ymax - gt[3]) / gt[5])
            px_ymax = int((clip_ymin - gt[3]) / gt[5]) + 1
        else:
            # 南上影像（罕见）：y_origin 在左下角，行号向上递增
            px_ymin = int((clip_ymin - gt[3]) / gt[5])
            px_ymax = int((clip_ymax - gt[3]) / gt[5]) + 1

        # 确保像素坐标在有效范围内
        px_xmin = max(0, min(src_width - 1, px_xmin))
        px_xmax = max(px_xmin + 1, min(src_width, px_xmax))
        px_ymin = max(0, min(src_height - 1, px_ymin))
        px_ymax = max(px_ymin + 1, min(src_height, px_ymax))

        read_width = px_xmax - px_xmin
        read_height = px_ymax - px_ymin

        print(f"[信息] 读取Dn栅格范围: ({px_xmin},{px_ymin}) - ({px_xmax},{px_ymax}), 尺寸: {read_width}x{read_height}")

        # 读取指定范围的Dn数据
        band = src_ds.GetRasterBand(1)
        nodata_value = band.GetNoDataValue()
        dn_array = band.ReadAsArray(px_xmin, px_ymin, read_width, read_height)

        if dn_array is None:
            print("[错误] 无法读取Dn栅格数据")
            src_ds = None
            return None, None, None, None

        # 仅在必要时转换 dtype（避免已是 float32 时的冗余拷贝）
        if dn_array.dtype != np.float32:
            dn_array = dn_array.astype(np.float32)

        # 计算范围内Dn最大值（用于统计报告）
        # 使用 np.nanmax + np.where 避免布尔索引产生大数组拷贝
        if nodata_value is not None:
            max_dn_value = float(np.nanmax(
                np.where(np.abs(dn_array - nodata_value) > 1e-6, dn_array, np.nan)))
        else:
            max_dn_value = float(np.nanmax(dn_array))

        # 当所有有效像素均为 NaN（即全是 NoData）时快速退出
        if np.isnan(max_dn_value):
            print("[警告] 范围内没有有效Dn数据")
            src_ds = None
            return None, None, None, None

        print(f"[信息] 范围内Dn最大值: {max_dn_value:.4f} cm")

        # 计算危险性概率栅格
        print(f"[信息] 计算危险性概率，参数: a={a}, b={b}, c={c}")
        prob_array = compute_hazard_raster(dn_array, nodata_value, a, b, c)

        # 计算输出栅格的地理变换参数（基于裁剪后的左上角坐标，保留原始CRS）
        out_x_origin = gt[0] + px_xmin * gt[1]
        out_y_origin = gt[3] + px_ymin * gt[5]
        out_gt = (out_x_origin, gt[1], gt[2], out_y_origin, gt[4], gt[5])

        # 创建输出GeoTIFF（Float32格式），保留原始 CRS 和 GeoTransform
        driver = gdal.GetDriverByName('GTiff')
        out_ds = driver.Create(
            output_tif_path,
            read_width, read_height, 1,
            gdal.GDT_Float32,
            options=['COMPRESS=LZW', 'TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256']
        )

        if out_ds is None:
            print(f"[错误] 无法创建输出危险性栅格文件: {output_tif_path}")
            src_ds = None
            return None, None, None, None

        out_ds.SetGeoTransform(out_gt)
        # 使用源文件的投影坐标系（保持原始CRS，不假定EPSG:4326）
        if src_proj:
            out_ds.SetProjection(src_proj)
        else:
            # 默认使用WGS84
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            out_ds.SetProjection(srs.ExportToWkt())

        out_band = out_ds.GetRasterBand(1)
        out_band.WriteArray(prob_array)
        out_band.FlushCache()

        out_ds = None
        src_ds = None

        print(f"[信息] 危险性概率栅格已保存: {output_tif_path}")
        # 返回二维数组和地理变换参数，用于后续基于烈度圈的统计
        return output_tif_path, max_dn_value, prob_array, out_gt

    except Exception as exc:
        logger.error('生成危险性栅格失败: %s', exc, exc_info=True)
        print(f"[错误] 生成危险性栅格失败: {exc}")
        raise


# ============================================================
# 危险性等级分类与渲染
# ============================================================

def classify_hazard_levels(prob_flat, num_classes=5):
    """
    使用自然断点法对危险性概率值进行5类分级，返回各类边界值

    分类规则：
    - 概率值 = 0 的像素（Dn <= 0.1cm）归入第1类（低度危险）
    - 概率值 > 0 的像素按自然断点法分为5类

    参数:
        prob_flat (numpy.ndarray): 所有有效像素的概率值一维数组（已排除NoData）
        num_classes (int): 分类数目，默认5

    返回:
        list: 长度为 num_classes+1 的边界值列表（包含0和最大概率值）
              例如 [0.0, v1, v2, v3, v4, max_prob]
    """
    if prob_flat is None or len(prob_flat) == 0:
        print("[警告] 无有效概率数据，使用等间距分类")
        return [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

    # 提取大于0的概率值（Dn > 0.1cm 的危险像素）
    nonzero_probs = prob_flat[prob_flat > 0]

    if len(nonzero_probs) == 0:
        print("[信息] 所有有效像素概率均为0（Dn <= 0.1cm），不存在危险区")
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    print(f"[信息] 参与Jenks分类的非零概率像素数: {len(nonzero_probs)}")

    # 对非零概率值执行自然断点法分类
    breaks = compute_jenks_breaks(nonzero_probs, num_classes)

    # 确保第一个边界为0（包含零概率像素在第1类中）
    breaks[0] = 0.0

    return breaks


def apply_hazard_renderer(raster_layer, breaks):
    """
    为危险性概率栅格图层应用离散色彩渲染器（5类危险等级颜色）

    使用 QgsColorRampShader.Discrete 离散渲染，每类使用对应颜色。
    NoData值（-1.0）不参与渲染。

    参数:
        raster_layer (QgsRasterLayer): 危险性概率栅格图层
        breaks (list): 长度为6的边界值列表（由 classify_hazard_levels 返回）

    返回:
        bool: 渲染器应用是否成功
    """
    if raster_layer is None or not raster_layer.isValid():
        print("[错误] 无效的栅格图层，无法应用危险性渲染器")
        return False

    if breaks is None or len(breaks) != 6:
        print("[错误] 无效的边界值列表（长度应为6）")
        return False

    try:
        shader = QgsRasterShader()
        color_ramp_shader = QgsColorRampShader()
        # 使用离散分类（每个色阶对应一个等级）
        color_ramp_shader.setColorRampType(QgsColorRampShader.Discrete)

        color_ramp_items = []
        # 5个等级，每个等级对应 breaks[i] ~ breaks[i+1] 的范围
        for i in range(5):
            upper_bound = breaks[i + 1]
            color = HAZARD_COLORS[i]
            label = HAZARD_LEVEL_NAMES[i]
            item = QgsColorRampShader.ColorRampItem(upper_bound, color, label)
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

        print("[信息] 危险性栅格渲染器设置完成，使用5档分类")
        return True

    except Exception as exc:
        logger.error('应用危险性渲染器失败: %s', exc, exc_info=True)
        print(f"[错误] 应用危险性渲染器失败: {exc}")
        raise


# ============================================================
# 面积统计函数（基于烈度圈范围）
# ============================================================

def calculate_area_statistics_with_intensity(prob_array_2d, breaks, geotransform,
                                              intensity_polygon_coords, extent,
                                              raster_srs=None):
    """
    统计各危险等级的面积（平方公里）和占比（百分比），基于烈度圈最外圈范围

    面积计算方式根据栅格 CRS 自动选择：
    - 投影坐标（米，如 UTM）：pixel_area = abs(pixel_width * pixel_height)（单位 m²，转 km²）
    - 地理坐标（度，如 EPSG:4326）：按 cos(纬度) × 111km/度 换算

    当 raster_srs 为投影坐标系时，intensity_polygon_coords（EPSG:4326 经纬度）
    会在 _create_polygon_mask 中自动 reproject 到栅格 CRS，再计算掩膜。

    参数:
        prob_array_2d (numpy.ndarray): 危险性概率二维数组
        breaks (list): 长度为6的危险等级边界值列表
        geotransform (tuple): 地理变换参数 (x_origin, x_res, 0, y_origin, 0, y_res)
                              坐标系为栅格 CRS
        intensity_polygon_coords (list): 烈度圈最外圈坐标列表 [(lon, lat), ...]（EPSG:4326）
        extent (QgsRectangle): 地图范围（EPSG:4326，用于地理坐标系下计算纬度中心点）
        raster_srs (osr.SpatialReference 或 None): 栅格的空间参考，
            None 时默认为地理坐标系（保持向后兼容）

    返回:
        dict: 包含各危险等级面积和占比的字典，键为等级名称，值为字典 {area_km2, percent}
              额外包含 'total_valid_km2' 表示总有效面积（烈度圈内）
    """
    if not GDAL_AVAILABLE or prob_array_2d is None:
        print("[警告] 无法计算面积统计，返回空结果")
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result

    try:
        # 根据栅格 CRS 选择面积计算方式
        x_res = abs(geotransform[1])
        y_res = abs(geotransform[5])

        if raster_srs is not None and raster_srs.IsProjected():
            # 投影坐标系（米）：直接用物理像素面积
            pixel_area_m2 = x_res * y_res
            pixel_area_km2 = pixel_area_m2 / 1e6
            logger.info('calculate_area_statistics_with_intensity: 投影CRS，像素物理面积=%.2f m² (%.8f km²)',
                        pixel_area_m2, pixel_area_km2)
            print(f"[信息] 投影坐标系，像素分辨率: {x_res:.2f}m x {y_res:.2f}m, 像素面积: {pixel_area_km2:.8f} km²")
        else:
            # 地理坐标系（度）：按 cos(纬度) 换算
            center_lat = (extent.yMinimum() + extent.yMaximum()) / 2.0
            pixel_width_km = x_res * 111.0 * math.cos(math.radians(center_lat))
            pixel_height_km = y_res * 111.0
            pixel_area_km2 = pixel_width_km * pixel_height_km
            logger.info('calculate_area_statistics_with_intensity: 地理CRS，像素分辨率=%.6f°x%.6f°，像素面积=%.6f km²',
                        x_res, y_res, pixel_area_km2)
            print(f"[信息] 地理坐标系，像素分辨率: {x_res:.6f}° x {y_res:.6f}°, 像素面积: {pixel_area_km2:.6f} km²")

        # 创建烈度圈多边形的掩膜（传入 raster_srs 以支持自动坐标变换）
        if intensity_polygon_coords and len(intensity_polygon_coords) >= 3:
            intensity_mask = _create_polygon_mask(
                prob_array_2d.shape, geotransform, intensity_polygon_coords,
                raster_srs=raster_srs)
            print(f"[信息] 烈度圈掩膜创建完成，圈内像素数: {np.sum(intensity_mask)}")
        else:
            # 无烈度圈时，使用所有像素
            print("[警告] 无有效烈度圈坐标，使用全部像素进行统计")
            intensity_mask = np.ones(prob_array_2d.shape, dtype=bool)

        # 统计烈度圈内的总像素数
        total_pixels_in_intensity = int(np.sum(intensity_mask))
        total_area_km2 = total_pixels_in_intensity * pixel_area_km2

        if total_pixels_in_intensity == 0:
            print("[警告] 烈度圈内无有效像素")
            result = {}
            for name in HAZARD_LEVEL_NAMES:
                result[name] = {'area_km2': 0.0, 'percent': 0.0}
            result['total_valid_km2'] = 0.0
            return result

        # 单次 np.digitize 在整个 prob_array_2d 上计算 bin 索引（int 数组），
        # 再用 intensity_mask 索引 int 数组做 bincount：
        # 好处：避免了 prob_array_2d[intensity_mask] 提取大型 float32 子数组的内存拷贝。
        # 虽然 bin_idx 全体大小与 prob_array_2d 相近（int64 8字节 vs float32 4字节），
        # 但关键在于掩膜内子集 bin_idx[mask] 是 int64（每元素 8 字节），而原代码的
        # prob_in_intensity 是 float32（每元素 4 字节）；前者少了一步 float 提取+digitize
        # 的双重拷贝，整体内存峰值更低，且 digitize 直接在连续全数组上运行向量化更高效。
        breaks_f32 = np.array(breaks[1:5], dtype=np.float32)
        bin_idx = np.digitize(prob_array_2d.ravel(), breaks_f32, right=True)
        # bin_idx 范围 [0, 4]，对应 5 个危险等级
        # 用 intensity_mask 对 int 数组做布尔索引，比对大 float 数组索引更节省内存
        counts_per_class = np.bincount(bin_idx[intensity_mask.ravel()], minlength=5)[:5]

        # 统计各等级像素数
        result = {}
        for i in range(5):
            count = int(counts_per_class[i])
            area_km2 = count * pixel_area_km2
            percent = (count / total_pixels_in_intensity * 100.0) if total_pixels_in_intensity > 0 else 0.0
            result[HAZARD_LEVEL_NAMES[i]] = {
                'area_km2': round(area_km2, 2),
                'percent': round(percent, 2),
            }

        result['total_valid_km2'] = round(total_area_km2, 2)

        # 打印统计结果
        print("[信息] 危险性等级面积统计（基于烈度圈范围）:")
        for name in HAZARD_LEVEL_NAMES:
            info = result[name]
            print(f"  {name}: {info['area_km2']:.2f} km² ({info['percent']:.2f}%)")
        print(f"  总面积（烈度圈内）: {result['total_valid_km2']:.2f} km²")

        return result

    except Exception as exc:
        logger.error('计算面积统计失败: %s', exc, exc_info=True)
        print(f"[错误] 计算面积统计失败: {exc}")
        # 返回空结果
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result


def _create_polygon_mask(array_shape, geotransform, polygon_coords, raster_srs=None):
    """
    创建多边形掩膜数组（判断每个像素是否在多边形内）

    优先使用 GDAL RasterizeLayer（C 层扫描线填充，亿级像素场景秒级完成）。
    GDAL 不可用时降级到 matplotlib.path.Path.contains_points（保留 bbox 子区域优化）。

    当 raster_srs 为投影坐标系（如 UTM）时，传入的 polygon_coords（EPSG:4326 经纬度）
    会先自动 reproject 到栅格 CRS，再进行栅格化。

    参数:
        array_shape (tuple): 数组形状 (rows, cols)
        geotransform (tuple): 地理变换参数（栅格CRS坐标系下）
        polygon_coords (list): 多边形坐标列表 [(lon, lat), ...]（EPSG:4326 经纬度）
        raster_srs (osr.SpatialReference 或 None): 栅格的空间参考，
            若为投影坐标系则将 polygon_coords 从 EPSG:4326 转换到栅格 CRS

    返回:
        numpy.ndarray: 布尔掩膜数组，True 表示像素在多边形内
    """
    rows, cols = array_shape
    x_origin, x_res, _, y_origin, _, y_res = geotransform

    # 当栅格为投影坐标系时，将 EPSG:4326 多边形坐标 reproject 到栅格 CRS
    if raster_srs is not None and raster_srs.IsProjected():
        polygon_coords_in_raster_crs = transform_polygon_coords_to_raster_crs(
            polygon_coords, raster_srs)
    else:
        polygon_coords_in_raster_crs = polygon_coords

    # ----------------------------------------------------------------
    # GDAL rasterize 路径（优先）：在 C 层做扫描线填充，亿级像素通常 1-3 秒
    # ----------------------------------------------------------------
    if GDAL_AVAILABLE:
        _t0 = time.time()
        try:
            # 构造 OGR Memory 数据源和多边形要素
            mem_ogr_driver = ogr.GetDriverByName('Memory')
            mem_ogr_ds = mem_ogr_driver.CreateDataSource('memdata')

            ogr_srs = osr.SpatialReference()
            if raster_srs is not None:
                ogr_srs.ImportFromWkt(raster_srs.ExportToWkt())
            else:
                ogr_srs.ImportFromEPSG(4326)

            mem_layer = mem_ogr_ds.CreateLayer(
                'polygon', srs=ogr_srs, geom_type=ogr.wkbPolygon)

            ring = ogr.Geometry(ogr.wkbLinearRing)
            for x, y in polygon_coords_in_raster_crs:
                ring.AddPoint(x, y)
            # 确保环闭合
            first = polygon_coords_in_raster_crs[0]
            last = polygon_coords_in_raster_crs[-1]
            if first[0] != last[0] or first[1] != last[1]:
                ring.AddPoint(first[0], first[1])

            polygon_geom = ogr.Geometry(ogr.wkbPolygon)
            polygon_geom.AddGeometry(ring)

            feature = ogr.Feature(mem_layer.GetLayerDefn())
            feature.SetGeometry(polygon_geom)
            mem_layer.CreateFeature(feature)
            feature = None

            # 创建内存栅格（Byte 单波段，尺寸与目标掩膜一致）
            mem_raster_driver = gdal.GetDriverByName('MEM')
            mem_raster = mem_raster_driver.Create('', cols, rows, 1, gdal.GDT_Byte)
            mem_raster.SetGeoTransform(geotransform)
            if raster_srs is not None:
                mem_raster.SetProjection(raster_srs.ExportToWkt())

            burn_band = mem_raster.GetRasterBand(1)
            burn_band.Fill(0)

            # 将多边形烧录为 1（C 层扫描线填充）
            gdal.RasterizeLayer(mem_raster, [1], mem_layer, burn_values=[1])

            result_array = burn_band.ReadAsArray()
            mask = result_array.astype(bool)

            # 释放资源
            mem_raster = None
            mem_ogr_ds = None

            _elapsed = time.time() - _t0
            logger.debug(
                '_create_polygon_mask: GDAL rasterize 完成，耗时 %.3fs，圈内像素数=%d',
                _elapsed, int(mask.sum()))
            return mask

        except Exception as exc:
            logger.warning(
                '_create_polygon_mask: GDAL rasterize 失败(%s)，降级到 matplotlib', exc)
            # 降级到 matplotlib 路径

    # ----------------------------------------------------------------
    # matplotlib 降级路径（GDAL 不可用或 rasterize 异常时使用）
    # 保留 bbox 子区域优化：仅在多边形外接矩形内做 contains_points 检测
    # ----------------------------------------------------------------
    try:
        from matplotlib.path import Path
    except ImportError:
        print("[警告] matplotlib 未安装，无法创建多边形掩膜，使用全部像素")
        return np.ones(array_shape, dtype=bool)

    # 创建多边形路径
    polygon_path = Path(polygon_coords_in_raster_crs)

    # 计算多边形外接矩形（bbox）对应的像素范围，
    # 仅在 bbox 内构造坐标网格，大幅减少 contains_points 的计算量
    poly_xs = [c[0] for c in polygon_coords_in_raster_crs]
    poly_ys = [c[1] for c in polygon_coords_in_raster_crs]
    bbox_xmin = min(poly_xs)
    bbox_xmax = max(poly_xs)
    bbox_ymin = min(poly_ys)
    bbox_ymax = max(poly_ys)

    # 将地理坐标 bbox 转换为栅格像素坐标范围
    col_start = max(0, int((bbox_xmin - x_origin) / x_res))
    col_end = min(cols, int((bbox_xmax - x_origin) / x_res) + 2)
    if y_res < 0:
        # 北上影像（常规）：y_origin 在左上角
        row_start = max(0, int((bbox_ymax - y_origin) / y_res))
        row_end = min(rows, int((bbox_ymin - y_origin) / y_res) + 2)
    else:
        # 南上影像（罕见）：y_origin 在左下角
        row_start = max(0, int((bbox_ymin - y_origin) / y_res))
        row_end = min(rows, int((bbox_ymax - y_origin) / y_res) + 2)

    # 初始化全栅格掩膜（默认 False），只对 bbox 区域做 contains_points 检测
    mask = np.zeros(array_shape, dtype=bool)

    if col_start >= col_end or row_start >= row_end:
        logger.debug('_create_polygon_mask: bbox 超出栅格范围，返回空掩膜')
        return mask

    # 仅在 bbox 对应的像素子区域内构造坐标网格（减少内存和计算量）
    col_indices = np.arange(col_start, col_end)
    row_indices = np.arange(row_start, row_end)
    xs = x_origin + (col_indices + 0.5) * x_res
    ys = y_origin + (row_indices + 0.5) * y_res

    # 创建网格
    x_grid, y_grid = np.meshgrid(xs, ys)
    # 展平为点列表
    points = np.column_stack([x_grid.ravel(), y_grid.ravel()])

    # 判断点是否在多边形内
    inside = polygon_path.contains_points(points)

    # 将 bbox 区域结果写入全栅格掩膜
    mask[row_start:row_end, col_start:col_end] = inside.reshape(
        row_end - row_start, col_end - col_start)

    return mask


def calculate_area_statistics(prob_array_flat, breaks, dn_tif_path, extent,
                               buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    统计各危险等级的面积（平方公里）和占比（百分比）（原版本，不使用烈度圈）

    面积计算方式根据栅格 CRS 自动选择：
    - 投影坐标（米，如 UTM）：pixel_area = abs(pixel_width * pixel_height)（单位 m²，转 km²）
    - 地理坐标（度，如 EPSG:4326）：按 cos(纬度) × 111km/度 换算

    参数:
        prob_array_flat (numpy.ndarray): 所有有效像素的概率值一维数组
        breaks (list): 长度为6的危险等级边界值列表
        dn_tif_path (str): Dn.tif文件路径（用于获取像素分辨率和CRS）
        extent (QgsRectangle): 地图范围（EPSG:4326，地理坐标系下用于计算纬度中心点）
        buffer_degrees (float): 裁剪缓冲区大小（度）

    返回:
        dict: 包含各危险等级面积和占比的字典，键为等级名称，值为字典 {area_km2, percent}
              额外包含 'total_valid_km2' 表示总有效面积
    """
    if not GDAL_AVAILABLE or prob_array_flat is None or len(prob_array_flat) == 0:
        print("[警告] 无法计算面积统计，返回空结果")
        # 返回空结果（各等级面积为0）
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result

    try:
        # 获取像素分辨率和 CRS，根据 CRS 选择面积计算方式
        pixel_area_km2 = 1.0  # 默认值，将在下方被真实值覆盖
        if os.path.exists(dn_tif_path):
            ds = gdal.Open(dn_tif_path, gdal.GA_ReadOnly)
            if ds is not None:
                gt = ds.GetGeoTransform()
                pixel_width = abs(gt[1])
                pixel_height = abs(gt[5])
                ds = None

                raster_srs = get_raster_srs(dn_tif_path)
                if raster_srs.IsProjected():
                    # 投影坐标系（米）：直接用物理像素面积
                    pixel_area_m2 = pixel_width * pixel_height
                    pixel_area_km2 = pixel_area_m2 / 1e6
                    logger.info('calculate_area_statistics: 投影CRS，像素物理面积=%.2f m² (%.8f km²)',
                                pixel_area_m2, pixel_area_km2)
                    print(f"[信息] 投影坐标系，像素分辨率: {pixel_width:.2f}m x {pixel_height:.2f}m, "
                          f"像素面积: {pixel_area_km2:.8f} km²")
                else:
                    # 地理坐标系（度）：按 cos(纬度) 换算
                    center_lat = (extent.yMinimum() + extent.yMaximum()) / 2.0
                    pixel_width_km = pixel_width * 111.0 * math.cos(math.radians(center_lat))
                    pixel_height_km = pixel_height * 111.0
                    pixel_area_km2 = pixel_width_km * pixel_height_km
                    logger.info('calculate_area_statistics: 地理CRS，像素分辨率=%.6f°x%.6f°，像素面积=%.6f km²',
                                pixel_width, pixel_height, pixel_area_km2)
                    print(f"[信息] 地理坐标系，像素分辨率: {pixel_width:.6f}° x {pixel_height:.6f}°, "
                          f"像素面积: {pixel_area_km2:.6f} km²")

        total_pixels = len(prob_array_flat)
        total_area_km2 = total_pixels * pixel_area_km2

        # 统计各等级像素数
        result = {}
        for i in range(5):
            lower = breaks[i]
            upper = breaks[i + 1]
            if i == 0:
                # 第1类包含等于0的像素（Dn <= 0.1cm）
                mask = prob_array_flat <= upper
            elif i == 4:
                # 最后一类包含大于上一级下界的所有像素
                mask = prob_array_flat > breaks[i]
            else:
                mask = (prob_array_flat > lower) & (prob_array_flat <= upper)

            count = int(np.sum(mask))
            area_km2 = count * pixel_area_km2
            percent = (count / total_pixels * 100.0) if total_pixels > 0 else 0.0
            result[HAZARD_LEVEL_NAMES[i]] = {
                'area_km2': round(area_km2, 2),
                'percent': round(percent, 2),
            }

        result['total_valid_km2'] = round(total_area_km2, 2)

        # 打印统计结果
        print("[信息] 危险性等级面积统计:")
        for name in HAZARD_LEVEL_NAMES:
            info = result[name]
            print(f"  {name}: {info['area_km2']:.2f} km² ({info['percent']:.2f}%)")
        print(f"  总有效面积: {result['total_valid_km2']:.2f} km²")

        return result

    except Exception as exc:
        logger.error('计算面积统计失败: %s', exc, exc_info=True)
        print(f"[错误] 计算面积统计失败: {exc}")
        # 返回空结果
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result


def build_statistics_summary(area_stats):
    """
    生成统计摘要文字描述

    格式：
    总得来看，低度危险区面积为XX平方千米，占比XX.XX%；较低危险区面积为XX平方千米，
    占比XX.XX%；中等危险区面积为XX平方千米，占比XX.XX%；较高危险区面积为X平方千米，
    占比XX.XX%；高度危险区面积为X平方千米，占比X.XX%

    面积格式化规则：
    - 面积四舍五入保留两位小数
    - 占比四舍五入保留两位小数

    参数:
        area_stats (dict): calculate_area_statistics 返回的统计字典

    返回:
        str: 格式化的统计摘要文字
    """
    summary = "总得来看，"

    parts = []
    for level_name in HAZARD_LEVEL_NAMES:
        info = area_stats.get(level_name, {'area_km2': 0.0, 'percent': 0.0})
        area_km2 = info['area_km2']
        percent = info['percent']
        # 面积格式化：极小值（<=0.01）保留4位小数，避免显示 "0.00" 产生误导
        area_str = f"{area_km2:.4f}" if 0 < area_km2 <= 0.01 else f"{area_km2:.2f}"
        parts.append(f"{level_name}面积为{area_str}平方千米，占比{percent:.2f}%")

    summary += "；".join(parts)
    return summary

# ============================================================
# 图层加载函数
# ============================================================

def load_vector_layer(shp_path, layer_name):
    """
    加载SHP矢量图层

    参数:
        shp_path (str): SHP文件路径（相对或绝对路径）
        layer_name (str): 图层显示名称

    返回:
        QgsVectorLayer 或 None: 加载成功的矢量图层，失败返回None
    """
    abs_path = resolve_path(shp_path) if not os.path.isabs(shp_path) else shp_path
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

def style_province_layer(layer, epicenter_lon=None, epicenter_lat=None, extent=None):
    """
    设置省界图层样式（仅边界线，不配置标注）

    当传入震中坐标时，省界多边形图层仅绘制边界线；
    省份标注由独立的点图层（通过 create_province_label_layer 创建）负责。

    参数:
        layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float 或 None): 震中经度，用于标注偏移判断
        epicenter_lat (float 或 None): 震中纬度，用于标注偏移判断
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))  # 填充透明
    fill_sl.setStrokeColor(PROVINCE_COLOR)
    fill_sl.setStrokeWidth(PROVINCE_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.SolidLine)

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    if epicenter_lon is None:
        # 无震中信息时，直接在省界图层上配置标注
        _setup_province_labels(layer)
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """
    设置市界图层样式（虚线边界，透明填充）

    参数:
        layer (QgsVectorLayer): 市界多边形图层
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
    print("[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """
    设置县界图层样式（虚线边界，透明填充）

    参数:
        layer (QgsVectorLayer): 县界多边形图层
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
    print("[信息] 县界图层样式设置完成")


def _setup_province_labels(layer):
    """
    配置省界图层标注（无偏移，直接在省界图层上启用）

    参数:
        layer (QgsVectorLayer): 省界多边形图层
    """
    field_name = _find_name_field(layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过标注设置")
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
    print(f"[信息] 省界标注已配置，字段: {field_name}")


def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent):
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移。

    当省份质心与震中坐标重合时，标注点向右下角偏移3mm，避免遮挡震中五角星标识。

    参数:
        province_layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float): 震中经度（度）
        epicenter_lat (float): 震中纬度（度）
        extent (QgsRectangle): 地图范围，用于计算偏移量（mm转度）

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
    lon_offset_deg = offset_mm / MAP_WIDTH_MM * map_width_deg   # 向右偏移（经度增大）
    lat_offset_deg = offset_mm / MAP_WIDTH_MM * map_height_deg  # 向下偏移（纬度减小）

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

    # 仅遍历与 extent 相交的要素，避免处理图幅外所有省份（全国级数据可能有30+省）
    request = QgsFeatureRequest()
    if extent is not None:
        request.setFilterRect(extent)

    for feat in province_layer.getFeatures(request):
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        centroid = geom.centroid()
        if centroid is None or centroid.isEmpty():
            continue
        cx = centroid.asPoint().x()
        cy = centroid.asPoint().y()

        px, py = cx, cy
        if abs(cx - epicenter_lon) < PROVINCE_EPICENTER_COINCIDENCE_TOL and abs(cy - epicenter_lat) < PROVINCE_EPICENTER_COINCIDENCE_TOL:
            # 质心与震中重合，向右下角偏移3mm
            px = cx + lon_offset_deg
            py = cy - lat_offset_deg
            offset_count += 1
            print(f"[信息] 省份标注偏移：质心({cx:.6f}, {cy:.6f}) -> 偏移后({px:.6f}, {py:.6f})")

        prov_name = feat[field_name]
        new_feat = QgsFeature(layer_fields)
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
        new_feat.setAttribute("province_name", prov_name)
        feats_to_add.append(new_feat)

    if feats_to_add:
        provider.addFeatures(feats_to_add)
    label_layer.updateExtents()

    print(f"[信息] 省份标注：共 {len(feats_to_add)} 个省份，其中 {offset_count} 个进行了偏移（向右下角3mm）")

    # 设置透明点符号（只显示标注文字）
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


# ============================================================
# 震中与辅助图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层（红色五角星+白色描边）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）

    返回:
        QgsVectorLayer: 震中点图层
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
    加载地级市点位图层并设置符号样式（同心圆标记）

    参数:
        extent (QgsRectangle): 地图范围（暂未使用，保留接口一致性）

    返回:
        QgsVectorLayer 或 None: 地级市点图层，文件不存在返回None
    """
    abs_path = resolve_path(CITY_POINTS_SHP_PATH) if not os.path.isabs(CITY_POINTS_SHP_PATH) else CITY_POINTS_SHP_PATH
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    symbol_size_mm = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0

    # 白色背景圆（最大）
    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 外圆（透明填充）
    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 内圆（实心黑色）
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
    print("[信息] 加载地级市点位图层完成")
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的内存线图层

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
    创建市界图例用的内存线图层（虚线样式）

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
    创建县界图例用的内存线图层（虚线样式）

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
# 布局创建函数
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale,
                        map_height_mm, breaks=None, ordered_layers=None):
    """
    创建QGIS打印布局，包含地图、指北针、比例尺、经纬度网格和图例

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图范围
        scale (int): 地图比例尺分母
        map_height_mm (float): 地图区域高度（毫米）
        breaks (list 或 None): 危险性等级边界值列表（6个值）
        ordered_layers (list 或 None): 按显示顺序排列的图层列表（顶层在前）

    返回:
        QgsPrintLayout: 创建好的打印布局对象
    """
    try:
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("地震滑坡危险性评估图")
        layout.setUnits(QgsUnitTypes.LayoutMillimeters)

        output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm, QgsUnitTypes.LayoutMillimeters))

        map_left = BORDER_LEFT_MM
        map_top = BORDER_TOP_MM

        # 创建地图项
        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(map_left, map_top, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, map_height_mm, QgsUnitTypes.LayoutMillimeters))
        map_item.setExtent(extent)
        map_item.setCrs(CRS_WGS84)
        map_item.setFrameEnabled(True)
        map_item.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
        map_item.setFrameStrokeColor(QColor(0, 0, 0))
        map_item.setBackgroundEnabled(True)
        map_item.setBackgroundColor(QColor(255, 255, 255))
        layout.addLayoutItem(map_item)

        # 设置图层顺序
        layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
        if layers_to_set:
            map_item.setLayers(layers_to_set)
            map_item.setKeepLayerSet(True)
        map_item.invalidateCache()

        # 添加各布局组件
        _setup_map_grid(map_item, extent)
        _add_north_arrow(layout, map_height_mm)
        _add_hazard_legend(layout, map_height_mm, output_height_mm, breaks,
                          scale=scale, extent=extent, center_lat=latitude)

        return layout

    except Exception as exc:
        logger.error('创建打印布局失败: %s', exc, exc_info=True)
        raise


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格（仅显示内侧刻度线和外侧注记）

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

    # 只在上边和左边显示经纬度注记
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
    在地图右上角添加指北针（SVG指北针图案）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        map_height_mm (float): 地图区域高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    # 创建并加载指北针SVG
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_hazard_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)




def _add_hazard_legend(layout, map_height_mm, output_height_mm, breaks=None,
                       scale=None, extent=None, center_lat=None):
    """
    添加危险性评估图图例区域

    图例结构：
    - 顶部标题"图  例"
    - 基础图例（3行2列）：震中、地级市、省界、市界、县界
    - 危险性等级图例（5个分开的色块，从低到高）
    - 底部：比例尺

    图例色块使用分开样式（每个色块之间有间距），各色块独立显示。

    参数:
        layout (QgsPrintLayout): 打印布局对象
        map_height_mm (float): 地图区域高度（毫米）
        output_height_mm (float): 布局总高度（毫米）
        breaks (list 或 None): 危险性等级边界值列表（6个值）
        scale (int 或 None): 比例尺分母（用于绘制比例尺）
        extent (QgsRectangle 或 None): 地图范围（用于计算比例尺）
        center_lat (float 或 None): 地图中心纬度（用于计算比例尺）
    """
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

    # 公共文本格式定义
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    basic_item_format = QgsTextFormat()
    basic_item_format.setFont(QFont("SimSun", BASIC_LEGEND_FONT_SIZE_PT))
    basic_item_format.setSize(BASIC_LEGEND_FONT_SIZE_PT)
    basic_item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    basic_item_format.setColor(QColor(0, 0, 0))

    hazard_label_format = QgsTextFormat()
    hazard_label_format.setFont(QFont("SimSun", HAZARD_LEGEND_ITEM_FONT_SIZE_PT))
    hazard_label_format.setSize(HAZARD_LEGEND_ITEM_FONT_SIZE_PT)
    hazard_label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    hazard_label_format.setColor(QColor(0, 0, 0))

    # 图例背景矩形
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

    # 标题标签
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

    # 基础图例项（3行2列布局）
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

    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
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
                            PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM)
        elif draw_type == "dash_line_city":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_line_county":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)

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

    # ---- 危险性等级图例（5个分开的色块）----
    if breaks is not None and len(breaks) == 6:
        hazard_section_start_y = top_legend_start_y + top_legend_height + 2.0

        # 危险性图例标题：使用SimHei字体
        hazard_title_format = QgsTextFormat()
        hazard_title_format.setFont(QFont("SimHei", 10))
        hazard_title_format.setSize(10)
        hazard_title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        hazard_title_format.setColor(QColor(0, 0, 0))

        hazard_title_label = QgsLayoutItemLabel(layout)
        hazard_title_label.setText("危险性等级")
        hazard_title_label.setTextFormat(hazard_title_format)
        hazard_title_label.attemptMove(
            QgsLayoutPoint(legend_x, hazard_section_start_y, QgsUnitTypes.LayoutMillimeters))
        hazard_title_label.attemptResize(QgsLayoutSize(legend_width, 5.0, QgsUnitTypes.LayoutMillimeters))
        hazard_title_label.setHAlign(Qt.AlignHCenter)
        hazard_title_label.setVAlign(Qt.AlignVCenter)
        hazard_title_label.setFrameEnabled(False)
        hazard_title_label.setBackgroundEnabled(False)
        layout.addLayoutItem(hazard_title_label)

        # 色块绘制参数
        colorbar_start_y = hazard_section_start_y + 6.0
        colorbar_width = 8.0  # 色块宽度（毫米）
        colorbar_height = HAZARD_LEGEND_ROW_HEIGHT_MM  # 单个色块高度（毫米）
        colorbar_gap = HAZARD_LEGEND_GAP_MM  # 色块之间间距（毫米，分开显示）
        colorbar_left_pad = 3.0  # 色块左边距（毫米）
        label_gap = 2.0  # 色块与标签之间间距（毫米）
        label_width = legend_width - colorbar_left_pad - colorbar_width - label_gap - 2.0

        # 检查总高度是否超出图例区域（需为比例尺预留空间），若超出则压缩色块高度
        scale_bar_reserve = 18.0 if scale is not None else 0.0
        total_needed = colorbar_height * 5 + colorbar_gap * 4
        available_height = legend_y + legend_height - colorbar_start_y - scale_bar_reserve - 2.0
        if total_needed > available_height and available_height > 0:
            # 按比例压缩色块高度和间距
            compress_ratio = available_height / total_needed
            colorbar_height = colorbar_height * compress_ratio
            colorbar_gap = colorbar_gap * compress_ratio
            print(f"[信息] 图例高度不足，压缩色块高度至 {colorbar_height:.2f}mm，间距至 {colorbar_gap:.2f}mm")

        # 逐个绘制5个分开的色块及对应的危险等级名称标签
        for i in range(5):
            color = HAZARD_COLORS[i]
            color_str = f"{color.red()},{color.green()},{color.blue()},255"

            # 每个色块的Y起始坐标（色块分开，之间有间距）
            box_y = colorbar_start_y + i * (colorbar_height + colorbar_gap)

            # 绘制色块矩形（带黑色细边框）
            color_box = QgsLayoutItemShape(layout)
            color_box.setShapeType(QgsLayoutItemShape.Rectangle)
            color_box.attemptMove(
                QgsLayoutPoint(legend_x + colorbar_left_pad, box_y, QgsUnitTypes.LayoutMillimeters))
            color_box.attemptResize(
                QgsLayoutSize(colorbar_width, colorbar_height, QgsUnitTypes.LayoutMillimeters))
            box_symbol = QgsFillSymbol.createSimple({
                'color': color_str,
                'outline_color': '80,80,80,255',
                'outline_width': '0.15',
                'outline_width_unit': 'MM',
            })
            color_box.setSymbol(box_symbol)
            color_box.setFrameEnabled(False)
            layout.addLayoutItem(color_box)

            # 绘制危险等级名称标签（垂直居中于色块）
            label_x = legend_x + colorbar_left_pad + colorbar_width + label_gap
            # 标签高度与色块高度一致，确保垂直居中
            name_label = QgsLayoutItemLabel(layout)
            name_label.setText(HAZARD_LEVEL_NAMES[i])
            name_label.setTextFormat(hazard_label_format)
            name_label.attemptMove(QgsLayoutPoint(label_x, box_y, QgsUnitTypes.LayoutMillimeters))
            name_label.attemptResize(QgsLayoutSize(label_width, colorbar_height, QgsUnitTypes.LayoutMillimeters))
            name_label.setHAlign(Qt.AlignLeft)
            name_label.setVAlign(Qt.AlignVCenter)
            name_label.setFrameEnabled(False)
            name_label.setBackgroundEnabled(False)
            layout.addLayoutItem(name_label)

        print(f"[信息] 危险性等级图例添加完成，共5个分开色块")
    else:
        print("[信息] 无有效危险性分级数据，跳过危险性图例")

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
    在图例指定位置绘制红色五角星图标

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 图标左边界X坐标（毫米）
        center_y (float): 图标垂直中心Y坐标（毫米）
        width (float): 图标区域宽度（毫米）
        height (float): 图标区域高度（毫米）
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
    在图例指定位置绘制地级市同心圆图标（白底外圆+黑色内实心圆）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 图标左边界X坐标（毫米）
        center_y (float): 图标垂直中心Y坐标（毫米）
        width (float): 图标区域宽度（毫米）
        height (float): 图标区域高度（毫米）
    """
    icon_size = min(width, height) * 0.6
    center_x = x + width / 2.0

    # 白底外圆
    outer_circle = QgsLayoutItemShape(layout)
    outer_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    outer_circle.attemptMove(
        QgsLayoutPoint(center_x - icon_size / 2.0, center_y - icon_size / 2.0, QgsUnitTypes.LayoutMillimeters))
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

    # 黑色内实心圆
    inner_size = icon_size * 0.4
    inner_circle = QgsLayoutItemShape(layout)
    inner_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    inner_circle.attemptMove(
        QgsLayoutPoint(center_x - inner_size / 2.0, center_y - inner_size / 2.0, QgsUnitTypes.LayoutMillimeters))
    inner_circle.attemptResize(QgsLayoutSize(inner_size, inner_size, QgsUnitTypes.LayoutMillimeters))
    inner_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    inner_circle.setSymbol(inner_symbol)
    inner_circle.setFrameEnabled(False)
    layout.addLayoutItem(inner_circle)


def _draw_line_icon(layout, x, center_y, width, color, line_width_mm):
    """
    在图例指定位置绘制实线图标

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 线段左起点X坐标（毫米）
        center_y (float): 线段垂直中心Y坐标（毫米）
        width (float): 线段长度（毫米）
        color (QColor): 线段颜色
        line_width_mm (float): 线段宽度（毫米）
    """
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    line_height = max(line_width_mm, 0.5)
    line_shape.attemptMove(
        QgsLayoutPoint(x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
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
    在图例指定位置绘制虚线图标（通过多个短矩形模拟虚线效果）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 虚线左起点X坐标（毫米）
        center_y (float): 虚线垂直中心Y坐标（毫米）
        width (float): 虚线总长度（毫米）
        color (QColor): 虚线颜色
        line_width_mm (float): 线段宽度（毫米）
        dash_gap_mm (float): 虚线间隔长度（毫米）
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
        dash_shape.attemptMove(
            QgsLayoutPoint(current_x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(
            QgsLayoutSize(actual_dash_length, line_height, QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)
        current_x += pattern_length


# ============================================================
# PNG导出函数
# ============================================================

def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片文件

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出PNG文件路径（相对或绝对路径）
        dpi (int): 输出分辨率，默认150 DPI

    返回:
        str 或 None: 成功返回输出文件绝对路径，失败返回None
    """
    try:
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

    except Exception as exc:
        logger.error('PNG导出异常: %s', exc, exc_info=True)
        raise


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_hazard_map(longitude, latitude, magnitude,
                                   a, b, c,
                                   output_path="output_hazard_map.png",
                                   dn_tif_path=None,
                                   intensity_kml_path=None):
    """
    生成地震滑坡危险性评估图（主入口函数）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        a (float): 危险性公式参数 a（P(f) = a * (1 - EXP(b * Dn^c))）
        b (float): 危险性公式参数 b
        c (float): 危险性公式参数 c
        output_path (str): 输出PNG文件路径，默认为 output_hazard_map.png
        dn_tif_path (str 或 None): Dn.tif文件路径，None时使用默认路径
        intensity_kml_path (str 或 None): 烈度.kml文件路径，用于定义统计范围
                                          None时使用全部像素进行统计

    返回:
        tuple: (output_image_path, max_dn_value, statistics_summary)
               - output_image_path: 输出PNG文件路径（失败为None）
               - max_dn_value: 最大Newmark位移值（cm），失败为None
               - statistics_summary: 统计摘要文字字符串
    """
    logger.info('开始生成危险性评估图: lon=%.4f lat=%.4f M=%.1f a=%s b=%s c=%s output=%s kml=%s',
                longitude, latitude, magnitude, a, b, c, output_path, intensity_kml_path)
    try:
        return _generate_earthquake_hazard_map_impl(
            longitude, latitude, magnitude, a, b, c, output_path, dn_tif_path, intensity_kml_path
        )
    except Exception as exc:
        logger.error('生成危险性评估图失败: %s', exc, exc_info=True)
        raise


def _compute_bbox_from_coords(coords):
    """
    根据坐标列表计算外接矩形（QgsRectangle）。

    参数:
        coords (list): 坐标列表 [(lon, lat), ...]

    返回:
        QgsRectangle 或 None（坐标为空时返回 None）
    """
    if not coords:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return QgsRectangle(min(lons), min(lats), max(lons), max(lats))


def _generate_earthquake_hazard_map_impl(longitude, latitude, magnitude,
                                         a, b, c,
                                         output_path, dn_tif_path, intensity_kml_path):
    """
    generate_earthquake_hazard_map 的内部实现函数

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c
        output_path (str): 输出PNG文件路径
        dn_tif_path (str 或 None): Dn.tif文件路径
        intensity_kml_path (str 或 None): 烈度.kml文件路径

    返回:
        tuple: (output_image_path, max_dn_value, statistics_summary)
    """
    print("=" * 60)
    print(f"[开始] 生成地震滑坡危险性评估图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  公式参数: a={a}, b={b}, c={c}")
    print(f"  输出: {output_path}")
    print(f"  烈度KML: {intensity_kml_path}")
    print(f"  GDAL可用: {GDAL_AVAILABLE}")
    print("=" * 60)

    # 确定Dn.tif文件路径
    tif_path = dn_tif_path if dn_tif_path else DN_TIF_PATH
    abs_tif_path = resolve_path(tif_path) if not os.path.isabs(tif_path) else tif_path

    # 解析烈度KML文件
    intensity_polygons = {}
    outermost_intensity_coords = None
    if intensity_kml_path:
        abs_kml_path = resolve_path(intensity_kml_path) if not os.path.isabs(intensity_kml_path) else intensity_kml_path
        intensity_polygons = parse_intensity_kml(abs_kml_path)
        if intensity_polygons:
            min_intensity, outermost_intensity_coords = get_outermost_intensity_polygon(intensity_polygons)
            if outermost_intensity_coords:
                print(f"[信息] 使用烈度圈 {min_intensity}度 作为统计范围，顶点数: {len(outermost_intensity_coords)}")
            else:
                logger.warning('无法获取KML最外圈烈度圈坐标，面积统计回退为全部像素统计: kml=%s', intensity_kml_path)
                print("[警告] 无法获取最外圈烈度圈坐标，将使用全部像素进行统计")
        else:
            logger.warning('KML解析结果为空，面积统计回退为全部像素统计: kml=%s', intensity_kml_path)
            print("[警告] KML解析结果为空，将使用全部像素进行统计")
    else:
        logger.warning('未提供烈度KML文件，面积统计将使用矩形extent范围（非烈度圈多边形）')

    # 获取震级配置（地图范围和比例尺）
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    # 计算地图范围（基于震中和震级，不受烈度圈影响）
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图显示范围(extent): {extent.toString()}")

    # 计算用于危险性栅格生成的范围（compute_extent），需覆盖显示范围和 KML 外圈
    skip_area_stats = False
    kml_bbox = _compute_bbox_from_coords(outermost_intensity_coords) if outermost_intensity_coords else None
    if kml_bbox is None:
        # 无 KML 外圈：直接使用显示范围
        compute_extent = extent
        print("[信息] 无烈度圈范围，compute_extent = extent")
    else:
        print(f"[信息] KML 外圈范围(kml_bbox): {kml_bbox.toString()}")
        if not extent.intersects(kml_bbox):
            # 无交集：使用显示范围，面积统计直接输出 0
            compute_extent = extent
            skip_area_stats = True
            print("[信息] 烈度圈范围与地图显示范围无交集，面积统计输出为 0")
        else:
            # 有交集：取并集
            compute_extent = QgsRectangle(extent)
            compute_extent.combineExtentWith(kml_bbox)
            print(f"[信息] 烈度圈与显示范围有交集，compute_extent(并集): {compute_extent.toString()}")

    # 计算地图像素高度
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 初始化QGIS（统一通过 QGISManager 管理）
    from core.qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    temp_manager = get_temp_manager()
    # 天地图注记临时文件路径
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_temp_annotation_hazard.png")
    # 指北针SVG临时文件路径
    svg_temp_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_north_arrow_hazard_temp.svg")

    # 初始化统计结果（用于异常时返回默认值）
    statistics_summary = "统计信息生成失败"
    output_image_path = None
    max_dn_value = None
    try:
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        # ---- 步骤1：下载天地图注记（可降级）----
        annotation_raster = None
        try:
            annotation_raster = download_tianditu_annotation_tiles(
                extent, width_px, height_px, temp_annotation_path)
        except Exception as exc:
            logger.warning('天地图注记下载失败，跳过注记图层: %s', exc)
            print(f"[警告] 天地图注记下载失败，跳过注记图层: {exc}")

        # ---- 步骤2：生成危险性概率栅格 ----
        hazard_layer = None
        breaks = None
        max_dn_value = None
        area_stats = {}
        prob_array_2d = None
        geotransform = None

        if not GDAL_AVAILABLE:
            print("[警告] GDAL不可用，无法生成危险性栅格，跳过危险性图层")
        elif not os.path.exists(abs_tif_path):
            print(f"[警告] Dn.tif文件不存在: {abs_tif_path}，跳过危险性图层")
        else:
            try:
                # 生成危险性概率TIF文件（裁剪范围 + 公式计算）
                hazard_tif_path = temp_manager.get_temp_file(suffix="_hazard_prob.tif")
                result_path, max_dn_value, prob_array_2d, geotransform = generate_hazard_tif(
                    abs_tif_path, hazard_tif_path, compute_extent, a, b, c)

                if result_path and prob_array_2d is not None:
                    # 展平数组用于Jenks分类
                    prob_flat = prob_array_2d.flatten()

                    # 使用自然断点法对概率值分级
                    breaks = classify_hazard_levels(prob_flat, num_classes=5)

                    # 加载危险性概率栅格图层并应用颜色渲染
                    hazard_layer = QgsRasterLayer(result_path, "危险性评估")
                    if hazard_layer.isValid():
                        apply_hazard_renderer(hazard_layer, breaks)
                        project.addMapLayer(hazard_layer)
                        print("[信息] 危险性评估栅格图层加载成功")
                    else:
                        print("[警告] 危险性评估栅格图层无效，跳过")
                        hazard_layer = None

                    # 计算各危险等级面积统计（基于烈度圈范围）
                    if outermost_intensity_coords and geotransform:
                        if skip_area_stats:
                            # 烈度圈与显示范围无交集，直接输出 0
                            area_stats = {name: {'area_km2': 0.0, 'percent': 0.0}
                                          for name in HAZARD_LEVEL_NAMES}
                            area_stats['total_valid_km2'] = 0.0
                        else:
                            # 动态获取 Dn.tif 的 CRS，传入面积统计函数
                            dn_raster_srs = get_raster_srs(abs_tif_path)
                            # 使用烈度圈范围进行统计（传入 compute_extent 保证坐标一致）
                            area_stats = calculate_area_statistics_with_intensity(
                                prob_array_2d, breaks, geotransform,
                                outermost_intensity_coords, compute_extent,
                                raster_srs=dn_raster_srs)
                    else:
                        # 无烈度圈时使用全部像素统计（原逻辑），面积基于矩形extent范围
                        logger.warning(
                            '未能获取烈度圈多边形坐标，面积统计回退为矩形extent范围（非多边形掩膜）: kml=%s',
                            intensity_kml_path
                        )
                        area_stats = calculate_area_statistics(
                            prob_flat, breaks, abs_tif_path, extent)
                else:
                    print("[警告] 危险性概率栅格生成失败或无有效数据")
                    area_stats = {name: {'area_km2': 0.0, 'percent': 0.0}
                                  for name in HAZARD_LEVEL_NAMES}
                    area_stats['total_valid_km2'] = 0.0

            except Exception as exc:
                logger.warning('生成危险性栅格失败，跳过: %s', exc)
                print(f"[警告] 生成危险性栅格失败，跳过: {exc}")
                area_stats = {name: {'area_km2': 0.0, 'percent': 0.0}
                              for name in HAZARD_LEVEL_NAMES}
                area_stats['total_valid_km2'] = 0.0

        # ---- 步骤3：加载矢量边界图层（可降级）----
        county_layer = None
        try:
            county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界_地图")
            if county_layer:
                style_county_layer(county_layer)
                project.addMapLayer(county_layer)
        except Exception as exc:
            logger.warning('加载县界图层失败，跳过: %s', exc)
            print(f"[警告] 加载县界图层失败，跳过: {exc}")

        city_layer = None
        try:
            city_layer = load_vector_layer(CITY_SHP_PATH, "市界_地图")
            if city_layer:
                style_city_layer(city_layer)
                project.addMapLayer(city_layer)
        except Exception as exc:
            logger.warning('加载市界图层失败，跳过: %s', exc)
            print(f"[警告] 加载市界图层失败，跳过: {exc}")

        province_layer = None
        try:
            province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界_地图")
            if province_layer:
                style_province_layer(province_layer, longitude, latitude, extent)
                project.addMapLayer(province_layer)
        except Exception as exc:
            logger.warning('加载省界图层失败，跳过: %s', exc)
            print(f"[警告] 加载省界图层失败，跳过: {exc}")

        # 创建省份标注点图层（支持震中附近偏移）
        province_label_layer = None
        if province_layer:
            try:
                province_label_layer = create_province_label_layer(
                    province_layer, longitude, latitude, extent)
                if province_label_layer:
                    project.addMapLayer(province_label_layer, False)
                    print(f"[信息] 省份标注图层已添加，要素数量: {province_label_layer.featureCount()}")
                else:
                    print("[警告] 省份标注图层创建失败，回退到直接配置标注")
                    _setup_province_labels(province_layer)
            except Exception as exc:
                logger.warning('创建省份标注图层失败: %s', exc)
                try:
                    _setup_province_labels(province_layer)
                except Exception as fallback_exc:
                    logger.warning('回退标注配置也失败: %s', fallback_exc)

        # ---- 步骤4：加载辅助点位和图例图层（可降级）----
        city_point_layer = None
        try:
            city_point_layer = create_city_point_layer(extent)
            if city_point_layer:
                project.addMapLayer(city_point_layer)
        except Exception as exc:
            logger.warning('加载地级市点位图层失败，跳过: %s', exc)
            print(f"[警告] 加载地级市点位图层失败，跳过: {exc}")

        province_legend_layer = None
        try:
            province_legend_layer = create_province_legend_layer()
            if province_legend_layer:
                project.addMapLayer(province_legend_layer)
        except Exception as exc:
            logger.warning('创建省界图例图层失败，跳过: %s', exc)

        city_legend_layer = None
        try:
            city_legend_layer = create_city_legend_layer()
            if city_legend_layer:
                project.addMapLayer(city_legend_layer)
        except Exception as exc:
            logger.warning('创建市界图例图层失败，跳过: %s', exc)

        county_legend_layer = None
        try:
            county_legend_layer = create_county_legend_layer()
            if county_legend_layer:
                project.addMapLayer(county_legend_layer)
        except Exception as exc:
            logger.warning('创建县界图例图层失败，跳过: %s', exc)

        # ---- 步骤6：创建震中图层 ----
        epicenter_layer = None
        try:
            epicenter_layer = create_epicenter_layer(longitude, latitude)
            if epicenter_layer:
                project.addMapLayer(epicenter_layer)
        except Exception as exc:
            logger.warning('创建震中图层失败，跳过: %s', exc)
            print(f"[警告] 创建震中图层失败，跳过: {exc}")

        # 注记图层最后加载（显示在最上层）
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # ---- 步骤7：设置图层显示顺序（顶层在列表前）----
        ordered_layers = [lyr for lyr in [
            epicenter_layer,  # 震中（最顶层）
            annotation_raster,  # 天地图注记
            city_point_layer,  # 地级市点位
            province_label_layer,  # 省份标注（独立点图层）
            province_layer,  # 省界
            city_layer,  # 市界
            county_layer,  # 县界
            hazard_layer,  # 危险性评估热力图（最底层）
        ] if lyr is not None]

        # ---- 步骤8：创建打印布局（关键步骤，失败则抛出异常）----
        try:
            layout = create_print_layout(
                project, longitude, latitude, magnitude,
                extent, scale, map_height_mm,
                breaks=breaks,
                ordered_layers=ordered_layers
            )
        except Exception as exc:
            logger.error('创建打印布局失败: %s', exc, exc_info=True)
            raise

        # ---- 步骤9：导出PNG（关键步骤，失败则抛出异常）----
        try:
            output_image_path = export_layout_to_png(layout, output_path, OUTPUT_DPI)
        except Exception as exc:
            logger.error('导出PNG失败: %s', exc, exc_info=True)
            raise

        # ---- 步骤10：生成统计摘要文字 ----
        try:
            statistics_summary = build_statistics_summary(area_stats)
            print(f"[信息] 统计摘要: {statistics_summary}")
        except Exception as exc:
            logger.warning('生成统计摘要失败: %s', exc)
            statistics_summary = "统计信息生成失败"

    finally:
        # 清理所有临时文件和目录
        temp_manager.cleanup()

        # 清理指北针SVG临时文件
        if os.path.exists(svg_temp_path):
            try:
                os.remove(svg_temp_path)
            except OSError:
                pass

        # 清理天地图注记临时文件（PNG + 世界文件）
        if os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw_path = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw_path):
                    os.remove(pgw_path)
            except OSError:
                pass

    print("=" * 60)
    if output_image_path:
        print(f"[完成] 危险性评估图已输出: {output_image_path}")
        print(f"[完成] 统计摘要: {statistics_summary}")
    else:
        print("[失败] 危险性评估图输出失败")
    print("=" * 60)

    return output_image_path, max_dn_value, statistics_summary


# ============================================================
# 测试方法
# ============================================================

def run_all_tests():
    """
    运行所有单元测试（不依赖QGIS环境的纯函数测试）

    测试内容包括：
    - 震级配置获取
    - 地图范围计算
    - 罗马数字转换
    - 危险性概率公式计算
    - 自然断点法分级
    - 面积统计摘要生成
    - 危险性概率栅格向量化计算
    - 刻度间隔选取
    - KML解析
    - 多边形掩膜 bbox 优化
    - np.digitize 分类计数
    - numpy降级路径下采样
    - gt[5] 符号处理（北上/南上影像像素坐标）
    """
    print("\n" + "=" * 60)
    print("运行 earthquake_hazard_map 全部测试")
    print("=" * 60)

    # ---- 测试1：震级配置获取 ----
    print("\n--- 测试1: get_magnitude_config ---")
    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000, f"期望150000，实际{config_s['scale']}"
    print(f"  M4.5 -> 比例尺1:{config_s['scale']} ✓")

    config_m = get_magnitude_config(6.5)
    assert config_m["scale"] == 500000, f"期望500000，实际{config_m['scale']}"
    print(f"  M6.5 -> 比例尺1:{config_m['scale']} ✓")

    config_l = get_magnitude_config(7.5)
    assert config_l["scale"] == 1500000, f"期望1500000，实际{config_l['scale']}"
    print(f"  M7.5 -> 比例尺1:{config_l['scale']} ✓")

    # ---- 测试2：地图范围计算 ----
    print("\n--- 测试2: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum(), "震中经度应在范围内"
    assert extent.yMinimum() < 39.9 < extent.yMaximum(), "震中纬度应在范围内"
    print(f"  15km半径范围计算正确 ✓")
    print(f"  范围: ({extent.xMinimum():.4f},{extent.yMinimum():.4f}) - "
          f"({extent.xMaximum():.4f},{extent.yMaximum():.4f})")

    # ---- 测试3：罗马数字转换 ----
    print("\n--- 测试3: int_to_roman ---")
    assert int_to_roman(4) == "IV", f"期望IV，实际{int_to_roman(4)}"
    assert int_to_roman(9) == "IX", f"期望IX，实际{int_to_roman(9)}"
    assert int_to_roman(8) == "VIII", f"期望VIII，实际{int_to_roman(8)}"
    print("  罗马数字转换正确: 4->IV, 9->IX, 8->VIII ✓")

    # ---- 测试4：危险性概率公式 ----
    print("\n--- 测试4: calculate_hazard_probability ---")
    # Dn <= 0.1cm 时概率为0
    p_safe = calculate_hazard_probability(0.05, a=0.335, b=-0.048, c=0.565)
    assert p_safe == 0.0, f"Dn=0.05cm时应为0，实际{p_safe}"
    print(f"  Dn=0.05cm (<=0.1): P={p_safe} ✓")

    p_safe2 = calculate_hazard_probability(0.1, a=0.335, b=-0.048, c=0.565)
    assert p_safe2 == 0.0, f"Dn=0.1cm时应为0，实际{p_safe2}"
    print(f"  Dn=0.10cm (=阈值): P={p_safe2} ✓")

    # Dn > 0.1cm 时概率 > 0
    p_hazard = calculate_hazard_probability(10.0, a=0.335, b=-0.048, c=0.565)
    assert p_hazard > 0.0, f"Dn=10cm时概率应>0，实际{p_hazard}"
    assert 0.0 <= p_hazard <= 1.0, f"概率应在[0,1]范围内，实际{p_hazard}"
    print(f"  Dn=10.0cm: P={p_hazard:.4f} ✓")

    # 验证概率值被限制在[0,1]
    p_clamped = calculate_hazard_probability(1000.0, a=0.335, b=-0.048, c=0.565)
    assert 0.0 <= p_clamped <= 1.0, f"极大Dn时概率应在[0,1]，实际{p_clamped}"
    print(f"  Dn=1000cm (极大值限制): P={p_clamped:.4f} ✓")

    # ---- 测试5：向量化危险性概率计算 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试5: compute_hazard_raster ---")
        test_dn = np.array([[0.0, 0.05, 0.1, 1.0, 10.0],
                            [50.0, -9999.0, 100.0, 0.08, 5.0]], dtype=np.float64)
        prob_result = compute_hazard_raster(test_dn, nodata_value=-9999.0,
                                            a=0.335, b=-0.048, c=0.565)

        # NoData位置应为0（判定为不危险）
        assert prob_result[1, 1] == 0.0, f"NoData位置应为0（不危险），实际{prob_result[1, 1]}"
        print(f"  NoData(-9999)位置: prob={prob_result[1, 1]}（判定为不危险）✓")

        # Dn <= 0.1cm 的位置应为0
        assert prob_result[0, 0] == 0.0, f"Dn=0位置应为0，实际{prob_result[0, 0]}"
        assert prob_result[0, 1] == 0.0, f"Dn=0.05位置应为0，实际{prob_result[0, 1]}"
        assert prob_result[0, 2] == 0.0, f"Dn=0.1位置应为0，实际{prob_result[0, 2]}"
        print(f"  Dn<=0.1cm位置概率均为0 ✓")

        # Dn > 0.1cm 的位置应有正概率
        assert prob_result[0, 3] > 0.0, f"Dn=1.0位置应>0，实际{prob_result[0, 3]}"
        assert prob_result[0, 4] > 0.0, f"Dn=10.0位置应>0，实际{prob_result[0, 4]}"
        # 概率值应随Dn增大而增大（b为负时）
        assert prob_result[0, 4] >= prob_result[0, 3], \
            f"Dn=10时概率应>=Dn=1时，实际{prob_result[0, 4]} vs {prob_result[0, 3]}"
        print(f"  Dn>0.1cm时概率正确计算，且随Dn增大而增大 ✓")

        # 所有概率值应在[0,1]范围内
        assert float(np.min(prob_result)) >= 0.0, "所有概率应>=0"
        assert float(np.max(prob_result)) <= 1.0, "所有概率应<=1"
        print(f"  所有概率值均在[0,1]范围内 ✓")
    else:
        print("\n--- 测试5: compute_hazard_raster (GDAL不可用，跳过) ---")

    # ---- 测试6：自然断点法分级 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试6: classify_hazard_levels ---")
        # 创建测试概率数据
        np.random.seed(42)
        test_probs = np.concatenate([
            np.zeros(200),  # 大量零概率（安全区）
            np.random.uniform(0.001, 0.1, 100),  # 低危险
            np.random.uniform(0.1, 0.3, 80),  # 较低危险
            np.random.uniform(0.3, 0.6, 60),  # 中等危险
            np.random.uniform(0.6, 0.8, 40),  # 较高危险
            np.random.uniform(0.8, 1.0, 20),  # 高度危险
        ])

        breaks = classify_hazard_levels(test_probs, num_classes=5)
        assert len(breaks) == 6, f"边界值列表长度应为6，实际{len(breaks)}"
        assert breaks[0] == 0.0, f"第一个边界应为0，实际{breaks[0]}"
        # 边界值应单调递增
        for i in range(len(breaks) - 1):
            assert breaks[i] <= breaks[i + 1], \
                f"边界值应单调递增，breaks[{i}]={breaks[i]} > breaks[{i + 1}]={breaks[i + 1]}"
        print(f"  边界值列表: {[f'{v:.4f}' for v in breaks]} ✓")
        print(f"  边界值长度为6，第一个为0，单调递增 ✓")

        # 测试空数据情况
        breaks_empty = classify_hazard_levels(np.array([]), num_classes=5)
        assert len(breaks_empty) == 6, "空数据时应返回长度为6的等间距列表"
        print(f"  空数据时返回默认等间距分类 ✓")

        # 测试全零数据情况
        breaks_zero = classify_hazard_levels(np.zeros(100), num_classes=5)
        assert breaks_zero[0] == 0.0, "全零数据时第一个边界应为0"
        print(f"  全零概率数据时正确处理 ✓")
    else:
        print("\n--- 测试6: classify_hazard_levels (GDAL不可用，跳过) ---")

    # ---- 测试7：统计摘要生成 ----
    print("\n--- 测试7: build_statistics_summary ---")
    test_area_stats = {
        "低度危险区": {'area_km2': 1500.0, 'percent': 60.0},
        "较低危险区": {'area_km2': 500.0, 'percent': 20.0},
        "中等危险区": {'area_km2': 300.0, 'percent': 12.0},
        "较高危险区": {'area_km2': 150.0, 'percent': 6.0},
        "高度危险区": {'area_km2': 50.0, 'percent': 2.0},
        'total_valid_km2': 2500.0,
    }
    summary = build_statistics_summary(test_area_stats)
    assert "低度危险区" in summary, f"摘要应包含低度危险区，实际: {summary}"
    assert "平方千米" in summary, "摘要应包含面积单位"
    assert "占比" in summary, "摘要应包含占比信息"
    assert "总得来看" in summary, "摘要应以总得来看开头"
    # 面积格式化：保留两位小数
    assert "1500.00" in summary, f"面积1500.0应格式化为1500.00，实际: {summary}"
    print(f"  统计摘要生成正确 ✓")
    print(f"  摘要示例: {summary[:80]}...")

    # 测试小于1面积的格式化
    test_small_stats = {
        "低度危险区": {'area_km2': 0.35, 'percent': 0.01},
        "较低危险区": {'area_km2': 0.0, 'percent': 0.0},
        "中等危险区": {'area_km2': 0.0, 'percent': 0.0},
        "较高危险区": {'area_km2': 0.0, 'percent': 0.0},
        "高度危险区": {'area_km2': 0.0, 'percent': 0.0},
        'total_valid_km2': 0.36,
    }
    summary_small = build_statistics_summary(test_small_stats)
    assert "0.35" in summary_small, f"面积小于1时应保留两位小数，实际: {summary_small}"
    print(f"  小面积（<1）格式化正确（保留两位小数）✓")

    # ---- 测试8：刻度间隔选取 ----
    print("\n--- 测试8: _choose_tick_step ---")
    step_small = _choose_tick_step(0.5)
    assert step_small in [0.01, 0.02, 0.05, 0.1, 0.2, 0.25], f"小范围刻度应为小值，实际{step_small}"
    print(f"  0.5度范围 -> 刻度间隔: {step_small} ✓")

    step_large = _choose_tick_step(10.0)
    assert step_large in [1.0, 2.0, 5.0], f"大范围刻度应为大值，实际{step_large}"
    print(f"  10度范围 -> 刻度间隔: {step_large} ✓")

    # ---- 测试9：KML烈度值提取 ----
    print("\n--- 测试9: _extract_intensity_value ---")
    assert _extract_intensity_value("4度") == 4, "应能解析 '4度'"
    assert _extract_intensity_value("5度") == 5, "应能解析 '5度'"
    assert _extract_intensity_value("IV度") == 4, "应能解析 'IV度'"
    assert _extract_intensity_value("VIII") == 8, "应能解析 'VIII'"
    assert _extract_intensity_value("10") == 10, "应能解析纯数字 '10'"
    assert _extract_intensity_value("无效") is None, "无法解析时应返回None"
    print(f"  烈度值提取正确: '4度'->4, 'IV度'->4, 'VIII'->8 ✓")

    # ---- 测试10：KML坐标解析 ----
    print("\n--- 测试10: _parse_kml_coordinates ---")
    coords_str = "116.0,39.0,0 117.0,39.0,0 117.0,40.0,0 116.0,40.0,0"
    coords = _parse_kml_coordinates(coords_str)
    assert len(coords) == 4, f"应解析4个坐标点，实际{len(coords)}"
    assert coords[0] == (116.0, 39.0), f"第一个点应为(116.0, 39.0)，实际{coords[0]}"
    print(f"  坐标解析正确，共{len(coords)}个点 ✓")

    # ---- 测试11：多边形掩膜 bbox 优化 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试11: _create_polygon_mask bbox优化 ---")
        # 构造 4x4 栅格（北上影像，y_res < 0），多边形仅覆盖中央 2x2 区域
        gt_test = (0.0, 1.0, 0.0, 4.0, 0.0, -1.0)  # x_origin=0, x_res=1, y_origin=4, y_res=-1
        shape_test = (4, 4)
        # 多边形：[1,3] x [1,3] 范围（像素坐标）
        poly_test = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0), (1.0, 1.0)]
        mask_test = _create_polygon_mask(shape_test, gt_test, poly_test)
        assert mask_test.shape == shape_test, "掩膜形状应与栅格一致"
        assert mask_test.sum() > 0, "掩膜内应有像素"
        # bbox 外的角像素（如 [0,0]）应为 False
        assert not mask_test[0, 0], "bbox 外角像素应为 False"
        assert not mask_test[3, 3], "bbox 外角像素应为 False"
        print(f"  多边形掩膜 bbox 优化正确，圈内像素数: {mask_test.sum()} ✓")

        # 测试 gt[5] > 0（南上影像）场景
        gt_south = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)  # y_origin=0, y_res=+1（南上）
        poly_south = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0), (1.0, 1.0)]
        mask_south = _create_polygon_mask(shape_test, gt_south, poly_south)
        assert mask_south.shape == shape_test, "南上影像掩膜形状应与栅格一致"
        assert mask_south.sum() > 0, "南上影像掩膜内应有像素"
        print(f"  南上影像（gt[5]>0）掩膜正确，圈内像素数: {mask_south.sum()} ✓")
    else:
        print("\n--- 测试11: _create_polygon_mask (GDAL不可用，跳过) ---")

    # ---- 测试12：np.digitize 分类计数 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试12: np.digitize 分类计数 ---")
        test_probs_dig = np.array([0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                                   0.6, 0.7, 0.8, 0.9, 1.0], dtype=np.float32)
        test_breaks_dig = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        # 使用 float32 断点保持精度一致，right=True 匹配原始左开右闭语义
        breaks_f32_test = np.array(test_breaks_dig[1:5], dtype=np.float32)
        bin_idx = np.digitize(test_probs_dig, breaks_f32_test, right=True)
        counts = np.bincount(bin_idx, minlength=5)[:5]
        assert len(counts) == 5, "应分为5类"
        assert int(np.sum(counts)) == len(test_probs_dig), \
            f"总计数{int(np.sum(counts))}应等于像素数{len(test_probs_dig)}"
        # 验证第0类（<=0.2）：0.0, 0.0, 0.1, 0.2 共4个
        assert counts[0] == 4, f"第0类(<=0.2)应有4个像素，实际{counts[0]}"
        # 验证第4类（>0.8）：0.9, 1.0 共2个
        assert counts[4] == 2, f"第4类(>0.8)应有2个像素，实际{counts[4]}"
        print(f"  digitize 分类计数正确，各类计数: {counts.tolist()} ✓")
    else:
        print("\n--- 测试12: np.digitize (GDAL不可用，跳过) ---")

    # ---- 测试13：numpy降级路径下采样 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试13: numpy降级路径（_compute_jenks_numpy）下采样 ---")
        # 创建超过 MAX_NUMPY_SAMPLES 的数据，确保函数在合理时间内完成
        np.random.seed(123)
        large_data = np.random.rand(5000).astype(np.float64)
        large_sorted = np.sort(large_data)
        _t0 = time.time()
        breaks_numpy = _compute_jenks_numpy(
            large_sorted, 5, float(large_sorted[0]), float(large_sorted[-1]))
        _elapsed = time.time() - _t0
        assert len(breaks_numpy) == 6, f"应返回6个边界值，实际{len(breaks_numpy)}"
        for i in range(len(breaks_numpy) - 1):
            assert breaks_numpy[i] <= breaks_numpy[i + 1], \
                f"边界值应单调递增：breaks[{i}]={breaks_numpy[i]:.6f} > breaks[{i+1}]={breaks_numpy[i+1]:.6f}"
        # 下采样到 MAX_NUMPY_SAMPLES(2000) 后，5000条数据应在合理时间内完成（<60秒）
        assert _elapsed < 60, f"numpy降级路径超时: {_elapsed:.1f}s（超出60秒限制）"
        print(f"  numpy降级路径下采样后能正确计算断点（耗时 {_elapsed*1000:.0f}ms），"
              f"边界值: {[f'{v:.4f}' for v in breaks_numpy]} ✓")

        # 验证大数据集（>MAX_SAMPLES=10000）也能正常运行 compute_jenks_breaks
        large_flat = np.random.rand(50000).astype(np.float32)
        breaks_large = compute_jenks_breaks(large_flat, num_classes=5)
        assert len(breaks_large) == 6, f"大数据集应返回6个边界值，实际{len(breaks_large)}"
        for i in range(len(breaks_large) - 1):
            assert breaks_large[i] <= breaks_large[i + 1], \
                f"大数据集边界值应单调递增：breaks[{i}]={breaks_large[i]:.6f}"
        print(f"  大数据集（50000个样本）降采样+Jenks正常完成 ✓")
    else:
        print("\n--- 测试13: numpy降级路径 (GDAL不可用，跳过) ---")

    # ---- 测试14：generate_hazard_tif gt[5] > 0（像素坐标计算）----
    if GDAL_AVAILABLE:
        print("\n--- 测试14: generate_hazard_tif gt[5] 符号处理 ---")
        # 验证像素坐标计算对于 gt[5] < 0（北上）和 gt[5] > 0（南上）的数学正确性
        # 北上影像：y_origin=40, y_res=-0.01，查询 clip_ymin=39.0, clip_ymax=39.5
        gt_north = (115.0, 0.01, 0.0, 40.0, 0.0, -0.01)
        clip_ymax_n, clip_ymin_n = 39.5, 39.0
        px_ymin_n = int((clip_ymax_n - gt_north[3]) / gt_north[5])  # (39.5-40)/(-0.01) = 50
        px_ymax_n = int((clip_ymin_n - gt_north[3]) / gt_north[5]) + 1  # (39.0-40)/(-0.01)+1 = 101
        assert px_ymin_n >= 0 and px_ymin_n < px_ymax_n, \
            f"北上影像：px_ymin({px_ymin_n}) 应 < px_ymax({px_ymax_n})"
        print(f"  北上影像（gt[5]<0）像素坐标计算正确: ymin={px_ymin_n}, ymax={px_ymax_n} ✓")

        # 南上影像：y_origin=30, y_res=+0.01，查询 clip_ymin=30.5, clip_ymax=31.0
        gt_south_tif = (115.0, 0.01, 0.0, 30.0, 0.0, 0.01)
        clip_ymin_s, clip_ymax_s = 30.5, 31.0
        px_ymin_s = int((clip_ymin_s - gt_south_tif[3]) / gt_south_tif[5])  # (30.5-30)/0.01 = 50
        px_ymax_s = int((clip_ymax_s - gt_south_tif[3]) / gt_south_tif[5]) + 1  # (31.0-30)/0.01+1 = 101
        assert px_ymin_s >= 0 and px_ymin_s < px_ymax_s, \
            f"南上影像：px_ymin({px_ymin_s}) 应 < px_ymax({px_ymax_s})"
        print(f"  南上影像（gt[5]>0）像素坐标计算正确: ymin={px_ymin_s}, ymax={px_ymax_s} ✓")
    else:
        print("\n--- 测试14: gt[5] 符号处理 (GDAL不可用，跳过) ---")

    # ---- 测试15：GDAL rasterize 与 matplotlib 路径结果一致性 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试15: GDAL rasterize 路径与 matplotlib 路径一致性 ---")
        gt_t15 = (0.0, 1.0, 0.0, 4.0, 0.0, -1.0)  # 4x4 北上影像
        shape_t15 = (4, 4)
        poly_t15 = [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0), (1.0, 1.0)]

        # GDAL rasterize 路径（直接调用，GDAL_AVAILABLE=True 时优先）
        mask_gdal = _create_polygon_mask(shape_t15, gt_t15, poly_t15)

        # matplotlib 路径（直接调用内部逻辑，绕过 GDAL）
        try:
            from matplotlib.path import Path as _MplPath
            _poly_path = _MplPath(poly_t15)
            _xs = 0.0 + (np.arange(4) + 0.5) * 1.0
            _ys = 4.0 + (np.arange(4) + 0.5) * (-1.0)
            _xg, _yg = np.meshgrid(_xs, _ys)
            _pts = np.column_stack([_xg.ravel(), _yg.ravel()])
            mask_mpl = _poly_path.contains_points(_pts).reshape(4, 4)
            # 允许 ±1 像素边界差异（栅格化算法不同导致边界像素可能有差异）
            diff = int(np.sum(np.abs(mask_gdal.astype(int) - mask_mpl.astype(int))))
            # 4x4 网格中多边形边界恰好穿过部分像素中心，两种算法对边界像素的归属判断
            # 可能不同（GDAL 使用扫描线填充，matplotlib 使用射线检测），
            # 允许最多 4 个边界像素差异（<=25% 总像素），此处仅验证算法大体一致
            small_tolerance = 4
            assert diff <= small_tolerance, \
                f"GDAL与matplotlib掩膜差异{diff}个像素，超出容差{small_tolerance}"
            assert mask_gdal.shape == shape_t15, "GDAL掩膜形状应与栅格一致"
            assert mask_gdal.sum() > 0, "GDAL掩膜内应有像素"
            assert not mask_gdal[0, 0], "bbox 外角像素应为 False"
            assert not mask_gdal[3, 3], "bbox 外角像素应为 False"
            print(f"  GDAL圈内像素数: {mask_gdal.sum()}, matplotlib圈内像素数: {mask_mpl.sum()}, "
                  f"边界差异: {diff}个像素 ✓")
        except ImportError:
            # matplotlib 不可用时只验证 GDAL 路径结果基本正确
            assert mask_gdal.shape == shape_t15, "GDAL掩膜形状应与栅格一致"
            assert mask_gdal.sum() > 0, "GDAL掩膜内应有像素"
            print(f"  GDAL掩膜正确（matplotlib不可用，跳过一致性比对），圈内像素数: {mask_gdal.sum()} ✓")
    else:
        print("\n--- 测试15: GDAL rasterize 一致性 (GDAL不可用，跳过) ---")

    print("\n" + "=" * 60)
    print("全部测试执行完成 ✓")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        # 运行测试模式（不需要QGIS环境的纯函数测试）
        run_all_tests()
    elif len(sys.argv) >= 7:
        # 命令行运行模式：传入震中、震级和公式参数
        # 用法: python earthquake_hazard_map.py <经度> <纬度> <震级> <a> <b> <c>
        #       [输出文件名] [Dn_tif路径] [烈度kml路径]
        try:
            _lon = float(sys.argv[1])
            _lat = float(sys.argv[2])
            _mag = float(sys.argv[3])
            _a = float(sys.argv[4])
            _b = float(sys.argv[5])
            _c = float(sys.argv[6])
            _out = sys.argv[7] if len(sys.argv) > 7 else \
                f"earthquake_hazard_M{_mag}_{_lon}_{_lat}.png"
            _dn_tif = sys.argv[8] if len(sys.argv) > 8 else None
            _intensity_kml = sys.argv[9] if len(sys.argv) > 9 else None
            _img_path, _max_dn, _summary = generate_earthquake_hazard_map(
                _lon, _lat, _mag, _a, _b, _c, _out, _dn_tif, _intensity_kml)
            print(f"\n最大Dn值: {_max_dn} cm")
            print(f"\n统计摘要:\n{_summary}")
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_hazard_map.py <经度> <纬度> <震级> <a> <b> <c> "
                  "[输出文件名] [Dn_tif路径] [烈度kml路径]")
    else:
        # 使用默认参数演示运行
        print("使用默认参数运行示例...")
        _img_path, _max_dn, _summary = generate_earthquake_hazard_map(
            longitude=103.36, latitude=34.09,
            magnitude=3.0,
            a=0.1169,
            b=-0.1803,
            c=0.5165,
            output_path="earthquake_hazard_M7.0.png",
            dn_tif_path="../../data/geology/ia/Dn.tif",
            intensity_kml_path=None  # 可选：传入烈度KML文件路径
        )
        if _img_path:
            print(f"\n最大Dn值: {_max_dn} cm")
            print(f"\n统计摘要:\n{_summary}")