# -*- coding: utf-8 -*-
"""
地震烈度图生成工具
基于QGIS 3.40.15，读取KML烈度圈数据，叠加天地图底图、省市县边界、断裂，
添加指北针、图例、说明文字、比例尺，输出PNG地图。

使用方式：在PyCharm中配置QGIS Python环境后直接运行本文件
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

# 天地图密钥
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

# 天地图矢量底图URL（经纬度投影 c）
TIANDITU_VEC_URL = (
    "http://t{s}.tianditu.gov.cn/vec_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 天地图矢量注记URL（经纬度投影 c）
TIANDITU_CVA_URL = (
    "http://t{s}.tianditu.gov.cn/cva_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# -------------------- 数据文件路径（常量） --------------------

# 省界shp文件路径
PROVINCE_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国省份行政区划数据/省级行政区划/省.shp")
)

# 市界shp文件路径
CITY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国市级行政区划数据/市级行政区划/市.shp")
)

# 县界shp文件路径
COUNTY_SHP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/"
                 "全国县级行政区划数据/县级行政区划/县.shp")
)

# 全国六代图断裂KMZ文件路径
FAULT_KMZ_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/geology/断层/全国六代图断裂.KMZ")
)

# -------------------- 烈度圈颜色配置（RGB，从高到低） --------------------

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

# 烈度圈线宽
INTENSITY_LINE_WIDTH = 0.8

# 阿拉伯数字 → 罗马数字全角字符映射
ROMAN_NUMERAL_MAP = {
    1: 'Ⅰ', 2: 'Ⅱ', 3: 'Ⅲ', 4: 'Ⅳ', 5: 'Ⅴ',
    6: 'Ⅵ', 7: 'Ⅶ', 8: 'Ⅷ', 9: 'Ⅸ', 10: 'Ⅹ',
    11: 'Ⅺ', 12: 'Ⅻ'
}

# 输出图片DPI
OUTPUT_DPI = 300

# 输出图片尺寸（毫米，A4横向）
OUTPUT_WIDTH_MM = 297
OUTPUT_HEIGHT_MM = 210


# -------------------- 工具函数 --------------------


def parse_kml_file(kml_file_path):
    """
    解析KML文件，提取所有烈度圈信息。

    参数:
        kml_file_path: str — KML文件的完整路径

    返回:
        list[dict] — 烈度圈信息列表，按烈度从大到小排序。
            每个元素: {
                'intensity': int,          # 烈度值（阿拉伯数字）
                'name':      str,          # 原始名称，如"5度"
                'coordinates': list[tuple] # 坐标 [(lon, lat), ...]
            }
    """
    tree = ET.parse(kml_file_path)
    root = tree.getroot()

    # KML默认命名空间
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

    # 按烈度从大到小排序（最内圈最大）
    intensity_list.sort(key=lambda x: x['intensity'], reverse=True)
    return intensity_list


def _extract_intensity_from_name(name_text):
    """
    从名称中提取烈度数字，如 "5度" → 5。

    参数:
        name_text: str — Placemark名称

    返回:
        int 或 None
    """
    match = re.search(r'(\d+)\s*度', name_text)
    if match:
        return int(match.group(1))
    return None


def _parse_coordinates_string(coords_text):
    """
    解析KML坐标字符串 "lon,lat,alt lon,lat,alt ..." 为列表。

    参数:
        coords_text: str — KML格式坐标字符串

    返回:
        list[tuple] — [(lon, lat), ...]
    """
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
    """
    将阿拉伯数字烈度转换为罗马数字字符串。

    参数:
        intensity_value: int — 烈度值

    返回:
        str — 罗马数字，如 "Ⅶ"
    """
    if intensity_value in ROMAN_NUMERAL_MAP:
        return ROMAN_NUMERAL_MAP[intensity_value]
    # 超出预定义范围时手动拼接
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
    """
    使用球面多边形面积公式计算面积（平方千米）。

    参数:
        coordinates: list[tuple] — [(lon, lat), ...]

    返回:
        float — 面积（平方千米），保留整数
    """
    R = 6371.0  # 地球平均半径（千米）
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
    """
    根据所有烈度圈坐标计算地图显示范围（含缓冲区）。

    参数:
        intensity_list: list[dict] — 烈度圈信息列表
        buffer_ratio:   float      — 缓冲比例，默认0.15（15%）

    返回:
        tuple — (xmin, ymin, xmax, ymax) 经纬度范围
    """
    all_lons = []
    all_lats = []
    for item in intensity_list:
        for lon, lat in item['coordinates']:
            all_lons.append(lon)
            all_lats.append(lat)

    if not all_lons or not all_lats:
        raise ValueError("烈度圈坐标为空，无法计算显示范围")

    xmin, xmax = min(all_lons), max(all_lons)
    ymin, ymax = min(all_lats), max(all_lats)

    dx = (xmax - xmin) * buffer_ratio
    dy = (ymax - ymin) * buffer_ratio
    return (xmin - dx, ymin - dy, xmax + dx, ymax + dy)


def analyze_description_text(description_text, intensity_list):
    """
    分析说明文字模板，将占位符X替换为实际数值。

    规则:
        - "极震区地震烈度可达X度"   → 替换为最大烈度（罗马数字）
        - "极震区面积估算为X平方千米" → 替换为最大烈度圈面积
        - "地震烈度VI度以上区域面积达X平方千米" → 替换为≥6度的总面积

    参数:
        description_text: str        — 说明文字模板
        intensity_list:   list[dict] — 烈度圈列表（已按烈度从大到小排序）

    返回:
        str — 填充完成的说明文字
    """
    if not intensity_list:
        return description_text

    # 最大烈度（极震区）
    max_intensity = intensity_list[0]['intensity']
    max_roman = intensity_to_roman(max_intensity)

    # 极震区面积
    max_area = calculate_polygon_area_km2(intensity_list[0]['coordinates'])

    # VI度（6度）及以上区域总面积
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
    return result


def get_current_datetime_string():
    """
    获取当前日期格式化字符串。

    返回:
        str — 如 "2026年02月28日"
    """
    now = datetime.now()
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"

# ============================================================================
#  第二部分：QGIS图层加载与样式设置
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
    """
    设置QGIS项目坐标系为 EPSG:4326（WGS84经纬度）。

    参数:
        project: QgsProject — QGIS项目实例
    """
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    project.setCrs(crs)
    print("项目坐标系设置为 EPSG:4326")


def load_tianditu_basemap(project):
    """
    加载天地图矢量底图 + 矢量注记两个图层。

    参数:
        project: QgsProject — QGIS项目实例

    返回:
        tuple — (vec_layer, cva_layer) 底图图层和注记图层
    """
    # 矢量底图
    vec_uri = (
        f"type=xyz&url={TIANDITU_VEC_URL}"
        f"&zmin=1&zmax=18&crs=EPSG:4326"
    ).replace("{s}", "0")

    vec_layer = QgsRasterLayer(vec_uri, "天地图矢量底图", "wms")
    if vec_layer.isValid():
        project.addMapLayer(vec_layer)
        print("天地图矢量底图加载成功")
    else:
        print("警告：天地图矢量底图加载失败")

    # 矢量注记
    cva_uri = (
        f"type=xyz&url={TIANDITU_CVA_URL}"
        f"&zmin=1&zmax=18&crs=EPSG:4326"
    ).replace("{s}", "0")

    cva_layer = QgsRasterLayer(cva_uri, "天地图矢量注记", "wms")
    if cva_layer.isValid():
        project.addMapLayer(cva_layer)
        print("天地图矢量注记加载成功")
    else:
        print("警告：天地图矢量注记加载失败")

    return vec_layer, cva_layer


def load_boundary_layers(project):
    """
    加载省、市、县三级行政边界图层并设置样式。

    参数:
        project: QgsProject — QGIS项目实例

    返回:
        tuple — (province_layer, city_layer, county_layer)
    """
    # 省界 — 深灰、较粗
    province_layer = QgsVectorLayer(PROVINCE_SHP_PATH, "省界", "ogr")
    if province_layer.isValid():
        _set_boundary_style(province_layer, QColor(80, 80, 80), 0.6)
        project.addMapLayer(province_layer)
        print("省界图层加载成功")
    else:
        print(f"警告：省界加载失败，路径: {PROVINCE_SHP_PATH}")

    # 市界 — 中灰、中等
    city_layer = QgsVectorLayer(CITY_SHP_PATH, "市界", "ogr")
    if city_layer.isValid():
        _set_boundary_style(city_layer, QColor(150, 150, 150), 0.4)
        project.addMapLayer(city_layer)
        print("市界图层加载成功")
    else:
        print(f"警告：市界加载失败，路径: {CITY_SHP_PATH}")

    # 县界 — 浅灰、较细
    county_layer = QgsVectorLayer(COUNTY_SHP_PATH, "县界", "ogr")
    if county_layer.isValid():
        _set_boundary_style(county_layer, QColor(200, 200, 200), 0.3)
        project.addMapLayer(county_layer)
        print("县界图层加载成功")
    else:
        print(f"警告：县界加载失败，路径: {COUNTY_SHP_PATH}")

    return province_layer, city_layer, county_layer


def _set_boundary_style(layer, color, width):
    """
    将面图层设置为 "仅显示边界线、填充透明" 的样式。

    参数:
        layer: QgsVectorLayer — 矢量图层
        color: QColor         — 边界线颜色
        width: float          — 边界线宽度（毫米）
    """
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    if symbol.symbolLayerCount() > 0:
        sym_layer = symbol.symbolLayer(0)
        if isinstance(sym_layer, QgsSimpleFillSymbolLayer):
            sym_layer.setColor(QColor(0, 0, 0, 0))       # 填充透明
            sym_layer.setStrokeColor(color)
            sym_layer.setStrokeWidth(width)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def load_fault_layer(project):
    """
    加载全国六代图断裂图层（KMZ格式），设置为红色虚线样式。

    参数:
        project: QgsProject — QGIS项目实例

    返回:
        QgsVectorLayer — 断裂图层
    """
    fault_layer = QgsVectorLayer(FAULT_KMZ_PATH, "全国六代图断裂", "ogr")
    if fault_layer.isValid():
        symbol = QgsSymbol.defaultSymbol(fault_layer.geometryType())
        line_sl = QgsSimpleLineSymbolLayer()
        line_sl.setColor(QColor(255, 0, 0))
        line_sl.setWidth(0.5)
        line_sl.setPenStyle(Qt.DashLine)
        symbol.changeSymbolLayer(0, line_sl)
        fault_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        project.addMapLayer(fault_layer)
        print("断裂图层加载成功")
    else:
        print(f"警告：断裂图层加载失败，路径: {FAULT_KMZ_PATH}")
    return fault_layer


def create_intensity_layer(project, intensity_list):
    """
    根据解析出的烈度圈数据创建内存矢量图层，设置分类渲染与标注。

    参数:
        project:        QgsProject  — QGIS项目实例
        intensity_list: list[dict]  — 烈度圈信息列表

    返回:
        QgsVectorLayer — 烈度圈图层
    """
    # 创建内存线图层
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
        # 构建线几何（确保闭合）
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

    # 分类渲染
    _set_intensity_renderer(layer, intensity_list)
    # 标注
    _set_intensity_labels(layer)

    project.addMapLayer(layer)
    print(f"烈度圈图层创建成功，共 {len(intensity_list)} 个烈度圈")
    return layer


def _set_intensity_renderer(layer, intensity_list):
    """
    为烈度圈图层设置基于规则的分类渲染（不同烈度不同颜色）。

    参数:
        layer:          QgsVectorLayer — 烈度圈图层
        intensity_list: list[dict]     — 烈度圈信息
    """
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
    """
    为烈度圈图层设置标注（显示罗马数字 + "度"）。

    参数:
        layer: QgsVectorLayer — 烈度圈图层
    """
    settings = QgsPalLayerSettings()
    # 表达式：roman字段 + "度"
    settings.fieldName = "concat(\"roman\", '度')"
    settings.isExpression = True
    settings.placement = QgsPalLayerSettings.Line
    settings.enabled = True

    # 字体：Times New Roman（罗马数字属英文字符）
    fmt = QgsTextFormat()
    font = QFont("Times New Roman", 10)
    font.setBold(True)
    fmt.setFont(font)
    fmt.setSize(10)
    fmt.setColor(QColor(200, 0, 0))

    # 白色描边增强可读性
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
    """
    创建震中标记图层（红色五角星）。

    参数:
        project: QgsProject — QGIS项目实例
        lon:     float      — 震中经度
        lat:     float      — 震中纬度

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

    # 红色五角星符号
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
#  关键修正：使用 QgsPrintLayout 而非 QgsLayout（setName是QgsPrintLayout的方法）
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
    """
    创建打印布局（A4横向）。
    注意：必须使用 QgsPrintLayout（而非QgsLayout），因为setName()仅属于QgsPrintLayout。

    参数:
        project:     QgsProject — QGIS项目实例
        layout_name: str        — 布局名称

    返回:
        QgsPrintLayout — 打印布局对象
    """
    manager = project.layoutManager()
    # 删除同名旧布局
    old = manager.layoutByName(layout_name)
    if old:
        manager.removeLayout(old)

    # ★ 关键：使用 QgsPrintLayout 而非 QgsLayout
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(layout_name)

    # A4横向
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(OUTPUT_WIDTH_MM, OUTPUT_HEIGHT_MM))

    manager.addLayout(layout)
    print(f"打印布局 '{layout_name}' 创建成功")
    return layout


def add_map_item(layout, project, extent):
    """
    在布局中添加地图框并设置显示范围和经纬度网格。

    参数:
        layout:  QgsPrintLayout — 打印布局
        project: QgsProject     — QGIS项目实例
        extent:  tuple          — (xmin, ymin, xmax, ymax)

    返回:
        QgsLayoutItemMap — 地图框对象
    """
    map_item = QgsLayoutItemMap(layout)

    # 地图框位置与大小（单位：毫米）
    map_x, map_y = 5, 5
    map_w, map_h = 210, 200

    map_item.attemptMove(QgsLayoutPoint(map_x, map_y))
    map_item.attemptResize(QgsLayoutSize(map_w, map_h))

    # 设置显示范围
    xmin, ymin, xmax, ymax = extent
    map_item.setExtent(QgsRectangle(xmin, ymin, xmax, ymax))

    # 边框
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))

    # 经纬度网格
    grid = map_item.grid()
    grid.setEnabled(True)
    grid.setIntervalX(0.25)
    grid.setIntervalY(0.25)
    grid.setStyle(QgsLayoutItemMapGrid.Cross)
    grid.setCrossLength(2.0)
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinuteSecond)
    grid.setAnnotationPrecision(0)

    # 网格标注字体
    grid_fmt = QgsTextFormat()
    grid_fmt.setFont(QFont("Times New Roman", 7))
    grid_fmt.setSize(7)
    grid.setAnnotationTextFormat(grid_fmt)

    layout.addLayoutItem(map_item)
    print("地图框添加成功")
    return map_item


def add_north_arrow(layout):
    """
    在布局右上角添加指北针。

    参数:
        layout: QgsPrintLayout — 打印布局

    返回:
        QgsLayoutItemPicture — 指北针对象
    """
    arrow = QgsLayoutItemPicture(layout)

    # 搜索QGIS内置指北针SVG
    svg_found = False
    svg_paths = QgsApplication.svgPaths()
    if svg_paths:
        for svg_dir in svg_paths:
            candidate = os.path.join(svg_dir, 'arrows', 'NorthArrow_11.svg')
            if os.path.exists(candidate):
                arrow.setPicturePath(candidate)
                svg_found = True
                print(f"  指北针SVG: {candidate}")
                break

    if not svg_found:
        # 尝试QGIS安装目录下的常见路径
        prefix = os.environ.get('QGIS_PREFIX_PATH', '')
        fallback_paths = [
            os.path.join(prefix, 'svg', 'arrows', 'NorthArrow_11.svg'),
            os.path.join(prefix, '..', 'svg', 'arrows', 'NorthArrow_11.svg'),
            os.path.join(prefix, 'resources', 'svg', 'arrows', 'NorthArrow_11.svg'),
        ]
        for fb in fallback_paths:
            if os.path.exists(fb):
                arrow.setPicturePath(fb)
                svg_found = True
                print(f"  指北针SVG(fallback): {fb}")
                break

    if not svg_found:
        print("  警告：未找到指北针SVG文件")

    # 放在地图右上角区域
    arrow.attemptMove(QgsLayoutPoint(195, 8))
    arrow.attemptResize(QgsLayoutSize(15, 15))

    layout.addLayoutItem(arrow)
    print("指北针添加成功")
    return arrow


def add_description_text(layout, description_text):
    """
    在布局右侧区域添加说明文字（含白色背景框）。

    参数:
        layout:           QgsPrintLayout — 打印布局
        description_text: str            — 完整说明文字

    返回:
        QgsLayoutItemLabel — 说明文字标签对象
    """
    # 背景矩形
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(218, 5))
    bg.attemptResize(QgsLayoutSize(75, 95))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 230))
    layout.addLayoutItem(bg)

    # 文字标签（宋体）
    label = QgsLayoutItemLabel(layout)
    label.setText(description_text)

    font = QFont("SimSun", 8)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(8)
    fmt.setColor(QColor(0, 0, 0))
    label.setFont(font)
    label.setTextFormat(fmt)
    label.setMarginX(2)
    label.setMarginY(2)

    label.attemptMove(QgsLayoutPoint(219, 6))
    label.attemptResize(QgsLayoutSize(73, 93))
    label.setMode(QgsLayoutItemLabel.ModeFont)

    layout.addLayoutItem(label)
    print("说明文字添加成功")
    return label


def add_legend(layout, intensity_list):
    """
    在布局左下角添加图例（手动构建，标题"图 例"用黑体）。

    参数:
        layout:         QgsPrintLayout — 打印布局
        intensity_list: list[dict]     — 烈度圈信息列表

    返回:
        QgsLayoutItemShape — 图例背景对象
    """
    # 计算图例高度
    num_fixed = 5       # 震中 + 省界 + 市界 + 县界 + 断裂
    num_intensity = len(intensity_list)
    num_items = num_fixed + num_intensity
    line_h = 5.5        # 每行高度（毫米）
    title_h = 9         # 标题高度
    legend_h = title_h + num_items * line_h + 4
    legend_w = 48
    legend_y = 205 - legend_h

    # 背景框
    bg = QgsLayoutItemShape(layout)
    bg.setShapeType(QgsLayoutItemShape.Rectangle)
    bg.attemptMove(QgsLayoutPoint(7, legend_y))
    bg.attemptResize(QgsLayoutSize(legend_w, legend_h))
    bg.setFrameEnabled(True)
    bg.setFrameStrokeColor(QColor(0, 0, 0))
    bg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3))
    bg_sym = bg.symbol()
    bg_sym.setColor(QColor(255, 255, 255, 230))
    layout.addLayoutItem(bg)

    # 图例标题 "图  例"（黑体 SimHei）
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_font = QFont("SimHei", 10)
    title_font.setBold(True)
    title_label.setFont(title_font)
    title_fmt = QgsTextFormat()
    title_fmt.setFont(title_font)
    title_fmt.setSize(10)
    title_fmt.setColor(QColor(0, 0, 0))
    title_label.setTextFormat(title_fmt)
    title_label.setHAlign(Qt.AlignCenter)
    title_label.attemptMove(QgsLayoutPoint(9, legend_y + 1))
    title_label.attemptResize(QgsLayoutSize(legend_w - 4, 8))
    layout.addLayoutItem(title_label)

    # 图例内容（宋体 SimSun）
    legend_lines = [
        "★  震中位置",
        "——  省界",
        "- -  市界",
        "· ·  县界",
        "- - -  断裂",
    ]
    for item in sorted(intensity_list, key=lambda x: x['intensity'], reverse=True):
        roman = intensity_to_roman(item['intensity'])
        legend_lines.append(f"——  {roman}度烈度圈")

    content_label = QgsLayoutItemLabel(layout)
    content_label.setText("\n".join(legend_lines))
    content_font = QFont("SimSun", 7)
    content_label.setFont(content_font)
    content_fmt = QgsTextFormat()
    content_fmt.setFont(content_font)
    content_fmt.setSize(7)
    content_fmt.setColor(QColor(0, 0, 0))
    content_label.setTextFormat(content_fmt)

    content_y = legend_y + title_h + 1
    content_label.attemptMove(QgsLayoutPoint(10, content_y))
    content_label.attemptResize(QgsLayoutSize(legend_w - 6, num_items * line_h + 2))
    layout.addLayoutItem(content_label)

    print("图例添加成功")
    return bg


def add_scale_bar(layout, map_item):
    """
    在布局右下角添加线段比例尺。

    参数:
        layout:   QgsPrintLayout   — 打印布局
        map_item: QgsLayoutItemMap — 关联的地图框

    返回:
        QgsLayoutItemScaleBar — 比例尺对象
    """
    sb = QgsLayoutItemScaleBar(layout)
    sb.setLinkedMap(map_item)
    sb.setStyle('Line Ticks Up')
    sb.setUnits(QgsUnitTypes.DistanceKilometers)
    sb.setUnitLabel("千米")
    sb.setNumberOfSegments(4)
    sb.setNumberOfSegmentsLeft(0)
    sb.setHeight(3)

    # 字体
    fmt = QgsTextFormat()
    fmt.setFont(QFont("Times New Roman", 7))
    fmt.setSize(7)
    sb.setTextFormat(fmt)

    # 右下角
    sb.attemptMove(QgsLayoutPoint(220, 182))
    sb.attemptResize(QgsLayoutSize(60, 10))

    layout.addLayoutItem(sb)
    print("比例尺添加成功")
    return sb


def add_datetime_label(layout):
    """
    在比例尺下方添加制图机构和制图日期。

    参数:
        layout: QgsPrintLayout — 打印布局

    返回:
        QgsLayoutItemLabel — 日期标签对象
    """
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

    label.attemptMove(QgsLayoutPoint(220, 194))
    label.attemptResize(QgsLayoutSize(60, 12))
    label.setHAlign(Qt.AlignLeft)

    layout.addLayoutItem(label)
    print(f"制图时间添加成功: {current_date}")
    return label


def export_layout_to_png(layout, output_path, dpi=OUTPUT_DPI):
    """
    将打印布局导出为PNG图片。

    参数:
        layout:      QgsPrintLayout — 打印布局
        output_path: str            — 输出PNG文件完整路径
        dpi:         int            — 输出分辨率，默认300

    返回:
        bool — 是否导出成功
    """
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
                                    epicenter_lon=None, epicenter_lat=None):
    """
    生成地震烈度图 —— 主函数。

    参数:
        kml_file_path:    str   — KML文件路径（包含烈度圈数据）
        description_text: str   — 说明文字模板（含占位符X）
        output_png_path:  str   — 输出PNG图片完整路径
        epicenter_lon:    float — 震中经度（可选，默认从最大烈度圈质心估算）
        epicenter_lat:    float — 震中纬度（可选，默认从最大烈度圈质心估算）

    返回:
        bool — 是否成功
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
        print(f"  {item['name']} → {roman}度, 坐标点: {len(item['coordinates'])}, "
              f"面积≈{int(area)}km²")

    # ---- 2. 填充说明文字 ----
    print("\n[2/12] 分析说明文字...")
    final_desc = analyze_description_text(description_text, intensity_list)
    print(f"  {final_desc}")

    # ---- 3. 计算震中 ----
    if epicenter_lon is None or epicenter_lat is None:
        coords = intensity_list[0]['coordinates']
        epicenter_lon = sum(c[0] for c in coords) / len(coords)
        epicenter_lat = sum(c[1] for c in coords) / len(coords)
        print(f"\n[3/12] 自动估算震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")
    else:
        print(f"\n[3/12] 指定震中: ({epicenter_lon:.4f}, {epicenter_lat:.4f})")

    # ---- 4. 计算范围 ----
    print("\n[4/12] 计算显示范围...")
    extent = get_extent_from_intensities(intensity_list, buffer_ratio=0.2)
    print(f"  经度[{extent[0]:.4f}, {extent[2]:.4f}] 纬度[{extent[1]:.4f}, {extent[3]:.4f}]")

    # ---- 5. 初始化项目 ----
    print("\n[5/12] 初始化QGIS项目...")
    project = QgsProject.instance()
    project.removeAllMapLayers()
    set_project_crs(project)

    # ---- 6. 天地图底图 ----
    print("\n[6/12] 加载天地图底图...")
    load_tianditu_basemap(project)

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
    map_item = add_map_item(layout, project, extent)
    add_north_arrow(layout)
    add_description_text(layout, final_desc)
    add_legend(layout, intensity_list)
    add_scale_bar(layout, map_item)
    add_datetime_label(layout)

    # ---- 12. 导出 ----
    print("\n[12/12] 导出PNG...")
    out_dir = os.path.dirname(output_png_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    success = export_layout_to_png(layout, output_png_path)

    print("\n" + "=" * 60)
    print("生成成功！" if success else "生成失败！")
    if success:
        print(f"输出: {output_png_path}")
    print("=" * 60)
    return success


# ============================================================================
#  单元测试
# ============================================================================


def test_parse_kml():
    """测试KML解析：验证烈度提取、坐标解析、排序正确性"""
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
        assert len(result) == 4, f"预期4个，实际{len(result)}个"
        assert result[0]['intensity'] == 7, f"最大烈度应为7，实际{result[0]['intensity']}"
        assert result[-1]['intensity'] == 4, f"最小烈度应为4，实际{result[-1]['intensity']}"
        for item in result:
            assert len(item['coordinates']) > 0
        print("  ✓ KML解析通过")
    finally:
        os.unlink(tmp)


def test_roman_numeral():
    """测试罗马数字转换：1~12"""
    print("\n--- 测试: 罗马数字转换 ---")
    expected = {
        1: 'Ⅰ', 2: 'Ⅱ', 3: 'Ⅲ', 4: 'Ⅳ', 5: 'Ⅴ',
        6: 'Ⅵ', 7: 'Ⅶ', 8: 'Ⅷ', 9: 'Ⅸ', 10: 'Ⅹ',
        11: 'Ⅺ', 12: 'Ⅻ'
    }
    for num, exp in expected.items():
        got = intensity_to_roman(num)
        assert got == exp, f"{num} → {got}，预期{exp}"
    print("  ✓ 罗马数字转换通过")


def test_description_analysis():
    """测试说明文字填充：占位符X替换为实际数值"""
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
    assert 'Ⅶ度' in result, "应包含Ⅶ度"
    assert '可达X度' not in result, "占位符应已替换"
    print(f"  填充结果: {result}")
    print("  ✓ 说明文字分析通过")


def test_area_calculation():
    """测试面积计算：0.1°×0.1°矩形在纬度34°处约102km²"""
    print("\n--- 测试: 面积计算 ---")
    coords = [
        (103.20, 34.00), (103.30, 34.00),
        (103.30, 34.10), (103.20, 34.10),
        (103.20, 34.00)
    ]
    area = calculate_polygon_area_km2(coords)
    print(f"  0.1°×0.1° 面积 = {area} km²")
    assert 50 < area < 200, f"面积异常: {area}"
    print("  ✓ 面积计算通过")


def test_extent_calculation():
    """测试显示范围：缓冲区是否正确包含所有坐标"""
    print("\n--- 测试: 显示范围计算 ---")
    mock_data = [
        {'intensity': 7, 'name': '7度',
         'coordinates': [(103.20, 34.10), (103.30, 34.00)]},
        {'intensity': 5, 'name': '5度',
         'coordinates': [(102.50, 34.50), (103.80, 33.50)]},
    ]
    xmin, ymin, xmax, ymax = get_extent_from_intensities(mock_data, 0.15)
    assert xmin < 102.50, f"xmin({xmin})应 < 102.50"
    assert xmax > 103.80, f"xmax({xmax})应 > 103.80"
    assert ymin < 33.50, f"ymin({ymin})应 < 33.50"
    assert ymax > 34.50, f"ymax({ymax})应 > 34.50"
    print(f"  范围: [{xmin:.3f},{ymin:.3f}] → [{xmax:.3f},{ymax:.3f}]")
    print("  ✓ 显示范围计算通过")


def run_all_tests():
    """执行全部单元测试"""
    print("=" * 50)
    print("运行全部单元测试")
    print("=" * 50)

    tests = [
        test_parse_kml,
        test_roman_numeral,
        test_description_analysis,
        test_area_calculation,
        test_extent_calculation,
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

    print("\n" + "=" * 50)
    print(f"测试结果: {passed} 通过 / {failed} 失败 / 共 {len(tests)} 项")
    print("=" * 50)


# ============================================================================
#  主入口（PyCharm直接运行）
# ============================================================================

if __name__ == '__main__':
    # 初始化QGIS环境（在PyCharm中运行时必须）
    from qgis.core import QgsApplication

    qgs = QgsApplication([], False)
    qgs.initQgis()

    try:
        # ---------- 1. 运行单元测试 ----------
        run_all_tests()

        # ---------- 2. 生成地震烈度图 ----------
        # KML文件路径（请替换为实际路径）
        kml_path = "../../data/geology/n0432881302350072.kml"

        # 说明文字模板（X为占位符，程序自动替换）
        desc_text = (
            "据中国地震台网正式测定:2026年01月26日14时56分"
            "甘肃甘南州选部县(103.25°,34.06°)发生5.5级地震,"
            "震源深度10千米。\n"
            "综合考虑震中附近地质构造背景、地震波衰减特性，"
            "估计了本次地震的地震动预测图。\n"
            "预计极震区地震烈度可达X度，极震区面积估算为X平方千米，"
            "地震烈度VI度以上区域面积达X平方千米。"
        )

        # 输出路径
        output = "../../data/geology/kml_2_map.png"

        if os.path.exists(kml_path):
            generate_seismic_intensity_map(
                kml_file_path=kml_path,
                description_text=desc_text,
                output_png_path=output,
                epicenter_lon=114.41,
                epicenter_lat=39.31
            )
        else:
            print(f"\n提示: KML文件不存在 ({kml_path})")
            print("请将 kml_path 改为实际路径后重新运行。")
            print("单元测试已完成。")

    finally:
        qgs.exitQgis()