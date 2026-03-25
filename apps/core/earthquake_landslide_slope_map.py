# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中历史滑坡、斜坡分布图生成脚本
参考 earthquake_geological_map2.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

功能说明：
- 加载天地图矢量底图和注记作为底图
- 加载"滑坡.shp"和"斜坡.shp"文件
- 统计输出图范围内滑坡和斜坡数量
- 图例位于主图左下角，包含震中、地级市、省界、市界、县界、烈度、滑坡、斜坡
- 图例布局：基本图例项采用3行2列布局，滑坡和斜坡单列展示
"""

import os
import sys
import math
import re
import logging
from xml.etree import ElementTree as ET

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
    QgsFeatureRequest,
    QgsFeature,
    QgsField,
    QgsLayoutExporter,
    QgsMapSettings,
    QgsMapRendererCustomPainterJob,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QPainter


# ============================================================
# Django settings 导入（可选）
# ============================================================
try:
    from django.conf import settings as _django_settings
    _DJANGO_AVAILABLE = True
except ImportError:
    _django_settings = None
    _DJANGO_AVAILABLE = False

from tianditu_basemap_downloader import (
    download_tianditu_basemap_tiles,
    download_tianditu_annotation_tiles,
)

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.earthquake_landslide_slope_map')

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

# 滑坡和斜坡数据文件路径
LANDSLIDE_SHP_PATH = (
    getattr(_django_settings, 'LANDSLIDE_SHP_PATH',
            _DEFAULT_BASE + '滑坡和危险斜坡/滑坡.shp')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '滑坡和危险斜坡/滑坡.shp'
)
SLOPE_SHP_PATH = (
    getattr(_django_settings, 'SLOPE_SHP_PATH',
            _DEFAULT_BASE + '滑坡和危险斜坡/斜坡.shp')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '滑坡和危险斜坡/斜坡.shp'
)

# 省市边界数据路径
PROVINCE_SHP_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp'
)
CITY_SHP_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp'
)
COUNTY_SHP_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp'
)
# 地级市点位数据
CITY_POINTS_SHP_PATH = (
    getattr(_django_settings, 'CITY_POINTS_SHP_PATH',
            _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp'
)

# === 布局尺寸常量 ===
# 由于图例在主图内部左下角，不需要单独的图例区域宽度
MAP_TOTAL_WIDTH_MM = 200.0
BORDER_LEFT_MM = 4.0
BORDER_TOP_MM = 4.0
BORDER_BOTTOM_MM = 2.0
BORDER_RIGHT_MM = 4.0
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - BORDER_RIGHT_MM

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

# === 图例框尺寸常量 ===
LEGEND_WIDTH_MM = 41.0
LEGEND_HEIGHT_MM = 38.0

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

# === 滑坡样式配置 ===
# 滑坡：实心圆点，带边框
LANDSLIDE_COLOR = QColor(12,162,31)
LANDSLIDE_STROKE_COLOR = QColor(2,23,4)
LANDSLIDE_STROKE_WIDTH_MM = 0.1
LANDSLIDE_SIZE_MM = 2.0

# === 斜坡样式配置 ===
# 斜坡：实心圆点，带边框
SLOPE_COLOR = QColor(61,108,190)
SLOPE_STROKE_COLOR = QColor(7,12,21)
SLOPE_STROKE_WIDTH_MM = 0.1
SLOPE_SIZE_MM = 2.0

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 工具函数
# ============================================================

import time
from qgis.PyQt.QtCore import QEventLoop, QTimer


def render_xyz_tiles_to_raster(project, extent, width_px, height_px, output_path):
    """
    将XYZ瓦片图层渲染为本地栅格图像
    使用事件循环等待瓦片加载完成

    参数:
        project: QgsProject, QGIS项目实例
        extent: QgsRectangle, 渲染范围
        width_px: int, 输出图像宽度(像素)
        height_px: int, 输出图像高度(像素)
        output_path: str, 输出文件路径

    返回:
        QgsRasterLayer或None, 渲染后的栅格图层
    """
    from qgis.PyQt.QtCore import QSize, QEventLoop, QTimer
    from qgis.PyQt.QtGui import QImage, QPainter
    from qgis.core import QgsMapSettings, QgsMapRendererCustomPainterJob

    # 获取天地图图层
    tianditu_layers = []
    for layer_id, layer in project.mapLayers().items():
        if "天地图" in layer.name() and layer.type() == layer.RasterLayer:
            tianditu_layers.append(layer)

    if not tianditu_layers:
        print("[警告] 未找到天地图图层")
        return None

    print(f"[信息] 准备渲染 {len(tianditu_layers)} 个天地图图层...")

    # 创建地图设置
    settings = QgsMapSettings()
    settings.setDestinationCrs(CRS_WGS84)
    settings.setExtent(extent)
    settings.setOutputSize(QSize(width_px, height_px))
    settings.setLayers(tianditu_layers)
    settings.setBackgroundColor(QColor(255, 255, 255))

    # 创建图像
    image = QImage(QSize(width_px, height_px), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))

    # 第一次渲染 - 触发瓦片下载
    painter = QPainter(image)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    # 等待瓦片加载 - 使用事件循环处理异步请求
    print("[信息] 等待瓦片下载...")

    # 处理事件循环，让网络请求有机会完成
    loop = QEventLoop()

    # 多次尝试渲染，每次等待一段时间让瓦片加载
    max_attempts = 400
    wait_ms = 1000  # 每次等待1秒

    for attempt in range(max_attempts):
        # 处理待处理的事件（包括网络响应）
        QgsApplication.processEvents()

        # 使用定时器等待
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(wait_ms)
        loop.exec_()

        # 重新渲染
        image.fill(QColor(255, 255, 255))
        painter = QPainter(image)
        job = QgsMapRendererCustomPainterJob(settings, painter)
        job.start()
        job.waitForFinished()
        painter.end()

        # 检查图像是否有内容（不是纯白色）
        # 简单检查：看中心点像素是否不是白色
        center_color = image.pixelColor(width_px // 2, height_px // 2)
        if center_color != QColor(255, 255, 255):
            print(f"[信息] 瓦片加载完成（第 {attempt + 1} 次尝试）")
            break

        print(f"[信息] 等待瓦片加载... ({attempt + 1}/{max_attempts})")

    # 保存图像
    if image.save(output_path, "PNG"):
        print(f"[信息] 底图图像已保存: {output_path}")
    else:
        print(f"[错误] 无法保存底图图像")
        return None

    # 创建world文件以定义地理参考
    world_file_path = output_path.replace(".png", ".pgw")
    x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
    y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
    with open(world_file_path, 'w') as f:
        f.write(f"{x_res}\n")
        f.write("0\n")
        f.write("0\n")
        f.write(f"{-y_res}\n")
        f.write(f"{extent.xMinimum()}\n")
        f.write(f"{extent.yMaximum()}\n")

    # 加载为栅格图层
    raster_layer = QgsRasterLayer(output_path, "底图栅格", "gdal")
    if raster_layer.isValid():
        print(f"[信息] 成功渲染底图为本地栅格")
        return raster_layer
    else:
        print(f"[错误] 无法加载渲染的栅格图层")
        return None


import requests
import math
from PIL import Image
from io import BytesIO



def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数

    参数:
        magnitude: float, 地震震级

    返回:
        dict, 包含radius_km, map_size_km, scale等配置
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
        longitude: float, 震中经度
        latitude: float, 震中纬度
        half_size_km: float, 地图半幅宽度(公里)

    返回:
        QgsRectangle, 地图范围矩形
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
        extent: QgsRectangle, 地图范围
        map_width_mm: float, 地图宽度(毫米)

    返回:
        float, 地图高度(毫米)
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
        relative_path: str, 相对路径

    返回:
        str, 绝对路径
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字

    参数:
        num: int, 阿拉伯数字

    返回:
        str, 罗马数字字符串
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
        range_deg: float, 地理范围(度)
        target_min: int, 目标最小刻度数
        target_max: int, 目标最大刻度数

    返回:
        float, 刻度间隔(度)
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
        output_path: str, SVG文件输出路径

    返回:
        str, SVG文件路径
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
        layer: QgsVectorLayer, 矢量图层
        candidates: list, 候选字段名列表

    返回:
        str或None, 找到的字段名
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


def count_features_in_extent(layer, extent):
    """
    统计图层中位于指定范围内的要素数量

    参数:
        layer: QgsVectorLayer, 矢量图层
        extent: QgsRectangle, 地图范围

    返回:
        int, 要素数量
    """
    if layer is None or not layer.isValid():
        return 0

    count = 0
    request = QgsFeatureRequest().setFilterRect(extent)
    for feature in layer.getFeatures(request):
        geom = feature.geometry()
        if geom and not geom.isEmpty():
            # 检查要素的中心点是否在范围内
            centroid = geom.centroid().asPoint()
            if extent.contains(centroid):
                count += 1
    return count


# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据

    参数:
        kml_path: str, KML文件路径

    返回:
        list, 烈度圈数据列表，每项包含intensity和coords
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
        name: str, Placemark名称

    返回:
        int或None, 烈度值
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
        placemark: XML元素, Placemark节点
        ns: str, XML命名空间

    返回:
        list, 坐标列表[(lon, lat), ...]
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
        text: str, KML坐标文本

    返回:
        list, 坐标列表[(lon, lat), ...]
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
        intensity_data: list, 烈度圈数据列表

    返回:
        QgsVectorLayer或None, 烈度圈图层
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

    # 创建光晕效果的线符号
    halo_sl = QgsSimpleLineSymbolLayer()
    halo_sl.setColor(INTENSITY_HALO_COLOR)
    halo_sl.setWidth(INTENSITY_HALO_WIDTH_MM)
    halo_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    halo_sl.setPenStyle(Qt.SolidLine)

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
        layer: QgsVectorLayer, 烈度圈图层
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

def load_tianditu_basemap():
    """
    加载天地图矢量底图(XYZ Tiles方式)
    使用 vec_c (EPSG:4326 投影) 以匹配项目坐标系

    返回:
        QgsRasterLayer或None, 天地图底图图层
    """
    # 使用 vec_c (EPSG:4326经纬度投影) 而非 vec_w (Web墨卡托投影)
    # 这样可以与项目的 WGS84 坐标系匹配
    tianditu_url = (
        "http://t0.tianditu.gov.cn/vec_c/wmts?"
        "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
        "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        "&tk=" + TIANDITU_TK
    )

    # 构建XYZ图层URI
    uri = f"type=xyz&url={tianditu_url}&zmax=18&zmin=0"

    layer = QgsRasterLayer(uri, "天地图矢量底图", "wms")

    if layer.isValid():
        print("[信息] 成功加载天地图矢量底图 (EPSG:4326)")
        return layer

    print("[警告] 天地图矢量底图加载失败，尝试备用方式...")
    # 备用方式：使用DataServer接口 (vec_c)
    uri_simple = f"type=xyz&url=http://t0.tianditu.gov.cn/DataServer?T=vec_c&x={{x}}&y={{y}}&l={{z}}&tk={TIANDITU_TK}&zmax=18&zmin=0"
    layer = QgsRasterLayer(uri_simple, "天地图矢量底图", "wms")
    if layer.isValid():
        print("[信息] 使用备用方式成功加载天地图矢量底图")
        return layer

    print("[错误] 无法加载天地图矢量底图")
    return None


def load_tianditu_annotation():
    """
    加载天地图矢量注记图层
    使用 cva_c (EPSG:4326 投影) 以匹配项目坐标系

    返回:
        QgsRasterLayer或None, 天地图注记图层
    """
    # 使用 cva_c (EPSG:4326经纬度投影) 而非 cva_w (Web墨卡托投影)
    tianditu_url = (
        "http://t0.tianditu.gov.cn/cva_c/wmts?"
        "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
        "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        "&tk=" + TIANDITU_TK
    )

    uri = f"type=xyz&url={tianditu_url}&zmax=18&zmin=0"

    layer = QgsRasterLayer(uri, "天地图矢量注记", "wms")

    if layer.isValid():
        print("[信息] 成功加载天地图矢量注记 (EPSG:4326)")
        return layer

    print("[警告] 天地图矢量注记加载失败，尝试备用方式...")
    uri_simple = f"type=xyz&url=http://t0.tianditu.gov.cn/DataServer?T=cva_c&x={{x}}&y={{y}}&l={{z}}&tk={TIANDITU_TK}&zmax=18&zmin=0"
    layer = QgsRasterLayer(uri_simple, "天地图矢量注记", "wms")
    if layer.isValid():
        print("[信息] 使用备用方式成功加载天地图注记")
        return layer

    print("[警告] 天地图矢量注记加载失败")
    return None


def load_tianditu_annotation():
    """
    加载天地图矢量注记图层

    返回:
        QgsRasterLayer或None, 天地图注记图层
    """
    # 天地图矢量注记URL（Web墨卡托投影）
    tianditu_url = (
        "http://t0.tianditu.gov.cn/cva_w/wmts?"
        "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=cva&STYLE=default&TILEMATRIXSET=w"
        "&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}"
        "&tk=" + TIANDITU_TK
    )

    uri = f"type=xyz&url={tianditu_url}&zmax=18&zmin=0"

    layer = QgsRasterLayer(uri, "天地图矢量注记", "wms")

    if layer.isValid():
        print("[信息] 成功加载天地图矢量注记")
        return layer

    print("[警告] 天地图矢量注记加载失败，尝试备用方式...")
    uri_simple = f"type=xyz&url=http://t0.tianditu.gov.cn/DataServer?T=cva_w&x={{x}}&y={{y}}&l={{z}}&tk={TIANDITU_TK}&zmax=18&zmin=0"
    layer = QgsRasterLayer(uri_simple, "天地图矢量注记", "wms")
    if layer.isValid():
        print("[信息] 使用备用方式成功加载天地图注记")
        return layer

    print("[警告] 天地图矢量注记加载失败")
    return None


def load_landslide_layer(shp_path):
    """
    加载滑坡图层

    参数:
        shp_path: str, 滑坡SHP文件路径

    返回:
        QgsVectorLayer或None, 滑坡图层
    """
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[警告] 滑坡文件不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "滑坡", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载滑坡图层: {abs_path}")
        return None

    # 设置滑坡样式：实心圆点，带细边框
    marker_sl = QgsSimpleMarkerSymbolLayer()
    marker_sl.setShape(Qgis.MarkerShape.Circle)
    marker_sl.setColor(LANDSLIDE_COLOR)
    marker_sl.setStrokeColor(LANDSLIDE_STROKE_COLOR)
    marker_sl.setStrokeWidth(LANDSLIDE_STROKE_WIDTH_MM)
    marker_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    marker_sl.setSize(LANDSLIDE_SIZE_MM)
    marker_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, marker_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print(f"[信息] 成功加载滑坡图层: {abs_path}")
    return layer


def load_slope_layer(shp_path):
    """
    加载斜坡图层

    参数:
        shp_path: str, 斜坡SHP文件路径

    返回:
        QgsVectorLayer或None, 斜坡图层
    """
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[警告] 斜坡文件不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "斜坡", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载斜坡图层: {abs_path}")
        return None

    # 设置斜坡样式：实心圆点，带细边框
    marker_sl = QgsSimpleMarkerSymbolLayer()
    marker_sl.setShape(Qgis.MarkerShape.Circle)
    marker_sl.setColor(SLOPE_COLOR)
    marker_sl.setStrokeColor(SLOPE_STROKE_COLOR)
    marker_sl.setStrokeWidth(SLOPE_STROKE_WIDTH_MM)
    marker_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    marker_sl.setSize(SLOPE_SIZE_MM)
    marker_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, marker_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print(f"[信息] 成功加载斜坡图层: {abs_path}")
    return layer


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）

    参数:
        shp_path: str, SHP文件路径
        layer_name: str, 图层名称

    返回:
        QgsVectorLayer或None, 矢量图层
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

def style_province_layer(layer, center_lon=None, center_lat=None, extent=None):
    """
    设置省界图层样式

    参数:
        layer: QgsVectorLayer, 省界图层
        center_lon: float或None, 震中经度，用于标注偏移判断
        center_lat: float或None, 震中纬度，用于标注偏移判断
        extent: QgsRectangle或None, 地图范围，用于计算偏移量
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
        # 无震中信息时，直接在省界图层上配置标注
        _setup_province_labels(layer)
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """
    设置市界图层样式
    颜色: R=160, G=160, B=160
    线宽: 0.24mm
    虚线间隔: 0.3mm

    参数:
        layer: QgsVectorLayer, 市界图层
    """
    symbol = QgsFillSymbol()

    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))  # 透明填充
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

    print(
        f"[信息] 市界图层样式设置完成 - 颜色: RGB({CITY_COLOR.red()},{CITY_COLOR.green()},{CITY_COLOR.blue()}), "
        f"线宽: {CITY_LINE_WIDTH_MM}mm, 虚线间隔: {CITY_DASH_GAP_MM}mm")


def style_county_layer(layer):
    """
    设置县界图层样式
    颜色: R=160, G=160, B=160
    线宽: 0.14mm
    虚线间隔: 0.2mm

    参数:
        layer: QgsVectorLayer, 县界图层
    """
    symbol = QgsFillSymbol()

    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))  # 透明填充
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

    print(
        f"[信息] 县界图层样式设置完成 - 颜色: RGB({COUNTY_COLOR.red()},{COUNTY_COLOR.green()},{COUNTY_COLOR.blue()}), "
        f"线宽: {COUNTY_LINE_WIDTH_MM}mm, 虚线间隔: {COUNTY_DASH_GAP_MM}mm")


def _setup_province_labels(layer):
    """
    配置省界图层标注

    参数:
        layer: QgsVectorLayer, 省界图层
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


def _setup_point_labels(layer, field_name, font_size_pt, color):
    """
    为点图层配置标注

    参数:
        layer: QgsVectorLayer, 点图层
        field_name: str, 标注字段名
        font_size_pt: int, 字体大小(点)
        color: QColor, 字体颜色
    """
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
    settings.displayAll = True

    text_format = QgsTextFormat()
    font = QFont("SimSun", font_size_pt)
    text_format.setFont(font)
    text_format.setSize(font_size_pt)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(color)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.6)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabelsEnabled(True)
    layer.setLabeling(labeling)


def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent):
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移。

    当省份质心与震中坐标重合时，标注点向右下角偏移3mm，避免遮挡震中五角星标识。

    参数:
        province_layer: QgsVectorLayer, 省界多边形图层
        epicenter_lon: float, 震中经度（度）
        epicenter_lat: float, 震中纬度（度）
        extent: QgsRectangle或None, 地图范围，用于计算偏移量（mm转度）

    返回:
        QgsVectorLayer或None, 配置好标注的内存点图层，失败返回None
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
# 震中和图例图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层：红色五角星+白边

    参数:
        longitude: float, 震中经度
        latitude: float, 震中纬度

    返回:
        QgsVectorLayer, 震中图层
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
    加载地级市点位数据

    参数:
        extent: QgsRectangle, 地图范围（用于筛选）

    返回:
        QgsVectorLayer或None, 地级市点位图层
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

    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

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

    # 不再设置标注，只显示点位
    layer.setLabelsEnabled(False)

    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成（不显示标注）")
    return layer


def create_intensity_legend_layer():
    """
    创建烈度图例用的线图层

    返回:
        QgsVectorLayer, 烈度图例图层
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
    print("[信息] 创建烈度图例图层")
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的线图层

    返回:
        QgsVectorLayer, 省界图例图层
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
    print("[信息] 创建省界图例线图层")
    return layer


def create_city_legend_layer():
    """
    创建市界图例用的线图层
    线宽: 0.24mm，虚线间隔: 0.3mm

    返回:
        QgsVectorLayer, 市界图例图层
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
    print(f"[信息] 创建市界图例线图层 - 线宽: {CITY_LINE_WIDTH_MM}mm, 虚线间隔: {CITY_DASH_GAP_MM}mm")
    return layer


def create_county_legend_layer():
    """
    创建县界图例用的线图层
    线宽: 0.14mm，虚线间隔: 0.2mm

    返回:
        QgsVectorLayer, 县界图例图层
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
    print(f"[信息] 创建县界图例线图层 - 线宽: {COUNTY_LINE_WIDTH_MM}mm, 虚线间隔: {COUNTY_DASH_GAP_MM}mm")
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm, ordered_layers=None):
    """
    创建QGIS打印布局

    参数:
        project: QgsProject, QGIS项目实例
        longitude: float, 震中经度
        latitude: float, 震中纬度
        magnitude: float, 地震震级
        extent: QgsRectangle, 地图范围
        scale: int, 比例尺
        map_height_mm: float, 地图高度(毫米)
        ordered_layers: list或None, 按渲染顺序排列的图层列表（第一项在最上层）

    返回:
        QgsPrintLayout, 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("历史滑坡斜坡分布图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm,
                                   QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    # 添加地图项
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

    # 显式设置地图项渲染的图层列表，确保底图等所有图层都能在布局中正确显示
    # setKeepLayerSet确保震中五角星始终在最上层，不被注记等图层遮挡
    layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
    if layers_to_set:
        map_item.setLayers(layers_to_set)
        map_item.setKeepLayerSet(True)
    map_item.invalidateCache()

    # 添加经纬度网格
    _setup_map_grid(map_item, extent)
    # 添加指北针
    _add_north_arrow(layout, map_height_mm)
    # 添加比例尺
    _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)
    # 添加图例（位于主图左下角）
    _add_legend(layout, map_item, map_height_mm)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格

    参数:
        map_item: QgsLayoutItemMap, 地图项
        extent: QgsRectangle, 地图范围
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
    print("[信息] 经纬度网格设置完成")


def _add_north_arrow(layout, map_height_mm):
    """
    添加指北针

    参数:
        layout: QgsPrintLayout, 打印布局
        map_height_mm: float, 地图高度(毫米)
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    # 创建指北针SVG
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)
    print(f"[信息] 指北针添加完成")


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """
    添加比例尺

    参数:
        layout: QgsPrintLayout, 打印布局
        map_item: QgsLayoutItemMap, 地图项
        scale: int, 比例尺
        extent: QgsRectangle, 地图范围
        center_lat: float, 中心纬度
        map_height_mm: float, 地图高度(毫米)
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

    # 比例尺背景
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

    # 比例尺文本
    scale_label = QgsLayoutItemLabel(layout)
    scale_label.setText(f"1:{scale:,}")
    label_format = QgsTextFormat()
    label_format.setFont(QFont("Times New Roman", SCALE_FONT_SIZE_PT))
    label_format.setSize(SCALE_FONT_SIZE_PT)
    label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    label_format.setColor(QColor(0, 0, 0))
    scale_label.setTextFormat(label_format)
    scale_label.attemptMove(QgsLayoutPoint(sb_x, sb_y + 0.5, QgsUnitTypes.LayoutMillimeters))
    scale_label.attemptResize(QgsLayoutSize(sb_width, 4.5, QgsUnitTypes.LayoutMillimeters))
    scale_label.setHAlign(Qt.AlignHCenter)
    scale_label.setVAlign(Qt.AlignVCenter)
    scale_label.setFrameEnabled(False)
    scale_label.setBackgroundEnabled(False)
    layout.addLayoutItem(scale_label)

    # 比例尺条
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

    # 刻度标签
    label_y = bar_y + bar_h + 0.3
    label_h = 3.5
    tick_format = QgsTextFormat()
    tick_format.setFont(QFont("Times New Roman", SCALE_FONT_SIZE_PT))
    tick_format.setSize(SCALE_FONT_SIZE_PT)
    tick_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    tick_format.setColor(QColor(0, 0, 0))

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

    print(f"[信息] 比例尺添加完成，1:{scale:,}")


def _add_legend(layout, map_item, map_height_mm):
    """
    添加图例（位于主图左下角）
    - 基本图例项（震中、地级市、省界、市界、县界、烈度）采用3行2列布局
    - 滑坡和斜坡图例项单行展示

    参数:
        layout: QgsPrintLayout, 打印布局
        map_item: QgsLayoutItemMap, 地图项
        map_height_mm: float, 地图高度(毫米)
    """
    # 图例位置：主图左下角
    map_left = BORDER_LEFT_MM
    map_bottom = BORDER_TOP_MM + map_height_mm

    # 使用常量设置图例尺寸
    legend_width = LEGEND_WIDTH_MM
    legend_height = LEGEND_HEIGHT_MM
    legend_x = map_left
    legend_y = map_bottom - legend_height

    # 公共文本格式
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    item_format = QgsTextFormat()
    item_format.setFont(QFont("SimSun", LEGEND_ITEM_FONT_SIZE_PT))
    item_format.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format.setColor(QColor(0, 0, 0))

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

    # 基本图例项：3行2列（震中、地级市、省界、市界、县界、烈度）
    basic_legend_start_y = legend_y + 7.0

    col_count = 2  # 2列
    row_count = 3  # 3行
    left_pad = 2.0
    right_pad = 2.0
    col_gap = 1.0
    row_height = 7.0
    icon_width = 4.0
    icon_height = 2.5
    icon_text_gap = 1.0

    available_width = legend_width - left_pad - right_pad - (col_count - 1) * col_gap
    col_width = available_width / col_count

    # 图例项：3行2列排列
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度", "solid_line_black"),
    ]

    for idx, (display_name, draw_type) in enumerate(legend_items):
        row = idx // col_count
        col = idx % col_count

        item_x = legend_x + left_pad + col * (col_width + col_gap)
        item_y = basic_legend_start_y + row * row_height
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
        elif draw_type == "solid_line_black":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            INTENSITY_LEGEND_COLOR, INTENSITY_LEGEND_LINE_WIDTH_MM)

        text_x = item_x + icon_width + icon_text_gap
        text_width = col_width - icon_width - icon_text_gap

        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(display_name)
        text_label.setTextFormat(item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, row_height - 1.0, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    basic_legend_height = row_count * row_height

    # 滑坡和斜坡图例项：单行展示（两个图例项水平排列在同一行）
    hazard_legend_start_y = basic_legend_start_y + basic_legend_height
    hazard_row_height = 7.0
    hazard_icon_width = 4.0
    hazard_icon_text_gap = 1.0

    # 滑坡和斜坡在同一行，使用与基本图例相同的列布局
    hazard_items = [
        ("滑坡", LANDSLIDE_COLOR, LANDSLIDE_STROKE_COLOR, LANDSLIDE_STROKE_WIDTH_MM, LANDSLIDE_SIZE_MM),
        ("危险斜坡", SLOPE_COLOR, SLOPE_STROKE_COLOR, SLOPE_STROKE_WIDTH_MM, SLOPE_SIZE_MM),
    ]

    for idx, (display_name, fill_color, stroke_color, stroke_width, size) in enumerate(hazard_items):
        col = idx % col_count  # 两个图例项在同一行的两列
        row = 0  # 同一行

        item_x = legend_x + left_pad + col * (col_width + col_gap)
        item_y = hazard_legend_start_y + row * hazard_row_height
        icon_center_y = item_y + hazard_row_height / 2.0

        # 绘制带边框的圆点图标
        _draw_point_icon_with_stroke(layout, item_x, icon_center_y, hazard_icon_width,
                                     fill_color, stroke_color, stroke_width, size)

        text_x = item_x + hazard_icon_width + hazard_icon_text_gap
        text_width = col_width - hazard_icon_width - hazard_icon_text_gap

        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(display_name)
        text_label.setTextFormat(item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, hazard_row_height - 1.0, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    print("[信息] 图例添加完成（位于主图左下角）")


def _draw_star_icon(layout, x, center_y, width, height):
    """
    在图例中绘制红色五角星图标

    参数:
        layout: QgsPrintLayout, 打印布局
        x: float, X坐标
        center_y: float, 中心Y坐标
        width: float, 图标宽度
        height: float, 图标高度
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
        layout: QgsPrintLayout, 打印布局
        x: float, X坐标
        center_y: float, 中心Y坐标
        width: float, 图标宽度
        height: float, 图标高度
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


def _draw_line_icon(layout, x, center_y, width, color, line_width_mm):
    """
    在图例中绘制实线图标

    参数:
        layout: QgsPrintLayout, 打印布局
        x: float, X坐标
        center_y: float, 中心Y坐标
        width: float, 图标宽度
        color: QColor, 线条颜色
        line_width_mm: float, 线宽(毫米)
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
        layout: QgsPrintLayout, 打印布局
        x: float, 起始X坐标
        center_y: float, 中心Y坐标
        width: float, 图标总宽度
        color: QColor, 线条颜色
        line_width_mm: float, 线宽(毫米)
        dash_gap_mm: float, 虚线间隔(毫米)
    """
    line_height = max(line_width_mm, 0.5)
    color_str = f"{color.red()},{color.green()},{color.blue()},255"

    # 虚线参数
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


def _draw_point_icon_with_stroke(layout, x, center_y, width, fill_color, stroke_color, stroke_width_mm, size_mm):
    """
    在图例中绘制带边框的圆点图标

    参数:
        layout: QgsPrintLayout, 打印布局
        x: float, X坐标
        center_y: float, 中心Y坐标
        width: float, 图标宽度
        fill_color: QColor, 填充颜色
        stroke_color: QColor, 边框颜色
        stroke_width_mm: float, 边框宽度(毫米)
        size_mm: float, 圆点大小(毫米)
    """
    center_x = x + width / 2.0

    circle = QgsLayoutItemShape(layout)
    circle.setShapeType(QgsLayoutItemShape.Ellipse)
    circle.attemptMove(QgsLayoutPoint(center_x - size_mm / 2.0, center_y - size_mm / 2.0,
                                      QgsUnitTypes.LayoutMillimeters))
    circle.attemptResize(QgsLayoutSize(size_mm, size_mm, QgsUnitTypes.LayoutMillimeters))

    fill_color_str = f"{fill_color.red()},{fill_color.green()},{fill_color.blue()},255"
    stroke_color_str = f"{stroke_color.red()},{stroke_color.green()},{stroke_color.blue()},255"
    circle_symbol = QgsFillSymbol.createSimple({
        'color': fill_color_str,
        'outline_color': stroke_color_str,
        'outline_width': str(stroke_width_mm),
        'outline_width_unit': 'MM',
    })
    circle.setSymbol(circle_symbol)
    circle.setFrameEnabled(False)
    layout.addLayoutItem(circle)


# ============================================================
# 主生成函数
# ============================================================
def generate_earthquake_landslide_slope_map(longitude, latitude, magnitude,
                                            output_path="output_landslide_slope_map.png",
                                            kml_path=None,
                                            basemap_path=None, annotation_path=None):
    """
    生成地震震中历史滑坡、斜坡分布图（主入口函数）

    参数:
        longitude: float, 震中经度
        latitude: float, 震中纬度
        magnitude: float, 地震震级
        output_path: str, 输出文件路径
        kml_path: str或None, 烈度圈KML文件路径

    返回:
        tuple, (str或None, str), 成功返回(输出文件路径, 统计信息)，失败返回(None, 统计信息)
    """
    logger.info('开始生成滑坡斜坡分布图: lon=%.4f lat=%.4f M=%.1f output=%s',
                longitude, latitude, magnitude, output_path)
    try:
        return _generate_earthquake_landslide_slope_map_impl(
            longitude, latitude, magnitude, output_path, kml_path,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成滑坡斜坡分布图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_landslide_slope_map_impl(longitude, latitude, magnitude,
                                                   output_path, kml_path,
                                                   basemap_path=None, annotation_path=None):
    """generate_earthquake_landslide_slope_map 的实际实现。"""
    print("=" * 60)
    print(f"[开始] 生成地震震中历史滑坡、斜坡分布图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print("=" * 60)

    # 获取震级配置
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    map_size_km = config["map_size_km"]
    print(f"[信息] 震级配置: 范围{map_size_km}km, 比例尺1:{scale}")

    # 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 计算地图高度
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    # 创建项目
    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # ============================================================
    # 直接下载天地图瓦片并生成本地栅格底图
    # ============================================================

    # 计算渲染像素尺寸（基于DPI和地图尺寸）
    width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
    height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

    # 下载天地图瓦片
    temp_basemap_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_temp_basemap.png"
    )
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_temp_annotation_landslide.png"
    )
    if basemap_path:
        basemap_raster = QgsRasterLayer(basemap_path, "天地图底图", "gdal")
        if not basemap_raster.isValid():
            basemap_raster = None
    else:
        basemap_raster = download_tianditu_basemap_tiles(extent, width_px, height_px, temp_basemap_path)
    if annotation_path:
        annotation_raster = QgsRasterLayer(annotation_path, "天地图注记", "gdal")
        if not annotation_raster.isValid():
            annotation_raster = None
    else:
        annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px, temp_annotation_path)

    # 添加本地栅格底图（如果下载成功）
    if basemap_raster:
        project.addMapLayer(basemap_raster)
    if annotation_raster:
        project.addMapLayer(annotation_raster)

    # 加载斜坡图层（先加载，显示在下层）
    slope_layer = load_slope_layer(SLOPE_SHP_PATH)
    if slope_layer:
        project.addMapLayer(slope_layer)

    # 加载滑坡图层（后加载，显示在上层）
    landslide_layer = load_landslide_layer(LANDSLIDE_SHP_PATH)
    if landslide_layer:
        project.addMapLayer(landslide_layer)

    # 统计范围内的滑坡和斜坡数量
    slope_count = count_features_in_extent(slope_layer, extent) if slope_layer else 0
    landslide_count = count_features_in_extent(landslide_layer, extent) if landslide_layer else 0
    stats_message = f"本次地震震中附近{int(map_size_km / 2)}km范围内有危险斜坡{slope_count}处，历史滑坡{landslide_count}处"
    print(f"[统计] {stats_message}")

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

    # 创建省份标注图层（单独图层，支持震中附近自动偏移）
    province_label_layer = None
    if province_layer:
        province_label_layer = create_province_label_layer(province_layer, longitude, latitude, extent)
        if province_label_layer:
            project.addMapLayer(province_label_layer)

    # 加载地级市点位图层
    city_point_layer = create_city_point_layer(extent)
    if city_point_layer:
        project.addMapLayer(city_point_layer)

    # 创建图例用图层
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

    # 解析烈度圈KML
    intensity_data = []
    intensity_layer = None
    if kml_path:
        abs_kml = kml_path
        if not os.path.isabs(kml_path):
            abs_kml = resolve_path(kml_path)
        intensity_data = parse_intensity_kml(abs_kml)
        if intensity_data:
            intensity_layer = create_intensity_layer(intensity_data)
            if intensity_layer:
                project.addMapLayer(intensity_layer)

    # 创建震中图层
    epicenter_layer = create_epicenter_layer(longitude, latitude)
    if epicenter_layer:
        project.addMapLayer(epicenter_layer)

    # 创建打印布局（按正确渲染顺序传入图层：列表第一项在最上层）
    ordered_layers = [lyr for lyr in [
        epicenter_layer,
        province_label_layer,
        annotation_raster,
        intensity_layer,
        city_point_layer,
        province_layer,
        city_layer,
        county_layer,
        landslide_layer,
        slope_layer,
        basemap_raster,
    ] if lyr is not None]
    layout = create_print_layout(project, longitude, latitude, magnitude,
                                 extent, scale, map_height_mm, ordered_layers)

    # 导出为PNG
    result = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    # 清理临时SVG文件
    svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_temp.svg")
    if os.path.exists(svg_temp):
        try:
            os.remove(svg_temp)
        except OSError:
            pass

    # 清理临时底图文件
    if not basemap_path and os.path.exists(temp_basemap_path):
        try:
            os.remove(temp_basemap_path)
            pgw_path = temp_basemap_path.replace(".png", ".pgw")
            if os.path.exists(pgw_path):
                os.remove(pgw_path)
        except OSError:
            pass
    if not annotation_path and os.path.exists(temp_annotation_path):
        try:
            os.remove(temp_annotation_path)
            pgw_path = temp_annotation_path.replace(".png", ".pgw")
            if os.path.exists(pgw_path):
                os.remove(pgw_path)
        except OSError:
            pass

    print("=" * 60)
    if result:
        print(f"[完成] 历史滑坡、斜坡分布图已输出: {result}")
    else:
        print("[失败] 历史滑坡、斜坡分布图输出失败")
    print("=" * 60)
    return result, stats_message


def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片

    参数:
        layout: QgsPrintLayout, 打印布局对象
        output_path: str, 输出文件路径
        dpi: int, 输出分辨率

    返回:
        str或None, 成功返回输出文件路径，失败返回None
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
# 程序入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) >= 4:
        try:
            lon = float(sys.argv[1])
            lat = float(sys.argv[2])
            mag = float(sys.argv[3])
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_landslide_slope_M{mag}_{lon}_{lat}.png"
            kml = sys.argv[5] if len(sys.argv) > 5 else None
            result, stats = generate_earthquake_landslide_slope_map(lon, lat, mag, out, kml)
            print(f"\n{stats}")
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_landslide_slope_map.py <经度> <纬度> <震级> [输出文件名] [kml路径]")
    else:
        print("使用默认参数运行（示例地震 M7.8）...")
        result, stats = generate_earthquake_landslide_slope_map(
            longitude=118.18, latitude=39.63,
            magnitude=7.8, output_path="earthquake_landslide_slope_M7.8.png"
        )
        print(f"\n{stats}")
        print(f"\n{result}")

