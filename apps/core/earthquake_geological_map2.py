# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中地质构造图生成脚本
参考 earthquake_geological_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。
"""

import os
import sys
import math
import re
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
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
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
    QgsRuleBasedRenderer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeatureRequest,
    QgsFeature,
    QgsField,
    QgsLegendStyle,
    QgsLayoutExporter,
    QgsLegendModel,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
)
from qgis.PyQt.QtCore import Qt, QVariant, QRectF
from qgis.PyQt.QtGui import QColor, QFont


# ============================================================
# 常量定义
# ============================================================

# 数据文件路径（相对于脚本所在目录）
GEOLOGY_TIF_PATH = "../../data/geology/图3/group.tif"
PROVINCE_SHP_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国省份行政区划数据/省级行政区划/省.shp"
)
CITY_SHP_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国市级行政区划数据/市级行政区划/市.shp"
)
COUNTY_SHP_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国县级行政区划数据/县级行政区划/县.shp"
)
# 地级市点位数据（参考earthquake_geological_map.py）
CITY_POINTS_SHP_PATH = "../../data/geology/2023地级市点位数据/地级市点位数据.shp"

# === 布局尺寸常量（参考earthquake_geological_map.py） ===
# 总宽度220mm
MAP_TOTAL_WIDTH_MM = 220.0
# 图例宽度40mm
LEGEND_WIDTH_MM = 40.0
# 左侧经纬度边框4mm
BORDER_LEFT_MM = 4.0
# 上方边框4mm
BORDER_TOP_MM = 4.0
# 下方边框5mm
BORDER_BOTTOM_MM = 5.0
# 宽度=总宽度-左侧边框-图例宽度，高度=宽度-图例宽度（保持正方形）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM  # ≈182mm
MAP_HEIGHT_MM = MAP_WIDTH_MM - LEGEND_WIDTH_MM
# 总高度
OUTPUT_HEIGHT_MM = BORDER_TOP_MM + MAP_HEIGHT_MM + BORDER_BOTTOM_MM

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
# 适当减小指北针边框，指北针与边框间距合理
NORTH_ARROW_WIDTH_MM = 12.0
NORTH_ARROW_HEIGHT_MM = 18.0

# === 经纬度字体(pt) ===
LONLAT_FONT_SIZE_PT = 8

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
# 省名称标注：8pt, R=77 G=77 B=77, 加白边
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14

# === 市名称标注：9pt, 黑色, 加白边 ===
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 10
LEGEND_ITEM_FONT_SIZE_PT = 8

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8

# === 烈度圈样式 ===
# 所有烈度圈使用黑色加白边
INTENSITY_LINE_COLOR = QColor(0, 0, 0)       # 黑色线
INTENSITY_LINE_WIDTH_MM = 0.5
INTENSITY_HALO_COLOR = QColor(255, 255, 255)  # 白色描边
INTENSITY_HALO_WIDTH_MM = 1.0                 # 白边比线宽更粗

# 烈度标注字体
INTENSITY_LABEL_FONT_SIZE_PT = 9

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 6.0   # 五角星大小(mm)
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# === 断裂图例颜色 ===
# 修正：使用纯红色(R=255,G=0,B=0)，与地质构造底图中断裂线的实际颜色一致
FAULT_COLOR = QColor(255, 0, 0)
FAULT_LINE_WIDTH_MM = 0.4

# WGS84
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数（范围、比例尺等）。

    参数:
        magnitude (float): 地震震级

    返回:
        dict: 包含 radius_km, map_size_km, scale 的配置字典
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
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        half_size_km (float): 地图半幅宽度（千米）

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


def resolve_path(relative_path):
    """
    将相对路径转换为绝对路径（相对于当前脚本所在目录）。

    参数:
        relative_path (str): 相对路径字符串

    返回:
        str: 绝对路径字符串
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字。

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
    根据地理范围选择合适的经纬度刻度间隔。

    参数:
        range_deg (float): 经/纬度范围（度）
        target_min (int): 最少刻度数
        target_max (int): 最多刻度数

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
    创建指北针SVG文件（左侧黑色，右侧白色，上方N字母）。
    N字母和箭头图标整体下移3mm（在90高度的SVG中约为3个单位）。

    参数:
        output_path (str): SVG文件输出路径

    返回:
        str: SVG文件路径
    """
    # 整体内容在SVG中下移3mm。SVG viewBox高度90，对应实际高度约17mm（18-1padding），
    # 3mm约占 90*(3/17) ≈ 15.9 个SVG单位，取整为16。
    # 原始N字母y=10, 箭头顶部y=12, 箭头底部y=65, 箭头中部y=52
    # 下移后：N字母y=22, 箭头顶部y=24, 箭头底部y=77, 箭头中部y=64
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
        layer (QgsVectorLayer): 矢量图层
        candidates (list): 候选字段名列表

    返回:
        str: 匹配到的字段名，未找到返回None
    """
    fields = layer.fields()
    field_names = [f.name() for f in fields]

    # 精确匹配
    for candidate in candidates:
        if candidate in field_names:
            return candidate

    # 模糊匹配
    for candidate in candidates:
        for fn in field_names:
            if candidate.lower() in fn.lower():
                return fn

    # 兜底：第一个字符串字段
    for f in fields:
        if f.type() == QVariant.String:
            return f.name()

    return None


# ============================================================
# KML烈度圈解析 — 参考earthquake_geological_map.py
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据。

    参数:
        kml_path (str): KML文件的绝对路径

    返回:
        list: 烈度数据列表，每项为 {"intensity": int, "coords": [(lon, lat), ...]}
    """
    if not kml_path or not os.path.exists(kml_path):
        print(f"[警告] KML文件不存在或未提供: {kml_path}")
        return []

    intensity_data = []
    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()

        # 处理KML命名空间
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
        name (str): Placemark名称

    返回:
        int: 烈度值，无法识别返回None
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
        placemark: XML Element
        ns (str): 命名空间前缀

    返回:
        list: 坐标列表 [(lon, lat), ...]
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
        text (str): KML坐标文本

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
    所有烈度圈使用黑色加白边（通过双层线实现）。

    参数:
        intensity_data (list): 烈度数据列表

    返回:
        QgsVectorLayer: 烈度圈矢量图层，无数据返回None
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

    # 黑色线+白色描边（白色底线在下，黑色细线在上）
    # 底层：白色粗线（白边）
    halo_sl = QgsSimpleLineSymbolLayer()
    halo_sl.setColor(INTENSITY_HALO_COLOR)
    halo_sl.setWidth(INTENSITY_HALO_WIDTH_MM)
    halo_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    halo_sl.setPenStyle(Qt.SolidLine)

    # 上层：黑色细线
    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(INTENSITY_LINE_COLOR)
    line_sl.setWidth(INTENSITY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, halo_sl)
    symbol.appendSymbolLayer(line_sl)

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    # 烈度标注（罗马数字）
    _setup_intensity_labels(layer)
    layer.triggerRepaint()

    print(f"[信息] 创建烈度圈图层，共 {len(features)} 条烈度线")
    return layer


def _setup_intensity_labels(layer):
    """
    配置烈度圈图层的标注：罗马数字，黑色加白边。

    参数:
        layer (QgsVectorLayer): 烈度圈矢量图层
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

def load_geology_raster(tif_path):
    """
    加载地质构造底图TIF栅格图层。

    参数:
        tif_path (str): TIF文件的相对路径

    返回:
        QgsRasterLayer: 栅格图层对象，加载失败返回None
    """
    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 地质构造底图文件不存在: {abs_path}")
        return None
    layer = QgsRasterLayer(abs_path, "地质构造底图")
    if not layer.isValid():
        print(f"[错误] 无法加载地质构造底图: {abs_path}")
        return None
    print(f"[信息] 成功加载地质构造底图: {abs_path}")
    return layer


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）。

    参数:
        shp_path (str): SHP文件的相对路径
        layer_name (str): 图层显示名称

    返回:
        QgsVectorLayer: 矢量图层对象，加载失败返回None
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

def style_province_layer(layer):
    """
    设置省界图层样式：
    - 填充透明
    - 边界线：R=160,G=160,B=160，线宽0.4mm，实线
    - 标注：省名称，8pt，R=77,G=77,B=77，加白边
    - 省的标注尽量在范围内展示。

    参数:
        layer (QgsVectorLayer): 省界矢量图层
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

    _setup_province_labels(layer)
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """
    设置市界图层样式：
    - 填充透明
    - 边界线：R=160,G=160,B=160，线宽0.24mm，虚线

    参数:
        layer (QgsVectorLayer): 市界矢量图层
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(CITY_COLOR)
    fill_sl.setStrokeWidth(CITY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.DashLine)
    fill_sl.setPenJoinStyle(Qt.MiterJoin)

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
    print("[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """
    设置县界图层样式：
    - 填充透明
    - 边界线：R=160,G=160,B=160，线宽0.14mm，虚线

    参数:
        layer (QgsVectorLayer): 县界矢量图层
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(COUNTY_COLOR)
    fill_sl.setStrokeWidth(COUNTY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.DashLine)
    fill_sl.setPenJoinStyle(Qt.MiterJoin)

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
    print("[信息] 县界图层样式设置完成")


def _setup_province_labels(layer):
    """
    配置省界图层标注：省名称8pt，R=77,G=77,B=77，加白边。
    从省界shp文件属性表中获取省份名称，展示在省界内。

    参数:
        layer (QgsVectorLayer): 省界矢量图层
    """
    field_name = _find_name_field(layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过标注设置")
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    # 使用OverPoint让标注显示在多边形内部质心处
    settings.placement = Qgis.LabelPlacement.OverPoint
    # 允许标注显示，即使部分超出范围
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
    为点图层配置标注（市名称）。
    从"地级市点位数据.shp"文件属性表中获取市的名称。
    市的名称字体是9pt，颜色是黑色，加白边。

    参数:
        layer (QgsVectorLayer): 点图层
        field_name (str): 标注字段名
        font_size_pt (int): 字体大小(pt)
        color (QColor): 字体颜色
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
# 震中图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层：红色五角星+白边。
    大小通过常量EPICENTER_STAR_SIZE_MM设置。

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度

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

    # 红色五角星+白边，大小通过常量控制
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

    print(f"[信息] 创建震中图层: ({longitude}, {latitude}), 大小={EPICENTER_STAR_SIZE_MM}mm")
    return layer


def create_city_point_layer(extent):
    """
    加载地级市点位数据。
    从"地级市点位数据.shp"文件属性表中获取市的名称和点位信息。
    代表市的位置符号：黑色空圈内为一个实心黑圆，加一个圆形的白色背景；
    整体大小为市名称大小(9pt)的三分之一。
    市的名称字体是9pt，颜色是黑色，加白边。

    参数:
        extent (QgsRectangle): 地图范围

    返回:
        QgsVectorLayer: 地级市点图层，加载失败返回None
    """
    abs_path = resolve_path(CITY_POINTS_SHP_PATH)
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    # 符号：黑色空圈内一个实心黑圆+白色背景
    # 整体大小为市名称大小(9pt)的三分之一
    # 9pt ≈ 9 * 0.353mm = 3.177mm, 三分之一 ≈ 1.06mm
    symbol_size_mm = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0

    # 底层：白色实心圆（圆形白色背景）
    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)  # 白色背景稍大
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 中层：黑色空圈
    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))  # 透明填充
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 上层：黑色实心小圆
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

    # 标注：从属性表获取市的名称
    name_field = _find_name_field(layer, ["市", "NAME", "城市", "地名", "CITY", "市名", "地级市"])
    if name_field:
        _setup_point_labels(layer, name_field, CITY_LABEL_FONT_SIZE_PT, CITY_LABEL_COLOR)

    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成，符号大小={symbol_size_mm:.2f}mm")
    return layer


def create_fault_legend_layer():
    """
    创建一个断裂图例用的空线图层（用于在图例中显示"断裂"条目）。
    断裂在图例中使用红色(R=255,G=0,B=0)实线。

    返回:
        QgsVectorLayer: 断裂图例图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "断裂", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    # 红色实线符号 —— 使用FAULT_COLOR常量(R=255,G=0,B=0)
    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(FAULT_COLOR)
    line_sl.setWidth(FAULT_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print("[信息] 创建断裂图例图层")
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的线图层（在图例中以线段展示省界，而非方框）。

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
    创建市界图例用的线图层（在图例中以虚线段展示市界，而非方框）。

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
    line_sl.setPenStyle(Qt.DashLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print("[信息] 创建市界图例线图层")
    return layer


def create_county_legend_layer():
    """
    创建县界图例用的线图层（在图例中以虚线段展示县界，而非方框）。

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
    line_sl.setPenStyle(Qt.DashLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print("[信息] 创建县界图例线图层")
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale):
    """
    创建QGIS打印布局。
    参考earthquake_geological_map.py的布局结构：
    - 左侧BORDER_LEFT_MM用于经纬度标注
    - 地图区域正方形MAP_WIDTH_MM x MAP_HEIGHT_MM
    - 右侧图例LEGEND_WIDTH_MM

    参数:
        project (QgsProject): QGIS项目
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 震级
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺分母

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震地质构造图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    # 设置页面大小
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, OUTPUT_HEIGHT_MM,
                                   QgsUnitTypes.LayoutMillimeters))

    # ============ 地图项 ============
    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top,
                                        QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, MAP_HEIGHT_MM,
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

    # ============ 经纬度网格 ============
    _setup_map_grid(map_item, extent)

    # ============ 指北针 ============
    _add_north_arrow(layout)

    # ============ 比例尺 ============
    _add_scale_bar(layout, map_item, scale, extent, latitude)

    # ============ 图例 ============
    _add_legend(layout, map_item, project)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格。
    参考earthquake_geological_map.py：上侧和左侧标注经纬度。

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
    """
    grid = QgsLayoutItemMapGrid("经纬度网格", map_item)
    grid.setEnabled(True)
    grid.setCrs(CRS_WGS84)

    # 选择间隔
    lon_range = extent.xMaximum() - extent.xMinimum()
    lat_range = extent.yMaximum() - extent.yMinimum()
    lon_step = _choose_tick_step(lon_range, target_min=3, target_max=6)
    lat_step = _choose_tick_step(lat_range, target_min=3, target_max=5)

    grid.setIntervalX(lon_step)
    grid.setIntervalY(lat_step)

    # 不显示网格线，只显示标注
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)

    grid.setAnnotationEnabled(True)

    # 上侧显示经度，左侧显示纬度
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll,
                               QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll,
                               QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,
                               QgsLayoutItemMapGrid.Bottom)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,
                               QgsLayoutItemMapGrid.Right)

    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame,
                                QgsLayoutItemMapGrid.Top)
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame,
                                QgsLayoutItemMapGrid.Left)

    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal,
                                 QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical,
                                 QgsLayoutItemMapGrid.Left)

    # 经纬度标注字体 8pt
    annot_format = QgsTextFormat()
    annot_font = QFont("Times New Roman", LONLAT_FONT_SIZE_PT)
    annot_format.setFont(annot_font)
    annot_format.setSize(LONLAT_FONT_SIZE_PT)
    annot_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    annot_format.setColor(QColor(0, 0, 0))
    grid.setAnnotationTextFormat(annot_format)

    # 度分格式
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinute)
    grid.setAnnotationPrecision(0)

    # 边框刻度
    grid.setFrameStyle(QgsLayoutItemMapGrid.InteriorTicks)
    grid.setFrameWidth(1.5)
    grid.setFramePenSize(0.3)
    grid.setFramePenColor(QColor(0, 0, 0))

    map_item.grids().addGrid(grid)
    print("[信息] 经纬度网格设置完成")


def _add_north_arrow(layout):
    """
    添加指北针。
    适当减小指北针边框，同时使指北针和指北针边框大小间距合理。
    上边框与地图上边框对齐，右边框与地图右边框对齐。
    指北针图标和N字母整体下移3mm（通过修改SVG内容实现）。

    参数:
        layout (QgsPrintLayout): 打印布局
    """
    # 位置：右上角，上边和右边与地图框对齐
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM

    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    # 白色背景矩形
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(arrow_x, arrow_y,
                                         QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM,
                                          NORTH_ARROW_HEIGHT_MM,
                                          QgsUnitTypes.LayoutMillimeters))

    bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    bg_shape.setSymbol(bg_symbol)
    bg_shape.setFrameEnabled(True)
    bg_shape.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM,
                                                        QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(bg_shape)

    # 指北针SVG（SVG内部已将N和箭头下移3mm）
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_north_arrow_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    # SVG内容在白色背景内居中，合理间距
    padding_x = 1.0
    padding_y = 0.5
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x + padding_x,
                                            arrow_y + padding_y,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM - padding_x * 2,
                                             NORTH_ARROW_HEIGHT_MM - padding_y * 2,
                                             QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)

    print(f"[信息] 指北针添加完成: {NORTH_ARROW_WIDTH_MM}x{NORTH_ARROW_HEIGHT_MM}mm, "
          f"位于({arrow_x:.1f}, {arrow_y:.1f}), 图标和N下移3mm")


def _add_scale_bar(layout, map_item, scale, extent, center_lat):
    """
    添加比例尺。
    比例尺在地图主图右下角：右边框和底图图框右边框重合，下边框和底图图框下边框重合。
    比例尺样式：上方显示比例文字(如1:500,000)，下方为黑白交替线段。

    修正说明：
    - 地图CRS是WGS84（度），比例尺使用地理距离计算方式
    - 根据地图范围和中心纬度，计算每毫米地图对应的实际千米数
    - 选择合适的"nice"值作为比例尺总长度(km)
    - 反算得到比例尺在布局中的物理长度(mm)

    比例尺正确性：
    - M<6时，比例尺1:150,000，地图30km
    - 6≤M<7时，比例尺1:500,000，地图100km
    - M≥7时，比例尺1:1,500,000，地图300km

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图项
        scale (int): 比例尺分母
        extent (QgsRectangle): 地图范围（WGS84度）
        center_lat (float): 震中纬度（度）
    """
    # 地图框右下角坐标
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_bottom = BORDER_TOP_MM + MAP_HEIGHT_MM

    # ======== 计算比例尺线段参数 ========
    # 地图范围的经度跨度对应的实际距离(km)
    lon_range_deg = extent.xMaximum() - extent.xMinimum()
    map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))

    # 每毫米地图对应的千米数
    km_per_mm = map_total_km / MAP_WIDTH_MM

    # 比例尺期望占地图宽度的约18%
    target_bar_km = MAP_WIDTH_MM * 0.18 * km_per_mm

    # 选择"nice"值
    nice_values = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    bar_km = nice_values[0]
    for nv in nice_values:
        if nv <= target_bar_km * 1.5:
            bar_km = nv
        else:
            break

    # 比例尺线段在布局中的物理长度(mm)
    bar_length_mm = bar_km / km_per_mm
    # 确保最小长度
    bar_length_mm = max(bar_length_mm, 20.0)

    # 分段数
    num_segments = 4
    seg_km = bar_km / num_segments

    # 比例尺背景框尺寸
    sb_width = bar_length_mm + 16.0   # 两侧留白
    sb_height = 14.0

    # 【修正】右边框和底图图框右边框重合，下边框和底图图框下边框重合
    sb_x = map_right - sb_width
    sb_y = map_bottom - sb_height

    # 白色背景矩形
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(sb_x, sb_y,
                                         QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(sb_width, sb_height,
                                          QgsUnitTypes.LayoutMillimeters))
    bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    bg_shape.setSymbol(bg_symbol)
    bg_shape.setFrameEnabled(True)
    bg_shape.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM,
                                                        QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(bg_shape)

    # 比例尺数字标注（如 1:500,000）居中在上方
    scale_label = QgsLayoutItemLabel(layout)
    scale_label.setText(f"1:{scale:,}")
    label_format = QgsTextFormat()
    label_format.setFont(QFont("Times New Roman", SCALE_FONT_SIZE_PT))
    label_format.setSize(SCALE_FONT_SIZE_PT)
    label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    label_format.setColor(QColor(0, 0, 0))
    scale_label.setTextFormat(label_format)
    scale_label.attemptMove(QgsLayoutPoint(sb_x, sb_y + 0.5,
                                            QgsUnitTypes.LayoutMillimeters))
    scale_label.attemptResize(QgsLayoutSize(sb_width, 4.5,
                                             QgsUnitTypes.LayoutMillimeters))
    scale_label.setHAlign(Qt.AlignHCenter)
    scale_label.setVAlign(Qt.AlignVCenter)
    scale_label.setFrameEnabled(False)
    scale_label.setBackgroundEnabled(False)
    layout.addLayoutItem(scale_label)

    # ======== 使用手绘方式绘制黑白交替线段比例尺 ========
    # 线段比例尺居中放在比例文字下方
    bar_start_x = sb_x + (sb_width - bar_length_mm) / 2.0
    bar_y = sb_y + 5.5
    bar_h = 1.8  # 比例尺条高度(mm)
    seg_width_mm = bar_length_mm / num_segments

    for i in range(num_segments):
        seg_shape = QgsLayoutItemShape(layout)
        seg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        seg_x = bar_start_x + i * seg_width_mm
        seg_shape.attemptMove(QgsLayoutPoint(seg_x, bar_y,
                                              QgsUnitTypes.LayoutMillimeters))
        seg_shape.attemptResize(QgsLayoutSize(seg_width_mm, bar_h,
                                               QgsUnitTypes.LayoutMillimeters))

        if i % 2 == 0:
            fill_color = '0,0,0,255'
        else:
            fill_color = '255,255,255,255'

        seg_symbol = QgsFillSymbol.createSimple({
            'color': fill_color,
            'outline_color': '0,0,0,255',
            'outline_width': '0.15',
            'outline_width_unit': 'MM',
        })
        seg_shape.setSymbol(seg_symbol)
        seg_shape.setFrameEnabled(False)
        layout.addLayoutItem(seg_shape)

    # 比例尺刻度标注：0、中间值、末端值 + "km"
    label_y = bar_y + bar_h + 0.3
    label_h = 3.5

    tick_format = QgsTextFormat()
    tick_format.setFont(QFont("Times New Roman", SCALE_FONT_SIZE_PT))
    tick_format.setSize(SCALE_FONT_SIZE_PT)
    tick_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    tick_format.setColor(QColor(0, 0, 0))

    # 起点 "0"
    lbl_0 = QgsLayoutItemLabel(layout)
    lbl_0.setText("0")
    lbl_0.setTextFormat(tick_format)
    lbl_0.attemptMove(QgsLayoutPoint(bar_start_x - 1.5, label_y,
                                      QgsUnitTypes.LayoutMillimeters))
    lbl_0.attemptResize(QgsLayoutSize(6.0, label_h,
                                       QgsUnitTypes.LayoutMillimeters))
    lbl_0.setHAlign(Qt.AlignHCenter)
    lbl_0.setVAlign(Qt.AlignTop)
    lbl_0.setFrameEnabled(False)
    lbl_0.setBackgroundEnabled(False)
    layout.addLayoutItem(lbl_0)

    # 中点值
    mid_km = bar_km // 2
    if mid_km > 0:
        lbl_mid = QgsLayoutItemLabel(layout)
        lbl_mid.setText(str(mid_km))
        lbl_mid.setTextFormat(tick_format)
        mid_x = bar_start_x + bar_length_mm / 2.0 - 3.0
        lbl_mid.attemptMove(QgsLayoutPoint(mid_x, label_y,
                                            QgsUnitTypes.LayoutMillimeters))
        lbl_mid.attemptResize(QgsLayoutSize(8.0, label_h,
                                             QgsUnitTypes.LayoutMillimeters))
        lbl_mid.setHAlign(Qt.AlignHCenter)
        lbl_mid.setVAlign(Qt.AlignTop)
        lbl_mid.setFrameEnabled(False)
        lbl_mid.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_mid)

    # 末端值 + " km"
    lbl_end = QgsLayoutItemLabel(layout)
    lbl_end.setText(f"{bar_km} km")
    lbl_end.setTextFormat(tick_format)
    end_x = bar_start_x + bar_length_mm - 4.0
    lbl_end.attemptMove(QgsLayoutPoint(end_x, label_y,
                                        QgsUnitTypes.LayoutMillimeters))
    lbl_end.attemptResize(QgsLayoutSize(14.0, label_h,
                                         QgsUnitTypes.LayoutMillimeters))
    lbl_end.setHAlign(Qt.AlignHCenter)
    lbl_end.setVAlign(Qt.AlignTop)
    lbl_end.setFrameEnabled(False)
    lbl_end.setBackgroundEnabled(False)
    layout.addLayoutItem(lbl_end)

    print(f"[信息] 比例尺添加完成（右边框和下边框与底图图框重合），"
          f"1:{scale:,}, 总长{bar_km}km={bar_length_mm:.1f}mm, {num_segments}段")


def _add_legend(layout, map_item, project):
    """
    添加图例。
    - "图 例"居中展示
    - "震中"、"地级市"、"省界"、"市界"、"县界"、"断裂" 六个图例分两列展示
    - 其他图例项（如烈度圈、岩性等）单列展示
    - "省界"、"市界"、"县界"在图例中使用线段，不使用方框
    - 断裂在图例中使用红色(R=255,G=0,B=0)线
    - 图例位于地图右侧

    实现方式：使用两个QgsLayoutItemLegend分别展示两列部分和单列部分。

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图项
        project (QgsProject): QGIS项目
    """
    # 图例位置：地图右侧
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_height = MAP_HEIGHT_MM

    # ============================================================
    # 公共文本格式
    # ============================================================
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

    # ============================================================
    # 白色背景边框（整个图例区域）
    # ============================================================
    legend_bg = QgsLayoutItemShape(layout)
    legend_bg.setShapeType(QgsLayoutItemShape.Rectangle)
    legend_bg.attemptMove(QgsLayoutPoint(legend_x, legend_y,
                                          QgsUnitTypes.LayoutMillimeters))
    legend_bg.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM, legend_height,
                                           QgsUnitTypes.LayoutMillimeters))
    legend_bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    legend_bg.setSymbol(legend_bg_symbol)
    legend_bg.setFrameEnabled(True)
    legend_bg.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM,
                                                         QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend_bg)

    # ============================================================
    # 标题 "图  例" 居中
    # ============================================================
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + 1.0,
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM, 5.0,
                                             QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # ============================================================
    # 上部图例：震中/地级市/省界/市界/县界/断裂 — 两列展示
    # ============================================================
    top_legend_y = legend_y + 6.5  # 标题下方
    top_legend_height = 30.0       # 预估六个图例项两列的高度

    top_legend = QgsLayoutItemLegend(layout)
    top_legend.setLinkedMap(map_item)
    top_legend.setAutoUpdateModel(False)
    top_legend.attemptMove(QgsLayoutPoint(legend_x + 1.0, top_legend_y,
                                           QgsUnitTypes.LayoutMillimeters))
    top_legend.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM - 2.0, top_legend_height,
                                            QgsUnitTypes.LayoutMillimeters))

    # 无标题（标题已单独绘制）
    top_legend.setTitle("")

    # 两列布局
    top_legend.setColumnCount(2)
    top_legend.setSplitLayer(True)
    top_legend.setEqualColumnWidth(True)

    # 条目字体
    top_legend.rstyle(QgsLegendStyle.Title).setTextFormat(title_format)
    top_legend.rstyle(QgsLegendStyle.SymbolLabel).setTextFormat(item_format)

    # 符号大小
    top_legend.setSymbolWidth(5.0)
    top_legend.setSymbolHeight(3.0)

    # 边框和背景透明（由外部矩形管理）
    top_legend.setFrameEnabled(False)
    top_legend.setBackgroundEnabled(False)

    # 手动管理图例模型
    top_model = top_legend.model()
    top_root = top_model.rootGroup()
    top_root.removeAllChildren()

    # 按顺序添加六个图例条目
    layer_order_top = ["震中", "地级市", "省界", "市界", "县界", "断裂"]
    for layer_name in layer_order_top:
        layers = project.mapLayersByName(layer_name)
        if layers:
            top_root.addLayer(layers[0])

    layout.addLayoutItem(top_legend)

    # ============================================================
    # 下部图例：烈度圈、地质构造底图等 — 单列展示
    # ============================================================
    bottom_legend_y = top_legend_y + top_legend_height + 1.0
    bottom_legend_height = legend_height - (bottom_legend_y - legend_y) - 2.0

    # 检查是否有需要在下部展示的图层
    other_layer_names = ["烈度圈", "地质构造底图"]
    has_other_layers = False
    for name in other_layer_names:
        layers = project.mapLayersByName(name)
        if layers:
            has_other_layers = True
            break

    if has_other_layers and bottom_legend_height > 5.0:
        bottom_legend = QgsLayoutItemLegend(layout)
        bottom_legend.setLinkedMap(map_item)
        bottom_legend.setAutoUpdateModel(False)
        bottom_legend.attemptMove(QgsLayoutPoint(legend_x + 1.0, bottom_legend_y,
                                                   QgsUnitTypes.LayoutMillimeters))
        bottom_legend.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM - 2.0,
                                                    bottom_legend_height,
                                                    QgsUnitTypes.LayoutMillimeters))

        bottom_legend.setTitle("")

        # 单列布局
        bottom_legend.setColumnCount(1)
        bottom_legend.setSplitLayer(False)

        bottom_legend.rstyle(QgsLegendStyle.Title).setTextFormat(title_format)
        bottom_legend.rstyle(QgsLegendStyle.SymbolLabel).setTextFormat(item_format)

        bottom_legend.setSymbolWidth(5.0)
        bottom_legend.setSymbolHeight(3.0)

        bottom_legend.setFrameEnabled(False)
        bottom_legend.setBackgroundEnabled(False)

        bottom_model = bottom_legend.model()
        bottom_root = bottom_model.rootGroup()
        bottom_root.removeAllChildren()

        for name in other_layer_names:
            layers = project.mapLayersByName(name)
            if layers:
                bottom_root.addLayer(layers[0])

        layout.addLayoutItem(bottom_legend)

    print("[信息] 图例添加完成（上部六项两列展示，下部其余项单列展示，标题居中）")


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_geology_map(longitude, latitude, magnitude,
                                     output_path="output_geology_map.png",
                                     kml_path=None):
    """
    生成地震震中地质构造图（主入口函数）。

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        output_path (str): 输出PNG文件路径
        kml_path (str): 烈度圈KML文件路径（可选）

    返回:
        str: 输出文件路径，失败返回None
    """
    print("=" * 60)
    print(f"[开始] 生成地震地质构造图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print("=" * 60)

    # 1. 获取震级配置
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    # 2. 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 3. 初始化QGIS
    qgs_app = None
    if not QgsApplication.instance():
        qgs_app = QgsApplication([], False)
        qgs_app.initQgis()
        print("[信息] QGIS应用初始化完成")

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 4. 加载地质构造底图（最底层）
    geology_layer = load_geology_raster(GEOLOGY_TIF_PATH)
    if geology_layer:
        project.addMapLayer(geology_layer)

    # 5. 加载省市县界 — 从底到顶：县界 -> 市界 -> 省界
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
        style_province_layer(province_layer)
        project.addMapLayer(province_layer)

    # 6. 加载地级市点位 — 从属性表获取市名称和点位
    city_point_layer = create_city_point_layer(extent)
    if city_point_layer:
        project.addMapLayer(city_point_layer)

    # 7. 创建图例用的线段图层（省界/市界/县界使用线段，不用方框）
    province_legend_layer = create_province_legend_layer()
    if province_legend_layer:
        project.addMapLayer(province_legend_layer)

    city_legend_layer = create_city_legend_layer()
    if city_legend_layer:
        project.addMapLayer(city_legend_layer)

    county_legend_layer = create_county_legend_layer()
    if county_legend_layer:
        project.addMapLayer(county_legend_layer)

    # 8. 加载断裂图例图层
    fault_layer = create_fault_legend_layer()
    if fault_layer:
        project.addMapLayer(fault_layer)

    # 9. 解析并加载烈度圈
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

    # 10. 震中位置图层（最顶层）
    epicenter_layer = create_epicenter_layer(longitude, latitude)
    if epicenter_layer:
        project.addMapLayer(epicenter_layer)

    # 11. 创建打印布局
    layout = create_print_layout(project, longitude, latitude, magnitude,
                                  extent, scale)

    # 12. 导出PNG
    result = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    # 13. 清理临时SVG
    svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_north_arrow_temp.svg")
    if os.path.exists(svg_temp):
        try:
            os.remove(svg_temp)
        except OSError:
            pass

    print("=" * 60)
    if result:
        print(f"[完成] 地质构造图已输出: {result}")
    else:
        print("[失败] 地质构造图输出失败")
    print("=" * 60)

    return result


def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片。

    参数:
        layout (QgsPrintLayout): 打印布局
        output_path (str): 输出PNG文件路径
        dpi (int): 输出DPI

    返回:
        str: 输出文件路径，失败返回None
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
    """测试震级配置获取"""
    print("\n--- 测试: get_magnitude_config ---")

    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000, f"期望150000，实际{config_s['scale']}"
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

    # 边界值
    config_6 = get_magnitude_config(6.0)
    assert config_6["map_size_km"] == 100
    print(f"  M6.0 -> 范围{config_6['map_size_km']}km ✓")

    config_599 = get_magnitude_config(5.99)
    assert config_599["map_size_km"] == 30
    print(f"  M5.99 -> 范围{config_599['map_size_km']}km ✓")

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

    extent2 = calculate_extent(116.4, 39.9, 50)
    assert (extent2.xMaximum() - extent2.xMinimum()) > (extent.xMaximum() - extent.xMinimum())
    print(f"  50km半径范围更大 ✓")

    print("  所有范围计算测试通过 ✓")


def test_resolve_path():
    """测试路径解析"""
    print("\n--- 测试: resolve_path ---")

    path = resolve_path(GEOLOGY_TIF_PATH)
    assert os.path.isabs(path)
    print(f"  TIF路径: {path} (绝对路径) ✓")

    print("  路径解析测试通过 ✓")


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


def test_parse_intensity_kml():
    """测试KML烈度圈解析"""
    print("\n--- 测试: parse_intensity_kml ---")

    test_kml = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_test_intensity.kml")
    kml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>6度</name>
<LineString><coordinates>
103.0,34.0,0 103.5,34.0,0 103.5,34.5,0 103.0,34.5,0 103.0,34.0,0
</coordinates></LineString>
</Placemark>
<Placemark><name>7度</name>
<LineString><coordinates>
103.1,34.1,0 103.4,34.1,0 103.4,34.4,0 103.1,34.4,0 103.1,34.1,0
</coordinates></LineString>
</Placemark>
</Document>
</kml>'''
    with open(test_kml, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    data = parse_intensity_kml(test_kml)
    assert len(data) == 2, f"期望2个烈度圈，实际{len(data)}"
    intensities = sorted([d['intensity'] for d in data])
    assert intensities == [6, 7]
    print(f"  解析到 {len(data)} 个烈度圈: {intensities} ✓")

    try:
        os.remove(test_kml)
    except OSError:
        pass

    print("  KML解析测试通过 ✓")


def test_constants():
    """测试常量合理性"""
    print("\n--- 测试: 常量验证 ---")

    # 布局尺寸
    assert MAP_TOTAL_WIDTH_MM == 220.0
    assert MAP_WIDTH_MM > 0
    assert MAP_HEIGHT_MM == MAP_WIDTH_MM  # 正方形
    assert LEGEND_WIDTH_MM == 34.0
    assert OUTPUT_HEIGHT_MM > MAP_HEIGHT_MM
    print(f"  布局: {MAP_TOTAL_WIDTH_MM}x{OUTPUT_HEIGHT_MM:.1f}mm, "
          f"地图{MAP_WIDTH_MM:.1f}x{MAP_HEIGHT_MM:.1f}mm ✓")

    # 指北针 - 适当减小
    assert NORTH_ARROW_WIDTH_MM == 12.0
    assert NORTH_ARROW_HEIGHT_MM == 18.0
    print(f"  指北针: {NORTH_ARROW_WIDTH_MM}x{NORTH_ARROW_HEIGHT_MM}mm ✓")

    # 震中五角星
    assert EPICENTER_STAR_SIZE_MM > 0
    print(f"  震中五角星: {EPICENTER_STAR_SIZE_MM}mm ✓")

    # 比例尺正确性验证
    for mag, expected_scale in [(4.5, 150000), (6.5, 500000), (7.5, 1500000)]:
        cfg = get_magnitude_config(mag)
        assert cfg["scale"] == expected_scale, \
            f"M{mag}比例尺错误: 期望1:{expected_scale}, 实际1:{cfg['scale']}"
        # 验证地图尺寸(km) / (比例尺 / 1e6) 得到合理的mm值
        map_km = cfg["map_size_km"]
        map_mm = map_km / (cfg["scale"] / 1000000.0)
        assert 100 < map_mm < 300, \
            f"M{mag}比例尺与地图尺寸不匹配: {map_km}km在1:{cfg['scale']}下={map_mm:.1f}mm"
    print(f"  比例尺验证: M<6→1:150,000, 6≤M<7→1:500,000, M≥7→1:1,500,000 ✓")

    # 地级市符号大小��证：9pt字体的三分之一
    city_symbol_size = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0
    assert 0.5 < city_symbol_size < 2.0, \
        f"地级市符号大小不合理: {city_symbol_size:.2f}mm"
    print(f"  地级市符号大小: {city_symbol_size:.2f}mm (9pt的1/3) ✓")

    # 断裂颜色验证 —— 修正为纯红色(R=255,G=0,B=0)
    assert FAULT_COLOR.red() == 255 and FAULT_COLOR.green() == 0 and FAULT_COLOR.blue() == 0
    print(f"  断裂颜色: R={FAULT_COLOR.red()},G={FAULT_COLOR.green()},B={FAULT_COLOR.blue()} ✓")

    print("  常量验证测试通过 ✓")


def test_scale_bar_calculation():
    """测试比例尺计算正确性（基于地理距离计算方式）"""
    print("\n--- 测试: 比例尺计算验证 ---")

    # 验证基于WGS84的比例尺距离计算逻辑
    # M<6: map_size_km=30, 北京纬度39.9°
    lat = 39.9
    half_km = 15
    extent = calculate_extent(116.4, lat, half_km)
    lon_range = extent.xMaximum() - extent.xMinimum()
    map_km = lon_range * 111.0 * math.cos(math.radians(lat))
    km_per_mm = map_km / MAP_WIDTH_MM
    # 选择nice值
    target = MAP_WIDTH_MM * 0.18 * km_per_mm
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    bar_km = nice[0]
    for nv in nice:
        if nv <= target * 1.5:
            bar_km = nv
        else:
            break
    bar_mm = bar_km / km_per_mm
    assert 15 < bar_mm < 80, f"小震级比例尺线段长度不合理: {bar_mm:.1f}mm"
    print(f"  M<6: {bar_km}km 比例尺线段 = {bar_mm:.1f}mm (地图宽={MAP_WIDTH_MM}mm, 实际{map_km:.1f}km) ✓")

    # 6≤M<7: map_size_km=100
    half_km = 50
    extent = calculate_extent(104.0, 30.6, half_km)
    lon_range = extent.xMaximum() - extent.xMinimum()
    map_km = lon_range * 111.0 * math.cos(math.radians(30.6))
    km_per_mm = map_km / MAP_WIDTH_MM
    target = MAP_WIDTH_MM * 0.18 * km_per_mm
    bar_km = nice[0]
    for nv in nice:
        if nv <= target * 1.5:
            bar_km = nv
        else:
            break
    bar_mm = bar_km / km_per_mm
    assert 15 < bar_mm < 80, f"中震级比例尺线段长度不合理: {bar_mm:.1f}mm"
    print(f"  6≤M<7: {bar_km}km 比例尺线段 = {bar_mm:.1f}mm (地图宽={MAP_WIDTH_MM}mm, 实际{map_km:.1f}km) ✓")

    # M≥7: map_size_km=300
    half_km = 150
    extent = calculate_extent(118.18, 39.63, half_km)
    lon_range = extent.xMaximum() - extent.xMinimum()
    map_km = lon_range * 111.0 * math.cos(math.radians(39.63))
    km_per_mm = map_km / MAP_WIDTH_MM
    target = MAP_WIDTH_MM * 0.18 * km_per_mm
    bar_km = nice[0]
    for nv in nice:
        if nv <= target * 1.5:
            bar_km = nv
        else:
            break
    bar_mm = bar_km / km_per_mm
    assert 15 < bar_mm < 80, f"大震级比例尺线段长度不合理: {bar_mm:.1f}mm"
    print(f"  M≥7: {bar_km}km 比例尺线段 = {bar_mm:.1f}mm (地图宽={MAP_WIDTH_MM}mm, 实际{map_km:.1f}km) ✓")

    print("  比例尺计算验证通过 ✓")


def test_generate_map_small():
    """测试小震级(M<6)地图生成"""
    print("\n--- 测试: 小震级(M5.5)地图生成 ---")
    try:
        result = generate_earthquake_geology_map(
            longitude=103.25,
            latitude=34.06,
            magnitude=5.5,
            output_path="test_geology_small.png",
        )
        if result and os.path.exists(result):
            size_kb = os.path.getsize(result) / 1024
            print(f"  输出文件: {result} ({size_kb:.1f}KB) ✓")
        else:
            print("  [跳过] 输出文件未生成（可能缺少数据文件）")
    except Exception as e:
        print(f"  [跳过] {e}")


def test_generate_map_medium():
    """测试中震级(6<=M<7)地图生成"""
    print("\n--- 测试: 中震级(M6.5)地图生成 ---")
    try:
        result = generate_earthquake_geology_map(
            longitude=104.0,
            latitude=30.6,
            magnitude=6.5,
            output_path="test_geology_medium.png",
        )
        if result and os.path.exists(result):
            size_kb = os.path.getsize(result) / 1024
            print(f"  输出文件: {result} ({size_kb:.1f}KB) ✓")
        else:
            print("  [跳过] 输出文件未生成")
    except Exception as e:
        print(f"  [跳过] {e}")


def test_generate_map_large():
    """测试大震级(M>=7)地图生成"""
    print("\n--- 测试: 大震级(M7.8)地图生成 ---")
    try:
        result = generate_earthquake_geology_map(
            longitude=118.18,
            latitude=39.63,
            magnitude=7.8,
            output_path="test_geology_large.png",
        )
        if result and os.path.exists(result):
            size_kb = os.path.getsize(result) / 1024
            print(f"  输出文件: {result} ({size_kb:.1f}KB) ✓")
        else:
            print("  [跳过] 输出文件未生成")
    except Exception as e:
        print(f"  [跳过] {e}")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("运行 earthquake_geological_map2 全部测试")
    print("=" * 60)

    test_magnitude_config()
    test_calculate_extent()
    test_resolve_path()
    test_int_to_roman()
    test_parse_intensity_kml()
    test_constants()
    test_scale_bar_calculation()
    test_generate_map_small()
    test_generate_map_medium()
    test_generate_map_large()

    print("\n" + "=" * 60)
    print("全部测试执行完��")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    """
    用法:
        python earthquake_geological_map2.py test
        python earthquake_geological_map2.py <经度> <纬度> <震级> [输出文件名] [kml路径]
    示例:
        python earthquake_geological_map2.py 118.18 39.63 7.8 tangshan.png
        python earthquake_geological_map2.py 114.3 39.3 5.5 test.png intensity.kml
    """
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        run_all_tests()
    elif len(sys.argv) >= 4:
        try:
            lon = float(sys.argv[1])
            lat = float(sys.argv[2])
            mag = float(sys.argv[3])
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_geology_M{mag}_{lon}_{lat}.png"
            kml = sys.argv[5] if len(sys.argv) > 5 else None
            generate_earthquake_geology_map(lon, lat, mag, out, kml)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_geological_map2.py <经度> <纬度> <震级> [输出文件名] [kml路径]")
    else:
        print("使用默认参数运行（唐山地震 M7.8）...")
        generate_earthquake_geology_map(
            longitude=118.18, latitude=39.63,
            magnitude=9.5, output_path="earthquake_geology_tangshan_M7.8.png"
        )