# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中人口公里网格分布图生成脚本
参考 earthquake_geological_map2.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

主要功能：
- 加载人口公里网格分布图TIF文件作为底图
- 使用渐变色渲染：rgb(245,243,0) → rgb(245,0,0)
- 人口密度分5档展示图例：
    第一档：0~100人/平方公里
    第二档：100~200人/平方公里
    第三档：200~500人/平方公里
    第四档：500~1000人/平方公里
    第五档：＞1000人/平方公里
- 图例位于主图左下角
- 基本图例项（震中、地级市、省界、市界、县界、烈度）采用3行2列布局
"""

import os
import sys
import math
import re
import logging
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
logger = logging.getLogger('report.core.earthquake_population_map')

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
    QgsColorRampShader,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

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

# 人口公里网格分布图TIF文件
POPULATE_DIS_TIF_PATH = (
    getattr(_django_settings, 'POPULATION_TIF_PATH',
            _DEFAULT_BASE + '图6/全国人口密度-2023年数据.tif')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '图6/全国人口密度-2023年数据.tif'
)

# 省市边界数据
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
# 人口分布图不需要右侧大图例区域，调整为主图占满宽度
MAP_TOTAL_WIDTH_MM = 220.0
BORDER_LEFT_MM = 4.0
BORDER_TOP_MM = 4.0
BORDER_BOTTOM_MM = 2.0
BORDER_RIGHT_MM = 4.0
# 主图宽度 = 总宽度 - 左右边框
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

# === 省份标注与震中重合判断容差 ===
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_GAP_MM = 0.3
CITY_DASH_PATTERN = [8.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_GAP_MM = 0.2
COUNTY_DASH_PATTERN = [10.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 12
LEGEND_ITEM_FONT_SIZE_PT = 10
LEGEND_POPULATION_FONT_SIZE_PT = 10  # 人口图例字体大小

# === 图例尺寸 ===
LEGEND_WIDTH_MM = 35.0  # 图例宽度
LEGEND_HEIGHT_MM = 49.0  # 图例高度
# 新增：人口密度标题与下方图例间距（毫米）
LEGEND_POP_TITLE_GAP_MM = 5.5
# 新增：图例标题与上边框间距（毫米）
LEGEND_TITLE_TOP_GAP_MM = 1.3

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

# === 人口密度渐变色配置 ===
# 最左侧颜色：rgb(245, 243, 0) - 黄色
# 最右侧颜色：rgb(245, 0, 0) - 红色
POPULATION_COLOR_START = QColor(245, 243, 0)  # 黄色（低人口密度）
POPULATION_COLOR_END = QColor(245, 0, 0)  # 红色（高人口密度）

# === 人口密度分档配置 ===
# 单位：人/平方公里
# 注意：分档时使用 100.0001~200 这样的边界避免重叠
POPULATION_BREAKS = [
    {"min": 0, "max": 100, "label": "0~100"},
    {"min": 100.0001, "max": 200, "label": "100~200"},
    {"min": 200.0001, "max": 500, "label": "200~500"},
    {"min": 500.0001, "max": 1000, "label": "500~1000"},
    {"min": 1000.0001, "max": 100000, "label": ">1000"},  # 使用较大值作为上限
]

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数。

    参数:
        magnitude: 地震震级（浮点数）

    返回:
        dict: 包含radius_km、map_size_km、scale等配置的字典
    """
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度(km)计算地图范围(WGS84坐标)。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        half_size_km: 地图半幅宽度（公里）

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
    根据地图范围和宽度计算地图高度（保持宽高比）。

    参数:
        extent: QgsRectangle地图范围
        map_width_mm: 地图宽度（毫米）

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
    将相对路径转换为绝对路径。

    参数:
        relative_path: 相对于脚本所在目录的路径

    返回:
        str: 绝对路径
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字。

    参数:
        num: 阿拉伯数字（整数）

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
    根据地理范围选择合适的经纬度刻度间隔。

    参数:
        range_deg: 地理范围（度）
        target_min: 目标最小刻度数量
        target_max: 目标最大刻度数量

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
    创建指北针SVG文件。

    参数:
        output_path: SVG文件输出路径

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
    在矢量图层的字段列表中查找名称字段。

    参数:
        layer: QgsVectorLayer矢量图层
        candidates: 候选字段名列表

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


def interpolate_color(color1, color2, ratio):
    """
    在两个颜色之间进行线性插值。

    参数:
        color1: 起始颜色（QColor）
        color2: 结束颜色（QColor）
        ratio: 插值比例（0.0~1.0）

    返回:
        QColor: 插值后的颜色
    """
    r = int(color1.red() + (color2.red() - color1.red()) * ratio)
    g = int(color1.green() + (color2.green() - color1.green()) * ratio)
    b = int(color1.blue() + (color2.blue() - color1.blue()) * ratio)
    return QColor(r, g, b)


def get_population_color_for_value(value, max_value=1000):
    """
    根据人口密度值获取对应的渐变颜色。

    参数:
        value: 人口密度值（人/平方公里）
        max_value: 用于归一化的最大值

    返回:
        QColor: 对应的颜色
    """
    # 将值归一化到0~1范围
    ratio = min(value / max_value, 1.0)
    return interpolate_color(POPULATION_COLOR_START, POPULATION_COLOR_END, ratio)


def build_population_legend_list():
    """
    ���建人口密度图例列表。

    返回:
        list: [(min_val, max_val, color, label), ...]
        每个元素包含：最小值、最大值、颜色、标签
    """
    result = []

    # 定义5个分档
    breaks = [
        (0, 100, "0~100"),
        (100, 200, "100~200"),
        (200, 500, "200~500"),
        (500, 1000, "500~1000"),
        (1000, float('inf'), ">1000"),
    ]

    # 最大值用于颜色计算的归一化
    max_val_for_color = 1000

    for min_val, max_val, label in breaks:
        # 使用区间中点计算颜色，最后一档使用最大值颜色
        if max_val == float('inf'):
            color_val = max_val_for_color
        else:
            color_val = (min_val + max_val) / 2

        color = get_population_color_for_value(color_val, max_val_for_color)
        color_rgba = (color.red(), color.green(), color.blue(), 255)
        result.append((min_val, max_val, color_rgba, label))

    return result


# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据。

    参数:
        kml_path: KML文件路径

    返回:
        list: [{"intensity": int, "coords": [(lon, lat), ...]}, ...]
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
    从Placemark名称中提取烈度值。

    参数:
        name: Placemark名称字符串

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
    从Placemark中提取LineString坐标。

    参数:
        placemark: XML Placemark元素
        ns: XML命名空间

    返回:
        list: [(lon, lat), ...]坐标列表
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
    解析KML坐标文本为(lon, lat)元组列表。

    参数:
        text: KML坐标文本

    返回:
        list: [(lon, lat), ...]
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
    根据解析的烈度圈数据创建QGIS矢量图层。

    参数:
        intensity_data: 烈度圈数据列表

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

    # 设置烈度圈样式：白色光晕 + 黑色线条
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
    配置烈度圈图层的标注。

    参数:
        layer: QgsVectorLayer烈度圈图层
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

def load_population_raster(tif_path):
    """
    加载人口公里网格分布图TIF栅格图层并应用渐变色渲染。

    参数:
        tif_path: TIF文件路径（相对路径）

    返回:
        QgsRasterLayer: 加载并渲染后的栅格图层
    """
    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 人口分布图文件不存在: {abs_path}")
        return None

    layer = QgsRasterLayer(abs_path, "人口公里网格分布图")
    if not layer.isValid():
        print(f"[错误] 无法加载人口分布图: {abs_path}")
        return None

    # 应用渐变色渲染
    _apply_population_gradient_renderer(layer)

    print(f"[信息] 成功加载人口分布图: {abs_path}")
    return layer


def _apply_population_gradient_renderer(raster_layer):
    """
    为人口分布图栅格图层应用渐变色渲染。
    颜色从黄色(245,243,0)渐变到红色(245,0,0)。

    参数:
        raster_layer: QgsRasterLayer栅格图层
    """
    if raster_layer is None or not raster_layer.isValid():
        print("[错误] 无效的栅格图层")
        return

    # 获取栅格数据的统计信息
    provider = raster_layer.dataProvider()
    band = 1  # 假设是单波段

    # 尝试获取统计信息
    stats = provider.bandStatistics(band)
    min_val = stats.minimumValue
    max_val = stats.maximumValue

    # 如果统计信息无效，使用默认值
    if min_val >= max_val:
        min_val = 0
        max_val = 1000

    print(f"[信息] 人口密度数据范围: {min_val:.2f} ~ {max_val:.2f}")

    # 创建颜色渐变着色器
    color_ramp_shader = QgsColorRampShader()
    color_ramp_shader.setColorRampType(QgsColorRampShader.Interpolated)

    # 定义颜色渐变项
    # 从黄色(245,243,0)到红色(245,0,0)
    color_items = []

    # 分档断点值
    break_values = [0, 100, 200, 500, 1000]

    for value in break_values:
        if value <= max_val:
            # 计算颜色插值比例
            if value <= 0:
                ratio = 0.0
            elif value >= 1000:
                ratio = 1.0
            else:
                ratio = value / 1000.0

            color = interpolate_color(POPULATION_COLOR_START, POPULATION_COLOR_END, ratio)
            color_item = QgsColorRampShader.ColorRampItem(value, color, f"{value}")
            color_items.append(color_item)

    # 添加最大值颜色（红色）
    if max_val > 1000:
        color_item = QgsColorRampShader.ColorRampItem(max_val, POPULATION_COLOR_END, f"{max_val:.0f}")
        color_items.append(color_item)

    color_ramp_shader.setColorRampItemList(color_items)

    # 创建栅格着色器
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(color_ramp_shader)

    # 创建伪彩色渲染器
    renderer = QgsSingleBandPseudoColorRenderer(provider, band, raster_shader)

    # 应用渲染器
    raster_layer.setRenderer(renderer)
    raster_layer.triggerRepaint()

    print(f"[信息] 人口分布图渐变色渲染已应用: 黄色(245,243,0) -> 红色(245,0,0)")


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）。

    参数:
        shp_path: SHP文件路径（相对路径）
        layer_name: 图层名称

    返回:
        QgsVectorLayer: 矢量图层
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


def style_province_layer(layer, center_lon=None, center_lat=None, extent=None):
    """
    设置省界图层样式。

    参数:
        layer: QgsVectorLayer省界图层
        center_lon (float 或 None): 震中经度，用于标注偏移判断
        center_lat (float 或 None): 震中纬度，用于标注偏移判断
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量
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
    设置市界图层样式。
    颜色: R=160, G=160, B=160
    线宽: 0.24mm
    虚线间隔: 0.3mm

    参数:
        layer: QgsVectorLayer市界图层
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
    print(f"[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """
    设置县界图层样式。
    颜色: R=160, G=160, B=160
    线宽: 0.14mm
    虚线间隔: 0.2mm

    参数:
        layer: QgsVectorLayer县界图层
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
    print(f"[信息] 县界图层样式设置完成")


def _setup_province_labels(layer):
    """
    配置省界图层标注。

    参数:
        layer: QgsVectorLayer省界图层
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
    为点图层配置标注。

    参数:
        layer: QgsVectorLayer点图层
        field_name: 标注字段名
        font_size_pt: 字体大小（点）
        color: 字体颜色
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


# ============================================================
# 震中和图例图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层：红色五角星+白边。

    参数:
        longitude: 震中经度
        latitude: 震中纬度

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
    加载地级市点位数据（不显示标注，只显示点位）。

    参数:
        extent: QgsRectangle地图范围

    返回:
        QgsVectorLayer: 地级市点位图层
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
    创建烈度图例用的线图层。

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
    print("[信息] 创建烈度图例图层")
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的线图层。

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
    print("[信息] 创建省界图例线图层")
    return layer


def create_city_legend_layer():
    """
    创建市界图例用的线图层。
    线宽: 0.24mm，虚线间隔: 0.3mm

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
    print(f"[信息] 创建市界图例线图层")
    return layer


def create_county_legend_layer():
    """
    创建县界图例用的线图层。
    线宽: 0.14mm，虚线间隔: 0.2mm

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
    print(f"[信息] 创建县界图例线图层")
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm, ordered_layers=None):
    """
    创建QGIS打印布局。

    参数:
        project: QgsProject项目对象
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        extent: 地图范围
        scale: 比例尺
        map_height_mm: 地图高度（毫米）

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震人口分布图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm,
                                   QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

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
    if ordered_layers is not None:
        map_item.setLayers(ordered_layers)
        map_item.setKeepLayerSet(True)

    # 添加地图元素
    _setup_map_grid(map_item, extent)
    _add_north_arrow(layout, map_height_mm)
    _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)

    # 添加图例到左下角
    _add_population_legend(layout, map_item, project, map_height_mm, output_height_mm)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格。

    参数:
        map_item: QgsLayoutItemMap地图项
        extent: 地图范围
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
    添加指北针到地图右上角。

    参数:
        layout: QgsPrintLayout布局对象
        map_height_mm: 地图高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

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
    添加比例尺到地图右下角。

    参数:
        layout: QgsPrintLayout布局对象
        map_item: QgsLayoutItemMap地图项
        scale: 比例尺数值
        extent: 地图范围
        center_lat: 中心纬度
        map_height_mm: 地图高度（毫米）
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


def _add_population_legend(layout, map_item, project, map_height_mm, output_height_mm):
    """
    添加人口密度图例到主图左下角。
    图例位置：左边框与主图左边框对齐，下边框与主图下边框对齐。

    包含内容：
    - 基本图例项（震中、地级市、省界、市界、县界、烈度）采用3行2列布局
    - 人口密度分5档图例（带渐变色块）

    参数:
        layout: QgsPrintLayout布局对象
        map_item: QgsLayoutItemMap地图项
        project: QgsProject项目对象
        map_height_mm: 地图高度（毫米）
        output_height_mm: 输出总高度（毫米）
    """
    # 图例位置：主图左下角
    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM
    map_bottom = map_top + map_height_mm

    legend_width = LEGEND_WIDTH_MM
    legend_height = LEGEND_HEIGHT_MM

    # 图例左边框与主图左边框对齐，下边框与主图下边框对齐
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

    # 人口图例文本格式
    population_format = QgsTextFormat()
    population_format.setFont(QFont("Times New Roman", LEGEND_POPULATION_FONT_SIZE_PT))  # 修改为Times New Roman
    population_format.setSize(LEGEND_POPULATION_FONT_SIZE_PT)
    population_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    population_format.setColor(QColor(0, 0, 0))

    # 图例背景
    legend_bg = QgsLayoutItemShape(layout)
    legend_bg.setShapeType(QgsLayoutItemShape.Rectangle)
    legend_bg.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    legend_bg.attemptResize(QgsLayoutSize(legend_width, legend_height, QgsUnitTypes.LayoutMillimeters))
    legend_bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,230',  # 半透明白色背景
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
    # 使用可调节的上边距
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + LEGEND_TITLE_TOP_GAP_MM, QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(legend_width, 4.0, QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # ========================================
    # 基本图例项：3行2列平行排列
    # 第一行：震中、地级市、省界
    # 第二行：市界、县界、烈度
    # ========================================
    basic_legend_start_y = legend_y + 5.5

    # 3行2列布局参数
    col_count = 2  # 列数
    row_count = 3  # 行数
    left_pad = 2.0  # 左边距
    right_pad = 2.0  # 右边距
    col_gap = 1.0  # 列间距
    row_height = 4.5  # 行高
    icon_width = 4.0  # 图标宽度
    icon_height = 2.5  # 图标高度
    icon_text_gap = 0.5  # 图标与文字间距

    # 计算每列宽度
    available_width = legend_width - left_pad - right_pad - (col_count - 1) * col_gap
    col_width = available_width / col_count

    # 基本图例项定义：按3行2列顺序排列
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度", "solid_line_black"),
    ]

    for idx, (display_name, draw_type) in enumerate(legend_items):
        # 计算当前项的行列位置
        row = idx // col_count  # 行号：0或1
        col = idx % col_count  # 列号：0、1或2

        # 计算当前项的位置
        item_x = legend_x + left_pad + col * (col_width + col_gap)
        item_y = basic_legend_start_y + row * row_height
        icon_center_y = item_y + row_height / 2.0

        # 绘制图标
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

        # 绘制文字标签
        text_x = item_x + icon_width + icon_text_gap
        text_width = col_width - icon_width - icon_text_gap

        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(display_name)
        text_label.setTextFormat(item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y + 0.3, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, row_height - 0.6, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    # 基本图例区域高度
    basic_legend_height = row_count * row_height

    # ========================================
    # 人口密度图例：分5档显示
    # ========================================
    pop_legend_start_y = basic_legend_start_y + basic_legend_height + 2.0

    # 人口密度标题：三段式组合"人口密度(" + "人/km²" + ")"
    # 仅单位部分"人/km²"使用Times New Roman字体，其余使用SimHei字体
    pop_title_cn_format = QgsTextFormat()
    pop_title_cn_font = QFont("SimHei")
    pop_title_cn_font.setPointSizeF(float(LEGEND_ITEM_FONT_SIZE_PT))
    pop_title_cn_format.setFont(pop_title_cn_font)
    pop_title_cn_format.setSize(float(LEGEND_ITEM_FONT_SIZE_PT))
    pop_title_cn_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    pop_title_cn_format.setColor(QColor(0, 0, 0))

    pop_title_tnr_format = QgsTextFormat()
    pop_title_tnr_font = QFont("Times New Roman")
    pop_title_tnr_font.setPointSizeF(float(LEGEND_ITEM_FONT_SIZE_PT))
    pop_title_tnr_format.setFont(pop_title_tnr_font)
    pop_title_tnr_format.setSize(float(LEGEND_ITEM_FONT_SIZE_PT))
    pop_title_tnr_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    pop_title_tnr_format.setColor(QColor(0, 0, 0))

    # 以组合方式居中显示"人口密度(人/km²)"，其中仅 人/km² 使用 Times New Roman
    _pop_title_left_w = 20.5   # "人口密度(" 部分宽度（SimHei）
    _pop_title_unit_w = 7.5    # "人/km²" 部分宽度（Times New Roman）
    _pop_title_right_w = 6.0   # ")" 部分宽度（SimHei）
    title_group_width = _pop_title_left_w + _pop_title_unit_w + _pop_title_right_w
    title_group_x = legend_x + (legend_width - title_group_width) / 2.0

    pop_title_cn_left = QgsLayoutItemLabel(layout)
    pop_title_cn_left.setText("人口密度( ")
    pop_title_cn_left.setTextFormat(pop_title_cn_format)
    pop_title_cn_left.attemptMove(QgsLayoutPoint(title_group_x, pop_legend_start_y,
                                                  QgsUnitTypes.LayoutMillimeters))
    pop_title_cn_left.attemptResize(QgsLayoutSize(_pop_title_left_w, 4.5, QgsUnitTypes.LayoutMillimeters))
    pop_title_cn_left.setHAlign(Qt.AlignRight)
    pop_title_cn_left.setVAlign(Qt.AlignVCenter)
    pop_title_cn_left.setFrameEnabled(False)
    pop_title_cn_left.setBackgroundEnabled(False)
    layout.addLayoutItem(pop_title_cn_left)

    pop_title_unit = QgsLayoutItemLabel(layout)
    pop_title_unit.setText("人/km²")
    pop_title_unit.setTextFormat(pop_title_tnr_format)
    # 轻微上移，修正 Times New Roman 基线偏低问题
    pop_title_unit.attemptMove(QgsLayoutPoint(title_group_x + _pop_title_left_w, pop_legend_start_y - 0.4,
                                              QgsUnitTypes.LayoutMillimeters))
    pop_title_unit.attemptResize(QgsLayoutSize(_pop_title_unit_w, 4.5, QgsUnitTypes.LayoutMillimeters))
    pop_title_unit.setHAlign(Qt.AlignHCenter)
    pop_title_unit.setVAlign(Qt.AlignVCenter)
    pop_title_unit.setFrameEnabled(False)
    pop_title_unit.setBackgroundEnabled(False)
    layout.addLayoutItem(pop_title_unit)

    pop_title_cn_right = QgsLayoutItemLabel(layout)
    pop_title_cn_right.setText(" )")
    pop_title_cn_right.setTextFormat(pop_title_cn_format)
    pop_title_cn_right.attemptMove(QgsLayoutPoint(title_group_x + _pop_title_left_w + _pop_title_unit_w,
                                                   pop_legend_start_y,
                                                   QgsUnitTypes.LayoutMillimeters))
    pop_title_cn_right.attemptResize(QgsLayoutSize(_pop_title_right_w, 4.5, QgsUnitTypes.LayoutMillimeters))
    pop_title_cn_right.setHAlign(Qt.AlignLeft)
    pop_title_cn_right.setVAlign(Qt.AlignVCenter)
    pop_title_cn_right.setFrameEnabled(False)
    pop_title_cn_right.setBackgroundEnabled(False)
    layout.addLayoutItem(pop_title_cn_right)

    # 人口密度分档色块和标签
    # 修改为可调节间距
    pop_item_start_y = pop_legend_start_y + LEGEND_POP_TITLE_GAP_MM
    pop_icon_width = 5.0  # 色块宽度
    pop_icon_height = 2.5  # 色块高度
    pop_item_height = 4  # 每项高度
    pop_gap = 1.5  # 色块与文字间距

    population_legend_list = build_population_legend_list()

    for idx, (min_val, max_val, color_rgba, label) in enumerate(population_legend_list):
        item_y = pop_item_start_y + idx * pop_item_height

        # 绘制色块
        color_box = QgsLayoutItemShape(layout)
        color_box.setShapeType(QgsLayoutItemShape.Rectangle)
        color_box.attemptMove(QgsLayoutPoint(legend_x + left_pad, item_y, QgsUnitTypes.LayoutMillimeters))
        color_box.attemptResize(QgsLayoutSize(pop_icon_width, pop_icon_height, QgsUnitTypes.LayoutMillimeters))
        color_str = f"{color_rgba[0]},{color_rgba[1]},{color_rgba[2]},{color_rgba[3]}"
        box_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_color': '80,80,80,255',
            'outline_width': '0.1',
            'outline_width_unit': 'MM',
        })
        color_box.setSymbol(box_symbol)
        color_box.setFrameEnabled(False)
        layout.addLayoutItem(color_box)

        # 绘制标签
        text_x = legend_x + left_pad + pop_icon_width + pop_gap
        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(label)
        text_label.setTextFormat(population_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(legend_width - left_pad - pop_icon_width - pop_gap - right_pad,
                                               pop_icon_height, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    print(f"[信息] 人口密度图例添加完成，共 {len(population_legend_list)} 档")
    print("[信息] 基本图例项采用3行2列布局")
    print("[信息] 图例添加完成（位于主图左下角）")


def _draw_star_icon(layout, x, center_y, width, height):
    """
    在图例中绘制红色五角星图标。

    参数:
        layout: QgsPrintLayout布局对象
        x: 起始X坐标（毫米）
        center_y: 中心Y坐标（毫米）
        width: 图标宽度（毫米）
        height: 图标高度（毫米）
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
    在图例中绘制地级市圆点图标。

    参数:
        layout: QgsPrintLayout布局对象
        x: 起始X坐标（毫米）
        center_y: 中心Y坐标（毫米）
        width: 图标宽度（毫米）
        height: 图标高度（毫米）
    """
    icon_size = min(width, height) * 0.6
    center_x = x + width / 2.0

    # 外圈（白色填充，黑色边框）
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

    # 内圈（黑色实心）
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
    在图例中绘制实线图标。

    参数:
        layout: QgsPrintLayout布局对象
        x: 起始X坐标（毫米）
        center_y: 中心Y坐标（毫米）
        width: 图标宽度（毫米）
        color: 线条颜色（QColor）
        line_width_mm: 线宽（毫米）
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
    在图例中绘制虚线图标。

    参数:
        layout: QgsPrintLayout布局对象
        x: 起始X坐标（毫米）
        center_y: 中心Y坐标（毫米）
        width: 图标总宽度（毫米）
        color: 线条颜色（QColor）
        line_width_mm: 线宽（毫米）
        dash_gap_mm: 虚线间隔（毫米）
    """
    line_height = max(line_width_mm, 0.5)
    color_str = f"{color.red()},{color.green()},{color.blue()},255"

    # 计算虚线段长度和间隔
    dash_length_mm = max(dash_gap_mm * 3.5, 0.8)
    pattern_length = dash_length_mm + dash_gap_mm

    # 绘制多个虚线段
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
# 主生成函数
# ============================================================

def generate_earthquake_population_map(longitude, latitude, magnitude,
                                       output_path="output_population_map.png",
                                       kml_path=None,
                                       basemap_path=None, annotation_path=None):
    """
    生成地震震中人口公里网格分布图（主入口函数）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 地震震级
        output_path: 输出PNG文件路径
        kml_path: 烈度圈KML文件路径（可选）

    返回:
        str: 成功返回输出文件路径，失败返回None
    """
    logger.info('开始生成人口分布图: lon=%.4f lat=%.4f M=%.1f output=%s',
                longitude, latitude, magnitude, output_path)
    try:
        return _generate_earthquake_population_map_impl(
            longitude, latitude, magnitude, output_path, kml_path,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成人口分布图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_population_map_impl(longitude, latitude, magnitude,
                                              output_path, kml_path,
                                              basemap_path=None, annotation_path=None):
    """generate_earthquake_population_map 的实际实现。"""
    print("=" * 60)
    print(f"[开始] 生成地震人口公里网格分布图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print("=" * 60)

    # 获取震级配置
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    # 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 计算地图尺寸
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from core.qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    # 创建项目
    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 临时注记底图文件路径
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_temp_annotation_population.png"
    )

    try:
        # ============================================================
        # 下载天地图矢量注记瓦片（只下载注记，不下载底图）
        # ============================================================
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        if annotation_path:
            annotation_raster = QgsRasterLayer(annotation_path, "天地图注记", "gdal")
            if not annotation_raster.isValid():
                annotation_raster = None
        else:
            annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px, temp_annotation_path)

        # 加载人口分布图底图（带渐变色渲染）
        population_layer = load_population_raster(POPULATE_DIS_TIF_PATH)
        if population_layer:
            project.addMapLayer(population_layer)

        # 加载行政边界图层
        county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界_地图")
        if county_layer:
            style_county_layer(county_layer)
            project.addMapLayer(county_layer)

        city_layer = load_vector_layer(CITY_SHP_PATH, "市界_地图")
        if city_layer:
            style_city_layer(city_layer)
            project.addMapLayer(city_layer)

        province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界_地图")
        if province_layer:
            style_province_layer(province_layer, center_lon=longitude, center_lat=latitude, extent=extent)
            project.addMapLayer(province_layer)

        # 创建省份标注图层（支持震中附近省份标注自动偏移）
        province_label_layer = None
        if province_layer:
            province_label_layer = create_province_label_layer(province_layer, longitude, latitude, extent)
            if province_label_layer:
                project.addMapLayer(province_label_layer)

        # 加载地级市点位
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

        # 解析并添加烈度圈
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

        # 添加注记图层到项目（如果下载成功）
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # 创建打印布局
        # 按渲染顺序排列图层（列表第一项在最上层）
        # 震中图层放在最上层，确保震中五角星不被注记图层遮挡
        ordered_layers = [lyr for lyr in [
            epicenter_layer,        # 震中放在最上层，显示在注记之上
            annotation_raster,      # 天地图注记
            intensity_layer,
            city_point_layer,
            province_label_layer,   # 省份标注图层（在省界图层之上）
            province_layer,
            city_layer,
            county_layer,
            population_layer,
        ] if lyr is not None]

        layout = create_print_layout(project, longitude, latitude, magnitude,
                                     extent, scale, map_height_mm,
                                     ordered_layers=ordered_layers)

        # 导出PNG
        result = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    finally:
        # 清理临时文件
        svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_temp.svg")
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
    if result:
        print(f"[完成] 人口分布图已输出: {result}")
    else:
        print("[失败] 人口分布图输出失败")
    print("=" * 60)
    return result


def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片。

    参数:
        layout: QgsPrintLayout布局对象
        output_path: 输出文件路径
        dpi: 输出分辨率（默认150）

    返回:
        str: 成功返回文件绝对路径，失败返回None
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
# 测试方法
# ============================================================

def test_magnitude_config():
    """
    测试震级配置获取功能。
    验证不同震级返回正确的配置参数。
    """
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
    """
    测试地图范围计算功能。
    验证震中和半径参数能正确计算出地图范围。
    """
    print("\n--- 测试: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum()
    assert extent.yMinimum() < 39.9 < extent.yMaximum()
    delta_y = extent.yMaximum() - extent.yMinimum()
    assert abs(delta_y - 0.2703) < 0.02
    print(f"  15km半径范围: 纬度差{delta_y:.4f}° ✓")
    print("  所有范围计算测试通过 ✓")


def test_int_to_roman():
    """
    测试罗马数字转换功能。
    验证阿拉伯数字能正确转换为罗马数字。
    """
    print("\n--- 测试: int_to_roman ---")
    assert int_to_roman(4) == "IV"
    assert int_to_roman(5) == "V"
    assert int_to_roman(6) == "VI"
    assert int_to_roman(9) == "IX"
    assert int_to_roman(10) == "X"
    assert int_to_roman(12) == "XII"
    print("  IV=4, V=5, VI=6, IX=9, X=10, XII=12 ✓")
    print("  罗马数字转换测试通过 ✓")


def test_interpolate_color():
    """
    测试颜色插值功能。
    验证两个颜色之间的线性插值计算正确。
    """
    print("\n--- 测试: interpolate_color ---")

    # 测试起点颜色
    color_start = interpolate_color(POPULATION_COLOR_START, POPULATION_COLOR_END, 0.0)
    assert color_start.red() == POPULATION_COLOR_START.red()
    assert color_start.green() == POPULATION_COLOR_START.green()
    assert color_start.blue() == POPULATION_COLOR_START.blue()
    print(f"  ratio=0.0 -> RGB({color_start.red()},{color_start.green()},{color_start.blue()}) ✓")

    # 测试终点颜色
    color_end = interpolate_color(POPULATION_COLOR_START, POPULATION_COLOR_END, 1.0)
    assert color_end.red() == POPULATION_COLOR_END.red()
    assert color_end.green() == POPULATION_COLOR_END.green()
    assert color_end.blue() == POPULATION_COLOR_END.blue()
    print(f"  ratio=1.0 -> RGB({color_end.red()},{color_end.green()},{color_end.blue()}) ✓")

    # 测试中点颜色
    color_mid = interpolate_color(POPULATION_COLOR_START, POPULATION_COLOR_END, 0.5)
    expected_g = int((POPULATION_COLOR_START.green() + POPULATION_COLOR_END.green()) / 2)
    assert abs(color_mid.green() - expected_g) <= 1
    print(f"  ratio=0.5 -> RGB({color_mid.red()},{color_mid.green()},{color_mid.blue()}) ✓")

    print("  颜色插值测试通过 ✓")


def test_population_legend_list():
    """
    测试人口密度图例列表构建功能。
    验证返回正确的5档图例数据。
    """
    print("\n--- 测试: build_population_legend_list ---")

    legend_list = build_population_legend_list()

    # 验证返回5档
    assert len(legend_list) == 5
    print(f"  图例档数: {len(legend_list)} ✓")

    # 验证每档数据结构
    for idx, (min_val, max_val, color_rgba, label) in enumerate(legend_list):
        assert isinstance(min_val, (int, float))
        assert isinstance(max_val, (int, float))
        assert len(color_rgba) == 4
        assert isinstance(label, str)
        print(f"  第{idx + 1}档: {label} 人/km², 颜色RGB({color_rgba[0]},{color_rgba[1]},{color_rgba[2]}) ✓")

    # 验证分档标签
    expected_labels = ["0~100", "100~200", "200~500", "500~1000", ">1000"]
    actual_labels = [item[3] for item in legend_list]
    assert actual_labels == expected_labels
    print(f"  分档标签正确 ✓")

    print("  人口密度图例列表测试通过 ✓")


def test_population_color_gradient():
    """
    测试人口密度颜色渐变配置。
    验证起止颜色配置正确。
    """
    print("\n--- 测试: 人口密度颜色渐变配置 ---")

    # 验证起始颜色（黄色）
    assert POPULATION_COLOR_START.red() == 245
    assert POPULATION_COLOR_START.green() == 243
    assert POPULATION_COLOR_START.blue() == 0
    print(
        f"  起始颜色(低密度): RGB({POPULATION_COLOR_START.red()},{POPULATION_COLOR_START.green()},{POPULATION_COLOR_START.blue()}) ✓")

    # 验证结束颜色（红色）
    assert POPULATION_COLOR_END.red() == 245
    assert POPULATION_COLOR_END.green() == 0
    assert POPULATION_COLOR_END.blue() == 0
    print(
        f"  结束颜色(高密度): RGB({POPULATION_COLOR_END.red()},{POPULATION_COLOR_END.green()},{POPULATION_COLOR_END.blue()}) ✓")

    print("  人口密度颜色渐变配置测试通过 ✓")


def test_boundary_styles():
    """
    测试市界和县界样式参数。
    验证颜色、线宽、虚线间隔配置正确。
    """
    print("\n--- 测试: 市界和县界样式参数 ---")

    # 测试市界参数
    assert CITY_COLOR.red() == 160
    assert CITY_COLOR.green() == 160
    assert CITY_COLOR.blue() == 160
    assert CITY_LINE_WIDTH_MM == 0.24
    assert CITY_DASH_GAP_MM == 0.3
    print(f"  市界颜色: RGB({CITY_COLOR.red()},{CITY_COLOR.green()},{CITY_COLOR.blue()}) ✓")
    print(f"  市界线宽: {CITY_LINE_WIDTH_MM}mm ✓")
    print(f"  市界虚线间隔: {CITY_DASH_GAP_MM}mm ✓")

    # 测试县界参数
    assert COUNTY_COLOR.red() == 160
    assert COUNTY_COLOR.green() == 160
    assert COUNTY_COLOR.blue() == 160
    assert COUNTY_LINE_WIDTH_MM == 0.14
    assert COUNTY_DASH_GAP_MM == 0.2
    print(f"  县界颜色: RGB({COUNTY_COLOR.red()},{COUNTY_COLOR.green()},{COUNTY_COLOR.blue()}) ✓")
    print(f"  县界线宽: {COUNTY_LINE_WIDTH_MM}mm ✓")
    print(f"  县界虚线间隔: {COUNTY_DASH_GAP_MM}mm ✓")

    print("  市界和县界样式参数测试通过 ✓")


def test_tif_path():
    """
    测试人口分布图TIF文件路径配置。
    验证路径常量配置正确。
    """
    print("\n--- 测试: 人口分布图TIF文件路径 ---")

    assert POPULATE_DIS_TIF_PATH == "../../data/geology/图6/全国人口密度-2023年数据.tif"
    print(f"  TIF路径: {POPULATE_DIS_TIF_PATH} ✓")

    # 检查文件是否存在（如果数据文件可用）
    abs_path = resolve_path(POPULATE_DIS_TIF_PATH)
    if os.path.exists(abs_path):
        print(f"  文件存在: {abs_path} ✓")
    else:
        print(f"  [跳过] 文件不存在: {abs_path}")

    print("  人口分布图TIF文件路径测试完成")


def test_legend_layout():
    """
    测试图例布局配置。
    验证基本图例项采用3行2列布局。
    """
    print("\n--- 测试: 图例布局配置 ---")

    # 基本图例项定义
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度", "solid_line_black"),
    ]

    # 验证图例项数量为6（3行×2列）
    assert len(legend_items) == 6
    print(f"  图例项数量: {len(legend_items)} (3行×2列=6) ✓")

    # 验证3行2列布局
    col_count = 2
    row_count = 3

    for idx, (name, icon_type) in enumerate(legend_items):
        row = idx // col_count
        col = idx % col_count
        print(f"  {name}: 位于第{row + 1}行第{col + 1}列 (索引{idx}) ✓")

    # 验证第一行内容
    row1_items = [legend_items[i][0] for i in range(col_count)]
    assert row1_items == ["震中", "地级市", "省界"]
    print(f"  第一行: {row1_items} ✓")

    # 验证第二行内容
    row2_items = [legend_items[i][0] for i in range(col_count, col_count * 2)]
    assert row2_items == ["市界", "县界", "烈度"]
    print(f"  第二行: {row2_items} ✓")

    print("  图例布局配置测试通过 ✓")


def run_all_tests():
    """
    运行所有测试方法。
    """
    print("\n" + "=" * 60)
    print("运行 earthquake_population_map 全部测试")
    print("=" * 60)

    test_magnitude_config()
    test_calculate_extent()
    test_int_to_roman()
    test_interpolate_color()
    test_population_legend_list()
    test_population_color_gradient()
    test_boundary_styles()
    test_tif_path()
    test_legend_layout()

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
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_population_M{mag}_{lon}_{lat}.png"
            kml = sys.argv[5] if len(sys.argv) > 5 else None
            generate_earthquake_population_map(lon, lat, mag, out, kml)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_population_map.py <经度> <纬度> <震级> [输出文件名] [kml路径]")
    else:
        print("使用默认参数运行（唐山地震 M7.8）...")
        generate_earthquake_population_map(
            longitude=118.18, latitude=39.63,
            magnitude=7.8, output_path="earthquake_population_tangshan_M7.8.png"
        )

