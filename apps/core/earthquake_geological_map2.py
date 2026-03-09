# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中地质构造图生成脚本
参考 earthquake_geological_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

图例转义说明：
- 栅格图层Symbology中有Value值和对应的Color色块，Label是数字（Count值）
- 属性表中有Value字段和yanxing字段
- 通过Value字段关联：获取图层中Value对应的颜色 + 属性表中Value对应的yanxing
- 图例最终显示：色块 + yanxing名称
"""

import os
import sys
import math
import re
import struct
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
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsPalettedRasterRenderer,
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
# 地级市点位数据
CITY_POINTS_SHP_PATH = "../../data/geology/2023地级市点位数据/地级市点位数据.shp"

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
LONLAT_FONT_SIZE_PT = 8

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 10
LEGEND_ITEM_FONT_SIZE_PT = 8
LEGEND_YANXING_FONT_SIZE_PT = 7

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

# WGS84
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """根据震级获取对应的配置参数"""
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def calculate_extent(longitude, latitude, half_size_km):
    """根据震中经纬度和半幅宽度(km)计算地图范围(WGS84坐标)"""
    delta_lat = half_size_km / 111.0
    delta_lon = half_size_km / (111.0 * math.cos(math.radians(latitude)))
    xmin = longitude - delta_lon
    xmax = longitude + delta_lon
    ymin = latitude - delta_lat
    ymax = latitude + delta_lat
    return QgsRectangle(xmin, ymin, xmax, ymax)


def calculate_map_height_from_extent(extent, map_width_mm):
    """根据地图范围和宽度计算地图高度（保持宽高比）"""
    lon_range = extent.xMaximum() - extent.xMinimum()
    lat_range = extent.yMaximum() - extent.yMinimum()
    if lon_range <= 0:
        return map_width_mm
    aspect_ratio = lat_range / lon_range
    return map_width_mm * aspect_ratio


def resolve_path(relative_path):
    """将相对路径转换为绝对路径"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """将阿拉伯数字转换为罗马数字"""
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
    """根据地理范围选择合适的经纬度刻度间隔"""
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
    """创建指北针SVG文件"""
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
    """在矢量图层的字段列表中查找名称字段"""
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
# DBF文件读取函数
# ============================================================

def read_dbf_file(dbf_path):
    """读取DBF文件，返回(字段名列表, 记录列表)"""
    if not os.path.exists(dbf_path):
        return [], []
    try:
        with open(dbf_path, "rb") as f:
            header = f.read(32)
            if len(header) < 32:
                return [], []
            num_records = struct.unpack("<I", header[4:8])[0]
            header_size = struct.unpack("<H", header[8:10])[0]
            record_size = struct.unpack("<H", header[10:12])[0]
            fields = []
            while f.tell() < header_size - 1:
                fd = f.read(32)
                if not fd or fd[0:1] == b"\r":
                    break
                raw_name = fd[0:11].rstrip(b"\x00")
                name = ""
                for enc in ("gbk", "utf-8", "latin-1"):
                    try:
                        name = raw_name.decode(enc).strip("\x00")
                        break
                    except (UnicodeDecodeError, LookupError):
                        pass
                fields.append((name, fd[16]))
            f.seek(header_size)
            records = []
            for _ in range(num_records):
                raw = f.read(record_size)
                if not raw or raw[0:1] == b"\x1a":
                    break
                record = {}
                pos = 1
                for fname, flen in fields:
                    raw_val = raw[pos:pos + flen]
                    val = ""
                    for enc in ("gbk", "utf-8", "latin-1"):
                        try:
                            val = raw_val.decode(enc).strip()
                            break
                        except (UnicodeDecodeError, LookupError):
                            pass
                    record[fname] = val
                    pos += flen
                records.append(record)
        return [f[0] for f in fields], records
    except (IOError, OSError, struct.error, ValueError) as e:
        print(f"  DBF读取失败: {e}")
        return [], []


# ============================================================
# QML样式文件解析 - 按Value获取颜色
# ============================================================

def _parse_qml_colors_by_value(tif_path):
    """
    从QGIS QML样式文件解析每个Value对应的颜色。
    返回字典 {value(int): (r, g, b, 255)}

    QML中paletteEntry节点格式：
    <paletteEntry value="1" color="#ff5a50d7" label="88162555"/>
    - value: 就是我们需要的Value值（对应属性表的Value字段）
    - color: 颜色值
    - label: 在这个例子中是Count值，不是我们要显示的内容
    """
    base = os.path.splitext(tif_path)[0]
    qml_candidates = [
        base + ".qml",
        tif_path + ".qml",
        os.path.join(os.path.dirname(tif_path), "group.qml"),
    ]
    qml_path = None
    for p in qml_candidates:
        if os.path.exists(p):
            qml_path = p
            break
    if qml_path is None:
        print("  未找到QML样式文件")
        return {}

    try:
        tree = ET.parse(qml_path)
        root = tree.getroot()
        color_map = {}

        # 遍历所有 paletteEntry 节点
        for entry in root.iter():
            if entry.tag == "paletteEntry":
                val_str = entry.get("value")
                color_str = entry.get("color")
                if val_str is None or not color_str:
                    continue
                try:
                    val = int(float(val_str))
                except (ValueError, TypeError):
                    continue

                color_str = color_str.strip()
                if color_str.startswith("#"):
                    hex_c = color_str[1:]
                    if len(hex_c) == 6:
                        # #RRGGBB
                        r = int(hex_c[0:2], 16)
                        g = int(hex_c[2:4], 16)
                        b = int(hex_c[4:6], 16)
                        color_map[val] = (r, g, b, 255)
                    elif len(hex_c) == 8:
                        # #AARRGGBB（QGIS格式，前两位为Alpha）
                        r = int(hex_c[2:4], 16)
                        g = int(hex_c[4:6], 16)
                        b = int(hex_c[6:8], 16)
                        color_map[val] = (r, g, b, 255)

        print(f"  QML颜色映射解析完成，共 {len(color_map)} 个条目")
        return color_map
    except (IOError, OSError, ValueError, TypeError, ET.ParseError) as e:
        print(f"  QML解析失败: {e}")
        return {}


def _get_raster_layer_colors(raster_layer):
    """
    从已加载的栅格图层渲染器中获取Value对应的颜色。
    返回字典 {value(int): (r, g, b, 255)}
    """
    color_map = {}
    if raster_layer is None or not raster_layer.isValid():
        return color_map

    renderer = raster_layer.renderer()
    if renderer is None:
        return color_map

    # 检查是否是调色板渲染器（Paletted/Unique values）
    if isinstance(renderer, QgsPalettedRasterRenderer):
        classes = renderer.classes()
        for cls in classes:
            try:
                val = int(cls.value)
                color = cls.color
                color_map[val] = (color.red(), color.green(), color.blue(), 255)
            except (ValueError, TypeError):
                continue
        print(f"  从栅格图层渲染器获取颜色，共 {len(color_map)} 个条目")

    return color_map


# ============================================================
# 读取属性表并建立Value到yanxing的映射
# ============================================================

def read_tif_attribute_table_yanxing(tif_path):
    """
    读取TIF属性表，建立Value到yanxing的映射。
    返回字典 {value(int): yanxing_name(str)}

    属性表结构（如图2所示）：
    - Value: 1, 2, 3, 4, 5...（与图层Symbology中的Value对应）
    - Count: 88162555, 3241894657...（这是图层Symbology中的Label）
    - Grouping: 62, 53, 31...
    - yanxing: 坚硬岩组，大..., 坚硬岩组，坚..., 等
    """
    result = {}
    base = os.path.splitext(tif_path)[0]
    candidate_dbf = [
        tif_path + ".vat.dbf",
        base + ".vat.dbf",
        base + ".VAT.dbf",
        base + ".dbf",
    ]
    dbf_path = None
    for p in candidate_dbf:
        if os.path.exists(p):
            dbf_path = p
            break
    if dbf_path is None:
        print("  未找到TIF属性表文件（.vat.dbf）")
        return result

    print(f"  读取属性表: {dbf_path}")
    fields, records = read_dbf_file(dbf_path)
    if not records:
        print("  属性表为空")
        return result

    print(f"  属性表字段: {fields}")
    fl = {f.lower(): f for f in fields}

    # 查找Value字段
    value_field = fl.get("value") or fl.get("val") or (fields[0] if fields else None)
    if not value_field:
        print("  未找到Value字段")
        return result

    # 查找yanxing字段
    yanxing_field = None
    for k in ["yanxing", "yanxing1", "yx", "lithology", "YANXING"]:
        if k.lower() in fl:
            yanxing_field = fl[k.lower()]
            break
    if yanxing_field is None:
        print("  未找到yanxing字段")
        return result

    print(f"  使用字段: Value={value_field}, yanxing={yanxing_field}")

    # 建立Value到yanxing的映射
    for rec in records:
        try:
            value = int(float(rec.get(value_field, 0) or 0))
        except (ValueError, TypeError):
            continue
        yanxing = rec.get(yanxing_field, "").strip()
        if yanxing:
            result[value] = yanxing

    print(f"  读取到 {len(result)} 个Value-yanxing映射")
    return result


def build_yanxing_legend_list(tif_path, raster_layer=None):
    """
    构建岩性图例列表。

    逻辑：
    1. 从QML样式文件或栅格图层渲染器获取 Value -> Color 映射
    2. 从属性表获取 Value -> yanxing 映射
    3. 通过Value关联，生成 [(value, color_rgba, yanxing_name), ...]

    返回: [(value, (r,g,b,255), yanxing_name), ...]
    """
    # 1. 获取Value到颜色的映射
    color_map = _parse_qml_colors_by_value(tif_path)

    # 如果QML解析失败，尝试从图层渲染器获取
    if not color_map and raster_layer is not None:
        color_map = _get_raster_layer_colors(raster_layer)

    if not color_map:
        print("  警告: 未能获取颜色映射")

    # 2. 获取Value到yanxing的映射
    yanxing_map = read_tif_attribute_table_yanxing(tif_path)

    if not yanxing_map:
        print("  警告: 未能获取yanxing映射")
        return []

    # 3. 通过Value关联，生成图例列表
    result = []
    seen_yanxing = set()  # 避免重复的yanxing名称

    # 按Value排序
    for value in sorted(yanxing_map.keys()):
        yanxing = yanxing_map[value]

        # 跳过重复的yanxing
        if yanxing in seen_yanxing:
            continue
        seen_yanxing.add(yanxing)

        # 获取颜色，如果没有则使用默认灰色
        if value in color_map:
            color = color_map[value]
        else:
            color = (128, 128, 128, 255)
            print(f"  警告: Value={value} 未找到对应颜色，使用默认灰色")

        result.append((value, color, yanxing))

    print(f"  构建岩性图例列表完成，共 {len(result)} 项")
    return result


# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """解析KML文件，提取烈度圈坐标数据"""
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
    """从Placemark名称中提取烈度值"""
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
    """从Placemark中提取LineString坐标"""
    coords_list = []
    for ls in placemark.iter(ns + "LineString"):
        coords_elem = ls.find(ns + "coordinates")
        if coords_elem is not None and coords_elem.text:
            parsed = _parse_kml_coords(coords_elem.text)
            coords_list.extend(parsed)
    return coords_list


def _parse_kml_coords(text):
    """解析KML坐标文本为(lon, lat)元组列表"""
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
    """根据解析的烈度圈数据创建QGIS矢量图层"""
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
    """配置烈度圈图层的标注"""
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
    """加载地质构造底图TIF栅格图层"""
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
    """加载矢量图层（SHP文件）"""
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
    """设置省界图层样式"""
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
    """设置市界图层样式"""
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
    """设置县界图层样式"""
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
    """配置省界图层标注"""
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
    """为点图层配置标注"""
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
    """创建震中标记图层：红色五角星+白边"""
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
    """加载地级市点位数据"""
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

    name_field = _find_name_field(layer, ["市", "NAME", "城市", "地名", "CITY", "市名", "地级市"])
    if name_field:
        _setup_point_labels(layer, name_field, CITY_LABEL_FONT_SIZE_PT, CITY_LABEL_COLOR)

    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成")
    return layer


def create_intensity_legend_layer():
    """创建烈度图例用的线图层"""
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
    """创建省界图例用的线图层"""
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
    """创建市界图例用的线图层"""
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
    """创建县界图例用的线图层"""
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

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm, yanxing_list=None):
    """创建QGIS打印布局"""
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震地质构造图")
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

    _setup_map_grid(map_item, extent)
    _add_north_arrow(layout, map_height_mm)
    _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)
    _add_legend(layout, map_item, project, map_height_mm, output_height_mm, yanxing_list)

    return layout


def _setup_map_grid(map_item, extent):
    """配置地图经纬度网格"""
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
    """添加指北针"""
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM,
                                          QgsUnitTypes.LayoutMillimeters))
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

    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    padding_x = 1.0
    padding_y = 0.5
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x + padding_x, arrow_y + padding_y,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM - padding_x * 2,
                                             NORTH_ARROW_HEIGHT_MM - padding_y * 2,
                                             QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)
    print(f"[信息] 指北针添加完成")


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """添加比例尺"""
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


def _add_legend(layout, map_item, project, map_height_mm, output_height_mm, yanxing_list=None):
    """
    添加图例。
    - 上部：震中/地级市/省界/市界/县界/烈度（3列2行）
    - 下部：岩性图例（色块 + yanxing名称）
    """
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

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

    yanxing_format = QgsTextFormat()
    yanxing_format.setFont(QFont("SimSun", LEGEND_YANXING_FONT_SIZE_PT))
    yanxing_format.setSize(LEGEND_YANXING_FONT_SIZE_PT)
    yanxing_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    yanxing_format.setColor(QColor(0, 0, 0))

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

    # 上部图例
    top_legend_y = legend_y + 7.0
    top_legend_height = 18.0

    top_legend = QgsLayoutItemLegend(layout)
    top_legend.setLinkedMap(map_item)
    top_legend.setAutoUpdateModel(False)
    top_legend.attemptMove(QgsLayoutPoint(legend_x + 1.0, top_legend_y, QgsUnitTypes.LayoutMillimeters))
    top_legend.attemptResize(QgsLayoutSize(legend_width - 2.0, top_legend_height, QgsUnitTypes.LayoutMillimeters))
    top_legend.setTitle("")
    top_legend.setColumnCount(3)
    top_legend.setSplitLayer(True)
    top_legend.setEqualColumnWidth(True)
    top_legend.rstyle(QgsLegendStyle.Title).setTextFormat(title_format)
    top_legend.rstyle(QgsLegendStyle.SymbolLabel).setTextFormat(item_format)
    top_legend.setSymbolWidth(4.0)
    top_legend.setSymbolHeight(2.5)
    top_legend.setFrameEnabled(False)
    top_legend.setBackgroundEnabled(False)

    top_model = top_legend.model()
    top_root = top_model.rootGroup()
    top_root.removeAllChildren()

    layer_order_top = ["震中", "市界", "地级市", "县界", "省界", "烈度"]
    for layer_name in layer_order_top:
        layers = project.mapLayersByName(layer_name)
        if layers:
            top_root.addLayer(layers[0])

    layout.addLayoutItem(top_legend)

    # 岩性图例
    yanxing_legend_y = top_legend_y + top_legend_height + 2.0

    # 分隔线
    sep_line = QgsLayoutItemShape(layout)
    sep_line.setShapeType(QgsLayoutItemShape.Rectangle)
    sep_line.attemptMove(QgsLayoutPoint(legend_x + 2.0, yanxing_legend_y - 1.0, QgsUnitTypes.LayoutMillimeters))
    sep_line.attemptResize(QgsLayoutSize(legend_width - 4.0, 0.3, QgsUnitTypes.LayoutMillimeters))
    sep_symbol = QgsFillSymbol.createSimple({'color': '180,180,180,255', 'outline_style': 'no'})
    sep_line.setSymbol(sep_symbol)
    sep_line.setFrameEnabled(False)
    layout.addLayoutItem(sep_line)

    # 岩性标题
    yanxing_title = QgsLayoutItemLabel(layout)
    yanxing_title.setText("岩  性")
    yanxing_title.setTextFormat(title_format)
    yanxing_title.attemptMove(QgsLayoutPoint(legend_x, yanxing_legend_y + 1.0, QgsUnitTypes.LayoutMillimeters))
    yanxing_title.attemptResize(QgsLayoutSize(legend_width, 4.0, QgsUnitTypes.LayoutMillimeters))
    yanxing_title.setHAlign(Qt.AlignHCenter)
    yanxing_title.setVAlign(Qt.AlignVCenter)
    yanxing_title.setFrameEnabled(False)
    yanxing_title.setBackgroundEnabled(False)
    layout.addLayoutItem(yanxing_title)

    # 绘制岩性图例条目
    if yanxing_list:
        item_start_y = yanxing_legend_y + 6.0
        item_height = 3.5
        icon_width = 5.0
        icon_height = 2.5
        gap = 1.0
        left_pad = 2.0

        available_height = legend_height - (item_start_y - legend_y) - 2.0
        max_items = int(available_height / item_height)

        for idx, (value, color_rgba, yanxing_name) in enumerate(yanxing_list):
            if idx >= max_items:
                break

            item_y = item_start_y + idx * item_height

            # 色块
            color_box = QgsLayoutItemShape(layout)
            color_box.setShapeType(QgsLayoutItemShape.Rectangle)
            color_box.attemptMove(QgsLayoutPoint(legend_x + left_pad, item_y, QgsUnitTypes.LayoutMillimeters))
            color_box.attemptResize(QgsLayoutSize(icon_width, icon_height, QgsUnitTypes.LayoutMillimeters))
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

            # yanxing名称
            text_label = QgsLayoutItemLabel(layout)
            text_label.setText(yanxing_name)
            text_label.setTextFormat(yanxing_format)
            text_label.attemptMove(QgsLayoutPoint(legend_x + left_pad + icon_width + gap,
                                                   item_y - 0.3, QgsUnitTypes.LayoutMillimeters))
            text_label.attemptResize(QgsLayoutSize(legend_width - left_pad - icon_width - gap - 2.0,
                                                    item_height, QgsUnitTypes.LayoutMillimeters))
            text_label.setHAlign(Qt.AlignLeft)
            text_label.setVAlign(Qt.AlignVCenter)
            text_label.setFrameEnabled(False)
            text_label.setBackgroundEnabled(False)
            layout.addLayoutItem(text_label)

        print(f"[信息] 岩性图例添加完成，共 {min(len(yanxing_list), max_items)} 项")
    else:
        print("[信息] 无岩性数据，跳过岩性图例")

    print("[信息] 图例添加完成")


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_geology_map(longitude, latitude, magnitude,
                                     output_path="output_geology_map.png",
                                     kml_path=None):
    """生成地震震中地质构造图（主入口函数）"""
    print("=" * 60)
    print(f"[开始] 生成地震地质构造图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print("=" * 60)

    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    qgs_app = None
    if not QgsApplication.instance():
        qgs_app = QgsApplication([], False)
        qgs_app.initQgis()
        print("[信息] QGIS应用初始化完成")

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 加载地质构造底图
    geology_layer = load_geology_raster(GEOLOGY_TIF_PATH)
    if geology_layer:
        project.addMapLayer(geology_layer)

    # 构建岩性图例列表（通过Value关联颜色和yanxing）
    tif_abs_path = resolve_path(GEOLOGY_TIF_PATH)
    yanxing_list = build_yanxing_legend_list(tif_abs_path, geology_layer)
    print(f"[信息] 获取到 {len(yanxing_list)} 个岩性图例项")

    # 加载省市县界
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

    city_point_layer = create_city_point_layer(extent)
    if city_point_layer:
        project.addMapLayer(city_point_layer)

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

    epicenter_layer = create_epicenter_layer(longitude, latitude)
    if epicenter_layer:
        project.addMapLayer(epicenter_layer)

    layout = create_print_layout(project, longitude, latitude, magnitude,
                                  extent, scale, map_height_mm, yanxing_list)

    result = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_temp.svg")
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
    """将打印布局导出为PNG图片"""
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


def test_build_yanxing_legend_list():
    """测试岩性图例列表构建"""
    print("\n--- 测试: build_yanxing_legend_list ---")
    tif_path = resolve_path(GEOLOGY_TIF_PATH)
    if os.path.exists(tif_path):
        yanxing_list = build_yanxing_legend_list(tif_path)
        if yanxing_list:
            print(f"  成功构建 {len(yanxing_list)} 个岩性图例项 ✓")
            for item in yanxing_list[:3]:
                value, color, name = item
                print(f"    Value={value}, Color={color[:3]}, yanxing={name[:20]}...")
        else:
            print("  [跳过] 未能构建岩性图例列表")
    else:
        print(f"  [跳过] TIF文件不存在: {tif_path}")
    print("  岩性图例列表测试完成")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("运行 earthquake_geological_map2 全部测试")
    print("=" * 60)

    test_magnitude_config()
    test_calculate_extent()
    test_int_to_roman()
    test_build_yanxing_legend_list()

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
            magnitude=7.8, output_path="earthquake_geology_tangshan_M7.8.png"
        )