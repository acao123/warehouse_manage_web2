# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中地质构造图生成脚本
根据用户传入的震中经纬度和震级，加载地质构造底图及省市县界，输出PNG地质构造图。

完全适配QGIS 3.40.15 API。
"""

import os
import sys
import math

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
    QgsFillSymbol,
    QgsSimpleFillSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeatureRequest,
    QgsFeature,
    QgsLegendStyle,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont


# ============================================================
# 常量定义
# ============================================================

# 数据文件路径（相对于脚本所在目录的相对路径）
GEOLOGY_TIF_PATH = "../../data/geology/图3/group.tif"
PROVINCE_SHP_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
CITY_SHP_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
COUNTY_SHP_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"

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
BORDER_WIDTH_MM = 0.35        # 地图框、指北针、图例、比例尺边框宽度(mm)
LONLAT_FONT_SIZE_PT = 7       # 经纬度标注字体大小(pt)

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

# 省名称标注：字体13pt，颜色R=77,G=77,B=77，字体加白边
PROVINCE_LABEL_FONT_SIZE_PT = 13
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# 市（省会）名称标注：字体12pt，颜色黑色，字体加白边
CITY_LABEL_FONT_SIZE_PT = 12
CITY_LABEL_COLOR = QColor(0, 0, 0)

# WGS84地理坐标系
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
    字体：SimHei 13pt，颜色：R=77,G=77,B=77，白色描边。

    参数:
        layer (QgsVectorLayer): 省界矢量图层
    """
    field_name = _find_name_field(layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过标注设置")
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = field_name
    # QGIS 3.40: 使用 Qgis.LabelPlacement 枚举
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

    # 红色五角星符号
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)
    marker = QgsSimpleMarkerSymbolLayer()
    marker.setShape(QgsSimpleMarkerSymbolLayer.Star)
    marker.setSize(6.0)
    marker.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    marker.setColor(QColor(180, 0, 0))
    marker.setStrokeColor(QColor(139, 0, 0))
    marker.setStrokeWidth(0.3)
    symbol.appendSymbolLayer(marker)

    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    print(f"[信息] 震中位置图层创建完成: ({longitude}, {latitude})")
    return layer


def create_city_point_layer(city_layer, extent):
    """
    创建市级行政中心点图层：
    - 从市界面状图层中提取质心作为位置
    - 使用圆圈内黑色方块符号表示
    - 标注市名称，12pt黑色字体加白边

    参数:
        city_layer (QgsVectorLayer): 市界矢量图层
        extent (QgsRectangle): 地图显示范围

    返回:
        QgsVectorLayer: 市级行政中心点图层
    """
    uri = "Point?crs=EPSG:4326&field=name:string(100)"
    point_layer = QgsVectorLayer(uri, "市级行政中心", "memory")
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

    # 设置符号：圆圈内黑色方块（两层叠加）
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 外层圆圈
    circle = QgsSimpleMarkerSymbolLayer()
    circle.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    circle.setSize(3.5)
    circle.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    circle.setColor(QColor(255, 255, 255, 0))
    circle.setStrokeColor(QColor(0, 0, 0))
    circle.setStrokeWidth(0.3)
    symbol.appendSymbolLayer(circle)

    # 内层方块
    square = QgsSimpleMarkerSymbolLayer()
    square.setShape(QgsSimpleMarkerSymbolLayer.Square)
    square.setSize(1.8)
    square.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    square.setColor(QColor(0, 0, 0))
    square.setStrokeColor(QColor(0, 0, 0))
    square.setStrokeWidth(0.1)
    symbol.appendSymbolLayer(square)

    renderer = QgsSingleSymbolRenderer(symbol)
    point_layer.setRenderer(renderer)

    # 配置市名称标注
    _setup_point_labels(point_layer, "name", CITY_LABEL_FONT_SIZE_PT, CITY_LABEL_COLOR)

    point_layer.triggerRepaint()
    print(f"[信息] 市级行政中心点图层创建完成，共 {len(features)} 个要素")
    return point_layer

# ============================================================
# 布局创建与地图元素配置
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
    完全适配QGIS 3.40.15 API:
    - setAnnotationPosition(AnnotationPosition, BorderSide)
    - setAnnotationDisplay(DisplayMode, BorderSide)  用于隐藏特定边的标注
    - setAnnotationDirection(AnnotationDirection, BorderSide)

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺分母
    """
    grid = QgsLayoutItemMapGrid("经纬网", map_item)

    # 根据比例尺决定网格间隔
    if scale <= 150000:
        interval = 0.1        # 小范围：0.1度
    elif scale <= 500000:
        interval = 0.5        # 中范围：0.5度
    else:
        interval = 1.0        # 大范围：1度

    grid.setIntervalX(interval)
    grid.setIntervalY(interval)

    # 网格显示样式：仅显示边框和标注，不显示网格线
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)

    # 启用标注，小数位数1位
    grid.setAnnotationEnabled(True)
    grid.setAnnotationPrecision(1)

    # 经纬度标注字体：Arial 7pt 黑色
    text_format = QgsTextFormat()
    font = QFont("Arial", LONLAT_FONT_SIZE_PT)
    text_format.setFont(font)
    text_format.setSize(LONLAT_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(0, 0, 0))
    grid.setAnnotationTextFormat(text_format)

    # ---- QGIS 3.40 API ----
    # setAnnotationPosition(AnnotationPosition, BorderSide)
    # AnnotationPosition: InsideMapFrame=0, OutsideMapFrame=1
    # BorderSide: Left=0, Right=1, Bottom=2, Top=3
    # 左侧和底部：标注在地图框外侧
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Bottom)
    # 右侧和顶部也设置为外侧（位置本身不控制显示/隐藏）
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Right)
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame, QgsLayoutItemMapGrid.Top)

    # 使用 setAnnotationDisplay(DisplayMode, BorderSide) 控制显示/隐藏
    # DisplayMode: ShowAll=0, LatitudeOnly=1, LongitudeOnly=2, HideAll=3
    # 左侧显示纬度，底部显示经度，右侧和顶部全部隐藏
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.LatitudeOnly, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.LongitudeOnly, QgsLayoutItemMapGrid.Bottom)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Top)

    # setAnnotationDirection(AnnotationDirection, BorderSide)
    # 左侧标注垂直显示，底部标注水平显示
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDirection(QgsLayoutItemMapGrid.Horizontal, QgsLayoutItemMapGrid.Bottom)

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
    白色背景矩形 + 指北针SVG，上边和地图上边框对齐，右侧和地图右边框对齐。

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

    # 位置计算：地图右上角内侧
    bg_x = margin_mm + map_width_mm - arrow_width_mm - 1.0
    bg_y = margin_mm + 1.0

    # 白色背景矩形
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

    # 指北针SVG图片
    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(bg_x + 1.5, bg_y + 0.5, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(arrow_width_mm - 3.0, arrow_height_mm - 1.5, QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)

    print("[信息] 指北针添加完成")


def _add_legend(layout, map_item, margin_mm, map_width_mm, map_height_mm, legend_width_mm):
    """
    在地图右侧添加图例。
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

    legend_x = margin_mm + map_width_mm + 1.0
    legend_y = margin_mm
    legend.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(legend_width_mm - 2.0, map_height_mm, QgsUnitTypes.LayoutMillimeters))

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

    # 图例边框
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

    # 比例尺字体
    sb_text_format = QgsTextFormat()
    sb_text_format.setFont(QFont("Arial", 7))
    sb_text_format.setSize(7)
    sb_text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    scalebar.setTextFormat(sb_text_format)

    # 边框和背景
    scalebar.setFrameEnabled(True)
    scalebar.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    scalebar.setBackgroundEnabled(True)
    scalebar.setBackgroundColor(QColor(255, 255, 255))

    # 位置：地图右下角内侧
    sb_width = 40.0
    sb_height = 12.0
    sb_x = margin_mm + map_width_mm - sb_width - 3.0
    sb_y = margin_mm + map_height_mm - sb_height - 3.0
    scalebar.attemptMove(QgsLayoutPoint(sb_x, sb_y, QgsUnitTypes.LayoutMillimeters))
    scalebar.setLabelBarSpace(1.5)
    layout.addLayoutItem(scalebar)

    # 比例尺数字标注（如 1:500,000）
    scale_label = QgsLayoutItemLabel(layout)
    scale_label.setText(f"1:{scale:,}")
    label_format = QgsTextFormat()
    label_format.setFont(QFont("Arial", 7))
    label_format.setSize(7)
    label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    scale_label.setTextFormat(label_format)
    scale_label.attemptMove(QgsLayoutPoint(sb_x, sb_y - 4.0, QgsUnitTypes.LayoutMillimeters))
    scale_label.attemptResize(QgsLayoutSize(sb_width, 4.0, QgsUnitTypes.LayoutMillimeters))
    scale_label.setFrameEnabled(True)
    scale_label.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    scale_label.setBackgroundEnabled(True)
    scale_label.setBackgroundColor(QColor(255, 255, 255))
    scale_label.setHAlign(Qt.AlignHCenter)
    scale_label.setVAlign(Qt.AlignVCenter)
    layout.addLayoutItem(scale_label)

    print("[信息] 比例尺添加完成")


# ============================================================
# 主函数：生成地震震中地质构造图
# ============================================================

def generate_earthquake_geology_map(longitude, latitude, magnitude, output_path="output_map.png"):
    """
    根据震中经纬度和震级生成地质构造图PNG。

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级（M）
        output_path (str): 输出PNG文件路径

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
    # 4.1 地质构造底图（最底层）
    geology_layer = load_geology_raster(GEOLOGY_TIF_PATH)
    if geology_layer is None:
        print("[错误] 无法加载地质构造底图，程序退出")
        qgs.exitQgis()
        return None
    project.addMapLayer(geology_layer)

    # 4.2 县界
    county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界")
    if county_layer:
        style_county_layer(county_layer)
        project.addMapLayer(county_layer)

    # 4.3 市界
    city_layer = load_vector_layer(CITY_SHP_PATH, "市界")
    if city_layer:
        style_city_layer(city_layer)
        project.addMapLayer(city_layer)

    # 4.4 省界
    province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界")
    if province_layer:
        style_province_layer(province_layer)
        project.addMapLayer(province_layer)

    # 4.5 市级行政中心点图层
    city_point_layer = None
    if city_layer:
        city_point_layer = create_city_point_layer(city_layer, extent)
        if city_point_layer:
            project.addMapLayer(city_point_layer)

    # 4.6 震中位置点图层（最顶层）
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
    if not os.path.exists(resolve_path(GEOLOGY_TIF_PATH)):
        print("  [跳过] 数据文件不存在")
        return
    result = generate_earthquake_geology_map(118.18, 39.63, 7.8, "test_tangshan_M7.8.png")
    if result and os.path.exists(result):
        print(f"  输出: {result} ({os.path.getsize(result)/1024:.1f}KB) ✓")
    print()


def test_generate_map_small():
    """集成测试（M<6）：北京附近 M4.5。"""
    print("--- 集成测试: M<6 ---")
    if not os.path.exists(resolve_path(GEOLOGY_TIF_PATH)):
        print("  [跳过] 数据文件不存在")
        return
    result = generate_earthquake_geology_map(116.4, 39.9, 4.5, "test_small_M4.5.png")
    if result:
        print(f"  输出: {result} ✓")
    print()


def test_generate_map_medium():
    """集成测试（6<=M<7）：成都附近 M6.5。"""
    print("--- 集成测试: 6<=M<7 ---")
    if not os.path.exists(resolve_path(GEOLOGY_TIF_PATH)):
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
        python earthquake_geology_map.py test
        python earthquake_geology_map.py <经度> <纬度> <震级> [输出文件名]
    示例:
        python earthquake_geology_map.py 118.18 39.63 7.8 tangshan.png
    """
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        run_all_tests()
    elif len(sys.argv) >= 4:
        try:
            lon = float(sys.argv[1])
            lat = float(sys.argv[2])
            mag = float(sys.argv[3])
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_geology_M{mag}_{lon}_{lat}.png"
            generate_earthquake_geology_map(lon, lat, mag, out)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_geology_map.py <经度> <纬度> <震级> [输出文件名]")
    else:
        print("使用默认参数运行（唐山地震 M7.8）...")
        generate_earthquake_geology_map(
            longitude=118.18, latitude=39.63,
            magnitude=7.8, output_path="earthquake_geology_tangshan_M7.8.png"
        )