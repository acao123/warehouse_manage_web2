# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中地质构造图生成脚本
根据用户传入的震中经纬度和震级，加载地质构造底图及省市县界，输出PNG地质构造图。

完全适配QGIS 3.40.15 API。

代码结构分四部分：
  第一部分：模块导入与常量定义
  第二部分：工具函数（路径、坐标计算、SVG生成等）
  第三部分：图层加载与样式设置函数
  第四部分：布局创建函数、主生成函数、测试方法
"""

import os
import sys
import math

# ============================================================
# 第一部分：模块导入与常量定义
# ============================================================

# QGIS 相关模块导入
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
    QgsFillSymbol,
    QgsSimpleFillSymbolLayer,
    QgsLineSymbol,
    QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeatureRequest,
    QgsFeature,
    QgsLegendStyle,
    QgsFields,
    QgsField,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont


# ============================================================
# 常量定义
# ============================================================

# 地质构造图tif文件位置（相对于脚本所在目录）
TIF_GEOLOGY_PATH = "../../data/geology/图3/group.tif"
# 兼容旧名称
GEOLOGY_TIF_PATH = TIF_GEOLOGY_PATH

# 省界shp文件位置
SHP_PROVINCE_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
# 兼容旧名称
PROVINCE_SHP_PATH = SHP_PROVINCE_PATH

# 市界shp文件位置
SHP_CITY_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
# 兼容旧名称
CITY_SHP_PATH = SHP_CITY_PATH

# 县界shp文件位置
SHP_COUNTY_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"
# 兼容旧名称
COUNTY_SHP_PATH = SHP_COUNTY_PATH

# 地市级以上居民地res2_4m shp文件位置
SHP_RESIDENCE_PATH = "../../data/geology/地市级以上居民地res2_4m/res2_4m.shp"

# 输出图总宽度(mm)
MAP_TOTAL_WIDTH_MM = 200

# 震级与范围、比例尺对应关系
# small: M<6, medium: 6<=M<7, large: M>=7
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

# 边界样式参数
BORDER_WIDTH_MM = 0.35          # 地图框、指北针、图例、比例尺边框宽度(mm)
LONLAT_FONT_SIZE_PT = 8         # 经纬度标注字体大小(pt)，需求：8pt

# 省界样式：R=160,G=160,B=160，线宽0.4mm，实线
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4

# 市界样式：R=160,G=160,B=160，线宽0.24mm，虚线间隔0.3
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_INTERVAL = 0.3

# 县界样式：R=160,G=160,B=160，线宽0.14mm，虚线间隔0.3
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_INTERVAL = 0.3

# 省名称标注：字体8pt，颜色R=77,G=77,B=77，字体加白边
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# 居民地（地市级以上）名称标注：字体9pt，颜色黑色，字体加白边
RESIDENCE_LABEL_FONT_SIZE_PT = 9
RESIDENCE_LABEL_COLOR = QColor(0, 0, 0)

# 震中标记：红色五角星，外面加白边，内部纯红色，大小为8pt字体的三分之二
EPICENTER_FONT_REF_PT = 8       # 参考字号(pt)
EPICENTER_SIZE_MM = EPICENTER_FONT_REF_PT * (2.0 / 3.0) * 0.3528  # pt→mm，乘2/3

# WGS84地理坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 第二部分：工具函数（路径、坐标计算、SVG生成等）
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
    使用球面近似：1度纬度≈111km，1度经度≈111*cos(纬度)km。

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        half_size_km (float): 地图半幅宽度（千米）

    返回:
        QgsRectangle: 地图范围矩形（WGS84坐标）
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


def create_north_arrow_svg(output_path):
    """
    创建指北针SVG文件（左侧黑色，右侧白色，上方N字母）。

    参数:
        output_path (str): SVG文件输出路径

    返回:
        str: SVG文件路径
    """
    svg_content = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 80" width="50" height="80">
  <polygon points="25,5 15,55 25,45" fill="black" stroke="black" stroke-width="1"/>
  <polygon points="25,5 35,55 25,45" fill="white" stroke="black" stroke-width="1"/>
  <text x="25" y="3" text-anchor="middle" font-size="12" font-weight="bold"
        font-family="Arial" fill="black">N</text>
</svg>'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(svg_content)
    return output_path


def _find_name_field(layer, candidates):
    """
    在矢量图层的字段列表中查找名称字段。
    依次尝试：精确匹配 -> 模糊匹配（包含关键字）-> 返回第一个字符串字段。

    参数:
        layer (QgsVectorLayer): 矢量图层
        candidates (list): 候选字段名列表，按优先级排列

    返回:
        str: 匹配到的字段名，未找到则返回None
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

    # 兜底：返回第一个字符串类型字段
    for f in fields:
        if f.type() == QVariant.String:
            return f.name()

    return None


# ============================================================
# 第三部分：图层加载与样式设置函数
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



# ---- 图层样式设置函数 ----

def style_province_layer(layer):
    """
    设置省界图层样式：
    - 填充透明
    - 边界线：R=160,G=160,B=160，线宽0.4mm，实线
    - 标注：省名称居中，13pt，R=77,G=77,B=77，白色描边

    参数:
        layer (QgsVectorLayer): 省界矢量图层
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))                    # 填充完全透明
    fill_sl.setStrokeColor(PROVINCE_COLOR)                   # 边界颜色
    fill_sl.setStrokeWidth(PROVINCE_LINE_WIDTH_MM)           # 线宽
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.SolidLine)                     # 实线

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)

    # 配置省名称标注
    _setup_province_labels(layer)
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """
    设置市界图层样式：
    - 填充透明
    - 边界线：R=160,G=160,B=160，线宽0.24mm，虚线，间隔0.3

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
    - 边界线：R=160,G=160,B=160，线宽0.14mm，虚线，间隔0.3

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
    配置省界图层标注：从属性表获取省名称，居中显示在省界内。
    字体：SimHei 8pt，颜色：R=77,G=77,B=77，白色描边。

    参数:
        layer (QgsVectorLayer): 省界矢量图层
    """
    field_name = _find_name_field(layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过标注设置")
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    # QGIS 3.40: OverPoint将标注放置于要素中心点，适合面状要素居中标注
    settings.placement = Qgis.LabelPlacement.OverPoint

    text_format = QgsTextFormat()
    font = QFont("SimHei", PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setFont(font)
    text_format.setSize(PROVINCE_LABEL_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(PROVINCE_LABEL_COLOR)

    # 白色描边（字体加白边）
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1.0)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)
    print(f"[信息] 省界标注已配置，字段: {field_name}")


def _setup_point_labels(layer, field_name, font_size_pt, color):
    """
    为点图层配置标注（通用方法）。

    参数:
        layer (QgsVectorLayer): 点图层
        field_name (str): 标注使用的字段名
        font_size_pt (int): 字体大小(pt)
        color (QColor): 字体颜色
    """
    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    # QGIS 3.40 使用 Qgis.LabelPlacement 枚举
    settings.placement = Qgis.LabelPlacement.OverPoint

    text_format = QgsTextFormat()
    font = QFont("SimHei", font_size_pt)
    text_format.setFont(font)
    text_format.setSize(font_size_pt)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(color)

    # 白色描边
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)


def create_epicenter_layer(longitude, latitude):
    """
    创建震中位置的点图层，使用红色五角星符号表示。
    样式：红色五角星，外面加白色描边，内部纯红色，大小为8pt字体的三分之二。

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）

    返回:
        QgsVectorLayer: 震中点图层
    """
    uri = "Point?crs=EPSG:4326&field=name:string(50)"
    layer = QgsVectorLayer(uri, "震中位置", "memory")
    provider = layer.dataProvider()

    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(longitude, latitude)))
    feat.setAttributes(["震中位置"])
    provider.addFeatures([feat])
    layer.updateExtents()

    # 红色五角星符号：内部纯红色，外加白色描边
    # 大小 = 8pt × (2/3) 换算为mm（1pt≈0.3528mm）
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)
    marker = QgsSimpleMarkerSymbolLayer()
    marker.setShape(QgsSimpleMarkerSymbolLayer.Star)
    marker.setSize(EPICENTER_SIZE_MM)
    marker.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    marker.setColor(QColor(255, 0, 0))          # 内部纯红色
    marker.setStrokeColor(QColor(255, 255, 255)) # 白色描边
    marker.setStrokeWidth(0.5)
    marker.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(marker)

    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    print(f"[信息] 震中位置图层创建完成: ({longitude}, {latitude})")
    return layer


def load_kml_layer(kml_path, layer_name="烈度圈"):
    """
    加载KML烈度圈图层（参考earthquake_kml_map.py实现）。
    KML文件中每个Placemark的name属性为烈度值（如"4度"、"5度"等），
    使用不同颜色线段区分各烈度圈，加载后放在图层最顶部。

    参数:
        kml_path (str): KML文件路径（相对路径或绝对路径）
        layer_name (str): 图层名称，默认"烈度圈"

    返回:
        QgsVectorLayer 或 None: KML图层对象，加载失败返回None
    """
    # 解析路径：如果不是绝对路径，相对脚本目录解析
    if not os.path.isabs(kml_path):
        abs_path = resolve_path(kml_path)
    else:
        abs_path = kml_path

    if not os.path.exists(abs_path):
        print(f"[警告] KML文件不存在: {abs_path}，跳过烈度圈加载")
        return None

    # QGIS可以直接读取KML文件（OGR驱动）
    layer = QgsVectorLayer(abs_path, layer_name, "ogr")
    if not layer.isValid():
        print(f"[警告] 无法加载KML图层: {abs_path}")
        return None

    # 为烈度圈设置线段样式（按name字段分类着色）
    _style_kml_intensity_layer(layer)
    print(f"[信息] 成功加载KML烈度圈图层: {abs_path}")
    return layer


def _style_kml_intensity_layer(layer):
    """
    为KML烈度圈图层设置样式：不同烈度使用不同颜色的线段表示。
    烈度越高颜色越深（从外到内：浅橙 → 深红）。

    参数:
        layer (QgsVectorLayer): KML烈度圈矢量图层
    """
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    # 烈度颜色映射（罗马数字对应颜色）
    intensity_colors = {
        "4度": QColor(255, 235, 150),
        "5度": QColor(255, 200, 100),
        "6度": QColor(255, 160, 60),
        "7度": QColor(255, 100, 20),
        "8度": QColor(220, 50, 0),
        "9度": QColor(180, 0, 0),
        "10度": QColor(120, 0, 0),
        "11度": QColor(80, 0, 0),
        "12度": QColor(40, 0, 0),
    }

    # 获取图层几何类型，若为线图层或混合类型，使用线符号
    geom_type = layer.geometryType()
    categories = []

    for intensity_name, color in intensity_colors.items():
        # 创建线符号
        sym = QgsLineSymbol()
        sym.deleteSymbolLayer(0)
        line_sl = QgsSimpleLineSymbolLayer()
        line_sl.setColor(color)
        line_sl.setWidth(0.5)
        line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
        sym.appendSymbolLayer(line_sl)
        cat = QgsRendererCategory(intensity_name, sym, intensity_name)
        categories.append(cat)

    # 尝试使用分类渲染，若图层字段不含name则用默认样式
    name_field = _find_name_field(layer, ["name", "Name", "NAME", "描述"])
    if name_field:
        renderer = QgsCategorizedSymbolRenderer(name_field, categories)
        layer.setRenderer(renderer)
    else:
        # 默认红色线段
        sym = QgsLineSymbol.createSimple({"color": "255,0,0,255", "width": "0.5", "width_unit": "MM"})
        layer.renderer().setSymbol(sym)

    layer.triggerRepaint()


def load_residence_layer(shp_path, extent):
    """
    加载地市级以上居民地点图层（res2_4m.shp）。
    - 筛选地图范围内的居民地要素
    - 将PINYIN列内容转换为汉字展示（优先查找中文名称字段）
    - 符号：黑色空心圆圈内实心黑圆，加圆形白色背景
    - 符号整体大小为市名称大小（9pt）的三分之一
    - 标注：9pt黑色字体，加白边

    参数:
        shp_path (str): res2_4m.shp文件路径（相对路径）
        extent (QgsRectangle): 地图显示范围（WGS84）

    返回:
        QgsVectorLayer 或 None: 居民地点图层，加载失败返回None
    """
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[警告] 居民地SHP文件不存在: {abs_path}，跳过居民地加载")
        return None

    src_layer = QgsVectorLayer(abs_path, "居民地原始", "ogr")
    if not src_layer.isValid():
        print(f"[警告] 无法加载居民地图层: {abs_path}")
        return None

    # 查找名称字段（优先中文名称，其次拼音）
    fields = src_layer.fields()
    field_names = [f.name() for f in fields]

    # 中文名称字段候选
    chinese_field = _find_name_field(
        src_layer,
        ["NAME_CHN", "NAME_CN", "CNAME", "名称", "城市", "地名", "CITY_NAME",
         "居民地", "NAME", "name"]
    )
    pinyin_field = None
    for fn in field_names:
        if "PINYIN" in fn.upper():
            pinyin_field = fn
            break

    # 决定标注使用的字段
    label_field = chinese_field if chinese_field else pinyin_field
    if not label_field:
        label_field = field_names[0] if field_names else None

    print(f"[信息] 居民地标注字段: {label_field} (中文字段={chinese_field}, 拼音字段={pinyin_field})")

    # 创建内存图层，仅包含范围内的要素
    uri = "Point?crs=EPSG:4326&field=name:string(100)"
    point_layer = QgsVectorLayer(uri, "居民地", "memory")
    provider = point_layer.dataProvider()

    request = QgsFeatureRequest().setFilterRect(extent)
    features = []
    for feat in src_layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isNull():
            continue
        pt = geom.asPoint()
        if not extent.contains(pt):
            continue
        new_feat = QgsFeature()
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt.x(), pt.y())))
        name_val = feat[label_field] if label_field else ""
        new_feat.setAttributes([str(name_val) if name_val else ""])
        features.append(new_feat)

    if features:
        provider.addFeatures(features)
    point_layer.updateExtents()

    # 符号大小：需求"整体大小为市名称大小的三分之一"
    # 市名称字体9pt，三分之一即 9pt / 3 = 3pt，换算mm: 3pt × 0.3528mm/pt ≈ 1.06mm
    ref_font_pt = RESIDENCE_LABEL_FONT_SIZE_PT           # 9pt
    symbol_size_mm = ref_font_pt * (1.0 / 3.0) * 0.3528  # ≈1.06mm（外圆直径）

    # 构建三层叠加符号：白色背景圆 + 黑色空心圆 + 黑色实心圆
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 底层：白色实心背景圆（最大）
    bg = QgsSimpleMarkerSymbolLayer()
    bg.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    bg.setSize(symbol_size_mm * 1.2)
    bg.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    bg.setColor(QColor(255, 255, 255))
    bg.setStrokeStyle(Qt.NoPen)
    symbol.appendSymbolLayer(bg)

    # 中层：黑色空心圆（外圆）
    outer = QgsSimpleMarkerSymbolLayer()
    outer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    outer.setSize(symbol_size_mm)
    outer.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    outer.setColor(QColor(255, 255, 255, 0))   # 填充透明
    outer.setStrokeColor(QColor(0, 0, 0))
    outer.setStrokeWidth(0.35)
    outer.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(outer)

    # 顶层：黑色实心圆（内圆，约外圆的40%）
    inner = QgsSimpleMarkerSymbolLayer()
    inner.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    inner.setSize(symbol_size_mm * 0.4)
    inner.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    inner.setColor(QColor(0, 0, 0))
    inner.setStrokeStyle(Qt.NoPen)
    symbol.appendSymbolLayer(inner)

    renderer = QgsSingleSymbolRenderer(symbol)
    point_layer.setRenderer(renderer)

    # 配置标注：9pt黑色字体，加白边
    _setup_point_labels(point_layer, "name", RESIDENCE_LABEL_FONT_SIZE_PT, RESIDENCE_LABEL_COLOR)

    point_layer.triggerRepaint()
    print(f"[信息] 居民地点图层创建完成，共 {len(features)} 个要素")
    return point_layer


def create_city_point_layer(city_layer, extent):
    """
    【保留作备用】从市界面状图层创建市级行政中心点图层。
    当res2_4m.shp不可用时，回退使用此方法。

    参数:
        city_layer (QgsVectorLayer): 市界矢量图层
        extent (QgsRectangle): 地图显示范围

    返回:
        QgsVectorLayer: 市级行政中心点图层
    """
    uri = "Point?crs=EPSG:4326&field=name:string(100)"
    point_layer = QgsVectorLayer(uri, "市级行政中心(备用)", "memory")
    provider = point_layer.dataProvider()

    field_name = _find_name_field(city_layer, ["市", "NAME", "name", "市名", "CITY", "城市", "地名"])

    # 从市界面状图层提取在范围内的质心点
    request = QgsFeatureRequest().setFilterRect(extent)
    features = []
    for feat in city_layer.getFeatures(request):
        geom = feat.geometry()
        if geom.isNull():
            continue
        centroid = geom.centroid().asPoint()
        if extent.contains(centroid):
            new_feat = QgsFeature()
            new_feat.setGeometry(QgsGeometry.fromPointXY(centroid))
            name = feat[field_name] if field_name else ""
            new_feat.setAttributes([name])
            features.append(new_feat)

    if features:
        provider.addFeatures(features)
    point_layer.updateExtents()

    # 符号大小与居民地一致：9pt × (1/3) × 0.3528mm/pt ≈ 1.06mm（外圆直径）
    ref_font_pt = RESIDENCE_LABEL_FONT_SIZE_PT
    symbol_size_mm = ref_font_pt * (1.0 / 3.0) * 0.3528

    # 设置符号：圆圈内黑色实心圆（三层叠加）
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    bg = QgsSimpleMarkerSymbolLayer()
    bg.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    bg.setSize(symbol_size_mm * 1.2)
    bg.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    bg.setColor(QColor(255, 255, 255))
    bg.setStrokeStyle(Qt.NoPen)
    symbol.appendSymbolLayer(bg)

    outer = QgsSimpleMarkerSymbolLayer()
    outer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    outer.setSize(symbol_size_mm)
    outer.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    outer.setColor(QColor(255, 255, 255, 0))
    outer.setStrokeColor(QColor(0, 0, 0))
    outer.setStrokeWidth(0.35)
    outer.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(outer)

    inner = QgsSimpleMarkerSymbolLayer()
    inner.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    inner.setSize(symbol_size_mm * 0.4)
    inner.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    inner.setColor(QColor(0, 0, 0))
    inner.setStrokeStyle(Qt.NoPen)
    symbol.appendSymbolLayer(inner)

    renderer = QgsSingleSymbolRenderer(symbol)
    point_layer.setRenderer(renderer)

    # 配置市名称标注
    _setup_point_labels(point_layer, "name", RESIDENCE_LABEL_FONT_SIZE_PT, RESIDENCE_LABEL_COLOR)

    point_layer.triggerRepaint()
    print(f"[信息] 市级行政中心点图层(备用)创建完成，共 {len(features)} 个要素")
    return point_layer

# ============================================================
# 第四部分：布局创建函数、主生成函数、测试方法
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale):
    """
    创建打印布局，添加地图、指北针、图例、比例尺等元素。

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图显示范围
        scale (int): 地图比例尺分母

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(f"地震震中地质构造图_M{magnitude}")

    # 布局尺寸参数(mm)
    map_width_mm = 140.0
    map_height_mm = 140.0
    legend_width_mm = 58.0
    margin_mm = 1.0

    page_width_mm = MAP_TOTAL_WIDTH_MM
    page_height_mm = map_height_mm + 2 * margin_mm

    # 设置页面大小
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(page_width_mm, page_height_mm, QgsUnitTypes.LayoutMillimeters))

    # === 1. 添加地图项 ===
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(margin_mm, margin_mm, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_width_mm, map_height_mm, QgsUnitTypes.LayoutMillimeters))
    map_item.setCrs(CRS_WGS84)
    map_item.setExtent(extent)
    map_item.setScale(scale)

    # 地图框边框
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    map_item.setFrameStrokeColor(QColor(0, 0, 0))

    # 设置经纬度网格
    _setup_map_grid(map_item, extent, scale)

    layout.addLayoutItem(map_item)
    print("[信息] 地图项添加完成")

    # === 2. 添加指北针（地图右上角） ===
    _add_north_arrow(layout, margin_mm, map_width_mm)

    # === 3. 添加图例（地图右侧） ===
    _add_legend(layout, map_item, margin_mm, map_width_mm, map_height_mm, legend_width_mm)

    # === 4. 添加比例尺（地图右下角） ===
    _add_scale_bar(layout, map_item, margin_mm, map_width_mm, map_height_mm, scale)

    return layout


def _setup_map_grid(map_item, extent, scale):
    """
    设置地图经纬度网格线和标注。
    需求：地图框上侧和左侧标注经纬度，格式X°X′N（纬度）/X°X′E（经度），
          经度最多6个，纬度最多5个，字体8pt。
    完全适配QGIS 3.40.15 API.

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺分母
    """
    grid = QgsLayoutItemMapGrid("经纬网", map_item)

    # 根据比例尺决定网格间隔，同时保证经度≤6、纬度≤5
    span_lon = extent.xMaximum() - extent.xMinimum()
    span_lat = extent.yMaximum() - extent.yMinimum()

    # 初始间隔
    if scale <= 150000:
        interval = 0.1
    elif scale <= 500000:
        interval = 0.5
    else:
        interval = 1.0

    # 调整间隔使经度标注≤6个、纬度标注≤5个
    while (span_lon / interval) > 6 or (span_lat / interval) > 5:
        interval *= 2

    grid.setIntervalX(interval)
    grid.setIntervalY(interval)

    # 网格显示样式：仅显示边框和标注，不显示网格线
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)

    # 启用标注，格式：度分（X°X′N/E）
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinute)
    grid.setAnnotationPrecision(0)

    # 经纬度标注字体：Arial 8pt 黑色
    text_format = QgsTextFormat()
    font = QFont("Arial", LONLAT_FONT_SIZE_PT)
    text_format.setFont(font)
    text_format.setSize(LONLAT_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(0, 0, 0))
    grid.setAnnotationTextFormat(text_format)

    # ---- QGIS 3.40 API ----
    # 所有边设为框外侧
    for side in (QgsLayoutItemMapGrid.Left,
                 QgsLayoutItemMapGrid.Right,
                 QgsLayoutItemMapGrid.Bottom,
                 QgsLayoutItemMapGrid.Top):
        grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, side)

    # 需求：地图框上侧标注经度（LongitudeOnly），左侧标注纬度（LatitudeOnly）
    # 右侧和下侧隐藏
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.LatitudeOnly, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.LongitudeOnly, QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Bottom)

    # 标注方向：左侧纬度垂直，上侧经度水平
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Top)

    # 边框样式：斑马纹
    grid.setFrameStyle(QgsLayoutItemMapGrid.Zebra)
    grid.setFrameWidth(2.0)
    grid.setFramePenSize(BORDER_WIDTH_MM)
    grid.setFramePenColor(QColor(0, 0, 0))

    map_item.grids().addGrid(grid)
    print("[信息] 地图网格设置完成")


def _add_north_arrow(layout, margin_mm, map_width_mm):
    """
    在地图右上角添加指北针。
    需求：白色背景，黑色0.35mm边框，指针左侧黑色半箭头，右侧白色半箭头，箭头顶端写N。
    位置：上边和地图上边框对齐，右侧和地图右边框对齐（内嵌在地图右上角）。

    参数:
        layout (QgsPrintLayout): 布局对象
        margin_mm (float): 页面边距(mm)
        map_width_mm (float): 地图宽度(mm)
    """
    arrow_width_mm = 12.0
    arrow_height_mm = 18.0

    # 生成指北针SVG文件
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "north_arrow.svg")
    create_north_arrow_svg(svg_path)

    # 位置：右边与地图右边框对齐，上边与地图上边框对齐（放在地图右上角）
    bg_x = margin_mm + map_width_mm - arrow_width_mm  # 右边对齐
    bg_y = margin_mm                                   # 上边对齐

    # 白色背景矩形（带黑色0.35mm边框）
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(bg_x, bg_y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(arrow_width_mm, arrow_height_mm, QgsUnitTypes.LayoutMillimeters))

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

    # 指北针SVG图片（左黑右白半箭头+N字）
    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(bg_x + 1.0, bg_y + 0.5, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(arrow_width_mm - 2.0, arrow_height_mm - 1.0, QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)

    print("[信息] 指北针添加完成")


def _add_legend(layout, map_item, margin_mm, map_width_mm, map_height_mm, legend_width_mm):
    """
    在地图右侧添加图例。
    需求：图例左边框与地图右边框重合，图例下边框与地图下边框平行。
    标题"图 例"，字体SimHei 10pt；符号标签SimSun 7pt。

    参数:
        layout (QgsPrintLayout): 布局对象
        map_item (QgsLayoutItemMap): 地图项
        margin_mm (float): 页面边距(mm)
        map_width_mm (float): 地图宽度(mm)
        map_height_mm (float): 地图高度(mm)
        legend_width_mm (float): 图例区域宽度(mm)
    """
    legend = QgsLayoutItemLegend(layout)
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(True)

    # 图例左边框与地图右边框重合：legend_x = margin_mm + map_width_mm
    legend_x = margin_mm + map_width_mm
    # 图例下边框与地图下边框平行：legend_y使图例底部对齐地图底部
    legend_y = margin_mm
    legend.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(legend_width_mm, map_height_mm, QgsUnitTypes.LayoutMillimeters))

    legend.setTitle("图  例")

    # 标题字体
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", 10))
    title_format.setSize(10)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))
    legend.rstyle(QgsLegendStyle.Title).setTextFormat(title_format)

    # 符号标签字体
    item_format = QgsTextFormat()
    item_format.setFont(QFont("SimSun", 7))
    item_format.setSize(7)
    item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format.setColor(QColor(0, 0, 0))
    legend.rstyle(QgsLegendStyle.SymbolLabel).setTextFormat(item_format)

    # 图例边框（黑色0.35mm）
    legend.setFrameEnabled(True)
    legend.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    legend.setFrameStrokeColor(QColor(0, 0, 0))
    legend.setBackgroundColor(QColor(255, 255, 255))
    legend.setBackgroundEnabled(True)

    legend.setSymbolWidth(5.0)
    legend.setSymbolHeight(3.0)

    layout.addLayoutItem(legend)
    print("[信息] 图例添加完成")


def _add_scale_bar(layout, map_item, margin_mm, map_width_mm, map_height_mm, scale):
    """
    在地图右下角添加线段比例尺和比例尺数字标注。
    需求：线段比例尺，白色背景，黑色0.35mm边框，字体8pt。
    位置：右边框与地图右边框重合，下边框与地图下边框重合。

    参数:
        layout (QgsPrintLayout): 布局对象
        map_item (QgsLayoutItemMap): 地图项
        margin_mm (float): 页面边距(mm)
        map_width_mm (float): 地图宽度(mm)
        map_height_mm (float): 地图高度(mm)
        scale (int): 比例尺分母
    """
    scalebar = QgsLayoutItemScaleBar(layout)
    scalebar.setLinkedMap(map_item)
    scalebar.setStyle('Single Box')
    scalebar.setUnits(QgsUnitTypes.DistanceKilometers)
    scalebar.setUnitLabel("km")

    # 根据比例尺选择每段长度
    if scale <= 150000:
        scalebar.setNumberOfSegments(2)
        scalebar.setNumberOfSegmentsLeft(1)
        scalebar.setUnitsPerSegment(2.0)
    elif scale <= 500000:
        scalebar.setNumberOfSegments(2)
        scalebar.setNumberOfSegmentsLeft(1)
        scalebar.setUnitsPerSegment(10.0)
    else:
        scalebar.setNumberOfSegments(2)
        scalebar.setNumberOfSegmentsLeft(1)
        scalebar.setUnitsPerSegment(50.0)

    scalebar.setHeight(3.0)

    # 比例尺字体：8pt
    sb_text_format = QgsTextFormat()
    sb_text_format.setFont(QFont("Arial", 8))
    sb_text_format.setSize(8)
    sb_text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    scalebar.setTextFormat(sb_text_format)

    # 边框和背景
    scalebar.setFrameEnabled(True)
    scalebar.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    scalebar.setBackgroundEnabled(True)
    scalebar.setBackgroundColor(QColor(255, 255, 255))

    # 位置：右边框与地图右边框重合，下边框与地图下边框重合
    sb_width = 40.0
    sb_height = 12.0
    sb_x = margin_mm + map_width_mm - sb_width  # 右边对齐地图右边框
    sb_y = margin_mm + map_height_mm - sb_height  # 下边对齐地图下边框
    scalebar.attemptMove(QgsLayoutPoint(sb_x, sb_y, QgsUnitTypes.LayoutMillimeters))
    scalebar.setLabelBarSpace(1.5)
    layout.addLayoutItem(scalebar)

    # 比例尺数字标注（如 1:500,000）放在比例尺上方
    scale_label = QgsLayoutItemLabel(layout)
    scale_label.setText(f"1:{scale:,}")
    label_format = QgsTextFormat()
    label_format.setFont(QFont("Arial", 8))
    label_format.setSize(8)
    label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    scale_label.setTextFormat(label_format)
    scale_label.attemptMove(QgsLayoutPoint(sb_x, sb_y - 5.0, QgsUnitTypes.LayoutMillimeters))
    scale_label.attemptResize(QgsLayoutSize(sb_width, 5.0, QgsUnitTypes.LayoutMillimeters))
    scale_label.setFrameEnabled(True)
    scale_label.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    scale_label.setBackgroundEnabled(True)
    scale_label.setBackgroundColor(QColor(255, 255, 255))
    scale_label.setHAlign(Qt.AlignHCenter)
    scale_label.setVAlign(Qt.AlignVCenter)
    layout.addLayoutItem(scale_label)

    print("[信息] 比例尺添加完成")


def generate_earthquake_geology_map(longitude, latitude, magnitude,
                                     output_path="output_map.png",
                                     kml_path=None):
    """
    根据震中经纬度和震级生成地质构造图PNG。
    加载图层顺序（从底到顶）：地质构造底图 → 县界 → 市界 → 省界 →
    居民地点图层 → 烈度圈KML（可选）→ 震中标记。

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级（M）
        output_path (str): 输出PNG文件路径，默认"output_map.png"
        kml_path (str): 烈度圈KML文件路径（相对或绝对路径），None则跳过

    返回:
        str: 输出文件的绝对路径，失败返回None
    """
    print("=" * 60)
    print(f"开始生成地震震中地质构造图")
    print(f"  震中经度: {longitude}°")
    print(f"  震中纬度: {latitude}°")
    print(f"  震级: M{magnitude}")
    print("=" * 60)

    # 1. 初始化QGIS（无GUI模式）
    qgs = QgsApplication([], False)
    qgs.initQgis()
    print("[信息] QGIS初始化完成")

    project = QgsProject.instance()
    project.setCrs(CRS_WGS84)

    # 2. 获取震级对应配置
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 地图范围 {config['map_size_km']}km x {config['map_size_km']}km, "
          f"比例尺 1:{scale:,}")

    # 3. 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 4. 按顺序加载图层（从底层到顶层）

    # 4.1 地质构造底图（最底层）：调用tif文件时不改动内部色块
    geology_layer = load_geology_raster(TIF_GEOLOGY_PATH)
    if geology_layer is None:
        print("[错误] 无法加载地质构造底图，程序退出")
        qgs.exitQgis()
        return None
    project.addMapLayer(geology_layer)

    # 4.2 县界
    county_layer = load_vector_layer(SHP_COUNTY_PATH, "县界")
    if county_layer:
        style_county_layer(county_layer)
        project.addMapLayer(county_layer)

    # 4.3 市界
    city_layer = load_vector_layer(SHP_CITY_PATH, "市界")
    if city_layer:
        style_city_layer(city_layer)
        project.addMapLayer(city_layer)

    # 4.4 省界
    province_layer = load_vector_layer(SHP_PROVINCE_PATH, "省界")
    if province_layer:
        style_province_layer(province_layer)
        project.addMapLayer(province_layer)

    # 4.5 地市级以上居民地（res2_4m.shp），优先使用专用文件
    residence_layer = load_residence_layer(SHP_RESIDENCE_PATH, extent)
    if residence_layer:
        project.addMapLayer(residence_layer)
    else:
        # 回退：从市界图层提取质心
        if city_layer:
            fallback_layer = create_city_point_layer(city_layer, extent)
            if fallback_layer:
                project.addMapLayer(fallback_layer)

    # 4.6 烈度圈KML（需求：展示在地图图层最上边，震中标记除外）
    if kml_path:
        kml_layer = load_kml_layer(kml_path, "烈度圈")
        if kml_layer:
            project.addMapLayer(kml_layer)

    # 4.7 震中位置点图层（最顶层）：红色五角星，白色描边
    epicenter_layer = create_epicenter_layer(longitude, latitude)
    project.addMapLayer(epicenter_layer)

    # 5. 创建打印布局
    layout = create_print_layout(project, longitude, latitude, magnitude, extent, scale)
    project.layoutManager().addLayout(layout)
    print("[信息] 打印布局创建完成")

    # 6. 导出PNG
    output_abs_path = export_layout_to_png(layout, output_path)

    # 7. 清理退出
    qgs.exitQgis()
    print("[信息] QGIS已退出")

    return output_abs_path


def export_layout_to_png(layout, output_path, dpi=300):
    """
    将打印布局导出为PNG图片。

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出PNG文件路径
        dpi (int): 输出分辨率，默认300dpi

    返回:
        str: 输出文件绝对路径，失败返回None
    """
    from qgis.core import QgsLayoutExporter

    abs_output_path = os.path.abspath(output_path)

    # 确保输出目录存在
    output_dir = os.path.dirname(abs_output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    settings.generateWorldFile = False

    result = exporter.exportToImage(abs_output_path, settings)

    if result == QgsLayoutExporter.Success:
        print(f"[成功] PNG图片已导出: {abs_output_path}")
        return abs_output_path
    else:
        error_map = {
            QgsLayoutExporter.FileError: "文件错误",
            QgsLayoutExporter.MemoryError: "内存错误",
            QgsLayoutExporter.SvgLayerError: "SVG图层错误",
            QgsLayoutExporter.PrintError: "打印错误",
            QgsLayoutExporter.Canceled: "已取消",
        }
        print(f"[错误] PNG导出失败: {error_map.get(result, f'未知错误(代码:{result})')}")
        return None


# ============================================================
# 测试方法
# ============================================================

def test_magnitude_config():
    """测试震级配置函数：验证三个区间和边界值的映射关系。"""
    print("\n--- 测试: 震级配置 ---")

    # M < 6
    c = get_magnitude_config(5.5)
    assert c["map_size_km"] == 30 and c["scale"] == 150000
    print(f"  M5.5 -> {c['map_size_km']}km, 1:{c['scale']:,} ✓")

    # 6 <= M < 7
    c = get_magnitude_config(6.5)
    assert c["map_size_km"] == 100 and c["scale"] == 500000
    print(f"  M6.5 -> {c['map_size_km']}km, 1:{c['scale']:,} ✓")

    # M >= 7
    c = get_magnitude_config(7.0)
    assert c["map_size_km"] == 300 and c["scale"] == 1500000
    print(f"  M7.0 -> {c['map_size_km']}km, 1:{c['scale']:,} ✓")

    # 边界值 M=6.0
    c = get_magnitude_config(6.0)
    assert c["map_size_km"] == 100
    print(f"  M6.0 -> {c['map_size_km']}km, 1:{c['scale']:,} ✓")

    # 边界值 M=5.99
    c = get_magnitude_config(5.99)
    assert c["map_size_km"] == 30
    print(f"  M5.99 -> {c['map_size_km']}km, 1:{c['scale']:,} ✓")

    print("  全部通过 ✓\n")


def test_constants():
    """测试常量定义：验证需求中指定的字体大小和文件路径常量。"""
    print("--- 测试: 常量定义 ---")

    # 省界标注字体必须是8pt（需求）
    assert PROVINCE_LABEL_FONT_SIZE_PT == 8, f"省界标注字体应为8pt, 当前={PROVINCE_LABEL_FONT_SIZE_PT}pt"
    print(f"  省界标注字体: {PROVINCE_LABEL_FONT_SIZE_PT}pt ✓")

    # 居民地标注字体必须是9pt（需求）
    assert RESIDENCE_LABEL_FONT_SIZE_PT == 9, f"居民地标注字体应为9pt"
    print(f"  居民地标注字体: {RESIDENCE_LABEL_FONT_SIZE_PT}pt ✓")

    # 经纬度标注字体必须是8pt（需求）
    assert LONLAT_FONT_SIZE_PT == 8, f"经纬度标注字体应为8pt"
    print(f"  经纬度标注字体: {LONLAT_FONT_SIZE_PT}pt ✓")

    # 震中大小 = 8pt × 2/3（需求）
    expected_size = 8 * (2.0 / 3.0) * 0.3528
    assert abs(EPICENTER_SIZE_MM - expected_size) < 0.001
    print(f"  震中标记大小: {EPICENTER_SIZE_MM:.4f}mm (8pt×2/3) ✓")

    # SHP_RESIDENCE_PATH 常量存在（需求）
    assert SHP_RESIDENCE_PATH, "SHP_RESIDENCE_PATH 常量不应为空"
    print(f"  居民地SHP路径常量: {SHP_RESIDENCE_PATH} ✓")

    # 边界宽度0.35mm（需求）
    assert BORDER_WIDTH_MM == 0.35
    print(f"  边框宽度: {BORDER_WIDTH_MM}mm ✓")

    print("  全部通过 ✓\n")


def test_calculate_extent():
    """测试地图范围计算：验证对称性和量级合理性。"""
    print("--- 测试: 地图范围计算 ---")

    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum()
    assert extent.yMinimum() < 39.9 < extent.yMaximum()

    delta_y = extent.yMaximum() - extent.yMinimum()
    # 30km / 111 ≈ 0.27度
    assert abs(delta_y - 0.2703) < 0.01
    print(f"  震中(116.4,39.9) 半径15km -> 纬度范围{delta_y:.4f}° ✓")
    print("  通过 ✓\n")


def test_resolve_path():
    """测试路径解析：验证输出为绝对路径。"""
    print("--- 测试: 路径解析 ---")
    result = resolve_path("../../data/test.tif")
    assert os.path.isabs(result)
    print(f"  '../../data/test.tif' -> '{result}' ✓")
    print("  通过 ✓\n")


def test_generate_map_large():
    """集成测试（M>=7）：唐山地震 M7.8。需要数据文件存在。"""
    print("--- 集成测试: M>=7 唐山地震 ---")
    if not os.path.exists(resolve_path(TIF_GEOLOGY_PATH)):
        print("  [跳过] 数据文件不存在")
        return
    kml = resolve_path("../../data/geology/n0432881302350072.kml") if os.path.exists(
        resolve_path("../../data/geology/n0432881302350072.kml")) else None
    result = generate_earthquake_geology_map(118.18, 39.63, 7.8,
                                             "test_tangshan_M7.8.png", kml_path=kml)
    if result and os.path.exists(result):
        print(f"  输出: {result} ({os.path.getsize(result)/1024:.1f}KB) ✓")
    print()


def test_generate_map_small():
    """集成测试（M<6）：北京附近 M4.5。"""
    print("--- 集成测试: M<6 ---")
    if not os.path.exists(resolve_path(TIF_GEOLOGY_PATH)):
        print("  [跳过] 数据文件不存在")
        return
    result = generate_earthquake_geology_map(116.4, 39.9, 4.5, "test_small_M4.5.png")
    if result:
        print(f"  输出: {result} ✓")
    print()


def test_generate_map_medium():
    """集成测试（6<=M<7）：成都附近 M6.5。"""
    print("--- 集成测试: 6<=M<7 ---")
    if not os.path.exists(resolve_path(TIF_GEOLOGY_PATH)):
        print("  [跳过] 数据文件不存在")
        return
    result = generate_earthquake_geology_map(104.0, 30.6, 6.5, "test_medium_M6.5.png")
    if result:
        print(f"  输出: {result} ✓")
    print()


def run_all_tests():
    """运行所有测试：先单元测试，再集成测试。"""
    print("=" * 60)
    print("开始运行所有测试...")
    print("=" * 60)
    test_magnitude_config()
    test_constants()
    test_calculate_extent()
    test_resolve_path()
    test_generate_map_large()
    test_generate_map_small()
    test_generate_map_medium()
    print("=" * 60)
    print("所有测试运行完毕")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    """
    用法:
        python earthquake_geological_map.py test
        python earthquake_geological_map.py <经度> <纬度> <震级> [输出文件名] [KML路径]
    示例:
        python earthquake_geological_map.py 118.18 39.63 7.8 tangshan.png
        python earthquake_geological_map.py 103.25 34.06 5.5 gansu.png ../../data/geology/kml/source.kml
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
            generate_earthquake_geology_map(lon, lat, mag, out, kml_path=kml)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_geological_map.py <经度> <纬度> <震级> [输出文件名] [KML路径]")
    else:
        print("使用默认参数运行（唐山地震 M7.8）...")
        generate_earthquake_geology_map(
            longitude=118.18, latitude=39.63,
            magnitude=7.8, output_path="earthquake_geology_tangshan_M7.8.png"
        )