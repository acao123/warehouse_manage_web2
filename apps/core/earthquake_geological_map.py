''''
你好，你是一名优秀的程序员和地质专家。
基于QGIS 3.40.15使用python根据用户传入地震震中位置经度(度)和纬度(度)以及震级（M）以及加载地质构造底图tif文件，省、市、县界shp文件，以及地市级以上居民地res2_4m shp文件，烈度圈.kml，然后根据要求输出png图
输出一个png图(图的总宽度为200mm)
	需求说明：
	1.当用户输入的震级M＜6时，是绘制震中附近15km的地质构造图；
	当震级6≤M＜7时，是绘制震中附近50km；当震级M≥7时，是绘制震中附近150km。
	也就是说：震级M＜6时，出的图长和宽都是30km,比例尺设置为1：150000；
	震级6≤M＜7时，出的图长和宽都是100km，比例尺设置为1：500000；震级M≥7时，出的图长和宽都是300km，比例尺设置为1：1500000。
	2.地图的边框为黑色实线，0.35mm
	3.从省界shp文件属性表表中获取省份名称，省份名称展示在省界内，省的名称写在该省的省界内，字体是8pt，颜色改为:R=77 G=77 B=77，字体加白边；
	4.指北针位置：放在地图的右上角，上边和地图上边框对齐，右侧和地图右边框对齐；样式参考”制图布局参考图3.png“，指北针样式：白色背景，黑色0.35mm边框，指针左侧是黑色半箭头，右侧是白色半箭头；
    5.图例位置：放在地图的右侧，图例左边框与地图右边框重合，图例下边框与地图下边框平行，岩性图例从属性表字段yanxing中获取，图例布局参考”制图布局参考图3.png“
	6.比例尺样式：使用线段比例尺，白色背景，黑色0.35mm边框，比例尺字体8pt；比例尺位置：放在地图右下角，比例尺的右边框与地图右边框重合，比例尺的下边框与地图下边框重合
	7.地图框上侧和左侧标注经纬度，形式为X°X′N，X°X′E；经度最多6个，纬度最多5个，经纬度的字体是8pt。
	8.从”地级市点位数据.shp“文件属性表中获取市的名称，和点位信息，代表市的位置符号更改，具体样式为：黑色空圈内为一个实心黑圆，加一个圆形的白色背景；整体大小为市名称大小的三分之一；市的名称字体是9pt，颜色是黑色，加白边
	9.烈度的加载参考earthquake_kml_map.py，需要展示在地图上
	10.代表震中位置的红色五角星外面加白边，内部为纯红色，大小为8pt字体的三分之二。
	11.调用 “地质构造图tif文件”的时候，不要改动内部的色块
	说明： 地质构造图tif文件位置：../../data/geology/图3/group.tif
		  省界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp
	      市界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp
	      县界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp
	      地级市点位数据.shp文件位置：../../data/geology/2023地级市点位数据/地级市点位数据.shp
	12.注释是中文注释，要求方法和参数需要有中文注释
	13.指省界颜色改为:R=160 G=160 B=160，0.4mm，市界颜色改为:R=160 G=160 B=160，0.24mm，虚线间隔为0.3，县界颜色改为:R=160 G=160 B=160，0.14mm虚线间隔为0.3
	14.代码需要无bug可运行，并写出测试方法
	输出图布局参考：制图布局参考图3.png，
	15.基于QGIS3.40.15 python环境，所以生产时一定需要参考对应版本API,生成的代码不能有bug
	注意：”制图布局参考图3.png“值提供布局和样式参考，具体还需要根据实际情况来写代码。
	输出完整代码，代码可能比较长，分四个部分输出，我会放到一个python文件中


'''


# -*- coding: utf-8 -*-
"""
地震地质构造图生成脚本（基于QGIS 3.40.15 PyQGIS）
功能：根据用户传入的震中经纬度、震级，加载地质构造底图tif、省/市/县界shp、
      地级市点位shp、烈度圈kml，输出包含图例、指北针、比例尺、经纬度标注的PNG图。

输出图总宽度为200mm。
依赖：QGIS 3.40.15 Python 环境
作者：acao123
日期：2026-03-08
"""

import os
import sys
import math
import re
from lxml import etree

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsRectangle,
    QgsPointXY,
    QgsGeometry,
    QgsFeature,
    QgsField,
    QgsSingleSymbolRenderer,
    QgsSimpleFillSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsMarkerSymbol,
    QgsFillSymbol,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutItemPicture,
    QgsLayoutItemLabel,
    QgsLayoutExporter,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsLayoutMeasurement,
    QgsUnitTypes,
    QgsLegendStyle,
)

from qgis.PyQt.QtCore import Qt, QSizeF, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# ============================================================
# 【第一部分：常量配置】
# ============================================================

# --- 脚本目录，用于计算数据文件的相对路径 ---
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 文件路径常量 ---
GEOLOGY_TIF_PATH = os.path.normpath(
    os.path.join(_SCRIPT_DIR, "../../data/geology/图3/group.tif"))
SHP_PROVINCE_PATH = os.path.normpath(os.path.join(
    _SCRIPT_DIR,
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"))
SHP_CITY_PATH = os.path.normpath(os.path.join(
    _SCRIPT_DIR,
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"))
SHP_COUNTY_PATH = os.path.normpath(os.path.join(
    _SCRIPT_DIR,
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"))
SHP_CITY_POINT_PATH = os.path.normpath(os.path.join(
    _SCRIPT_DIR, "../../data/geology/2023地级市点位数据/地级市点位数据.shp"))
INPUT_KML_PATH = r"../../data/geology/n0432881302350072.kml"

# --- 输出图尺寸 ---
TOTAL_WIDTH_MM = 200.0
OUTPUT_DPI = 300

# --- 地图边框：黑色实线 0.35mm ---
MAP_BORDER_WIDTH_MM = 0.35

# --- 省界样式：R=160 G=160 B=160, 0.4mm, 实线 ---
PROVINCE_BORDER_COLOR = QColor(160, 160, 160)
PROVINCE_BORDER_WIDTH_MM = 0.4

# --- 市界样式：R=160 G=160 B=160, 0.24mm, 虚线间隔0.3 ---
CITY_BORDER_COLOR = QColor(160, 160, 160)
CITY_BORDER_WIDTH_MM = 0.24
CITY_BORDER_DASH_MM = 0.3

# --- 县界样式：R=160 G=160 B=160, 0.14mm, 虚线间隔0.3 ---
COUNTY_BORDER_COLOR = QColor(160, 160, 160)
COUNTY_BORDER_WIDTH_MM = 0.14
COUNTY_BORDER_DASH_MM = 0.3

# --- 省名称标注：8pt, R=77 G=77 B=77, 加白边 ---
PROVINCE_LABEL_FONT_SIZE = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# --- 市名称标注：9pt, 黑色, 加白边 ---
CITY_LABEL_FONT_SIZE = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# --- 烈度圈样式 ---
INTENSITY_BORDER_WIDTH_MM = 0.35
INTENSITY_LABEL_FONT_SIZE = 8

# --- 震中标记：红色五角星，大小约8pt的2/3 ---
EPICENTER_SIZE_PT = 8.0 * 2.0 / 3.0

# --- 经纬度标注字体、比例尺字体：8pt ---
COORD_LABEL_FONT_SIZE = 8
SCALE_BAR_FONT_SIZE = 8


# ============================================================
# 【工具函数】
# ============================================================

def get_map_params_by_magnitude(magnitude):
    """
    根据震级获取地图参数

    参数:
        magnitude (float): 震级
    返回:
        dict: 包含 radius_km, side_km, scale_denom
    """
    if magnitude < 6.0:
        return {"radius_km": 15, "side_km": 30, "scale_denom": 150000}
    elif magnitude < 7.0:
        return {"radius_km": 50, "side_km": 100, "scale_denom": 500000}
    else:
        return {"radius_km": 150, "side_km": 300, "scale_denom": 1500000}


def km_to_degree_lon(km, latitude):
    """千米转经度差"""
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """千米转纬度差"""
    return km / 110.574


def calculate_extent(center_lon, center_lat, side_km):
    """
    根据震中和边长计算地图范围

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        side_km (float): 地图边长（千米）
    返回:
        QgsRectangle: 地图范围矩形
    """
    half_km = side_km / 2.0
    dlon = km_to_degree_lon(half_km, center_lat)
    dlat = km_to_degree_lat(half_km)
    return QgsRectangle(
        center_lon - dlon, center_lat - dlat,
        center_lon + dlon, center_lat + dlat
    )


def int_to_roman(num):
    """将阿拉伯数字转换为罗马数字"""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
    roman_num = ''
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            roman_num += syms[i]
            num -= val[i]
        i += 1
    return roman_num


def format_degree_label(value, is_lon=True):
    """
    将十进制度数格式化为 X°X′N / X°X′E

    参数:
        value (float): 十进制度数
        is_lon (bool): True=经度, False=纬度
    返回:
        str: 格式化字符串
    """
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes = int(round((abs_val - degrees) * 60))
    if minutes == 60:
        degrees += 1
        minutes = 0
    suffix = ("E" if value >= 0 else "W") if is_lon else ("N" if value >= 0 else "S")
    return f"{degrees}°{minutes:02d}′{suffix}"


def _find_field(layer, candidates):
    """
    在图层属性表中查找匹配字段名

    参数:
        layer (QgsVectorLayer): 矢量图层
        candidates (list): 候选字段名列表
    返回:
        str: 字段名或None
    """
    field_names = [f.name() for f in layer.fields()]
    for c in candidates:
        for fn in field_names:
            if fn.lower() == c.lower():
                return fn
    for c in candidates:
        for fn in field_names:
            if c.lower() in fn.lower():
                return fn
    return field_names[0] if field_names else None


def _make_text_format(font_family, size_pt, color,
                      buffer_enabled=False, buffer_size_mm=0.5):
    """
    创建 QgsTextFormat 对象

    参数:
        font_family (str): 字体名
        size_pt (float): 字号(pt)
        color (QColor): 颜色
        buffer_enabled (bool): 是否白边
        buffer_size_mm (float): 白边大小(mm)
    返回:
        QgsTextFormat
    """
    tf = QgsTextFormat()
    tf.setFont(QFont(font_family, int(size_pt)))
    tf.setSize(size_pt)
    tf.setSizeUnit(QgsUnitTypes.RenderPoints)
    tf.setColor(color)
    if buffer_enabled:
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(buffer_size_mm)
        buf.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        buf.setColor(QColor(255, 255, 255))
        tf.setBuffer(buf)
    return tf


# ============================================================
# 【KML烈度圈解析】（参考 earthquake_kml_map.py）
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件获取烈度圈数据

    参数:
        kml_path (str): KML文件路径
    返回:
        dict: {烈度值(int): [(lon,lat), ...]}
    """
    intensity_data = {}
    if not os.path.exists(kml_path):
        print(f"  *** KML文件不存在: {kml_path} ***")
        return intensity_data
    try:
        with open(kml_path, 'rb') as f:
            kml_content = f.read()
        root = etree.fromstring(kml_content)
        ns = root.nsmap.get(None, 'http://www.opengis.net/kml/2.2')
        nsmap = {'kml': ns}
        placemarks = root.findall('.//kml:Placemark', nsmap)
        if not placemarks:
            placemarks = root.findall('.//{' + ns + '}Placemark')
        if not placemarks:
            placemarks = root.findall('.//Placemark')
        print(f"  找到 {len(placemarks)} 个Placemark")
        for pm in placemarks:
            name = _get_kml_text(pm, 'name', nsmap, ns)
            intensity = _extract_intensity(name)
            if intensity is None:
                continue
            coords = _extract_kml_coords(pm, nsmap, ns)
            if coords:
                intensity_data[intensity] = coords
                print(f"    烈度 {intensity}度: {len(coords)} 个坐标点")
    except Exception as e:
        print(f"  *** KML解析失败: {e} ***")
    return intensity_data


def _get_kml_text(elem, tag, nsmap, ns):
    """获取KML元素文本"""
    for pat in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(pat, nsmap) if 'kml:' in pat else elem.find(pat)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _extract_intensity(name):
    """从名称提取烈度值"""
    if not name:
        return None
    m = re.search(r'(\d+)\s*度', name)
    if m:
        return int(m.group(1))
    try:
        return int(name.strip())
    except ValueError:
        return None


def _extract_kml_coords(pm, nsmap, ns):
    """从 Placemark 提取 LineString 坐标列表"""
    coords = []
    ls_elems = []
    for tag in ['kml:LineString', f'{{{ns}}}LineString', 'LineString']:
        try:
            found = pm.findall('.//' + tag, nsmap) if 'kml:' in tag else pm.findall('.//' + tag)
            ls_elems.extend(found)
        except Exception:
            pass
    for ls in ls_elems:
        coord_text = ""
        for ctag in ['kml:coordinates', f'{{{ns}}}coordinates', 'coordinates']:
            try:
                ce = ls.find(ctag, nsmap) if 'kml:' in ctag else ls.find(ctag)
            except Exception:
                ce = None
            if ce is not None and ce.text:
                coord_text = ce.text.strip()
                break
        if coord_text:
            for part in coord_text.replace('\n', ' ').replace('\t', ' ').split():
                fields = part.strip().split(',')
                if len(fields) >= 2:
                    try:
                        coords.append((float(fields[0]), float(fields[1])))
                    except ValueError:
                        continue
    return coords


# ============================================================
# 【第二部分：图层加载与样式设置】
# ============================================================

def load_geology_tif(tif_path):
    """
    加载地质构造底图tif（不改动内部色块）

    参数:
        tif_path (str): tif文件绝对路径
    返回:
        QgsRasterLayer 或 None
    """
    if not os.path.exists(tif_path):
        print(f"  *** 地质构造图不存在: {tif_path} ***")
        return None
    layer = QgsRasterLayer(tif_path, "地质构造图")
    if not layer.isValid():
        print(f"  *** 地质构造图加载失败: {tif_path} ***")
        return None
    print(f"  地质构造图加载成功: {tif_path}")
    return layer


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层

    参数:
        shp_path (str): shp文件绝对路径
        layer_name (str): 图层名称
    返回:
        QgsVectorLayer 或 None
    """
    if not os.path.exists(shp_path):
        print(f"  *** SHP文件不存在: {shp_path} ***")
        return None
    layer = QgsVectorLayer(shp_path, layer_name, "ogr")
    if not layer.isValid():
        print(f"  *** SHP加载失败: {shp_path} ***")
        return None
    print(f"  {layer_name} 加载成功: {layer.featureCount()} 个要素")
    return layer


def setup_province_style(layer):
    """
    设置省界图层样式 + 标注

    参数:
        layer (QgsVectorLayer): 省界图层
    说明:
        边界：R=160 G=160 B=160, 0.4mm 实线，填充透明
        标注：8pt R=77 G=77 B=77 加白边
    """
    sl = QgsSimpleFillSymbolLayer()
    sl.setColor(QColor(0, 0, 0, 0))
    sl.setStrokeColor(PROVINCE_BORDER_COLOR)
    sl.setStrokeWidth(PROVINCE_BORDER_WIDTH_MM)
    sl.setStrokeStyle(Qt.SolidLine)
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    # 标注
    field = _find_field(layer, ['省', 'name', 'NAME', 'PROVINCE', '省份'])
    if field:
        pal = QgsPalLayerSettings()
        pal.fieldName = field
        # ★ QGIS 3.40: 使用 Qgis.LabelPlacement 枚举
        pal.placement = Qgis.LabelPlacement.OverPoint
        pal.centroidInside = True
        pal.setFormat(_make_text_format(
            "宋体", PROVINCE_LABEL_FONT_SIZE, PROVINCE_LABEL_COLOR,
            buffer_enabled=True))
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
    else:
        print("  *** 未找到省名称字段 ***")
    layer.triggerRepaint()


def setup_city_boundary_style(layer):
    """
    设置市界图层样式

    参数:
        layer (QgsVectorLayer): 市界图层
    说明:
        R=160 G=160 B=160, 0.24mm, 虚线间隔0.3
    """
    sl = QgsSimpleFillSymbolLayer()
    sl.setColor(QColor(0, 0, 0, 0))
    sl.setStrokeColor(CITY_BORDER_COLOR)
    sl.setStrokeWidth(CITY_BORDER_WIDTH_MM)
    sl.setStrokeStyle(Qt.DashLine)
    sl.setPenJoinStyle(Qt.RoundJoin)
    if hasattr(sl, 'setCustomDashVector'):
        sl.setCustomDashVector([CITY_BORDER_DASH_MM, CITY_BORDER_DASH_MM])
        sl.setUseCustomDashPattern(True)
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def setup_county_boundary_style(layer):
    """
    设置县界图层样式

    参数:
        layer (QgsVectorLayer): 县界图层
    说明:
        R=160 G=160 B=160, 0.14mm, 虚线间隔0.3
    """
    sl = QgsSimpleFillSymbolLayer()
    sl.setColor(QColor(0, 0, 0, 0))
    sl.setStrokeColor(COUNTY_BORDER_COLOR)
    sl.setStrokeWidth(COUNTY_BORDER_WIDTH_MM)
    sl.setStrokeStyle(Qt.DashLine)
    sl.setPenJoinStyle(Qt.RoundJoin)
    if hasattr(sl, 'setCustomDashVector'):
        sl.setCustomDashVector([COUNTY_BORDER_DASH_MM, COUNTY_BORDER_DASH_MM])
        sl.setUseCustomDashPattern(True)
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def setup_city_point_style(layer):
    """
    设置地级市点位样式 + 标注

    参数:
        layer (QgsVectorLayer): 地级市点位图层
    说明:
        符号：白色背景圆 + 黑色空圈 + 黑色实心小圆
        标注：9pt 黑色 加白边
    """
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 白色背景圆
    bg = QgsSimpleMarkerSymbolLayer()
    bg.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    bg.setSize(2.5)
    bg.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    bg.setColor(QColor(255, 255, 255))
    bg.setStrokeColor(QColor(0, 0, 0))
    bg.setStrokeWidth(0.2)
    bg.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(bg)

    # 黑色空心外圈
    ring = QgsSimpleMarkerSymbolLayer()
    ring.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    ring.setSize(2.0)
    ring.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    ring.setColor(QColor(0, 0, 0, 0))
    ring.setStrokeColor(QColor(0, 0, 0))
    ring.setStrokeWidth(0.25)
    ring.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(ring)

    # 黑色实心内圆
    dot = QgsSimpleMarkerSymbolLayer()
    dot.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    dot.setSize(0.8)
    dot.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    dot.setColor(QColor(0, 0, 0))
    dot.setStrokeColor(QColor(0, 0, 0))
    dot.setStrokeWidth(0.0)
    symbol.appendSymbolLayer(dot)

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    # 标注
    field = _find_field(layer, ['name', 'NAME', '名称', '市名', 'city', 'CITY', '地名'])
    if field:
        pal = QgsPalLayerSettings()
        pal.fieldName = field
        # ★ QGIS 3.40: 使用 Qgis.LabelPlacement 枚举
        pal.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
        pal.setFormat(_make_text_format(
            "宋体", CITY_LABEL_FONT_SIZE, CITY_LABEL_COLOR,
            buffer_enabled=True))
        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
    else:
        print("  *** 未找到市名称字段 ***")
    layer.triggerRepaint()


def create_intensity_layer(intensity_data, crs):
    """
    根据烈度圈数据创建矢量图层

    参数:
        intensity_data (dict): {烈度: [(lon,lat), ...]}
        crs: 坐标参考系
    返回:
        QgsVectorLayer
    """
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "烈度圈", "memory")
    prov = layer.dataProvider()
    prov.addAttributes([
        QgsField("intensity", QVariant.Int),
        QgsField("label", QVariant.String),
    ])
    layer.updateFields()

    feats = []
    for intensity in sorted(intensity_data.keys()):
        coords = intensity_data[intensity]
        if len(coords) < 3:
            continue
        pts = [QgsPointXY(lon, lat) for lon, lat in coords]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPolygonXY([pts]))
        f.setAttributes([intensity, int_to_roman(intensity)])
        feats.append(f)

    prov.addFeatures(feats)
    layer.updateExtents()

    # 样式：黑色0.35mm边框 + 半透明白色填充
    sl = QgsSimpleFillSymbolLayer()
    sl.setColor(QColor(255, 255, 255, 128))
    sl.setStrokeColor(QColor(0, 0, 0))
    sl.setStrokeWidth(INTENSITY_BORDER_WIDTH_MM)
    sl.setStrokeStyle(Qt.SolidLine)
    sym = QgsFillSymbol()
    sym.changeSymbolLayer(0, sl)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))

    # 标注：罗马数字
    pal = QgsPalLayerSettings()
    pal.fieldName = 'label'
    pal.placement = Qgis.LabelPlacement.OverPoint
    pal.centroidInside = True
    pal.setFormat(_make_text_format(
        "Times New Roman", INTENSITY_LABEL_FONT_SIZE, QColor(0, 0, 0),
        buffer_enabled=True, buffer_size_mm=0.4))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    return layer


def create_epicenter_layer(center_lon, center_lat):
    """
    创建震中标记图层（红色五角星+白边）

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
    返回:
        QgsVectorLayer
    """
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "震中", "memory")
    prov = layer.dataProvider()
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(center_lon, center_lat)))
    prov.addFeatures([f])
    layer.updateExtents()

    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 底层白色大星（白边效果）
    bg = QgsSimpleMarkerSymbolLayer()
    bg.setShape(QgsSimpleMarkerSymbolLayer.Star)
    bg.setSize(EPICENTER_SIZE_PT + 1.5)
    bg.setSizeUnit(QgsUnitTypes.RenderPoints)
    bg.setColor(QColor(255, 255, 255))
    bg.setStrokeColor(QColor(255, 255, 255))
    bg.setStrokeWidth(0.3)
    bg.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(bg)

    # 上层纯红色星
    red = QgsSimpleMarkerSymbolLayer()
    red.setShape(QgsSimpleMarkerSymbolLayer.Star)
    red.setSize(EPICENTER_SIZE_PT)
    red.setSizeUnit(QgsUnitTypes.RenderPoints)
    red.setColor(QColor(255, 0, 0))
    red.setStrokeColor(QColor(255, 0, 0))
    red.setStrokeWidth(0.0)
    symbol.appendSymbolLayer(red)

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer

# ============================================================
# 【第三部分：布局创建函数】
# ============================================================

def create_map_layout(project, layers, map_extent, map_params,
                      center_lon, center_lat,
                      geology_layer=None, intensity_data=None, kml_path=None):
    """
    创建完整打印布局

    参数:
        project (QgsProject): 项目实例
        layers (dict): {名称: 图层对象}
        map_extent (QgsRectangle): 地图范围
        map_params (dict): 含 scale_denom 等
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        geology_layer: 地质构造图层
        intensity_data: 烈度圈数据
        kml_path: KML文件路径
    返回:
        QgsPrintLayout
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震地质构造图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    # 尺寸计算
    legend_panel_w = 70.0
    map_w = TOTAL_WIDTH_MM - legend_panel_w   # 130mm
    map_h = map_w                              # 正方形
    margin_top = 8.0
    margin_left = 12.0
    margin_bottom = 3.0
    total_h = margin_top + map_h + margin_bottom

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(TOTAL_WIDTH_MM, total_h,
                                   QgsUnitTypes.LayoutMillimeters))

    # --- 地图项 ---
    ml = margin_left
    mt = margin_top
    map_item = QgsLayoutItemMap(layout)
    map_item.setRect(0, 0, map_w, map_h)
    map_item.attemptMove(QgsLayoutPoint(ml, mt, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
    map_item.setExtent(map_extent)
    map_item.setBackgroundColor(QColor(255, 255, 255))
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setFrameStrokeWidth(
        QgsLayoutMeasurement(MAP_BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))

    # 图层顺序
    order = []
    for key in ['震中', '烈度圈', '地级市', '省界', '市界', '县界', '地质构造图']:
        lyr = layers.get(key)
        if lyr is not None:
            order.append(lyr)
    map_item.setLayers(order)
    layout.addLayoutItem(map_item)

    # --- 经纬度标注 ---
    _add_coord_labels(layout, map_extent, ml, mt, map_w, map_h)

    # --- 指北针 ---
    _add_north_arrow(layout, ml + map_w, mt)

    # --- 图例 ---
    _add_legend(layout, map_item, ml + map_w, mt,
                legend_panel_w, map_h, geology_layer, layers)

    # --- 比例尺 ---
    _add_scalebar(layout, map_item, ml, mt, map_w, map_h,
                  map_params['scale_denom'])

    return layout


def _add_coord_labels(layout, extent, ml, mt, mw, mh):
    """
    添加经纬度标注

    参数:
        layout: 布局
        extent: 地图范围
        ml, mt, mw, mh: 地图位置和尺寸 (mm)
    """
    xmin = extent.xMinimum()
    xmax = extent.xMaximum()
    ymin = extent.yMinimum()
    ymax = extent.yMaximum()

    # 上侧经度（最多6个）
    step = _coord_step(xmax - xmin, 6)
    v = math.ceil(xmin / step) * step
    while v <= xmax:
        frac = (v - xmin) / (xmax - xmin)
        x = ml + frac * mw
        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(format_degree_label(v, True))
        lbl.setTextFormat(_make_text_format(
            "Times New Roman", COORD_LABEL_FONT_SIZE, QColor(0, 0, 0)))
        lbl.adjustSizeToText()
        w = lbl.sizeWithUnits().width()
        lbl.attemptMove(QgsLayoutPoint(
            x - w / 2.0, mt - 6.0, QgsUnitTypes.LayoutMillimeters))
        lbl.setBackgroundEnabled(False)
        lbl.setFrameEnabled(False)
        layout.addLayoutItem(lbl)
        v += step

    # 左侧纬度（最多5个）
    step = _coord_step(ymax - ymin, 5)
    v = math.ceil(ymin / step) * step
    while v <= ymax:
        frac = (ymax - v) / (ymax - ymin)
        y = mt + frac * mh
        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(format_degree_label(v, False))
        lbl.setTextFormat(_make_text_format(
            "Times New Roman", COORD_LABEL_FONT_SIZE, QColor(0, 0, 0)))
        lbl.adjustSizeToText()
        h = lbl.sizeWithUnits().height()
        lbl.attemptMove(QgsLayoutPoint(
            0.5, y - h / 2.0, QgsUnitTypes.LayoutMillimeters))
        lbl.setBackgroundEnabled(False)
        lbl.setFrameEnabled(False)
        layout.addLayoutItem(lbl)
        v += step


def _coord_step(range_deg, max_ticks):
    """选择合适的刻度间隔"""
    for s in [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]:
        if 3 <= range_deg / s <= max_ticks:
            return s
    return range_deg / max_ticks


def _add_north_arrow(layout, map_right, map_top):
    """
    添加指北针（右上角与地图框对齐）

    参数:
        layout: 布局
        map_right (float): 地图右边X (mm)
        map_top (float): 地图上边Y (mm)
    """
    aw, ah = 12.0, 16.0
    pic = QgsLayoutItemPicture(layout)

    # 搜索QGIS内置SVG
    svg = None
    search_dirs = [
        os.path.join(QgsApplication.pkgDataPath(), 'svg', 'arrows'),
        os.path.join(QgsApplication.prefixPath(), '..', 'apps', 'qgis-ltr', 'svg', 'arrows'),
        os.path.join(QgsApplication.prefixPath(), 'svg', 'arrows'),
    ]
    for d in search_dirs:
        for name in ['NorthArrow_01.svg', 'NorthArrow_02.svg']:
            p = os.path.normpath(os.path.join(d, name))
            if os.path.exists(p):
                svg = p
                break
        if svg:
            break

    # 深度搜索
    if not svg:
        base = os.path.normpath(os.path.join(QgsApplication.prefixPath(), '..'))
        for root, dirs, files in os.walk(base):
            for f in files:
                if 'NorthArrow' in f and f.endswith('.svg'):
                    svg = os.path.join(root, f)
                    break
            if svg:
                break

    if svg:
        pic.setPicturePath(svg)
        print(f"  指北针SVG: {svg}")
    else:
        print("  *** 未找到指北针SVG ***")

    pic.attemptResize(QgsLayoutSize(aw, ah, QgsUnitTypes.LayoutMillimeters))
    pic.attemptMove(QgsLayoutPoint(
        map_right - aw, map_top, QgsUnitTypes.LayoutMillimeters))
    pic.setBackgroundEnabled(True)
    pic.setBackgroundColor(QColor(255, 255, 255))
    pic.setFrameEnabled(True)
    pic.setFrameStrokeColor(QColor(0, 0, 0))
    pic.setFrameStrokeWidth(
        QgsLayoutMeasurement(MAP_BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(pic)


def _add_legend(layout, map_item, lx, ly, lw, lh,
                geology_layer, layers):
    """
    添加图例面板

    参数:
        layout: 布局
        map_item: 地图项
        lx, ly, lw, lh: 图例位置和尺寸 (mm)
        geology_layer: 地质构造图层
        layers: 所有图层字典
    """
    # 标题 "图 例"
    title = QgsLayoutItemLabel(layout)
    title.setText("图   例")
    title.setTextFormat(_make_text_format("黑体", 10, QColor(0, 0, 0)))
    title.setHAlign(Qt.AlignCenter)
    title.attemptResize(QgsLayoutSize(lw, 6.0, QgsUnitTypes.LayoutMillimeters))
    title.attemptMove(QgsLayoutPoint(lx, ly + 1.0, QgsUnitTypes.LayoutMillimeters))
    title.setBackgroundEnabled(False)
    title.setFrameEnabled(False)
    layout.addLayoutItem(title)

    # 自动图例
    legend = QgsLayoutItemLegend(layout)
    legend.setLinkedMap(map_item)
    legend.setTitle("")
    legend.setAutoUpdateModel(False)

    # ★ 使用 QgsLegendStyle 设置字体（这里 setStyleFont 虽然也 deprecated
    #   但在 3.40 中仍可用且不会崩溃）
    legend.setStyleFont(QgsLegendStyle.Title, QFont("黑体", 9))
    legend.setStyleFont(QgsLegendStyle.Subgroup, QFont("宋体", 7))
    legend.setStyleFont(QgsLegendStyle.SymbolLabel, QFont("宋体", 7))

    root = legend.model().rootGroup()
    root.clear()
    for key in ['震中', '烈度圈', '省界', '市界', '县界', '地级市']:
        lyr = layers.get(key)
        if lyr is not None:
            root.addLayer(lyr)
    if geology_layer is not None:
        root.addLayer(geology_layer)

    legend.attemptResize(QgsLayoutSize(lw, lh - 8.0, QgsUnitTypes.LayoutMillimeters))
    legend.attemptMove(QgsLayoutPoint(lx, ly + 7.0, QgsUnitTypes.LayoutMillimeters))
    legend.setBackgroundEnabled(True)
    legend.setBackgroundColor(QColor(255, 255, 255))
    legend.setFrameEnabled(True)
    legend.setFrameStrokeColor(QColor(0, 0, 0))
    legend.setFrameStrokeWidth(
        QgsLayoutMeasurement(MAP_BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    legend.setColumnCount(1)
    legend.setResizeToContents(True)
    layout.addLayoutItem(legend)


def _add_scalebar(layout, map_item, ml, mt, mw, mh, scale_denom):
    """
    添加比例尺（地图右下角对齐）

    参数:
        layout: 布局
        map_item: 地图项
        ml, mt, mw, mh: 地图位置和尺寸
        scale_denom (int): 比例尺分母
    """
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle('Single Box')
    sb.setLinkedMap(map_item)
    sb.setUnits(QgsUnitTypes.DistanceKilometers)
    sb.setUnitLabel("km")

    if scale_denom <= 150000:
        sb.setNumberOfSegments(2)
        sb.setUnitsPerSegment(5)
    elif scale_denom <= 500000:
        sb.setNumberOfSegments(2)
        sb.setUnitsPerSegment(10)
    else:
        sb.setNumberOfSegments(3)
        sb.setUnitsPerSegment(50)
    sb.setNumberOfSegmentsLeft(0)

    # ★ QgsLayoutItemScaleBar 没有 setTextFormat()，使用 setFont()
    #   虽然标记为 deprecated 但在 3.40 中仍能正常运行
    sb.setFont(QFont("Times New Roman", SCALE_BAR_FONT_SIZE))

    sb.setBackgroundEnabled(True)
    sb.setBackgroundColor(QColor(255, 255, 255))
    sb.setFrameEnabled(True)
    sb.setFrameStrokeColor(QColor(0, 0, 0))
    sb.setFrameStrokeWidth(
        QgsLayoutMeasurement(MAP_BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    sb.setFillColor(QColor(0, 0, 0))
    sb.setFillColor2(QColor(255, 255, 255))
    sb.setLineColor(QColor(0, 0, 0))
    sb.setLineWidth(MAP_BORDER_WIDTH_MM)

    sb.applyDefaultSize()

    # 定位到右下角
    sz = sb.sizeWithUnits()
    mr = ml + mw
    mb = mt + mh
    sb.attemptMove(QgsLayoutPoint(
        mr - sz.width(), mb - sz.height(), QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(sb)

    # 比例尺数字
    rl = QgsLayoutItemLabel(layout)
    rl.setText(f"1:{scale_denom:,}")
    rl.setTextFormat(_make_text_format(
        "Times New Roman", SCALE_BAR_FONT_SIZE, QColor(0, 0, 0)))
    rl.adjustSizeToText()
    rh = rl.sizeWithUnits().height()
    rl.attemptMove(QgsLayoutPoint(
        mr - sz.width(), mb - sz.height() - rh - 0.5,
        QgsUnitTypes.LayoutMillimeters))
    rl.setBackgroundEnabled(False)
    rl.setFrameEnabled(False)
    layout.addLayoutItem(rl)


# ============================================================
# 【第四部分：主函数与测试】
# ============================================================

def generate_earthquake_geological_map(center_lon, center_lat, magnitude,
                                        kml_path=None, output_path=None):
    """
    生成地震地质构造图（主函数）

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        magnitude (float): 震级
        kml_path (str): 烈度圈KML路径（可选）
        output_path (str): 输出PNG路径（可选）
    返回:
        str: 输出路径或None
    """
    print("=" * 65)
    print("  地 震 地 质 构 造 图 生 成 工 具 (QGIS)")
    print("=" * 65)
    print(f"  震中: {center_lon}°E, {center_lat}°N")
    print(f"  震级: M{magnitude}")

    map_params = get_map_params_by_magnitude(magnitude)
    print(f"  半径: {map_params['radius_km']}km, "
          f"边长: {map_params['side_km']}km, "
          f"比例尺: 1:{map_params['scale_denom']:,}")

    map_extent = calculate_extent(center_lon, center_lat, map_params['side_km'])
    print(f"  范围: 经度[{map_extent.xMinimum():.4f}, {map_extent.xMaximum():.4f}], "
          f"纬度[{map_extent.yMinimum():.4f}, {map_extent.yMaximum():.4f}]")

    # 初始化QGIS
    qgs_app = QgsApplication.instance()
    standalone = False
    if qgs_app is None:
        print("\n[1/8] 初始化QGIS环境...")
        qgs_app = QgsApplication([], False)
        qgs_app.initQgis()
        standalone = True
    else:
        print("\n[1/8] 检测到已有QGIS环境")

    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    crs = QgsCoordinateReferenceSystem("EPSG:4326")

    all_layers = {}
    result_path = None

    try:
        # [2/8] 地质构造底图
        print("\n[2/8] 加载地质构造底图...")
        geo_lyr = load_geology_tif(GEOLOGY_TIF_PATH)
        if geo_lyr:
            project.addMapLayer(geo_lyr, False)
        else:
            print("  *** 警告: 底图加载失败，继续生成 ***")
        all_layers['地质构造图'] = geo_lyr

        # [3/8] 行政边界
        print("\n[3/8] 加载行政边界...")
        for shp_path, name, setup_fn in [
            (SHP_COUNTY_PATH, "县界", setup_county_boundary_style),
            (SHP_CITY_PATH, "市界", setup_city_boundary_style),
            (SHP_PROVINCE_PATH, "省界", setup_province_style),
        ]:
            lyr = load_vector_layer(shp_path, name)
            if lyr:
                setup_fn(lyr)
                project.addMapLayer(lyr, False)
            all_layers[name] = lyr

        # [4/8] 地级市点位
        print("\n[4/8] 加载地级市点位...")
        cp_lyr = load_vector_layer(SHP_CITY_POINT_PATH, "地级市")
        if cp_lyr:
            setup_city_point_style(cp_lyr)
            project.addMapLayer(cp_lyr, False)
        all_layers['地级市'] = cp_lyr

        # [5/8] 烈度圈
        print("\n[5/8] 加载烈度圈...")
        intensity_data = {}
        if kml_path and os.path.exists(kml_path):
            intensity_data = parse_intensity_kml(kml_path)
            if intensity_data:
                i_lyr = create_intensity_layer(intensity_data, crs)
                project.addMapLayer(i_lyr, False)
                all_layers['烈度圈'] = i_lyr
                print(f"  加载了 {len(intensity_data)} 个烈度圈")
            else:
                all_layers['烈度圈'] = None
        else:
            all_layers['烈度圈'] = None
            print("  跳过烈度圈" if not kml_path else f"  KML不存在: {kml_path}")

        # [6/8] 震中
        print("\n[6/8] 创建震中标记...")
        e_lyr = create_epicenter_layer(center_lon, center_lat)
        project.addMapLayer(e_lyr, False)
        all_layers['震中'] = e_lyr

        # [7/8] 布局
        print("\n[7/8] 创建打印布局...")
        layout = create_map_layout(
            project, all_layers, map_extent, map_params,
            center_lon, center_lat,
            geo_lyr, intensity_data, kml_path)

        # [8/8] 导出
        print("\n[8/8] 导出PNG...")
        if output_path is None:
            out_dir = os.path.normpath(os.path.join(_SCRIPT_DIR, "../../data/geology/"))
            os.makedirs(out_dir, exist_ok=True)
            output_path = os.path.join(out_dir, "output_earthquake_geological_map.png")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = OUTPUT_DPI

        rc = exporter.exportToImage(output_path, settings)
        if rc == QgsLayoutExporter.Success:
            fsize = os.path.getsize(output_path) / 1024
            print(f"\n  ✓ 导出成功: {output_path}")
            print(f"  文件大小: {fsize:.1f} KB, DPI: {OUTPUT_DPI}")
            result_path = output_path
        else:
            print(f"\n  ✗ 导出失败，错误码: {rc}")

    except Exception as e:
        print(f"\n  *** 出错: {e} ***")
        import traceback
        traceback.print_exc()

    finally:
        if standalone:
            qgs_app.exitQgis()

    print("\n" + "=" * 65)
    return result_path


# ============================================================
# 【测试方法】
# ============================================================

def test_generate_earthquake_geological_map():
    """
    测试：经度114.39, 纬度39.32, M5.5
    """
    print("=" * 65)
    print("  【测试】地震地质构造图生成")
    print("=" * 65)

    test_lon, test_lat, test_mag = 114.39, 39.32, 6.5
    print(f"  经度: {test_lon}, 纬度: {test_lat}, 震级: M{test_mag}")

    # 参数验证
    p = get_map_params_by_magnitude(test_mag)
    # assert p['radius_km'] == 15
    # assert p['side_km'] == 30
    # assert p['scale_denom'] == 150000
    print(f"  ✓ 参数: 半径={p['radius_km']}km, 边长={p['side_km']}km, "
          f"比例尺=1:{p['scale_denom']:,}")

    ext = calculate_extent(test_lon, test_lat, p['side_km'])
    assert ext.xMinimum() < test_lon < ext.xMaximum()
    assert ext.yMinimum() < test_lat < ext.yMaximum()
    print(f"  ✓ 范围: [{ext.xMinimum():.4f},{ext.xMaximum():.4f}] x "
          f"[{ext.yMinimum():.4f},{ext.yMaximum():.4f}]")

    assert int_to_roman(4) == "IV" and int_to_roman(7) == "VII"
    print("  ✓ 罗马数字")

    assert "E" in format_degree_label(test_lon, True)
    assert "N" in format_degree_label(test_lat, False)
    print(f"  ✓ 格式化: {format_degree_label(test_lon,True)}, "
          f"{format_degree_label(test_lat,False)}")

    assert get_map_params_by_magnitude(6.5)['scale_denom'] == 500000
    assert get_map_params_by_magnitude(7.5)['scale_denom'] == 1500000
    print("  ✓ 多震级验证")

    print("\n  --- 开始生成 ---")
    out = os.path.normpath(os.path.join(
        _SCRIPT_DIR, "../../data/geology/test_earthquake_geological_map.png"))

    result = generate_earthquake_geological_map(
        test_lon, test_lat, test_mag, kml_path=INPUT_KML_PATH, output_path=out)

    if result and os.path.exists(result) and os.path.getsize(result) > 0:
        print(f"\n  ✓ 测试通过！{result}")
    else:
        print("\n  ✗ 测试失败")
    return result


def test_with_kml():
    """带KML烈度圈测试"""
    import tempfile
    print("=" * 65)
    print("  【测试】带KML烈度圈")
    print("=" * 65)

    kml = _gen_test_kml(114.39, 39.32)
    kp = os.path.join(tempfile.gettempdir(), "test_intensity.kml")
    with open(kp, 'w', encoding='utf-8') as f:
        f.write(kml)

    d = parse_intensity_kml(kp)
    assert len(d) > 0
    print(f"  ✓ KML: {len(d)} 个烈度圈")

    out = os.path.normpath(os.path.join(
        _SCRIPT_DIR, "../../data/geology/test_geological_kml.png"))
    result = generate_earthquake_geological_map(
        114.39, 39.32, 6.5, kml_path=kp, output_path=out)

    if os.path.exists(kp):
        os.remove(kp)
    if result:
        print(f"\n  ✓ KML测试通过！{result}")
    else:
        print("\n  ✗ KML测试失败")
    return result


def _gen_test_kml(clon, clat):
    """生成测试KML"""
    kml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    kml += '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
    for intensity, r in [(6, 0.05), (5, 0.12), (4, 0.2)]:
        pts = []
        for i in range(37):
            a = 2.0 * math.pi * i / 36
            lon = clon + r * math.cos(a) / math.cos(math.radians(clat))
            lat = clat + r * math.sin(a)
            pts.append(f"{lon},{lat},0")
        kml += f'<Placemark><name>{intensity}度</name>\n'
        kml += '<description></description>\n'
        kml += f'<LineString><coordinates>\n{" ".join(pts)}\n'
        kml += '</coordinates></LineString>\n</Placemark>\n'
    kml += '</Document>\n</kml>'
    return kml


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    print("地震地质构造图生成工具 v1.0")
    print("基于QGIS 3.40.15 Python环境\n")
    result = test_generate_earthquake_geological_map()
    if result:
        print(f"\n输出: {result}")
    else:
        print("\n失败，请检查QGIS环境和数据文件")