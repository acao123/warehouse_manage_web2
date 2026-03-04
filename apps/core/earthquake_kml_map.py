# -*- coding: utf-8 -*-
"""
地震烈度图生成工具
基于 QGIS 3.40.15，读取 KML 烈度圈数据，叠加天地图底图、省市县边界、断裂，
添加指北针、说明文字、比例尺、制图日期，以及图正下方三行四列图例，输出 PNG 地图。

代码分四部分：
  第一部分：常量配置与工具函数
  第二部分：QGIS 图层加载与样式设置
  第三部分：地图布局与出图
  第四部分：主流程控制与测试
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
# 天地图 API 密钥
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

# 天地图矢量底图 URL（使用 Web Mercator _w 投影，与 QGIS 默认 XYZ 瓦片兼容）
TIANDITU_VEC_URL = (
    "http://t0.tianditu.gov.cn/vec_w/wmts"
    "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=vec&STYLE=default&TILEMATRIXSET=w"
    "&FORMAT=tiles"
    "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 天地图矢量注记 URL
TIANDITU_CVA_URL = (
    "http://t0.tianditu.gov.cn/cva_w/wmts"
    "?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cva&STYLE=default&TILEMATRIXSET=w"
    "&FORMAT=tiles"
    "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# -------------------- 数据文件路径（使用绝对路径） --------------------
# 省界
PROVINCE_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国省份行政区划数据/省级行政区划/省.shp")
)
# 市界
CITY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国市级行政区划数据/市级行政区划/市.shp")
)
# 县界
COUNTY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国县级行政区划数据/县级行政区划/县.shp")
)
# 断裂
FAULT_KMZ_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/断层/全国六代图断裂.KMZ")
)

# -------------------- 烈度圈颜色配置（RGB） --------------------
INTENSITY_COLORS = {
    12: (139, 0,   0),
    11: (178, 34,  34),
    10: (220, 20,  60),
    9:  (255, 0,   0),
    8:  (255, 69,  0),
    7:  (255, 99,  71),
    6:  (255, 140, 0),
    5:  (255, 165, 0),
    4:  (255, 200, 100),
    3:  (255, 230, 150),
    2:  (255, 245, 200),
    1:  (255, 255, 224),
}

# 烈度圈线宽（毫米）
INTENSITY_LINE_WIDTH = 0.8

# -------------------- 烈度罗马数字映射 --------------------
ROMAN_NUMERAL_MAP = {
    1: 'Ⅰ',  2: 'Ⅱ',  3: 'Ⅲ',  4: 'Ⅳ',
    5: 'Ⅴ',  6: 'Ⅵ',  7: 'Ⅶ',  8: 'Ⅷ',
    9: 'Ⅸ', 10: 'Ⅹ', 11: 'Ⅺ', 12: 'Ⅻ',
}

# -------------------- 输出参数 --------------------
OUTPUT_DPI        = 300
OUTPUT_WIDTH_MM   = 297   # A4 横向宽度（毫米）
OUTPUT_HEIGHT_MM  = 210   # A4 横向高度（毫米）

# -------------------- 比例尺配置（按震级分档） --------------------
SCALE_BAR_CONFIG = {
    # 震级 M < 6：1:150,000
    'small':  {'max_magnitude': 6.0, 'map_scale': 150000,
               'segment_km': 2,  'num_segments': 4},
    # 震级 6 ≤ M < 7：1:500,000
    'medium': {'max_magnitude': 7.0, 'map_scale': 500000,
               'segment_km': 5,  'num_segments': 4},
    # 震级 M ≥ 7：1:1,500,000
    'large':  {'max_magnitude': 99,  'map_scale': 1500000,
               'segment_km': 15, 'num_segments': 4},
}

# -------------------- 布局常量（毫米） --------------------
#  整体布局（A4 横向 297×210mm）：
#
#  ┌──────────────────┬───────────┐
#  │                  │  N(指北针)  │
#  │                  │  说明文字  │
#  │     地图框        │           │
#  │  (含烈度圈+       │  比例尺    │
#  │   行政边界+       │  制图时间  │
#  │   断裂)           │           │
#  └──────────────────┴───────────┘
#  │       图例(三行四列布局)        │
#  └─────────────────────────────┘

LAYOUT_MARGIN     = 2     # 页面四周边距（毫米）

# 地图框区域
LAYOUT_MAP_X = LAYOUT_MARGIN          # 2mm
LAYOUT_MAP_Y = LAYOUT_MARGIN          # 2mm
LAYOUT_MAP_W = 198                    # 地图框宽度
LAYOUT_MAP_H = 155                    # 地图框高度（底部: 2+155=157mm）

# 右侧面板区域
LAYOUT_RIGHT_X   = LAYOUT_MAP_X + LAYOUT_MAP_W + 2   # 202mm
LAYOUT_RIGHT_W   = OUTPUT_WIDTH_MM - LAYOUT_RIGHT_X - LAYOUT_MARGIN  # 93mm
LAYOUT_RIGHT_H   = LAYOUT_MAP_H                       # 155mm
LAYOUT_RIGHT_PAD = 3                                  # 内边距（毫米）

# 说明文字
LAYOUT_DESC_FONT_SIZE = 9     # 说明文字字号（磅）— 可用常量调整
LAYOUT_DESC_MAX_CHARS = 450   # 说明文字最大字符数

# 底部图例区域
LAYOUT_LEGEND_Y        = LAYOUT_MAP_Y + LAYOUT_MAP_H + 3   # 160mm
LAYOUT_LEGEND_H        = OUTPUT_HEIGHT_MM - LAYOUT_LEGEND_Y - LAYOUT_MARGIN  # 48mm
LAYOUT_LEGEND_COLS     = 4    # 图例列数
LAYOUT_LEGEND_ROWS     = 3    # 图例行数
LAYOUT_LEGEND_MAX_ITEMS = LAYOUT_LEGEND_COLS * LAYOUT_LEGEND_ROWS  # 12


# ============================================================================
#  工具函数
# ============================================================================

def parse_kml_file(kml_file_path):
    """
    解析 KML 文件，提取所有烈度圈信息，按烈度从大到小排序。

    KML 中每个 Placemark 的 <name> 包含烈度（如 '4度'、'5度'），
    <LineString><coordinates> 包含坐标串（经度,纬度,高度 空格分隔）。

    参数:
        kml_file_path: str — KML 文件路径

    返回:
        list[dict] — 烈度圈列表，每项含：
            intensity:   int             — 烈度值
            name:        str             — 原始名称（如 '4度'）
            coordinates: list[(lon,lat)] — 坐标列表
        （按 intensity 从大到小排序）
    """
    tree = ET.parse(kml_file_path)
    root = tree.getroot()
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    intensity_list = []

    for placemark in root.findall('.//kml:Placemark', ns):
        name_elem   = placemark.find('kml:name', ns)
        coords_elem = placemark.find('.//kml:coordinates', ns)
        if name_elem is None or coords_elem is None:
            continue
        name_text       = name_elem.text.strip()
        intensity_value = _extract_intensity_value(name_text)
        if intensity_value is None:
            continue
        coordinates = _parse_kml_coordinates(coords_elem.text.strip())
        intensity_list.append({
            'intensity':   intensity_value,
            'name':        name_text,
            'coordinates': coordinates,
        })

    # 按烈度从大到小排序（烈度圈一圈套一圈，越外层烈度依次递减）
    intensity_list.sort(key=lambda x: x['intensity'], reverse=True)
    return intensity_list


def _extract_intensity_value(name_text):
    """
    从名称文字中提取烈度数值。

    参数:
        name_text: str — 如 '4度'、'6度'

    返回:
        int 或 None
    """
    match = re.search(r'(\d+)\s*度', name_text)
    if match:
        return int(match.group(1))
    return None


def _parse_kml_coordinates(coords_text):
    """
    解析 KML 坐标字符串为 (lon, lat) 元组列表。

    参数:
        coords_text: str — 空格分隔的 'lon,lat,alt' 格式字符串

    返回:
        list[tuple(float, float)] — 经纬度坐标列表
    """
    coordinates = []
    for point_str in coords_text.strip().split():
        parts = point_str.strip().split(',')
        if len(parts) >= 2:
            try:
                coordinates.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return coordinates


def intensity_to_roman(intensity_value):
    """
    将烈度数值转换为罗马数字字符串（1-12 使用特殊罗马字符）。

    参数:
        intensity_value: int — 烈度值

    返回:
        str — 罗马数字
    """
    if intensity_value in ROMAN_NUMERAL_MAP:
        return ROMAN_NUMERAL_MAP[intensity_value]
    # 超出 12 时使用标准算法
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    sym = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
    result = ''
    num = intensity_value
    for v, s in zip(val, sym):
        while num >= v:
            result += s
            num -= v
    return result


def calculate_polygon_area_km2(coordinates):
    """
    使用球面积分公式计算经纬度坐标多边形面积（平方千米）。

    参数:
        coordinates: list[tuple(float, float)] — (lon, lat) 坐标列表

    返回:
        float — 面积（平方千米），保留整数
    """
    R = 6371.0  # 地球半径（千米）
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


def get_display_extent(intensity_list, buffer_ratio=0.2):
    """
    根据烈度圈坐标计算地图显示范围（四周加缓冲）。

    参数:
        intensity_list: list[dict] — 烈度圈列表
        buffer_ratio:   float      — 缓冲比例（默认 0.2）

    返回:
        tuple(xmin, ymin, xmax, ymax) — 地图显示范围（经纬度）
    """
    all_lons, all_lats = [], []
    for item in intensity_list:
        for lon, lat in item['coordinates']:
            all_lons.append(lon)
            all_lats.append(lat)
    if not all_lons:
        raise ValueError("烈度圈坐标为空，无法计算显示范围")
    xmin, xmax = min(all_lons), max(all_lons)
    ymin, ymax = min(all_lats), max(all_lats)
    dx = (xmax - xmin) * buffer_ratio
    dy = (ymax - ymin) * buffer_ratio
    return (xmin - dx, ymin - dy, xmax + dx, ymax + dy)


def analyze_description_text(description_text, intensity_list):
    """
    填充说明文字中的占位符 X，限制字数不超过 450 字，并在首行添加 2 字符全角缩进。

    自动替换：
      '极震区地震烈度可达X度'         → 替换为最大烈度罗马数字
      '极震区面积估算为X平方千米'     → 替换为最大烈度圈面积（平方千米）
      '地震烈度VI度以上区域面积达X平方千米' → 替换为 VI 度及以上区域总面积

    参数:
        description_text: str      — 含占位符的说明文字
        intensity_list:   list[dict] — 烈度圈列表（按烈度从大到小）

    返回:
        str — 处理后的说明文字（≤450 字，首行带 2 全角空格缩进）
    """
    if not intensity_list:
        return _add_first_line_indent(description_text[:LAYOUT_DESC_MAX_CHARS])

    max_intensity = intensity_list[0]['intensity']
    max_roman     = intensity_to_roman(max_intensity)
    max_area      = calculate_polygon_area_km2(intensity_list[0]['coordinates'])

    # VI 度以上区域：取烈度最低且 ≥ 6 的圈（最外层 VI 度圈）面积
    # intensity_list 已按烈度从大到小排序，reversed 后从低到高遍历，第一个 ≥ 6 即为最外层 VI 圈
    area_above_vi = 0.0
    vi_found = False
    for item in reversed(intensity_list):
        if item['intensity'] >= 6:
            area_above_vi = calculate_polygon_area_km2(item['coordinates'])
            vi_found = True
            break
    if not vi_found:
        print("提示：烈度圈中未找到 VI 度及以上区域，VI度以上面积记为 0")

    result = description_text
    result = re.sub(r'极震区地震烈度可达X度',
                    f'极震区地震烈度可达{max_roman}度', result)
    result = re.sub(r'极震区面积估算为X平方千米',
                    f'极震区面积估算为{int(max_area)}平方千米', result)
    result = re.sub(r'地震烈度VI度以上区域面积达X平方千米',
                    f'地震烈度Ⅵ度以上区域面积达{int(area_above_vi)}平方千米', result)

    # 限制字数不超过 450 字
    if len(result) > LAYOUT_DESC_MAX_CHARS:
        result = result[:LAYOUT_DESC_MAX_CHARS - 3] + '...'

    return _add_first_line_indent(result)


def _add_first_line_indent(text):
    """
    为文字首行添加 2 个全角空格缩进。

    参数:
        text: str — 原始文字

    返回:
        str — 首行带 2 全角空格缩进的文字
    """
    if text and not text.startswith('\u3000'):
        return '\u3000\u3000' + text
    return text


def get_scale_config(magnitude):
    """
    根据震级获取对应的比例尺配置。

    配置规则：
      M < 6.0  → 1:150,000（小比例尺）
      6.0 ≤ M < 7.0 → 1:500,000（中比例尺）
      M ≥ 7.0  → 1:1,500,000（大比例尺）

    参数:
        magnitude: float — 震级

    返回:
        dict — 比例尺配置项（map_scale, segment_km, num_segments）
    """
    if magnitude < 6.0:
        return SCALE_BAR_CONFIG['small']
    elif magnitude < 7.0:
        return SCALE_BAR_CONFIG['medium']
    else:
        return SCALE_BAR_CONFIG['large']


def extract_magnitude(description_text):
    """
    从说明文字中提取震级（M 值）。

    参数:
        description_text: str — 说明文字

    返回:
        float — 震级（找不到时默认 5.0）
    """
    match = re.search(r'(\d+\.?\d*)\s*级地震', description_text)
    if match:
        return float(match.group(1))
    match = re.search(r'(\d+\.?\d*)\s*级', description_text)
    if match:
        return float(match.group(1))
    return 5.0


def get_current_date_string():
    """
    获取当前日期字符串，格式：YYYY年MM月DD日。

    返回:
        str — 制图日期字符串
    """
    now = datetime.now()
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def build_legend_items(intensity_list):
    """
    构建图例项目列表，最多 12 项（三行四列）。

    固定项（5个）：震中位置、省界、市界、县界、断层
    烈度圈项（最多 7 个）：各烈度圈从大到小

    超过 12 项时截断，不展示多余图例。

    参数:
        intensity_list: list[dict] — 烈度圈列表（按烈度从大到小）

    返回:
        list[dict] — 图例项列表，每项含 type, label, [intensity, color]
    """
    # 固定图例项
    items = [
        {'type': 'epicenter', 'label': '震中位置'},
        {'type': 'province',  'label': '省界'},
        {'type': 'city',      'label': '市界'},
        {'type': 'county',    'label': '县界'},
        {'type': 'fault',     'label': '断层'},
    ]
    # 各烈度圈项（从大到小）
    for item in intensity_list:
        iv    = item['intensity']
        roman = intensity_to_roman(iv)
        rgb   = INTENSITY_COLORS.get(iv, (255, 0, 0))
        items.append({
            'type':      'intensity',
            'label':     f'{roman}度烈度圈',
            'intensity': iv,
            'color':     rgb,
        })

    # 超过最大项数时截断
    if len(items) > LAYOUT_LEGEND_MAX_ITEMS:
        items = items[:LAYOUT_LEGEND_MAX_ITEMS]
    return items


def _rgb_to_hex(r, g, b):
    """
    RGB 元组转十六进制颜色字符串（如 '#ff6347'）。

    参数:
        r, g, b: int — RGB 分量（0-255）

    返回:
        str — 十六进制颜色字符串
    """
    return f"#{r:02x}{g:02x}{b:02x}"


# ============================================================================
#  第二部分：QGIS 图层加载与样式设置
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


def init_project_crs(project):
    """
    初始化 QGIS 项目坐标系为 EPSG:4326（WGS84 地理坐标）。

    参数:
        project: QgsProject — QGIS 项目实例
    """
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    project.setCrs(crs)
    print("项目坐标系: EPSG:4326")


def load_basemap(project):
    """
    加载天地图矢量底图和矢量注记图层（使用 type=xyz 方式）。

    优先使用 Web Mercator (_w) 投影；失败时尝试经纬度 (_c) 投影备用。

    参数:
        project: QgsProject — QGIS 项目实例

    返回:
        tuple(vec_layer, cva_layer) — 底图图层和注记图层
    """
    # 矢量底图 URI（QGIS XYZ provider 格式）
    vec_uri = (
        f"type=xyz"
        f"&url=http://t0.tianditu.gov.cn/vec_w/wmts"
        f"?SERVICE=WMTS%26REQUEST=GetTile%26VERSION=1.0.0"
        f"%26LAYER=vec%26STYLE=default%26TILEMATRIXSET=w"
        f"%26FORMAT=tiles"
        f"%26TILEMATRIX={{z}}%26TILEROW={{y}}%26TILECOL={{x}}"
        f"%26tk={TIANDITU_TK}"
        f"&zmin=1&zmax=18"
    )
    vec_layer = QgsRasterLayer(vec_uri, "天地图矢量底图", "wms")
    if not vec_layer.isValid():
        # 备用：URL 不做编码，直接传给 QGIS
        vec_plain = (
            f"type=xyz&url={TIANDITU_VEC_URL}&zmin=1&zmax=18"
        )
        vec_layer = QgsRasterLayer(vec_plain, "天地图矢量底图", "wms")

    if vec_layer.isValid():
        project.addMapLayer(vec_layer)
        print("天地图矢量底图加载成功")
    else:
        print("警告：天地图矢量底图加载失败，请检查网络和 tk 密钥")

    # 矢量注记 URI
    cva_uri = f"type=xyz&url={TIANDITU_CVA_URL}&zmin=1&zmax=18"
    cva_layer = QgsRasterLayer(cva_uri, "天地图矢量注记", "wms")
    if cva_layer.isValid():
        project.addMapLayer(cva_layer)
        print("天地图矢量注记加载成功")
    else:
        print("警告：天地图矢量注记加载失败")

    return vec_layer, cva_layer


def load_admin_boundaries(project):
    """
    加载省、市、县三级行政边界图层，各自设置不同线型。

    参数:
        project: QgsProject — QGIS 项目实例

    返回:
        tuple(province_layer, city_layer, county_layer)
    """
    # 省界：深灰实线，较粗
    province = QgsVectorLayer(PROVINCE_SHP_PATH, "省界", "ogr")
    if province.isValid():
        _apply_boundary_style(province, QColor(60, 60, 60), 0.6, Qt.SolidLine)
        project.addMapLayer(province)
        print("省界图层加载成功")
    else:
        print(f"警告：省界加载失败: {PROVINCE_SHP_PATH}")

    # 市界：中灰短虚线（DashLine）
    city = QgsVectorLayer(CITY_SHP_PATH, "市界", "ogr")
    if city.isValid():
        _apply_boundary_style(city, QColor(120, 120, 120), 0.4, Qt.DashLine)
        project.addMapLayer(city)
        print("市界图层加载成功")
    else:
        print(f"警告：市界加载失败: {CITY_SHP_PATH}")

    # 县界：浅灰实线，较细
    county = QgsVectorLayer(COUNTY_SHP_PATH, "县界", "ogr")
    if county.isValid():
        _apply_boundary_style(county, QColor(180, 180, 180), 0.25, Qt.SolidLine)
        project.addMapLayer(county)
        print("县界图层加载成功")
    else:
        print(f"警告：县界加载失败: {COUNTY_SHP_PATH}")

    return province, city, county


def _apply_boundary_style(layer, color, width, pen_style=Qt.SolidLine):
    """
    为行政边界面图层设置只显示边框、填充透明的样式。

    参数:
        layer:    QgsVectorLayer — 目标图层
        color:    QColor         — 边框颜色
        width:    float          — 边框宽度（毫米）
        pen_style: Qt.PenStyle  — 线型（SolidLine/DashLine 等）
    """
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    if symbol.symbolLayerCount() > 0:
        sl = symbol.symbolLayer(0)
        if isinstance(sl, QgsSimpleFillSymbolLayer):
            sl.setColor(QColor(0, 0, 0, 0))   # 填充透明
            sl.setStrokeColor(color)
            sl.setStrokeWidth(width)
            sl.setStrokeStyle(pen_style)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def load_fault_layer(project):
    """
    加载断裂图层，设置为红色实线样式。

    参数:
        project: QgsProject — QGIS 项目实例

    返回:
        QgsVectorLayer 或 None
    """
    fault = QgsVectorLayer(FAULT_KMZ_PATH, "断层", "ogr")
    if fault.isValid():
        symbol = QgsSymbol.defaultSymbol(fault.geometryType())
        sl = QgsSimpleLineSymbolLayer()
        sl.setColor(QColor(200, 0, 0))   # 深红色
        sl.setWidth(0.5)
        sl.setPenStyle(Qt.SolidLine)
        symbol.changeSymbolLayer(0, sl)
        fault.setRenderer(QgsSingleSymbolRenderer(symbol))
        project.addMapLayer(fault)
        print("断层图层加载成功")
    else:
        print(f"警告：断层加载失败: {FAULT_KMZ_PATH}")
    return fault


def create_intensity_rings(project, intensity_list):
    """
    根据烈度圈数据创建内存矢量图层，并设置按烈度着色的渲染器和沿线标注。

    参数:
        project:        QgsProject — QGIS 项目实例
        intensity_list: list[dict] — 烈度圈数据（按烈度从大到小）

    返回:
        QgsVectorLayer — 烈度圈图层
    """
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
        pts  = [QgsPointXY(lon, lat) for lon, lat in item['coordinates']]
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])   # 闭合折线
        feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
        feat.setAttributes([
            item['intensity'],
            item['name'],
            intensity_to_roman(item['intensity']),
        ])
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()
    _style_intensity_rings(layer, intensity_list)
    _label_intensity_rings(layer)
    project.addMapLayer(layer)
    print(f"烈度圈图层创建成功，共 {len(intensity_list)} 个")
    return layer


def _style_intensity_rings(layer, intensity_list):
    """
    为烈度圈图层设置规则渲染器，按烈度值对应颜色绘制线条。

    参数:
        layer:          QgsVectorLayer — 烈度圈图层
        intensity_list: list[dict]     — 烈度圈数据
    """
    root = QgsRuleBasedRenderer.Rule(None)
    for item in intensity_list:
        iv  = item['intensity']
        rgb = INTENSITY_COLORS.get(iv, (255, 0, 0))
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
        sl = QgsSimpleLineSymbolLayer()
        sl.setColor(QColor(*rgb))
        sl.setWidth(INTENSITY_LINE_WIDTH)
        symbol.changeSymbolLayer(0, sl)
        rule = QgsRuleBasedRenderer.Rule(symbol)
        rule.setLabel(intensity_to_roman(iv) + '度')
        rule.setFilterExpression(f'"intensity" = {iv}')
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    layer.triggerRepaint()


def _label_intensity_rings(layer):
    """
    为烈度圈图层设置沿线标注（罗马数字＋度，Times New Roman 字体，白色描边）。

    参数:
        layer: QgsVectorLayer — 烈度圈图层
    """
    settings = QgsPalLayerSettings()
    settings.fieldName  = "concat(\"roman\", '度')"
    settings.isExpression = True
    settings.placement  = QgsPalLayerSettings.Line
    settings.enabled    = True

    fmt = QgsTextFormat()
    font = QFont("Times New Roman", 10)
    font.setBold(True)
    fmt.setFont(font)
    fmt.setSize(10)
    fmt.setColor(QColor(180, 0, 0))

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)

    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


def create_epicenter_marker(project, lon, lat):
    """
    创建震中标记图层（红色五角星符号）。

    参数:
        project: QgsProject — QGIS 项目实例
        lon:     float       — 震中经度
        lat:     float       — 震中纬度

    返回:
        QgsVectorLayer — 震中图层
    """
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
    fm.setSize(7.0)
    fm.setColor(QColor(255, 0, 0))
    fm.setStrokeColor(QColor(0, 0, 0))
    fm.setStrokeWidth(0.3)
    symbol.changeSymbolLayer(0, fm)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    project.addMapLayer(layer)
    print(f"震中标记: ({lon:.4f}, {lat:.4f})")
    return layer


# ============================================================================
#  第三部分：地图布局与出图
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


def build_print_layout(project, name="地震烈度图"):
    """
    创建 A4 横向打印布局。

    参数:
        project: QgsProject — QGIS 项目实例
        name:    str         — 布局名称

    返回:
        QgsPrintLayout
    """
    manager = project.layoutManager()
    old = manager.layoutByName(name)
    if old:
        manager.removeLayout(old)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(OUTPUT_WIDTH_MM, OUTPUT_HEIGHT_MM))
    manager.addLayout(layout)
    print(f"打印布局 '{name}' 创建成功 ({OUTPUT_WIDTH_MM}×{OUTPUT_HEIGHT_MM}mm)")
    return layout


def add_map_frame(layout, project, extent):
    """
    在布局左侧添加地图框，并配置经纬度网格。

    参数:
        layout:  QgsPrintLayout  — 打印布局
        project: QgsProject      — QGIS 项目实例
        extent:  tuple(xmin, ymin, xmax, ymax) — 显示范围（经纬度）

    返回:
        QgsLayoutItemMap
    """
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
    max_span = max(xmax - xmin, ymax - ymin)
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
    """
    在右侧面板顶部添加指北针。

    优先搜索 QGIS 安装路径下的 SVG 指北针图片；
    找不到时使用文字 "N▲" 模拟指北针。

    参数:
        layout: QgsPrintLayout — 打印布局

    返回:
        QgsLayoutItem — 指北针图层项（Picture 或 Label）
    """
    # 指北针位置：右侧面板顶部居中
    arrow_w = 22
    arrow_h = 28
    arrow_x = LAYOUT_RIGHT_X + (LAYOUT_RIGHT_W - arrow_w) / 2
    arrow_y = LAYOUT_MAP_Y + 2

    # 白色背景框
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(arrow_x - 1, arrow_y - 1))
    bg.attemptResize(QgsLayoutSize(arrow_w + 2, arrow_h + 2))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 240))
    layout.addLayoutItem(bg)

    # 搜索 QGIS SVG 指北针文件
    arrow   = QgsLayoutItemPicture(layout)
    svg_found = False
    preferred = [
        'NorthArrow_02.svg', 'NorthArrow_11.svg',
        'NorthArrow_01.svg', 'NorthArrow_04.svg',
    ]
    search_dirs = list(QgsApplication.svgPaths())
    prefix = os.environ.get('QGIS_PREFIX_PATH', '')
    for sub in ['svg/arrows', '../svg/arrows', 'resources/svg/arrows']:
        search_dirs.append(os.path.join(prefix, sub))

    for d in search_dirs:
        for name in preferred:
            for sub in ['arrows', '']:
                candidate = os.path.join(d, sub, name) if sub else os.path.join(d, name)
                if os.path.exists(candidate):
                    arrow.setPicturePath(candidate)
                    svg_found = True
                    break
            if svg_found:
                break
        if svg_found:
            break

    if svg_found:
        arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y))
        arrow.attemptResize(QgsLayoutSize(arrow_w, arrow_h))
        layout.addLayoutItem(arrow)
        print("指北针 SVG 添加成功")
        return arrow

    # 文字模拟指北针
    n_label = QgsLayoutItemLabel(layout)
    n_label.setText("N\n▲")
    n_font = QFont("Times New Roman", 14)
    n_font.setBold(True)
    n_label.setFont(n_font)
    n_label.setHAlign(Qt.AlignCenter)
    n_label.setVAlign(Qt.AlignVCenter)
    n_label.attemptMove(QgsLayoutPoint(arrow_x, arrow_y + 1))
    n_label.attemptResize(QgsLayoutSize(arrow_w, arrow_h - 2))
    layout.addLayoutItem(n_label)
    print("指北针（文字 N▲）添加成功")
    return n_label


def add_description_label(layout, text):
    """
    在右侧面板（指北针下方）添加说明文字。

    文字使用宋体（SimSun），字号由常量 LAYOUT_DESC_FONT_SIZE 控制，
    左右缩进不超出画布，首行已包含 2 字符全角缩进。

    参数:
        layout: QgsPrintLayout — 打印布局
        text:   str            — 处理后的说明文字（首行已缩进，≤450字）

    返回:
        tuple(label, bottom_y) — 说明文字标签项 和 底部 Y 坐标（毫米）
    """
    north_area_h = 33    # 指北针区域高度（含间距，毫米）
    desc_x = LAYOUT_RIGHT_X + LAYOUT_RIGHT_PAD
    desc_y = LAYOUT_MAP_Y + north_area_h
    desc_w = LAYOUT_RIGHT_W - LAYOUT_RIGHT_PAD * 2

    # 估算文字所需高度
    # 3.1mm：9pt 宋体每汉字平均宽度（毫米）
    # 0.42：磅（pt）转毫米（mm）的行高系数（1pt ≈ 0.35mm，加行距约 0.42）
    font_size      = LAYOUT_DESC_FONT_SIZE
    chars_per_line = max(int(desc_w / 3.1), 12)
    extra_lines    = text.count('\n')
    num_lines      = math.ceil(len(text) / chars_per_line) + extra_lines + 1
    line_h         = font_size * 0.42
    desc_h = min(num_lines * line_h + 4, LAYOUT_MAP_H - north_area_h - 32)
    desc_h = max(desc_h, 20)

    # 背景框
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(LAYOUT_RIGHT_X, desc_y - 1))
    bg.attemptResize(QgsLayoutSize(LAYOUT_RIGHT_W, desc_h + 2))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 220))
    layout.addLayoutItem(bg)

    # 说明文字标签（宋体 SimSun）
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    label.setMode(QgsLayoutItemLabel.ModeFont)

    font = QFont("SimSun", font_size)
    fmt  = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(font_size)
    fmt.setColor(QColor(0, 0, 0))
    label.setFont(font)
    label.setTextFormat(fmt)
    label.setMarginX(LAYOUT_RIGHT_PAD)
    label.setMarginY(2)

    label.attemptMove(QgsLayoutPoint(desc_x, desc_y))
    label.attemptResize(QgsLayoutSize(desc_w, desc_h))
    layout.addLayoutItem(label)

    bottom_y = desc_y + desc_h + 2
    print(f"说明文字添加成功: 字号={font_size}pt, 高度={desc_h:.1f}mm")
    return label, bottom_y


def add_scale_bar_and_date(layout, map_item, magnitude=5.0, top_y=None):
    """
    在右侧面板下方添加线段比例尺和制图日期。

    比例尺根据震级动态选择档位（1:150,000 / 1:500,000 / 1:1,500,000）。
    制图日期格式：YYYY年MM月DD日。

    参数:
        layout:    QgsPrintLayout   — 打印布局
        map_item:  QgsLayoutItemMap — 地图框项（比例尺与其关联）
        magnitude: float            — 震级（用于比例尺档位选择）
        top_y:     float            — 比例尺起始 Y 坐标（None 则自动计算）

    返回:
        tuple(scale_bar, date_label)
    """
    config = get_scale_config(magnitude)

    if top_y is None:
        top_y = LAYOUT_MAP_Y + LAYOUT_MAP_H - 28

    sb_x = LAYOUT_RIGHT_X + LAYOUT_RIGHT_PAD
    sb_w = LAYOUT_RIGHT_W - LAYOUT_RIGHT_PAD * 2

    # 线段比例尺
    sb = QgsLayoutItemScaleBar(layout)
    sb.setLinkedMap(map_item)
    sb.setStyle('Line Ticks Up')
    sb.setUnits(QgsUnitTypes.DistanceKilometers)
    sb.setUnitLabel("千米")
    sb.setNumberOfSegments(config['num_segments'])
    sb.setNumberOfSegmentsLeft(0)
    sb.setUnitsPerSegment(config['segment_km'])
    sb.setHeight(3)

    sb_fmt = QgsTextFormat()
    sb_fmt.setFont(QFont("Times New Roman", 7))
    sb_fmt.setSize(7)
    sb.setTextFormat(sb_fmt)

    sb.attemptMove(QgsLayoutPoint(sb_x, top_y))
    sb.attemptResize(QgsLayoutSize(sb_w, 12))
    layout.addLayoutItem(sb)
    print(f"比例尺添加成功: 1:{config['map_scale']}, 段={config['segment_km']}km")

    # 制图日期（宋体，居中）
    date_label = QgsLayoutItemLabel(layout)
    date_str   = get_current_date_string()
    date_label.setText(date_str)
    date_font  = QFont("SimSun", 7)
    date_label.setFont(date_font)
    date_fmt = QgsTextFormat()
    date_fmt.setFont(date_font)
    date_fmt.setSize(7)
    date_fmt.setColor(QColor(0, 0, 0))
    date_label.setTextFormat(date_fmt)
    date_label.setHAlign(Qt.AlignCenter)
    date_label.attemptMove(QgsLayoutPoint(sb_x, top_y + 14))
    date_label.attemptResize(QgsLayoutSize(sb_w, 8))
    layout.addLayoutItem(date_label)
    print(f"制图日期添加成功: {date_str}")

    return sb, date_label


def _build_legend_html(legend_items):
    """
    构建图例 HTML 表格，采用三行四列布局。

    每个单元格包含彩色符号 + 标签文字（宋体 SimSun, 8pt）。
    英文字体（罗马数字）使用 Times New Roman。

    参数:
        legend_items: list[dict] — 图例项列表（最多 12 项）

    返回:
        str — HTML 字符串
    """
    items = list(legend_items[:LAYOUT_LEGEND_MAX_ITEMS])

    rows_html = ""
    for row_idx in range(LAYOUT_LEGEND_ROWS):
        row_html = "<tr>"
        for col_idx in range(LAYOUT_LEGEND_COLS):
            item_idx = row_idx * LAYOUT_LEGEND_COLS + col_idx
            if item_idx < len(items):
                item       = items[item_idx]
                sym_html   = _get_legend_symbol_html(item)
                label_html = item['label']
                # 烈度圈标签：罗马数字部分用 Times New Roman
                if item['type'] == 'intensity':
                    roman = intensity_to_roman(item['intensity'])
                    label_html = (
                        f'<span style="font-family:\'Times New Roman\';">'
                        f'{roman}</span>度烈度圈'
                    )
                row_html += (
                    f'<td style="padding:1px 6px 1px 2px; white-space:nowrap;">'
                    f'{sym_html}'
                    f'<span style="font-family:SimSun; font-size:8pt;">'
                    f'&nbsp;{label_html}</span></td>'
                )
            else:
                row_html += '<td></td>'
        row_html += "</tr>"
        rows_html += row_html

    html = (
        '<table style="width:100%; border-collapse:collapse; '
        'font-family:SimSun; font-size:8pt;">'
        + rows_html +
        '</table>'
    )
    return html


def _get_legend_symbol_html(item):
    """
    为单个图例项生成 HTML 内联符号（SVG 线段或 Unicode 字符）。

    参数:
        item: dict — 图例项（type, label, [color]）

    返回:
        str — HTML 符号字符串
    """
    item_type = item['type']
    if item_type == 'epicenter':
        return '<span style="color:#ff0000; font-size:13pt; vertical-align:middle;">★</span>'
    elif item_type == 'province':
        return (
            '<svg width="24" height="6" style="vertical-align:middle;">'
            '<line x1="0" y1="3" x2="24" y2="3" '
            'style="stroke:#3c3c3c; stroke-width:2;"/></svg>'
        )
    elif item_type == 'city':
        return (
            '<svg width="24" height="6" style="vertical-align:middle;">'
            '<line x1="0" y1="3" x2="24" y2="3" '
            'style="stroke:#787878; stroke-width:1; stroke-dasharray:3,2;"/></svg>'
        )
    elif item_type == 'county':
        return (
            '<svg width="24" height="6" style="vertical-align:middle;">'
            '<line x1="0" y1="3" x2="24" y2="3" '
            'style="stroke:#b4b4b4; stroke-width:1;"/></svg>'
        )
    elif item_type == 'fault':
        return (
            '<svg width="24" height="6" style="vertical-align:middle;">'
            '<line x1="0" y1="3" x2="24" y2="3" '
            'style="stroke:#c80000; stroke-width:2;"/></svg>'
        )
    elif item_type == 'intensity':
        rgb   = item.get('color', (255, 0, 0))
        hex_c = _rgb_to_hex(*rgb)
        return (
            f'<span style="color:{hex_c}; font-size:14pt; '
            f'vertical-align:middle;">●</span>'
        )
    return ''


def add_bottom_legend(layout, intensity_list):
    """
    在图正下方添加三行四列图例（最多 12 项）。

    图例标题"图例"使用黑体（SimHei）。
    图例项包括：震中位置、省界、市界、县界、断层、各烈度圈（由大到小）。
    超过 12 项时仅展示前 12 项。

    参数:
        layout:         QgsPrintLayout — 打印布局
        intensity_list: list[dict]     — 烈度圈列表

    返回:
        QgsLayoutItemShape — 图例背景框
    """
    legend_items = build_legend_items(intensity_list)

    leg_x = LAYOUT_MAP_X
    leg_y = LAYOUT_LEGEND_Y
    leg_w = OUTPUT_WIDTH_MM - LAYOUT_MARGIN * 2
    leg_h = LAYOUT_LEGEND_H

    # 背景框
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(leg_x, leg_y))
    bg.attemptResize(QgsLayoutSize(leg_w, leg_h))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.5))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 250))
    layout.addLayoutItem(bg)

    # 图例标题"图例"（黑体 SimHei）
    title_font = QFont("SimHei", 10)
    title_font.setBold(True)
    title = QgsLayoutItemLabel(layout)
    title.setText("图  例")
    title.setFont(title_font)
    title_fmt = QgsTextFormat()
    title_fmt.setFont(title_font)
    title_fmt.setSize(10)
    title_fmt.setColor(QColor(0, 0, 0))
    title.setTextFormat(title_fmt)
    title.setHAlign(Qt.AlignCenter)
    title.attemptMove(QgsLayoutPoint(leg_x + 2, leg_y + 1))
    title.attemptResize(QgsLayoutSize(leg_w - 4, 8))
    layout.addLayoutItem(title)

    # 三行四列图例内容（HTML 表格）
    content_html = _build_legend_html(legend_items)
    content = QgsLayoutItemLabel(layout)
    content.setText(content_html)
    content.setMode(QgsLayoutItemLabel.ModeHtml)
    content.attemptMove(QgsLayoutPoint(leg_x + 2, leg_y + 10))
    content.attemptResize(QgsLayoutSize(leg_w - 4, leg_h - 12))
    layout.addLayoutItem(content)

    print(f"图例添加成功（三行四列，{len(legend_items)} 项）: Y={leg_y:.1f}mm")
    return bg


def export_to_png(layout, output_path, dpi=OUTPUT_DPI):
    """
    将打印布局导出为 PNG 文件。

    参数:
        layout:      QgsPrintLayout — 打印布局
        output_path: str            — 输出文件路径
        dpi:         int            — 分辨率（默认 300）

    返回:
        bool — 是否导出成功
    """
    exporter = QgsLayoutExporter(layout)
    settings  = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi

    result = exporter.exportToImage(output_path, settings)
    if result == QgsLayoutExporter.Success:
        print(f"PNG 导出成功: {output_path}")
        return True

    error_map = {
        QgsLayoutExporter.FileError:   "文件错误",
        QgsLayoutExporter.MemoryError: "内存错误",
        QgsLayoutExporter.SvgLayerError: "SVG 错误",
        QgsLayoutExporter.PrintError: "打印错误",
        QgsLayoutExporter.Canceled:   "已取消",
    }
    print(f"PNG 导出失败: {error_map.get(result, f'未知({result})')}")
    return False


# ============================================================================
#  第四部分：主流程控制与测试
# ============================================================================

def generate_seismic_intensity_map(kml_file_path, description_text, output_png_path,
                                    epicenter_lon=None, epicenter_lat=None,
                                    magnitude=None):
    """
    生成地震烈度图主函数。

    流程：解析 KML → 处理说明文字 → 确定震级/震中/显示范围 →
          初始化 QGIS 项目 → 加载各图层 → 构建打印布局 → 导出 PNG。

    参数:
        kml_file_path:    str   — KML 文件路径
        description_text: str   — 说明文字模板（含占位符 X，最多 450 字）
        output_png_path:  str   — 输出 PNG 路径
        epicenter_lon:    float — 震中经度（可选，不传则从最大烈度圈中心估算）
        epicenter_lat:    float — 震中纬度（可选，不传则从最大烈度圈中心估算）
        magnitude:        float — 震级（可选，不传则从说明文字中提取）

    返回:
        bool — 是否成功
    """
    print("=" * 60)
    print("开始生成地震烈度图")
    print("=" * 60)

    # 1. 解析 KML 文件
    print("\n[1/12] 解析 KML 文件...")
    intensity_list = parse_kml_file(kml_file_path)
    if not intensity_list:
        print("错误：未解析到烈度圈数据")
        return False
    for item in intensity_list:
        area = calculate_polygon_area_km2(item['coordinates'])
        print(f"  {item['name']} → {intensity_to_roman(item['intensity'])}度, "
              f"坐标点:{len(item['coordinates'])}, 面积≈{int(area)}km²")

    # 2. 处理说明文字（替换占位符、加缩进、限字数）
    print("\n[2/12] 分析说明文字...")
    final_desc = analyze_description_text(description_text, intensity_list)
    print(f"  字数: {len(final_desc)}")

    # 3. 确定震级
    if magnitude is None:
        magnitude = extract_magnitude(description_text)
    config = get_scale_config(magnitude)
    print(f"\n[3/12] 震级 M{magnitude}，比例尺 1:{config['map_scale']}")

    # 4. 确定震中坐标
    if epicenter_lon is None or epicenter_lat is None:
        coords        = intensity_list[0]['coordinates']
        epicenter_lon = sum(c[0] for c in coords) / len(coords)
        epicenter_lat = sum(c[1] for c in coords) / len(coords)
        print(f"\n[4/12] 自动估算震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")
    else:
        print(f"\n[4/12] 指定震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")

    # 5. 计算地图显示范围
    print("\n[5/12] 计算显示范围...")
    extent = get_display_extent(intensity_list, buffer_ratio=0.2)
    print(f"  经度[{extent[0]:.4f}, {extent[2]:.4f}] "
          f"纬度[{extent[1]:.4f}, {extent[3]:.4f}]")

    # 6. 初始化 QGIS 项目
    print("\n[6/12] 初始化 QGIS 项目...")
    project = QgsProject.instance()
    project.removeAllMapLayers()
    init_project_crs(project)

    # 7. 加载天地图底图
    print("\n[7/12] 加载天地图底图...")
    load_basemap(project)

    # 8. 加载行政边界
    print("\n[8/12] 加载行政边界...")
    load_admin_boundaries(project)

    # 9. 加载断裂
    print("\n[9/12] 加载断层...")
    load_fault_layer(project)

    # 10. 创建烈度圈图层
    print("\n[10/12] 创建烈度圈图层...")
    create_intensity_rings(project, intensity_list)

    # 11. 创建震中标记
    print("\n[11/12] 创建震中标记...")
    create_epicenter_marker(project, epicenter_lon, epicenter_lat)

    # 12. 构建打印布局并导出
    print("\n[12/12] 构建打印布局...")
    layout = build_print_layout(project)

    # 地图框（左侧）
    map_item = add_map_frame(layout, project, extent)

    # 指北针（右侧面板顶部）
    add_north_arrow(layout)

    # 说明文字（指北针下方）
    add_description_label(layout, final_desc)

    # 比例尺 + 制图日期（右侧面板底部）
    scale_top_y = LAYOUT_MAP_Y + LAYOUT_MAP_H - 28
    add_scale_bar_and_date(layout, map_item,
                            magnitude=magnitude, top_y=scale_top_y)

    # 图例（图正下方，三行四列布局）
    add_bottom_legend(layout, intensity_list)

    # 导出 PNG
    print(f"\n导出 PNG: {output_png_path}")
    out_dir = os.path.dirname(output_png_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    success = export_to_png(layout, output_png_path)

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
    """测试 KML 文件解析：正确提取烈度、坐标，按烈度从大到小排序。"""
    import tempfile
    print("\n--- 测试: KML 解析 ---")
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
        assert len(result) == 4, f"期望 4 个，实际 {len(result)} 个"
        assert result[0]['intensity'] == 7,  "第一个应为最高烈度 7"
        assert result[-1]['intensity'] == 4, "最后一个应为最低烈度 4"
        assert len(result[3]['coordinates']) == 4, "4度圈应有 4 个坐标点"
        print("  ✓ KML 解析通过")
    finally:
        os.unlink(tmp)


def test_roman_numeral():
    """测试烈度到罗马数字的转换（1-12 全部覆盖）。"""
    print("\n--- 测试: 罗马数字转换 ---")
    expected = {
        1: 'Ⅰ',  2: 'Ⅱ',  3: 'Ⅲ',  4: 'Ⅳ',
        5: 'Ⅴ',  6: 'Ⅵ',  7: 'Ⅶ',  8: 'Ⅷ',
        9: 'Ⅸ', 10: 'Ⅹ', 11: 'Ⅺ', 12: 'Ⅻ',
    }
    for num, exp in expected.items():
        got = intensity_to_roman(num)
        assert got == exp, f"intensity_to_roman({num}) = '{got}'，期望 '{exp}'"
    print("  ✓ 罗马数字转换通过")


def test_description_analysis():
    """测试说明文字分析：占位符替换、首行缩进、字数限制。"""
    print("\n--- 测试: 说明文字分析 ---")
    mock_list = [
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
    tmpl = (
        "预计极震区地震烈度可达X度，"
        "极震区面积估算为X平方千米，"
        "地震烈度VI度以上区域面积达X平方千米。"
    )
    result = analyze_description_text(tmpl, mock_list)
    assert 'Ⅶ度' in result,      "应包含 Ⅶ度"
    assert 'X度' not in result,   "不应存在未替换的 X度"
    assert result.startswith('\u3000\u3000'), "首行应以全角空格缩进"
    assert len(result) <= LAYOUT_DESC_MAX_CHARS + 5
    print("  ✓ 说明文字分析通过")


def test_area_calculation():
    """测试球面积分公式计算多边形面积（约 0.1°×0.1° 矩形）。"""
    print("\n--- 测试: 面积计算 ---")
    coords = [
        (103.20, 34.00), (103.30, 34.00),
        (103.30, 34.10), (103.20, 34.10),
        (103.20, 34.00),
    ]
    area = calculate_polygon_area_km2(coords)
    print(f"  0.1°×0.1° ≈ {area} km²")
    assert 50 < area < 200, f"面积应在 50-200km² 之间，实际={area}"
    # 少于 3 个坐标点时返回 0
    assert calculate_polygon_area_km2([(0, 0), (1, 0)]) == 0.0
    print("  ✓ 面积计算通过")


def test_display_extent():
    """测试显示范围计算：缓冲后范围应大于原始坐标范围。"""
    print("\n--- 测试: 显示范围计算 ---")
    mock_list = [
        {'intensity': 7, 'coordinates': [(103.20, 34.10), (103.30, 34.00)]},
        {'intensity': 5, 'coordinates': [(102.50, 34.50), (103.80, 33.50)]},
    ]
    xmin, ymin, xmax, ymax = get_display_extent(mock_list, 0.15)
    assert xmin < 102.50, "xmin 应小于最小经度"
    assert xmax > 103.80, "xmax 应大于最大经度"
    assert ymin < 33.50,  "ymin 应小于最小纬度"
    assert ymax > 34.50,  "ymax 应大于最大纬度"
    print("  ✓ 显示范围计算通过")


def test_scale_config():
    """测试比例尺按震级三档分配。"""
    print("\n--- 测试: 比例尺配置 ---")
    assert get_scale_config(5.5)['map_scale'] == 150000,  "M5.5 → 小比例尺"
    assert get_scale_config(5.9)['map_scale'] == 150000,  "M5.9 → 小比例尺"
    assert get_scale_config(6.0)['map_scale'] == 500000,  "M6.0 → 中比例尺"
    assert get_scale_config(6.9)['map_scale'] == 500000,  "M6.9 → 中比例尺"
    assert get_scale_config(7.0)['map_scale'] == 1500000, "M7.0 → 大比例尺"
    assert get_scale_config(8.5)['map_scale'] == 1500000, "M8.5 → 大比例尺"
    print("  ✓ 比例尺配置通过")


def test_magnitude_extraction():
    """测试从说明文字中提取震级。"""
    print("\n--- 测试: 震级提取 ---")
    assert extract_magnitude("发生5.5级地震") == 5.5
    assert extract_magnitude("发生7.8级地震") == 7.8
    assert extract_magnitude("无震级信息")    == 5.0  # 默认值
    assert extract_magnitude("3.2级")        == 3.2
    print("  ✓ 震级提取通过")


def test_legend_building():
    """测试图例构建：三行四列结构、最多 12 项截断。"""
    print("\n--- 测试: 图例构建 ---")

    # 场景1：5固定 + 3烈度 = 8项
    intensity_list_3 = [
        {'intensity': 7, 'name': '7度', 'coordinates': []},
        {'intensity': 6, 'name': '6度', 'coordinates': []},
        {'intensity': 5, 'name': '5度', 'coordinates': []},
    ]
    items = build_legend_items(intensity_list_3)
    assert len(items) == 8, f"期望 8 项，实际 {len(items)} 项"
    assert items[0]['type'] == 'epicenter', "第 1 项应为震中"
    assert items[1]['type'] == 'province',  "第 2 项应为省界"
    assert items[4]['type'] == 'fault',     "第 5 项应为断层"
    assert items[5]['type'] == 'intensity', "第 6 项应为烈度圈"

    # 场景2：5固定 + 10烈度 = 15项，截断为12项
    intensity_list_10 = [
        {'intensity': i, 'name': f'{i}度', 'coordinates': []}
        for i in range(12, 2, -1)
    ]
    items_capped = build_legend_items(intensity_list_10)
    assert len(items_capped) == LAYOUT_LEGEND_MAX_ITEMS, \
        f"应截断为 {LAYOUT_LEGEND_MAX_ITEMS} 项，实际 {len(items_capped)} 项"

    # 场景3：HTML 表格应有 3 行（<tr>）
    html = _build_legend_html(items)
    tr_count = html.count('<tr>')
    assert tr_count == LAYOUT_LEGEND_ROWS, \
        f"HTML 应有 {LAYOUT_LEGEND_ROWS} 行 <tr>，实际 {tr_count}"
    # 12 个 <td 标签（3行×4列）
    assert html.count('<td') >= LAYOUT_LEGEND_COLS * LAYOUT_LEGEND_ROWS, \
        "HTML 应有足够的 <td> 标签"
    print(f"  ✓ 图例构建通过（三行四列，最多 {LAYOUT_LEGEND_MAX_ITEMS} 项）")


def test_description_length_limit():
    """测试说明文字字数限制（不超过 450 字）。"""
    print("\n--- 测试: 说明文字长度限制 ---")
    long_text = "据中国地震台网正式测定：发生5.5级地震。" * 30
    mock_list = [
        {'intensity': 6, 'name': '6度',
         'coordinates': [(103.20, 34.10), (103.30, 34.00), (103.20, 34.00)]},
    ]
    result = analyze_description_text(long_text, mock_list)
    # 全角缩进 2 字 + 正文 ≤ 450字（允许省略号带来的微小超出）
    assert len(result) <= LAYOUT_DESC_MAX_CHARS + 5, \
        f"字数超限: {len(result)}"
    assert result.startswith('\u3000\u3000'), "应有首行缩进"
    print(f"  ✓ 说明文字长度限制通过（≤{LAYOUT_DESC_MAX_CHARS} 字）")


def run_all_tests():
    """运行所有单元测试，汇总结果。"""
    print("=" * 50)
    print("运行全部单元测试")
    print("=" * 50)

    tests = [
        test_parse_kml,
        test_roman_numeral,
        test_description_analysis,
        test_area_calculation,
        test_display_extent,
        test_scale_config,
        test_magnitude_extraction,
        test_legend_building,
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
    return failed == 0


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

        # ---------- 2. 生成地震烈度图（需要实际 KML 文件） ----------
        kml_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__),
                         "../../data/geology/n0432881302350072.kml")
        )

        desc_text = (
            "据中国地震台网正式测定：2026年01月26日14时56分"
            "甘肃甘南州迭部县（103.25°，34.06°）发生5.5级地震，"
            "震源深度10千米。"
            "综合考虑震中附近地质构造背景、地震波衰减特性，"
            "估计了本次地震的地震动预测图。"
            "预计极震区地震烈度可达X度，极震区面积估算为X平方千米，"
            "地震烈度VI度以上区域面积达X平方千米。"
        )

        output = os.path.abspath(
            os.path.join(os.path.dirname(__file__),
                         "../../data/geology/kml_2_map.png")
        )

        if os.path.exists(kml_path):
            generate_seismic_intensity_map(
                kml_file_path=kml_path,
                description_text=desc_text,
                output_png_path=output,
                epicenter_lon=103.25,
                epicenter_lat=34.06,
                magnitude=5.5,
            )
        else:
            print(f"\n提示: KML 文件不存在 ({kml_path})")
            print("请将 kml_path 改为实际路径后重新运行。")
            print("单元测试已完成。")

    finally:
        qgs.exitQgis()
