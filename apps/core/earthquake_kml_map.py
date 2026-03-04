# -*- coding: utf-8 -*-
"""
地震烈度图生成工具 — 第一部分：常量配置与工具函数
基于QGIS 3.40.15，读取KML烈度圈数据，叠加天地图底图、省市县边界、断裂，
添加指北针、图例、说明文字、比例尺，输出PNG地图。
"""

import os
import re
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

# ============================================================================
#  第一部分：常量配置与工具函数
# ============================================================================

# -------------------- 天地图配置 --------------------
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

# ★★★ 关键修正：天地图XYZ瓦片URL ★★★
# 使用标准XYZ格式，不做任何URL编码，让QGIS自行处理
# 使用Web Mercator(w)投影，与QGIS默认XYZ瓦片兼容
# 子域名用固定t0（不用{s}占位符，避免解析问题）

# 天地图矢量底图（彩色）
TIANDITU_VEC_URL = (
    "http://t0.tianditu.gov.cn/vec_w/wmts"
    "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=vec&STYLE=default&TILEMATRIXSET=w"
    "&FORMAT=tiles"
    "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 天地图矢量注记（地名标注）
TIANDITU_CVA_URL = (
    "http://t0.tianditu.gov.cn/cva_w/wmts"
    "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cva&STYLE=default&TILEMATRIXSET=w"
    "&FORMAT=tiles"
    "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# -------------------- 数据文件路径 --------------------
PROVINCE_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国省份行政区划数据/省级行政区划/省.shp")
)
CITY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国市级行政区划数据/市级行政区划/市.shp")
)
COUNTY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国县级行政区划数据/县级行政区划/县.shp")
)
FAULT_KMZ_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/断层/全国六代图断裂.KMZ")
)

# -------------------- 烈度圈颜色配置（RGB） --------------------
INTENSITY_COLORS = {
    12: (139, 0, 0),
    11: (178, 34, 34),
    10: (220, 20, 60),
    9:  (255, 0, 0),
    8:  (255, 69, 0),
    7:  (255, 99, 71),
    6:  (255, 140, 0),
    5:  (255, 165, 0),
    4:  (255, 200, 100),
    3:  (255, 230, 150),
    2:  (255, 245, 200),
    1:  (255, 255, 224),
}

INTENSITY_LINE_WIDTH = 0.8

ROMAN_NUMERAL_MAP = {
    1: 'Ⅰ', 2: 'Ⅱ', 3: 'Ⅲ', 4: 'Ⅳ', 5: 'Ⅴ',
    6: 'Ⅵ', 7: 'Ⅶ', 8: 'Ⅷ', 9: 'Ⅸ', 10: 'Ⅹ',
    11: 'Ⅺ', 12: 'Ⅻ'
}

OUTPUT_DPI = 300
OUTPUT_WIDTH_MM = 297
OUTPUT_HEIGHT_MM = 210

# -------------------- 比例尺配置 --------------------
SCALE_BAR_CONFIG = {
    'small':  {'max_magnitude': 6.0, 'map_scale': 150000,
               'segment_size_km': 2,  'num_segments': 4},
    'medium': {'max_magnitude': 7.0, 'map_scale': 500000,
               'segment_size_km': 5,  'num_segments': 4},
    'large':  {'max_magnitude': 99,  'map_scale': 1500000,
               'segment_size_km': 15, 'num_segments': 4},
}

# -------------------- 布局区域配置（毫米） --------------------
# 整体布局：左侧地图 | 右侧（说明文字 + 图例 + 比例尺 + 日期）
#
#  ┌──────────────────┬───────────┐
#  │                  │  说明文字  │
#  │                  │           │
#  │     地图框       ├───────────┤
#  │                  │   图 例   │
#  │                  │ (两列布局) │
#  │                  ├───────────┤
#  │                  │ 比例尺    │
#  │                  │ 日期      │
#  └──────────────────┴───────────┘

LAYOUT_MAP_X = 2
LAYOUT_MAP_Y = 2
LAYOUT_MAP_W = 200
LAYOUT_MAP_H = 206

LAYOUT_RIGHT_X = 205       # 右侧区域起始X
LAYOUT_RIGHT_W = 90        # 右侧区域宽度（加宽，防止文字溢出）
LAYOUT_RIGHT_PAD = 3       # 内边距

LAYOUT_DESC_Y = 2          # 说明文字起始Y
LAYOUT_DESC_FONT_SIZE = 9  # ★ 说明文字字号加大（原8太小）

LAYOUT_LEGEND_GAP = 2      # 图例与说明文字间距
LAYOUT_SCALEBAR_GAP = 2    # 比例尺与图例间距


# -------------------- 工具函数 --------------------

def parse_kml_file(kml_file_path):
    """解析KML文件，提取所有烈度圈信息，按烈度从大到小排序。"""
    tree = ET.parse(kml_file_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    intensity_list = []

    for placemark in root.findall('.//kml:Placemark', ns):
        name_elem = placemark.find('kml:name', ns)
        coords_elem = placemark.find('.//kml:coordinates', ns)
        if name_elem is None or coords_elem is None:
            continue
        name_text = name_elem.text.strip()
        intensity_value = _extract_intensity_from_name(name_text)
        if intensity_value is None:
            continue
        coordinates = _parse_coordinates_string(coords_elem.text.strip())
        intensity_list.append({
            'intensity': intensity_value,
            'name': name_text,
            'coordinates': coordinates
        })

    intensity_list.sort(key=lambda x: x['intensity'], reverse=True)
    return intensity_list


def _extract_intensity_from_name(name_text):
    match = re.search(r'(\d+)\s*度', name_text)
    if match:
        return int(match.group(1))
    return None


def _parse_coordinates_string(coords_text):
    coordinates = []
    for point in coords_text.strip().split():
        parts = point.strip().split(',')
        if len(parts) >= 2:
            try:
                coordinates.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return coordinates


def intensity_to_roman(intensity_value):
    if intensity_value in ROMAN_NUMERAL_MAP:
        return ROMAN_NUMERAL_MAP[intensity_value]
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    sym = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
    result = ''
    num = intensity_value
    for i in range(len(val)):
        while num >= val[i]:
            result += sym[i]
            num -= val[i]
    return result


def calculate_polygon_area_km2(coordinates):
    R = 6371.0
    n = len(coordinates)
    if n < 3:
        return 0.0
    coords_rad = [(math.radians(lon), math.radians(lat)) for lon, lat in coordinates]
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords_rad[j][0] * math.sin(coords_rad[i][1])
        area -= coords_rad[i][0] * math.sin(coords_rad[j][1])
    area_km2 = abs(area) / 2.0 * R * R
    return round(area_km2, 0)


def get_extent_from_intensities(intensity_list, buffer_ratio=0.15):
    all_lons = []
    all_lats = []
    for item in intensity_list:
        for lon, lat in item['coordinates']:
            all_lons.append(lon)
            all_lats.append(lat)
    if not all_lons or not all_lats:
        raise ValueError("烈度���坐标为空，无法计算显示范围")
    xmin, xmax = min(all_lons), max(all_lons)
    ymin, ymax = min(all_lats), max(all_lats)
    dx = (xmax - xmin) * buffer_ratio
    dy = (ymax - ymin) * buffer_ratio
    return (xmin - dx, ymin - dy, xmax + dx, ymax + dy)


def analyze_description_text(description_text, intensity_list):
    """填充占位符，限制最多500字。"""
    if not intensity_list:
        return description_text[:500]

    max_intensity = intensity_list[0]['intensity']
    max_roman = intensity_to_roman(max_intensity)
    max_area = calculate_polygon_area_km2(intensity_list[0]['coordinates'])

    area_above_vi = 0.0
    for item in intensity_list:
        if item['intensity'] >= 6:
            area_above_vi += calculate_polygon_area_km2(item['coordinates'])

    result = description_text
    result = re.sub(r'极震区地震烈度可达X度',
                    f'极震区地震烈度可达{max_roman}度', result)
    result = re.sub(r'极震区面积估算为X平方千米',
                    f'极震区面积估算为{int(max_area)}平方千米', result)
    result = re.sub(r'地震烈度VI度以上区域面积达X平方千米',
                    f'地震烈度Ⅵ度以上区域面积达{int(area_above_vi)}平方千米', result)

    if len(result) > 500:
        result = result[:497] + "..."
    return result


def get_current_datetime_string():
    now = datetime.now()
    return f"{now.year}年{now.month:02d}月{now.day:02d}日{now.hour:02d}时{now.minute:02d}分"


def get_scale_config_by_magnitude(magnitude):
    if magnitude < 6.0:
        return SCALE_BAR_CONFIG['small']
    elif magnitude < 7.0:
        return SCALE_BAR_CONFIG['medium']
    else:
        return SCALE_BAR_CONFIG['large']


def extract_magnitude_from_description(description_text):
    match = re.search(r'(\d+\.?\d*)\s*级地震', description_text)
    if match:
        return float(match.group(1))
    match = re.search(r'(\d+\.?\d*)\s*级', description_text)
    if match:
        return float(match.group(1))
    return 5.0

# ============================================================================
#  第二部分：QGIS图层加载与样式设置
#  ★ 关键修正：天地图底图彩色显示 + 注记可见
# ============================================================================

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsSymbol,
    QgsSimpleLineSymbolLayer,
    QgsSimpleFillSymbolLayer,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
    QgsMarkerSymbol,
    QgsFontMarkerSymbolLayer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont


def set_project_crs(project):
    """设置QGIS项目坐标系为 EPSG:4326。"""
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    project.setCrs(crs)
    print("项目坐标系设置为 EPSG:4326")


def load_tianditu_basemap(project):
    """
    加载天地图彩色矢量底图 + 矢量注记。

    ★★★ 关键修正说明 ★★★
    1. 使用 type=xyz，URL中不做百分号编码
    2. URL中的特殊字符（&=）保持原样，QGIS的XYZ provider会正确处理
    3. 使用 Web Mercator (w) 投影瓦片
    4. 底图和注记分别加载，注记在底图上方
    5. 添加 referer 和 zmin/zmax 参数确保加载成功
    """
    # ---- 矢量底图（彩色地图） ----
    vec_url = (
        f"http://t0.tianditu.gov.cn/vec_w/wmts"
        f"?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        f"&LAYER=vec&STYLE=default&TILEMATRIXSET=w"
        f"&FORMAT=tiles"
        f"&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
        f"&tk={TIANDITU_TK}"
    )
    vec_uri = f"type=xyz&url={vec_url}&zmin=1&zmax=18"

    vec_layer = QgsRasterLayer(vec_uri, "天地图矢量底图", "wms")
    if not vec_layer.isValid():
        # 备用方案：使用经纬度投影 c
        print("  尝试经纬度投影 vec_c ...")
        vec_url_c = (
            f"http://t0.tianditu.gov.cn/vec_c/wmts"
            f"?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            f"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
            f"&FORMAT=tiles"
            f"&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
            f"&tk={TIANDITU_TK}"
        )
        vec_uri_c = f"type=xyz&url={vec_url_c}&zmin=1&zmax=18&crs=EPSG:4326"
        vec_layer = QgsRasterLayer(vec_uri_c, "天地图矢量底图", "wms")

    if vec_layer.isValid():
        project.addMapLayer(vec_layer)
        print(f"天地图矢量底图加载成功 (valid={vec_layer.isValid()})")
    else:
        print("警告：天地图矢量底图加载失败！")
        print(f"  URI: {vec_uri[:120]}...")

    # ---- 矢量注记（地名、道路名等文字标注） ----
    cva_url = (
        f"http://t0.tianditu.gov.cn/cva_w/wmts"
        f"?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        f"&LAYER=cva&STYLE=default&TILEMATRIXSET=w"
        f"&FORMAT=tiles"
        f"&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
        f"&tk={TIANDITU_TK}"
    )
    cva_uri = f"type=xyz&url={cva_url}&zmin=1&zmax=18"

    cva_layer = QgsRasterLayer(cva_uri, "天地图矢量注记", "wms")
    if not cva_layer.isValid():
        print("  尝试经纬度投影 cva_c ...")
        cva_url_c = (
            f"http://t0.tianditu.gov.cn/cva_c/wmts"
            f"?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            f"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
            f"&FORMAT=tiles"
            f"&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
            f"&tk={TIANDITU_TK}"
        )
        cva_uri_c = f"type=xyz&url={cva_url_c}&zmin=1&zmax=18&crs=EPSG:4326"
        cva_layer = QgsRasterLayer(cva_uri_c, "天地图矢量注记", "wms")

    if cva_layer.isValid():
        project.addMapLayer(cva_layer)
        print(f"天地图矢量注记加载成功 (valid={cva_layer.isValid()})")
    else:
        print("警告：天地图矢量注记加载失败！")
        print(f"  URI: {cva_uri[:120]}...")

    return vec_layer, cva_layer


def load_boundary_layers(project):
    """加载省、市、县三级行政边界图层。"""
    province_layer = QgsVectorLayer(PROVINCE_SHP_PATH, "省界", "ogr")
    if province_layer.isValid():
        _set_boundary_style(province_layer, QColor(80, 80, 80), 0.6, Qt.SolidLine)
        project.addMapLayer(province_layer)
        print("省界图层加载成功")
    else:
        print(f"警告：省界加载失败，路径: {PROVINCE_SHP_PATH}")

    city_layer = QgsVectorLayer(CITY_SHP_PATH, "市界", "ogr")
    if city_layer.isValid():
        _set_boundary_style(city_layer, QColor(120, 120, 120), 0.4, Qt.DotLine)
        project.addMapLayer(city_layer)
        print("市界图层加载成功")
    else:
        print(f"警告：市界加载失败，路径: {CITY_SHP_PATH}")

    county_layer = QgsVectorLayer(COUNTY_SHP_PATH, "县界", "ogr")
    if county_layer.isValid():
        _set_boundary_style(county_layer, QColor(180, 180, 180), 0.25, Qt.SolidLine)
        project.addMapLayer(county_layer)
        print("县界图层加载成功")
    else:
        print(f"警告：县界加载失败，路径: {COUNTY_SHP_PATH}")

    return province_layer, city_layer, county_layer


def _set_boundary_style(layer, color, width, pen_style=Qt.SolidLine):
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    if symbol.symbolLayerCount() > 0:
        sym_layer = symbol.symbolLayer(0)
        if isinstance(sym_layer, QgsSimpleFillSymbolLayer):
            sym_layer.setColor(QColor(0, 0, 0, 0))
            sym_layer.setStrokeColor(color)
            sym_layer.setStrokeWidth(width)
            sym_layer.setStrokeStyle(pen_style)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def load_fault_layer(project):
    """加载断裂图层，红色实线样式。"""
    fault_layer = QgsVectorLayer(FAULT_KMZ_PATH, "全国六代图断裂", "ogr")
    if fault_layer.isValid():
        symbol = QgsSymbol.defaultSymbol(fault_layer.geometryType())
        line_sl = QgsSimpleLineSymbolLayer()
        line_sl.setColor(QColor(255, 0, 0))
        line_sl.setWidth(0.5)
        line_sl.setPenStyle(Qt.SolidLine)
        symbol.changeSymbolLayer(0, line_sl)
        fault_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        project.addMapLayer(fault_layer)
        print("断裂图层加载成功")
    else:
        print(f"警告：断裂图层加载失败，路径: {FAULT_KMZ_PATH}")
    return fault_layer


def create_intensity_layer(project, intensity_list):
    """创建烈度圈内存矢量图层。"""
    layer = QgsVectorLayer(
        "LineString?crs=EPSG:4326"
        "&field=intensity:integer"
        "&field=name:string"
        "&field=roman:string",
        "烈度圈", "memory"
    )
    if not layer.isValid():
        print("错误：创建烈度圈图层失败")
        return None

    provider = layer.dataProvider()
    features = []
    for item in intensity_list:
        feat = QgsFeature()
        points = [QgsPointXY(lon, lat) for lon, lat in item['coordinates']]
        if points and points[0] != points[-1]:
            points.append(points[0])
        feat.setGeometry(QgsGeometry.fromPolylineXY(points))
        feat.setAttributes([
            item['intensity'],
            item['name'],
            intensity_to_roman(item['intensity'])
        ])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    _set_intensity_renderer(layer, intensity_list)
    _set_intensity_labels(layer)
    project.addMapLayer(layer)
    print(f"烈度圈图层创建成功，共 {len(intensity_list)} 个烈度圈")
    return layer


def _set_intensity_renderer(layer, intensity_list):
    root_rule = QgsRuleBasedRenderer.Rule(None)
    for item in intensity_list:
        iv = item['intensity']
        rgb = INTENSITY_COLORS.get(iv, (255, 0, 0))
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
        sl = QgsSimpleLineSymbolLayer()
        sl.setColor(QColor(*rgb))
        sl.setWidth(INTENSITY_LINE_WIDTH)
        symbol.changeSymbolLayer(0, sl)
        rule = QgsRuleBasedRenderer.Rule(symbol)
        rule.setLabel(f"{intensity_to_roman(iv)}度")
        rule.setFilterExpression(f'"intensity" = {iv}')
        root_rule.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root_rule))
    layer.triggerRepaint()


def _set_intensity_labels(layer):
    settings = QgsPalLayerSettings()
    settings.fieldName = "concat(\"roman\", '度')"
    settings.isExpression = True
    settings.placement = QgsPalLayerSettings.Line
    settings.enabled = True

    fmt = QgsTextFormat()
    font = QFont("Times New Roman", 10)
    font.setBold(True)
    fmt.setFont(font)
    fmt.setSize(10)
    fmt.setColor(QColor(200, 0, 0))

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)

    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


def create_epicenter_layer(project, lon, lat):
    """创建震中标记图层（红色五角星）。"""
    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string",
        "震中", "memory"
    )
    if not layer.isValid():
        print("错误：创建震中图层失败")
        return None

    provider = layer.dataProvider()
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
    feat.setAttributes(["震中"])
    provider.addFeatures([feat])
    layer.updateExtents()

    symbol = QgsMarkerSymbol()
    fm = QgsFontMarkerSymbolLayer("SimSun", "★")
    fm.setSize(6.0)
    fm.setColor(QColor(255, 0, 0))
    fm.setStrokeColor(QColor(0, 0, 0))
    fm.setStrokeWidth(0.2)
    symbol.changeSymbolLayer(0, fm)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    project.addMapLayer(layer)
    print(f"震中标记加载成功: ({lon}, {lat})")
    return layer

# ============================================================================
#  第三部分：地图布局与出图
#  ★★★ 关键修正：
#  1. 说明文字加大字号、增加右侧宽度防止溢出
#  2. 图例分两列摆放（彩色），给比例尺留位置
#  3. 图例使用 HTML 实现彩色符号
# ============================================================================

from qgis.core import (
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutMeasurement,
    QgsLayoutExporter,
    QgsRectangle,
    QgsUnitTypes,
    QgsApplication,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont


def create_print_layout(project, layout_name="地震烈度图"):
    """创建A4横向打印布局。"""
    manager = project.layoutManager()
    old = manager.layoutByName(layout_name)
    if old:
        manager.removeLayout(old)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(layout_name)

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(OUTPUT_WIDTH_MM, OUTPUT_HEIGHT_MM))

    manager.addLayout(layout)
    print(f"打印布局 '{layout_name}' 创建成功")
    return layout


def add_map_item(layout, project, extent):
    """添加地图框。"""
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(LAYOUT_MAP_X, LAYOUT_MAP_Y))
    map_item.attemptResize(QgsLayoutSize(LAYOUT_MAP_W, LAYOUT_MAP_H))

    xmin, ymin, xmax, ymax = extent
    map_item.setExtent(QgsRectangle(xmin, ymin, xmax, ymax))

    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))

    # 经纬度网格
    grid = map_item.grid()
    grid.setEnabled(True)
    lon_span = xmax - xmin
    lat_span = ymax - ymin
    max_span = max(lon_span, lat_span)
    if max_span > 5:
        interval = 1.0
    elif max_span > 2:
        interval = 0.5
    elif max_span > 1:
        interval = 0.25
    else:
        interval = 0.1
    grid.setIntervalX(interval)
    grid.setIntervalY(interval)
    grid.setStyle(QgsLayoutItemMapGrid.Cross)
    grid.setCrossLength(2.0)
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinuteSecond)
    grid.setAnnotationPrecision(0)

    grid_fmt = QgsTextFormat()
    grid_fmt.setFont(QFont("Times New Roman", 7))
    grid_fmt.setSize(7)
    grid.setAnnotationTextFormat(grid_fmt)

    layout.addLayoutItem(map_item)
    print("地图框添加成功")
    return map_item


def add_north_arrow(layout):
    """在地图内右上角添加指北针。"""
    arrow = QgsLayoutItemPicture(layout)
    svg_found = False
    svg_paths = QgsApplication.svgPaths()
    preferred_svgs = [
        'NorthArrow_02.svg', 'NorthArrow_11.svg',
        'NorthArrow_01.svg', 'NorthArrow_04.svg',
    ]

    if svg_paths:
        for svg_dir in svg_paths:
            for svg_name in preferred_svgs:
                candidate = os.path.join(svg_dir, 'arrows', svg_name)
                if os.path.exists(candidate):
                    arrow.setPicturePath(candidate)
                    svg_found = True
                    break
            if svg_found:
                break

    if not svg_found:
        prefix = os.environ.get('QGIS_PREFIX_PATH', '')
        for d in [os.path.join(prefix, 'svg', 'arrows'),
                   os.path.join(prefix, '..', 'svg', 'arrows'),
                   os.path.join(prefix, 'resources', 'svg', 'arrows')]:
            for svg_name in preferred_svgs:
                fb = os.path.join(d, svg_name)
                if os.path.exists(fb):
                    arrow.setPicturePath(fb)
                    svg_found = True
                    break
            if svg_found:
                break

    arrow_x = LAYOUT_MAP_X + LAYOUT_MAP_W - 20
    arrow_y = LAYOUT_MAP_Y + 3

    # 白色背景框
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(arrow_x - 1, arrow_y - 1))
    bg.attemptResize(QgsLayoutSize(17, 22))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 240))
    layout.addLayoutItem(bg)

    arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y))
    arrow.attemptResize(QgsLayoutSize(15, 20))
    layout.addLayoutItem(arrow)

    if not svg_found:
        # 文字模拟指北针
        n_label = QgsLayoutItemLabel(layout)
        n_label.setText("N\n▲")
        n_font = QFont("Times New Roman", 14)
        n_font.setBold(True)
        n_label.setFont(n_font)
        n_label.setHAlign(Qt.AlignCenter)
        n_label.attemptMove(QgsLayoutPoint(arrow_x, arrow_y + 1))
        n_label.attemptResize(QgsLayoutSize(15, 18))
        layout.addLayoutItem(n_label)

    print("指北针添加成功")
    return arrow


def add_description_text(layout, description_text):
    """
    右侧上部添加说明文字。
    ★ 修正：加大字号到9pt，增加右侧宽度，避免文字溢出。
    """
    font_size = LAYOUT_DESC_FONT_SIZE  # 9pt
    # 估算高度：右侧可用宽度内，每行大约多少字
    usable_w = LAYOUT_RIGHT_W - LAYOUT_RIGHT_PAD * 2 - 2  # 约84mm
    # 9pt宋体中文约3.2mm宽，每行约26字
    chars_per_line = max(int(usable_w / 3.2), 16)
    char_count = len(description_text)
    extra_lines = description_text.count('\n')
    num_lines = math.ceil(char_count / chars_per_line) + extra_lines
    line_height_mm = font_size * 0.45  # pt到mm粗略转换
    estimated_height = num_lines * line_height_mm + LAYOUT_RIGHT_PAD * 2 + 4
    desc_h = min(estimated_height, 90)
    desc_h = max(desc_h, 30)  # 最小高度

    # 背景矩形
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X, LAYOUT_DESC_Y))
    bg.attemptResize(QgsLayoutSize(LAYOUT_RIGHT_W, desc_h))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 230))
    layout.addLayoutItem(bg)

    # 文字标签 — 宋体 SimSun，字号加大
    label = QgsLayoutItemLabel(layout)
    label.setText(description_text)

    font = QFont("SimSun", font_size)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(font_size)
    fmt.setColor(QColor(0, 0, 0))
    label.setFont(font)
    label.setTextFormat(fmt)
    label.setMarginX(2)
    label.setMarginY(2)

    label.attemptMove(QgsLayoutPoint(
        LAYOUT_RIGHT_X + LAYOUT_RIGHT_PAD,
        LAYOUT_DESC_Y + LAYOUT_RIGHT_PAD
    ))
    label.attemptResize(QgsLayoutSize(
        LAYOUT_RIGHT_W - LAYOUT_RIGHT_PAD * 2,
        desc_h - LAYOUT_RIGHT_PAD * 2
    ))
    label.setMode(QgsLayoutItemLabel.ModeFont)

    layout.addLayoutItem(label)
    desc_bottom_y = LAYOUT_DESC_Y + desc_h
    print(f"说明文字添加成功: 字号={font_size}pt, 高度={desc_h:.1f}mm")
    return label, desc_bottom_y


def _rgb_to_hex(r, g, b):
    """RGB元组转HTML颜色字符串。"""
    return f"#{r:02x}{g:02x}{b:02x}"


def add_legend(layout, intensity_list, top_y):
    """
    ★★★ 彩色��例，两列布局 ★★★

    使用HTML模式渲染图例，实现彩色符号。
    左列：震中、省界、市界、县界、断层
    右列：各烈度圈（彩色圆点 + 文字）

    参数:
        layout:         QgsPrintLayout — 打印布局
        intensity_list: list[dict]     — 烈度圈信息列表
        top_y:          float          — 图例顶部Y坐标
    """
    legend_y = top_y + LAYOUT_LEGEND_GAP

    # 计算图例需要的高度
    num_left = 5   # 震中+省界+市界+县界+断层
    num_right = len(intensity_list)
    num_rows = max(num_left, num_right)
    row_h = 5.5     # 每行高度mm
    title_h = 10    # 标题行高度
    legend_h = title_h + num_rows * row_h + 6

    # 确保不超出页面
    max_h = OUTPUT_HEIGHT_MM - legend_y - 28  # 留28mm给比例尺+日期
    if legend_h > max_h:
        legend_h = max_h

    legend_w = LAYOUT_RIGHT_W

    # ---- 背景框 ----
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X, legend_y))
    bg.attemptResize(QgsLayoutSize(legend_w, legend_h))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.5))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 240))
    layout.addLayoutItem(bg)

    # ---- 标题 "图  例"（黑体 SimHei） ----
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_font = QFont("SimHei", 11)
    title_font.setBold(True)
    title_label.setFont(title_font)
    title_fmt = QgsTextFormat()
    title_fmt.setFont(title_font)
    title_fmt.setSize(11)
    title_fmt.setColor(QColor(0, 0, 0))
    title_label.setTextFormat(title_fmt)
    title_label.setHAlign(Qt.AlignCenter)
    title_label.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X + 2, legend_y + 1))
    title_label.attemptResize(QgsLayoutSize(legend_w - 4, 9))
    layout.addLayoutItem(title_label)

    # ---- 构建HTML彩色图例 ----
    content_y = legend_y + title_h

    # 左列HTML：固定图例项（震中、边界、断层）
    left_html = _build_left_legend_html()
    # 右列HTML：烈度圈（彩色）
    right_html = _build_right_legend_html(intensity_list)

    # ---- 左列标签（HTML模式） ----
    left_label = QgsLayoutItemLabel(layout)
    left_label.setText(left_html)
    left_label.setMode(QgsLayoutItemLabel.ModeHtml)
    left_label.attemptMove(QgsLayoutPoint(
        LAYOUT_RIGHT_X + 2, content_y
    ))
    col_w = (legend_w - 4) / 2
    left_label.attemptResize(QgsLayoutSize(col_w, legend_h - title_h - 4))
    layout.addLayoutItem(left_label)

    # ---- 右列标签（HTML模式） ----
    right_label = QgsLayoutItemLabel(layout)
    right_label.setText(right_html)
    right_label.setMode(QgsLayoutItemLabel.ModeHtml)
    right_label.attemptMove(QgsLayoutPoint(
        LAYOUT_RIGHT_X + 2 + col_w, content_y
    ))
    right_label.attemptResize(QgsLayoutSize(col_w, legend_h - title_h - 4))
    layout.addLayoutItem(right_label)

    legend_bottom_y = legend_y + legend_h
    print(f"图例添加成功(彩色两列): Y={legend_y:.1f}, H={legend_h:.1f}mm")
    return bg, legend_bottom_y


def _build_left_legend_html():
    """
    构建左列图例HTML：震中、省界、市界、县界、断层。
    使用HTML内联样式实现彩色符号。
    """
    html = """
    <table style="font-family:SimSun; font-size:8pt; border-spacing:1px 3px;">
      <tr>
        <td style="color:#ff0000; font-size:12pt; text-align:center; width:20px;">★</td>
        <td>震中位置</td>
      </tr>
      <tr>
        <td style="text-align:center;">
          <svg width="20" height="3"><line x1="0" y1="1" x2="20" y2="1"
            style="stroke:#505050;stroke-width:2"/></svg>
        </td>
        <td>省界</td>
      </tr>
      <tr>
        <td style="text-align:center;">
          <svg width="20" height="3"><line x1="0" y1="1" x2="20" y2="1"
            style="stroke:#787878;stroke-width:1;stroke-dasharray:2,2"/></svg>
        </td>
        <td>市界</td>
      </tr>
      <tr>
        <td style="text-align:center;">
          <svg width="20" height="3"><line x1="0" y1="1" x2="20" y2="1"
            style="stroke:#b4b4b4;stroke-width:1"/></svg>
        </td>
        <td>县界</td>
      </tr>
      <tr>
        <td style="text-align:center;">
          <svg width="20" height="3"><line x1="0" y1="1" x2="20" y2="1"
            style="stroke:#ff0000;stroke-width:2"/></svg>
        </td>
        <td style="color:#ff0000;">断层</td>
      </tr>
    </table>
    """
    return html


def _build_right_legend_html(intensity_list):
    """
    构建右列图例HTML：各烈度圈（彩色圆点 + 文字）。
    每个烈度圈用对应颜色的圆点表示。
    """
    rows = ""
    for item in sorted(intensity_list, key=lambda x: x['intensity'], reverse=True):
        iv = item['intensity']
        rgb = INTENSITY_COLORS.get(iv, (255, 0, 0))
        hex_color = _rgb_to_hex(*rgb)
        roman = intensity_to_roman(iv)
        rows += f"""
      <tr>
        <td style="text-align:center; width:20px;">
          <span style="color:{hex_color}; font-size:14pt;">●</span>
        </td>
        <td style="font-family:SimSun; font-size:8pt;">
          <span style="font-family:'Times New Roman';">{roman}</span>度烈度圈
        </td>
      </tr>
        """

    html = f"""
    <table style="font-family:SimSun; font-size:8pt; border-spacing:1px 3px;">
      {rows}
    </table>
    """
    return html


def add_scale_bar(layout, map_item, magnitude=5.0, top_y=None):
    """
    比例尺放在右侧图例下方。
    根据震级动态调整比例。
    """
    scale_config = get_scale_config_by_magnitude(magnitude)
    map_scale = scale_config['map_scale']
    segment_size_km = scale_config['segment_size_km']
    num_segments = scale_config['num_segments']

    sb = QgsLayoutItemScaleBar(layout)
    sb.setLinkedMap(map_item)
    sb.setStyle('Line Ticks Up')
    sb.setUnits(QgsUnitTypes.DistanceKilometers)
    sb.setUnitLabel("千米")
    sb.setNumberOfSegments(num_segments)
    sb.setNumberOfSegmentsLeft(0)
    sb.setUnitsPerSegment(segment_size_km)
    sb.setHeight(3)

    fmt = QgsTextFormat()
    fmt.setFont(QFont("Times New Roman", 7))
    fmt.setSize(7)
    sb.setTextFormat(fmt)

    # 放在图例下方
    if top_y is not None:
        sb_y = top_y + LAYOUT_SCALEBAR_GAP
    else:
        sb_y = OUTPUT_HEIGHT_MM - 25

    sb.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X + 5, sb_y))
    sb.attemptResize(QgsLayoutSize(LAYOUT_RIGHT_W - 10, 10))

    layout.addLayoutItem(sb)
    sb_bottom_y = sb_y + 12
    print(f"比例尺添加成功: 1:{map_scale}, Y={sb_y:.1f}mm")
    return sb, sb_bottom_y


def add_datetime_label(layout, top_y=None):
    """制图机构和制图日期，放在比例尺下方。"""
    label = QgsLayoutItemLabel(layout)
    current_date = get_current_datetime_string()
    label.setText(f"中国地震灾害防御中心\n{current_date}")

    font = QFont("SimSun", 7)
    label.setFont(font)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(7)
    fmt.setColor(QColor(0, 0, 0))
    label.setTextFormat(fmt)

    if top_y is not None:
        dt_y = top_y + 1
    else:
        dt_y = OUTPUT_HEIGHT_MM - 14

    label.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X + 5, dt_y))
    label.attemptResize(QgsLayoutSize(LAYOUT_RIGHT_W - 10, 12))
    label.setHAlign(Qt.AlignLeft)

    layout.addLayoutItem(label)
    print(f"制图时间添加成功: {current_date}")
    return label


def export_layout_to_png(layout, output_path, dpi=OUTPUT_DPI):
    """导出PNG。"""
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi

    result = exporter.exportToImage(output_path, settings)

    if result == QgsLayoutExporter.Success:
        print(f"PNG导出成功: {output_path}")
        return True

    error_map = {
        QgsLayoutExporter.FileError: "文件错误",
        QgsLayoutExporter.MemoryError: "内存错误",
        QgsLayoutExporter.SvgLayerError: "SVG图层错误",
        QgsLayoutExporter.PrintError: "打印错误",
        QgsLayoutExporter.Canceled: "已取消",
    }
    print(f"PNG导出失败: {error_map.get(result, f'未知错误({result})')}")
    return False

# ============================================================================
#  第四部分：主流程控制与测试
# ============================================================================


def generate_seismic_intensity_map(kml_file_path, description_text, output_png_path,
                                    epicenter_lon=None, epicenter_lat=None,
                                    magnitude=None):
    """
    生成地震烈度图 —— 主函数。

    参数:
        kml_file_path:    str   — KML文件路径
        description_text: str   — 说明文字模板（含占位符X，最��500字）
        output_png_path:  str   — 输出PNG路径
        epicenter_lon:    float — 震中经度（可选）
        epicenter_lat:    float — 震中纬度（可选）
        magnitude:        float — 震级（可选，用于比例尺动态调整）
    """
    print("=" * 60)
    print("开始生成地震烈度图")
    print("=" * 60)

    # ---- 1. 解析KML ----
    print("\n[1/12] 解析KML文件...")
    intensity_list = parse_kml_file(kml_file_path)
    if not intensity_list:
        print("错误：未解析到烈度圈数据")
        return False
    for item in intensity_list:
        roman = intensity_to_roman(item['intensity'])
        area = calculate_polygon_area_km2(item['coordinates'])
        print(f"  {item['name']} → {roman}度, 点数:{len(item['coordinates'])}, "
              f"面积≈{int(area)}km²")

    # ---- 2. 填充说明文字 ----
    print("\n[2/12] 分析说明文字...")
    final_desc = analyze_description_text(description_text, intensity_list)
    print(f"  字数: {len(final_desc)}")

    # ---- 2.5 确定震级 ----
    if magnitude is None:
        magnitude = extract_magnitude_from_description(description_text)
    print(f"  震级: M{magnitude}")
    scale_config = get_scale_config_by_magnitude(magnitude)
    print(f"  比例尺: 1:{scale_config['map_scale']}")

    # ---- 3. 震中 ----
    if epicenter_lon is None or epicenter_lat is None:
        coords = intensity_list[0]['coordinates']
        epicenter_lon = sum(c[0] for c in coords) / len(coords)
        epicenter_lat = sum(c[1] for c in coords) / len(coords)
        print(f"\n[3/12] 自动估算震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")
    else:
        print(f"\n[3/12] 指定震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")

    # ---- 4. 显示范围 ----
    print("\n[4/12] 计算显示范围...")
    extent = get_extent_from_intensities(intensity_list, buffer_ratio=0.2)
    print(f"  经度[{extent[0]:.4f}, {extent[2]:.4f}] 纬度[{extent[1]:.4f}, {extent[3]:.4f}]")

    # ---- 5. 初始化项目 ----
    print("\n[5/12] 初始化QGIS项目...")
    project = QgsProject.instance()
    project.removeAllMapLayers()
    set_project_crs(project)

    # ---- 6. 天地图底图（彩色） ----
    print("\n[6/12] 加载天地图底图...")
    vec_layer, cva_layer = load_tianditu_basemap(project)

    # ★ 检查底图状态
    if vec_layer and vec_layer.isValid():
        print(f"  底图状态: valid, provider={vec_layer.providerType()}")
    else:
        print("  ⚠ 底图无效！请检查网络连接和天地图tk密钥")

    if cva_layer and cva_layer.isValid():
        print(f"  注记状态: valid, provider={cva_layer.providerType()}")
    else:
        print("  ⚠ 注记无效！请检查网络连接和天地图tk密钥")

    # ---- 7. 行政边界 ----
    print("\n[7/12] 加载行政边界...")
    load_boundary_layers(project)

    # ---- 8. 断裂 ----
    print("\n[8/12] 加载断裂...")
    load_fault_layer(project)

    # ---- 9. 烈度圈 ----
    print("\n[9/12] 创建烈度圈...")
    create_intensity_layer(project, intensity_list)

    # ---- 10. 震中 ----
    print("\n[10/12] 创建震中标记...")
    create_epicenter_layer(project, epicenter_lon, epicenter_lat)

    # ---- 11. 打印布局 ----
    print("\n[11/12] 构建打印布局...")
    layout = create_print_layout(project)

    # 地图框
    map_item = add_map_item(layout, project, extent)

    # 指北针
    add_north_arrow(layout)

    # 说明文字（右侧上部）
    desc_label, desc_bottom_y = add_description_text(layout, final_desc)

    # 图例（右侧，说明文字下方，彩色两列）
    legend_bg, legend_bottom_y = add_legend(layout, intensity_list, desc_bottom_y)

    # 比例尺（图例下方）
    sb, sb_bottom_y = add_scale_bar(layout, map_item,
                                     magnitude=magnitude,
                                     top_y=legend_bottom_y)

    # 制图时间（比例尺下方）
    add_datetime_label(layout, top_y=sb_bottom_y)

    # ---- 12. 导出 ----
    print("\n[12/12] 导出PNG...")
    out_dir = os.path.dirname(output_png_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    success = export_layout_to_png(layout, output_png_path)

    print("\n" + "=" * 60)
    if success:
        print(f"✓ 生成成功！ → {output_png_path}")
    else:
        print("✗ 生成失败！")
    print("=" * 60)
    return success


# ============================================================================
#  单元测试
# ============================================================================

def test_parse_kml():
    import tempfile
    print("\n--- 测试: KML解析 ---")
    kml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>4度</name><description/>
<LineString><coordinates>
114.785,39.444,0 114.784,39.446,0 114.783,39.448,0 114.785,39.444,0
</coordinates></LineString></Placemark>
<Placemark><name>5度</name><description/>
<LineString><coordinates>
114.546,39.369,0 114.546,39.370,0 114.546,39.369,0
</coordinates></LineString></Placemark>
<Placemark><name>6度</name><description/>
<LineString><coordinates>
114.421,39.329,0 114.421,39.330,0 114.421,39.329,0
</coordinates></LineString></Placemark>
<Placemark><name>7度</name><description/>
<LineString><coordinates>
103.20,34.10,0 103.30,34.10,0 103.30,34.00,0 103.20,34.00,0 103.20,34.10,0
</coordinates></LineString></Placemark>
</Document>
</kml>'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.kml',
                                      delete=False, encoding='utf-8') as f:
        f.write(kml_content)
        tmp = f.name
    try:
        result = parse_kml_file(tmp)
        assert len(result) == 4
        assert result[0]['intensity'] == 7
        assert result[-1]['intensity'] == 4
        print("  ✓ KML解析通过")
    finally:
        os.unlink(tmp)


def test_roman_numeral():
    print("\n--- 测试: 罗马数字转换 ---")
    expected = {
        1: 'Ⅰ', 2: 'Ⅱ', 3: 'Ⅲ', 4: 'Ⅳ', 5: 'Ⅴ',
        6: 'Ⅵ', 7: 'Ⅶ', 8: 'Ⅷ', 9: 'Ⅸ', 10: 'Ⅹ',
        11: 'Ⅺ', 12: 'Ⅻ'
    }
    for num, exp in expected.items():
        assert intensity_to_roman(num) == exp
    print("  ✓ 罗马数字转换通过")


def test_description_analysis():
    print("\n--- 测试: 说明文字分析 ---")
    mock_data = [
        {'intensity': 7, 'name': '7度',
         'coordinates': [(103.20, 34.10), (103.30, 34.10),
                         (103.30, 34.00), (103.20, 34.00), (103.20, 34.10)]},
        {'intensity': 6, 'name': '6度',
         'coordinates': [(103.00, 34.20), (103.50, 34.20),
                         (103.50, 33.80), (103.00, 33.80), (103.00, 34.20)]},
        {'intensity': 5, 'name': '5度',
         'coordinates': [(102.50, 34.50), (103.80, 34.50),
                         (103.80, 33.50), (102.50, 33.50), (102.50, 34.50)]},
    ]
    template = (
        "预计极震区地震烈度可达X度，"
        "极震区面积估算为X平方千米，"
        "地震烈度VI度以上区域面积达X平方千米。"
    )
    result = analyze_description_text(template, mock_data)
    assert 'Ⅶ度' in result
    assert '可达X度' not in result
    assert len(result) <= 500
    print("  ✓ 说明文字分析通过")


def test_area_calculation():
    print("\n--- 测试: 面积计算 ---")
    coords = [
        (103.20, 34.00), (103.30, 34.00),
        (103.30, 34.10), (103.20, 34.10),
        (103.20, 34.00)
    ]
    area = calculate_polygon_area_km2(coords)
    print(f"  0.1°×0.1° = {area} km²")
    assert 50 < area < 200
    print("  ✓ 面积计算通过")


def test_extent_calculation():
    print("\n--- 测试: 显示范围计算 ---")
    mock_data = [
        {'intensity': 7, 'name': '7度',
         'coordinates': [(103.20, 34.10), (103.30, 34.00)]},
        {'intensity': 5, 'name': '5度',
         'coordinates': [(102.50, 34.50), (103.80, 33.50)]},
    ]
    xmin, ymin, xmax, ymax = get_extent_from_intensities(mock_data, 0.15)
    assert xmin < 102.50
    assert xmax > 103.80
    print("  ✓ 显示范围计算通过")


def test_scale_config():
    print("\n--- 测试: 比例尺配置 ---")
    assert get_scale_config_by_magnitude(5.5)['map_scale'] == 150000
    assert get_scale_config_by_magnitude(6.5)['map_scale'] == 500000
    assert get_scale_config_by_magnitude(7.8)['map_scale'] == 1500000
    print("  ✓ 比例尺配置通过")


def test_magnitude_extraction():
    print("\n--- 测试: 震级提取 ---")
    assert extract_magnitude_from_description("发生5.5级地震") == 5.5
    assert extract_magnitude_from_description("发生7.8级地震") == 7.8
    assert extract_magnitude_from_description("无震级") == 5.0
    print("  ✓ 震级提取通过")


def test_html_legend():
    print("\n--- 测试: HTML图例构建 ---")
    mock = [
        {'intensity': 7, 'name': '7度', 'coordinates': []},
        {'intensity': 5, 'name': '5度', 'coordinates': []},
    ]
    left = _build_left_legend_html()
    right = _build_right_legend_html(mock)
    assert '★' in left
    assert '省界' in left
    assert '断层' in left
    assert '●' in right
    assert 'Ⅶ' in right
    assert '#ff6347' in right  # intensity 7 color
    assert '#ffa500' in right  # intensity 5 color
    print("  ✓ HTML图例构建通过")


def test_description_length_limit():
    print("\n--- 测试: 说明文字长度限制 ---")
    long_text = "极震区地震烈度可达X度，极震区面积估算为X平方千米，" * 30
    mock_data = [
        {'intensity': 7, 'name': '7度',
         'coordinates': [(103.20, 34.10), (103.30, 34.10),
                         (103.30, 34.00), (103.20, 34.00), (103.20, 34.10)]},
    ]
    result = analyze_description_text(long_text, mock_data)
    assert len(result) <= 500
    print("  ✓ 说明文字长度限制通过")


def run_all_tests():
    print("=" * 50)
    print("运行全部单元测试")
    print("=" * 50)

    tests = [
        test_parse_kml,
        test_roman_numeral,
        test_description_analysis,
        test_area_calculation,
        test_extent_calculation,
        test_scale_config,
        test_magnitude_extraction,
        test_html_legend,
        test_description_length_limit,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 50}")
    print(f"测试结果: {passed} 通过 / {failed} 失败 / 共 {len(tests)} 项")
    print("=" * 50)


# ============================================================================
#  主入口
# ============================================================================

if __name__ == '__main__':
    from qgis.core import QgsApplication

    qgs = QgsApplication([], False)
    qgs.initQgis()

    try:
        # ---------- 1. 运行单元测试 ----------
        run_all_tests()

        # ---------- 2. 生成地震烈度图 ----------
        kml_path = "../../data/geology/n0432881302350072.kml"

        desc_text = (
            "据中国地震台网正式测定：2026年01月26日14时56分"
            "甘肃甘南州迭部县（103.25°，34.06°）发生5.5级地震，"
            "震源深度10千米。\n"
            "综合考虑震中附近地质构造背景、地震波衰减特性，"
            "估计了本次地震的地震动预测图。\n"
            "预计极震区地震烈度可达X度，极震区面积估算为X平方千米，"
            "地震烈度VI度以上区域面积达X平方千米。"
        )

        output = "../../data/geology/kml_2_map.png"

        if os.path.exists(kml_path):
            generate_seismic_intensity_map(
                kml_file_path=kml_path,
                description_text=desc_text,
                output_png_path=output,
                epicenter_lon=103.25,
                epicenter_lat=34.06,
                magnitude=5.5
            )
        else:
            print(f"\n提示: KML文件不存在 ({kml_path})")
            print("请将 kml_path 改为实际路径后重新运行。")
            print("单元测试已完成。")

    finally:
        qgs.exitQgis()