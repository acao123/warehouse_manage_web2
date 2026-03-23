# -*- coding: utf-8 -*-
"""
历史地震分布图生成脚本（基于QGIS 3.40.15）
功能：根据用户输入的震中经纬度、震级以及历史地震CSV文件，
      使用QGIS生成震中附近一定范围内的历史地震分布图，叠加省界、市界、县界、
      断裂图层，带经纬度边框，并输出统计信息。

参考 earthquake_elevation_map.py 的QGIS加载方式。

依赖：QGIS 3.40.15 Python环境
作者：acao123
"""

import os
import sys
import csv
import math
import zipfile
import datetime
import logging
import requests
from io import BytesIO
from lxml import etree
from PIL import Image

# ============================================================
# Django settings 导入（QGIS脚本可在Django项目外独立运行，因此做可选导入）
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
logger = logging.getLogger('report.core.earthquake_map')

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
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeature,
    QgsField,
    QgsLayoutExporter,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# ============================================================
# 天地图配置
# ============================================================
TIANDITU_TK = (
    getattr(_django_settings, 'TIANDITU_TK', '1ef76ef90c6eb961cb49618f9b1a399d')
    if _DJANGO_AVAILABLE else '1ef76ef90c6eb961cb49618f9b1a399d'
)

# ============================================================
# 布局尺寸常量（参考 earthquake_elevation_map.py）
# ============================================================
# 输出图总宽度（毫米）
MAP_TOTAL_WIDTH_MM = 220.0
# 左边距（毫米）
BORDER_LEFT_MM = 4.0
# 上边距（毫米）
BORDER_TOP_MM = 4.0
# 下边距（毫米）
BORDER_BOTTOM_MM = 2.0
# 右边距（毫米）
BORDER_RIGHT_MM = 1.0
# 图例宽度（毫米），图例位于右侧独立区域
LEGEND_WIDTH_MM = 50.0
# 地图内容宽度（右侧为独立图例区域，不与底图重叠）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM

# 输出DPI
OUTPUT_DPI = 150

# 边框线宽（毫米）
BORDER_WIDTH_MM = 0.35

# ============================================================
# 指北针尺寸（毫米）
# ============================================================
NORTH_ARROW_WIDTH_MM = 12.0
NORTH_ARROW_HEIGHT_MM = 18.0

# ============================================================
# 经纬度标注字体大小（磅）
# ============================================================
LONLAT_FONT_SIZE_PT = 10

# ============================================================
# 行政边界样式
# ============================================================
# 省界：深灰色实线
PROVINCE_COLOR = QColor(60, 60, 60)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)
# 省份质心与震中坐标重合判断容差（约0.1米精度，用于浮点数相等比较）
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6

# 市界：灰色虚线
CITY_COLOR = QColor(100, 100, 100)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_GAP_MM = 0.3

# 县界：浅灰色虚线
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_GAP_MM = 0.2

# ============================================================
# 断裂线样式（不同断层类型有不同线宽和颜色）
# ============================================================
# 全新世断层：红色，最粗
FAULT_HOLOCENE_COLOR = QColor(255, 0, 0)
FAULT_HOLOCENE_WIDTH_MM = 0.5
# 晚更新世断层：品红色，中等
FAULT_LATE_PLEISTOCENE_COLOR = QColor(255, 53, 255)
FAULT_LATE_PLEISTOCENE_WIDTH_MM = 0.35
# 早中更新世断层：绿色，最细
FAULT_EARLY_PLEISTOCENE_COLOR = QColor(16, 136, 16)
FAULT_EARLY_PLEISTOCENE_WIDTH_MM = 0.2
# 其他断层
FAULT_DEFAULT_COLOR = QColor(0, 0, 0)
FAULT_DEFAULT_WIDTH_MM = 0.15

# ============================================================
# 地震圆点配置（按等级分级）
# ============================================================
EARTHQUAKE_LEVEL_CONFIG = {
    1: {"min_mag": 4.7, "max_mag": 5.9, "color": QColor(0, 200, 0),   "size_mm": 2.5, "label": "4.7~5.9级"},
    2: {"min_mag": 6.0, "max_mag": 6.9, "color": QColor(255, 255, 0), "size_mm": 3.5, "label": "6.0~6.9级"},
    3: {"min_mag": 7.0, "max_mag": 7.9, "color": QColor(255, 165, 0), "size_mm": 4.5, "label": "7.0~7.9级"},
    4: {"min_mag": 8.0, "max_mag": 99.0, "color": QColor(255, 0, 0),  "size_mm": 5.0, "label": "8.0级以上"},
}

# ============================================================
# 震中五角星样式
# ============================================================
EPICENTER_STAR_SIZE_MM = 6.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# ============================================================
# 图例配置（右侧独立区域）
# ============================================================
# 图例标题字体大小（磅）
LEGEND_TITLE_FONT_SIZE_PT = 12
# 图例项目字体大小（磅）
LEGEND_ITEM_FONT_SIZE_PT = 10
# 图例项行高（毫米）
LEGEND_ROW_HEIGHT_MM = 6.0
# 图例内边距（毫米）
LEGEND_PADDING_MM = 2.0
# 图例图标宽度（毫米）
LEGEND_ICON_WIDTH_MM = 8.0
# 图标与文字间距（毫米）
LEGEND_ICON_TEXT_GAP_MM = 1.5

# ============================================================
# 比例尺字体大小（磅）
# ============================================================
SCALE_FONT_SIZE_PT = 8

# ============================================================
# 震级配置
# ============================================================
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

# ============================================================
# 数据文件路径（优先从 Django settings 读取，否则使用内置默认值）
# ============================================================
_DEFAULT_BASE = "../../data/geology/"

SHP_PROVINCE_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp'
)
SHP_CITY_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp'
)
SHP_COUNTY_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp'
)
KMZ_FAULT_PATH = (
    getattr(_django_settings, 'FAULT_KMZ_PATH',
            _DEFAULT_BASE + '断层/全国六代图断裂.KMZ')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '断层/全国六代图断裂.KMZ'
)
# 地级市点位数据
CITY_POINTS_SHP_PATH = (
    getattr(_django_settings, 'CITY_POINTS_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市点.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市点.shp'
)

# ============================================================
# 地级市点图层样式常量
# ============================================================
CITY_POINT_COLOR = QColor(0, 0, 0)
CITY_POINT_SIZE_MM = 1.5
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """
    根据震级获取配置参数

    参数:
        magnitude (float): 地震震级

    返回:
        dict: 包含radius_km, map_size_km, scale的配置字典
    """
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def get_earthquake_level(mag):
    """
    根据震级返回等级(1-4)，0=不在范围

    参数:
        mag (float): 震级

    返回:
        int: 等级
    """
    if 4.7 <= mag <= 5.9:
        return 1
    elif 6.0 <= mag <= 6.9:
        return 2
    elif 7.0 <= mag <= 7.9:
        return 3
    elif mag >= 8.0:
        return 4
    return 0


def haversine_distance(lon1, lat1, lon2, lat2):
    """
    Haversine公式计算球面距离

    参数:
        lon1, lat1, lon2, lat2 (float): 经纬度

    返回:
        float: 距离（千米）
    """
    R = 6371.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lat2 - lat1)
    dn = math.radians(lon2 - lon1)
    a = math.sin(dl / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dn / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度计算地图范围（WGS84坐标）

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度
        half_size_km (float): 地图半幅宽度（公里）

    返回:
        QgsRectangle: 地图范围矩形
    """
    delta_lat = half_size_km / 111.0
    delta_lon = half_size_km / (111.0 * math.cos(math.radians(latitude)))
    return QgsRectangle(
        longitude - delta_lon, latitude - delta_lat,
        longitude + delta_lon, latitude + delta_lat,
    )


def calculate_map_height_from_extent(extent, map_width_mm):
    """
    根据地图范围和宽度计算地图高度（保持宽高比）

    参数:
        extent (QgsRectangle): 地图范围
        map_width_mm (float): 地图宽度（毫米）

    返回:
        float: 地图高度（毫米）
    """
    lon_range = extent.xMaximum() - extent.xMinimum()
    lat_range = extent.yMaximum() - extent.yMinimum()
    if lon_range <= 0:
        return map_width_mm
    return map_width_mm * lat_range / lon_range


def resolve_path(relative_path):
    """
    将相对路径转换为绝对路径

    参数:
        relative_path (str): 相对路径

    返回:
        str: 绝对路径
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def _choose_tick_step(range_deg, target_min=3, target_max=6):
    """
    根据地理范围选择合适的经纬度刻度间隔

    参数:
        range_deg (float): 地理范围（度）
        target_min (int): 最小刻度数
        target_max (int): 最大刻度数

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


def _find_name_field(layer, candidates):
    """
    在矢量图层字段列表中查找名称字段

    参数:
        layer (QgsVectorLayer): 矢量图层
        candidates (list): 候选字段名列表

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


def create_north_arrow_svg(output_path):
    """
    创建指北针SVG文件

    参数:
        output_path (str): SVG文件输出路径

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


# ============================================================
# CSV读取函数
# ============================================================

def read_earthquake_csv(csv_path, encoding="gbk"):
    """
    读取历史地震CSV文件

    参数:
        csv_path (str): CSV文件路径
        encoding (str): 文件编码

    返回:
        list: 地震记录字典列表
    """
    earthquakes = []
    with open(csv_path, "r", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header:
            print(f"  CSV表头: {[h.strip() for h in header]}")
        for row in reader:
            if len(row) < 6:
                continue
            try:
                time_str = row[0].strip()
                lon, lat, mag = float(row[1]), float(row[2]), float(row[5])
                try:
                    depth = float(row[3].strip())
                except ValueError:
                    depth = 0.0
                location = row[4].strip()
                year, month, day = 0, 0, 0
                try:
                    dp = time_str.split(" ")[0] if " " in time_str else time_str
                    df = dp.split("/")
                    if len(df) >= 1:
                        s = df[0].strip().replace("—", "").replace("-", "")
                        year = int(s) if s.isdigit() else 0
                    if len(df) >= 2:
                        s = df[1].strip().replace("—", "").replace("-", "")
                        month = int(s) if s.isdigit() else 0
                    if len(df) >= 3:
                        s = df[2].strip().replace("—", "").replace("-", "")
                        day = int(s) if s.isdigit() else 0
                except Exception:
                    pass
                earthquakes.append({
                    "time_str": time_str, "lon": lon, "lat": lat, "depth": depth,
                    "location": location, "magnitude": mag,
                    "year": year, "month": month, "day": day,
                })
            except (ValueError, IndexError):
                continue
    print(f"  共 {len(earthquakes)} 条记录")
    return earthquakes


def filter_earthquakes(earthquakes, center_lon, center_lat, radius_km, min_magnitude=4.7):
    """
    筛选指定范围内的地震记录

    参数:
        earthquakes (list): 全部地震记录
        center_lon, center_lat (float): 震中经纬度
        radius_km (float): 筛选半径（千米）
        min_magnitude (float): 最小震级

    返回:
        list: 筛选后的地震记录
    """
    filtered = []
    for eq in earthquakes:
        if eq["magnitude"] < min_magnitude:
            continue
        dist = haversine_distance(center_lon, center_lat, eq["lon"], eq["lat"])
        if dist <= radius_km:
            ec = eq.copy()
            ec["distance"] = dist
            filtered.append(ec)
    print(f"  筛选到 {len(filtered)} 条")
    return filtered


# ============================================================
# KMZ断裂线解析函数
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent_dict):
    """
    解析KMZ断裂线文件

    参数:
        kmz_path (str): KMZ文件路径
        geo_extent_dict (dict): 地理范围 {"min_lon", "max_lon", "min_lat", "max_lat"}

    返回:
        dict: {"holocene":[], "late_pleistocene":[], "early_pleistocene":[], "default":[]}
    """
    empty = {"holocene": [], "late_pleistocene": [], "early_pleistocene": [], "default": []}
    if not os.path.exists(kmz_path):
        print(f"  *** KMZ不存在: {kmz_path} ***")
        return empty

    el = (geo_extent_dict["max_lon"] - geo_extent_dict["min_lon"]) * 0.3
    ea = (geo_extent_dict["max_lat"] - geo_extent_dict["min_lat"]) * 0.3
    ext = (
        geo_extent_dict["min_lon"] - el, geo_extent_dict["max_lon"] + el,
        geo_extent_dict["min_lat"] - ea, geo_extent_dict["max_lat"] + ea,
    )

    result = {"holocene": [], "late_pleistocene": [], "early_pleistocene": [], "default": []}
    try:
        with zipfile.ZipFile(kmz_path, 'r') as zf:
            kml_files = [n for n in zf.namelist() if n.lower().endswith('.kml')]
            if not kml_files:
                return empty
            for kn in kml_files:
                print(f"  解析: {kn}")
                _parse_kml_faults(zf.read(kn), ext, result)
    except zipfile.BadZipFile:
        try:
            with open(kmz_path, 'rb') as f:
                _parse_kml_faults(f.read(), ext, result)
        except Exception as e:
            print(f"  *** 失败: {e} ***")
    except Exception as e:
        print(f"  *** KMZ失败: {e} ***")

    total = sum(len(v) for v in result.values())
    print(f"  断裂: 全新世={len(result['holocene'])}, 晚更新世={len(result['late_pleistocene'])}, "
          f"早中更新世={len(result['early_pleistocene'])}, 其他={len(result['default'])}, 总={total}")
    return result


def _parse_kml_faults(kml_data, ext, result):
    """解析KML断裂数据"""
    try:
        root = etree.fromstring(kml_data)
    except Exception as e:
        print(f"    错误: {e}")
        return
    ns = root.nsmap.get(None, 'http://www.opengis.net/kml/2.2')
    nsmap = {'kml': ns}
    sc = _parse_kml_styles(root, nsmap, ns)
    folder_types = _parse_folder_structure(root, nsmap, ns)
    pms = root.findall('.//kml:Placemark', nsmap)
    if not pms:
        pms = root.findall('.//{' + ns + '}Placemark')
    if not pms:
        pms = root.findall('.//Placemark')
    print(f"    {len(pms)} 个Placemark")

    for pm in pms:
        name = _ft(pm, 'name', nsmap, ns)
        surl = _ft(pm, 'styleUrl', nsmap, ns)
        desc = _ft(pm, 'description', nsmap, ns)
        parent_folder_type = _get_parent_folder_type(pm, folder_types, nsmap, ns)
        ftype = _classify_fault(name, surl, desc, sc, parent_folder_type)
        for lc in _extract_ls_coords(pm, nsmap, ns):
            if len(lc) < 2:
                continue
            cur = []
            for lon, lat in lc:
                if ext[0] <= lon <= ext[1] and ext[2] <= lat <= ext[3]:
                    cur.append((lon, lat))
                else:
                    if len(cur) >= 2:
                        result[ftype].append(cur)
                    cur = []
            if len(cur) >= 2:
                result[ftype].append(cur)


def _parse_folder_structure(root, nsmap, ns):
    """
    解析KML的Folder结构，获取每个Folder的断层类型

    参数:
        root: XML根元素
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        dict: {folder_element: fault_type}
    """
    folder_types = {}
    folders = root.findall('.//kml:Folder', nsmap)
    if not folders:
        folders = root.findall('.//{' + ns + '}Folder')
    if not folders:
        folders = root.findall('.//Folder')
    for folder in folders:
        folder_name = _ft(folder, 'name', nsmap, ns)
        ftype = _classify_by_folder_name(folder_name)
        if ftype:
            folder_types[folder] = ftype
    return folder_types


def _classify_by_folder_name(folder_name):
    """
    根据Folder名称分类断层类型

    参数:
        folder_name (str): Folder名称
    返回:
        str: 断层类型或None
    """
    if not folder_name:
        return None
    name_lower = folder_name.lower()
    if "全新世" in folder_name or "holocene" in name_lower:
        return "holocene"
    if "晚更新世" in folder_name or "late pleistocene" in name_lower or "晚更新" in folder_name:
        return "late_pleistocene"
    if any(k in folder_name for k in ["早中更新世", "早更新世", "中更新世", "早-中更新世"]):
        return "early_pleistocene"
    if "early" in name_lower and "pleistocene" in name_lower:
        return "early_pleistocene"
    if "middle" in name_lower and "pleistocene" in name_lower:
        return "early_pleistocene"
    return None


def _get_parent_folder_type(pm, folder_types, nsmap, ns):
    """
    获取Placemark所属Folder的断层类型

    参数:
        pm: Placemark元素
        folder_types (dict): Folder类型映射
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        str: 断层类型或None
    """
    parent = pm.getparent()
    while parent is not None:
        if parent in folder_types:
            return folder_types[parent]
        tag_name = parent.tag.split('}')[-1] if '}' in parent.tag else parent.tag
        if tag_name == 'Folder':
            folder_name = _ft(parent, 'name', nsmap, ns)
            ftype = _classify_by_folder_name(folder_name)
            if ftype:
                return ftype
        parent = parent.getparent()
    return None


def _ft(elem, tag, nsmap, ns):
    """查找KML元素文本"""
    for p in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(p, nsmap) if 'kml:' in p else elem.find(p)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _parse_kml_styles(root, nsmap, ns):
    """解析KML样式映射"""
    sc = {}
    for tag in [f'kml:Style', f'{{{ns}}}Style', 'Style']:
        try:
            styles = root.findall('.//' + tag, nsmap) if 'kml:' in tag else root.findall('.//' + tag)
        except Exception:
            styles = []
        for s in styles:
            sid = s.get('id', '')
            if not sid:
                continue
            for lt in [f'kml:LineStyle', f'{{{ns}}}LineStyle', 'LineStyle']:
                try:
                    ls = s.find('.//' + lt, nsmap) if 'kml:' in lt else s.find('.//' + lt)
                except Exception:
                    ls = None
                if ls is not None:
                    for ct in [f'kml:color', f'{{{ns}}}color', 'color']:
                        try:
                            ce = ls.find(ct, nsmap) if 'kml:' in ct else ls.find(ct)
                        except Exception:
                            ce = None
                        if ce is not None and ce.text:
                            sc['#' + sid] = ce.text.strip()
                            break
                    break
    for tag in [f'kml:StyleMap', f'{{{ns}}}StyleMap', 'StyleMap']:
        try:
            smaps = root.findall('.//' + tag, nsmap) if 'kml:' in tag else root.findall('.//' + tag)
        except Exception:
            smaps = []
        for sm in smaps:
            sid = sm.get('id', '')
            if not sid:
                continue
            for pt in [f'kml:Pair', f'{{{ns}}}Pair', 'Pair']:
                try:
                    pairs = sm.findall(pt, nsmap) if 'kml:' in pt else sm.findall(pt)
                except Exception:
                    pairs = []
                for pair in pairs:
                    if _ft(pair, 'key', nsmap, ns) == 'normal':
                        su = _ft(pair, 'styleUrl', nsmap, ns)
                        if su in sc:
                            sc['#' + sid] = sc[su]
                        break
    return sc


def _classify_fault(name, style_url, description, style_colors, parent_folder_type=None):
    """判断断裂类型"""
    if parent_folder_type:
        return parent_folder_type
    combined = (name + " " + description).lower()
    if "全新世" in name or "全新世" in description or "holocene" in combined:
        return "holocene"
    if "晚更新世" in name or "晚更新世" in description or "late pleistocene" in combined:
        return "late_pleistocene"
    if any(k in name or k in description for k in ["早中更新世", "早更新世", "中更新世"]):
        return "early_pleistocene"
    cs = style_colors.get(style_url, "").lower().replace("#", "")
    if len(cs) >= 6:
        try:
            if len(cs) == 8:
                bb, gg, rr = int(cs[2:4], 16), int(cs[4:6], 16), int(cs[6:8], 16)
            else:
                bb, gg, rr = int(cs[0:2], 16), int(cs[2:4], 16), int(cs[4:6], 16)
            if rr > 180 and gg < 100 and bb < 100:
                return "holocene"
            if rr > 150 and gg < 100 and bb > 150:
                return "late_pleistocene"
            if gg > 100 and rr < 100 and bb < 100:
                return "early_pleistocene"
        except ValueError:
            pass
    return "default"


def _extract_ls_coords(pm, nsmap, ns):
    """提取LineString坐标列表"""
    all_lines = []
    ls_elems = []
    for tag in [f'kml:LineString', f'{{{ns}}}LineString', 'LineString']:
        try:
            found = pm.findall('.//' + tag, nsmap) if 'kml:' in tag else pm.findall('.//' + tag)
            ls_elems.extend(found)
        except Exception:
            pass
    for ls in ls_elems:
        ct = ""
        for ctag in [f'kml:coordinates', f'{{{ns}}}coordinates', 'coordinates']:
            try:
                ce = ls.find(ctag, nsmap) if 'kml:' in ctag else ls.find(ctag)
            except Exception:
                ce = None
            if ce is not None and ce.text:
                ct = ce.text.strip()
                break
        if ct:
            pts = _parse_coords(ct)
            if pts:
                all_lines.append(pts)
    return all_lines


def _parse_coords(text):
    """解析KML coordinates文本为(lon, lat)列表"""
    pts = []
    for p in text.replace('\n', ' ').replace('\t', ' ').split():
        f = p.strip().split(',')
        if len(f) >= 2:
            try:
                pts.append((float(f[0]), float(f[1])))
            except ValueError:
                continue
    return pts


# ============================================================
# 矢量图层加载与样式设置函数
# ============================================================

def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）

    参数:
        shp_path (str): SHP文件路径（相对路径会自动转换为绝对路径）
        layer_name (str): 图层名称

    返回:
        QgsVectorLayer或None
    """
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 矢量文件不存在: {abs_path}")
        return None
    layer = QgsVectorLayer(abs_path, layer_name, "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载矢量图层: {abs_path}")
        return None
    print(f"[信息] 成功加载矢量图层 '{layer_name}'")
    return layer


def style_province_layer(layer, center_lon=None, center_lat=None, extent=None):
    """
    设置省界图层样式（深灰色实线+省名标注）

    参数:
        layer (QgsVectorLayer): 省界图层
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


def _setup_province_labels(layer):
    """
    配置省界图层标注

    参数:
        layer (QgsVectorLayer): 省界图层
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


def style_city_layer(layer):
    """
    设置市界图层样式（灰色虚线）

    参数:
        layer (QgsVectorLayer): 市界图层
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
    print("[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """
    设置县界图层样式（浅灰色虚线）

    参数:
        layer (QgsVectorLayer): 县界图层
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
    print("[信息] 县界图层样式设置完成")


# ============================================================
# QGIS矢量图层创建函数
# ============================================================

def create_city_point_layer(extent):
    """
    加载地级市点位数据，配置黑色圆形标记，只显示在地图范围内的城市

    参数:
        extent (QgsRectangle): 地图范围（WGS84）

    返回:
        QgsVectorLayer 或 None
    """
    abs_path = resolve_path(CITY_POINTS_SHP_PATH)
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    symbol_size_mm = CITY_POINT_SIZE_MM

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
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成")
    return layer


def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层（红色五角星+白边）

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度

    返回:
        QgsVectorLayer: 震中标记图层
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


def create_earthquake_layer(filtered_quakes):
    """
    创建历史地震圆点矢量图层（按震级等级分类渲染）

    参数:
        filtered_quakes (list): 筛选后的地震记录列表

    返回:
        QgsVectorLayer: 地震圆点图层
    """
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "历史地震", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField("magnitude", QVariant.Double),
        QgsField("level", QVariant.Int),
    ])
    layer.updateFields()

    features = []
    for eq in filtered_quakes:
        lv = get_earthquake_level(eq["magnitude"])
        if lv == 0:
            continue
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(eq["lon"], eq["lat"])))
        feat.setAttribute("magnitude", eq["magnitude"])
        feat.setAttribute("level", lv)
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()

    # 按level字段分类渲染，不同等级使用不同大小和颜色
    categories = []
    for lv in sorted(EARTHQUAKE_LEVEL_CONFIG.keys()):
        cfg = EARTHQUAKE_LEVEL_CONFIG[lv]
        marker_sl = QgsSimpleMarkerSymbolLayer()
        marker_sl.setShape(Qgis.MarkerShape.Circle)
        marker_sl.setColor(cfg["color"])
        marker_sl.setStrokeColor(QColor(0, 0, 0))
        marker_sl.setStrokeWidth(0.15)
        marker_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
        marker_sl.setSize(cfg["size_mm"])
        marker_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        symbol = QgsMarkerSymbol()
        symbol.changeSymbolLayer(0, marker_sl)
        category = QgsRendererCategory(lv, symbol, cfg["label"])
        categories.append(category)

    renderer = QgsCategorizedSymbolRenderer("level", categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()

    print(f"[信息] 创建地震圆点图层，共 {len(features)} 个点")
    return layer


def create_fault_layer(fault_lines, fault_type):
    """
    根据断裂类型创建断裂线矢量图层（不同断层类型有不同线宽）

    参数:
        fault_lines (list): 该类型的断裂线列表，每项为[(lon, lat),...]
        fault_type (str): 断裂类型字符串

    返回:
        QgsVectorLayer或None
    """
    if not fault_lines:
        return None

    # 根据类型选择颜色、线宽和图层名称
    style_map = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH_MM, "全新世断层"),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM, "晚更新世断层"),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM, "早中更新世断层"),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH_MM, "其他断层"),
    }
    color, width_mm, layer_name = style_map.get(
        fault_type, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH_MM, "断层")
    )

    layer = QgsVectorLayer("LineString?crs=EPSG:4326", layer_name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("type", QVariant.String)])
    layer.updateFields()

    features = []
    for line_coords in fault_lines:
        if len(line_coords) < 2:
            continue
        points = [QgsPointXY(lon, lat) for lon, lat in line_coords]
        geom = QgsGeometry.fromPolylineXY(points)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("type", fault_type)
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(color)
    line_sl.setWidth(width_mm)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

    print(f"[信息] 创建{layer_name}图层，共 {len(features)} 条线，线宽 {width_mm}mm")
    return layer


# ============================================================
# 统计函数
# ============================================================

def generate_statistics(filtered_quakes, radius_km):
    """
    生成历史地震统计信息

    参数:
        filtered_quakes (list): 筛选后地震记录（已包含本次地震）
        radius_km (float): 筛选半径

    返回:
        str: 统计信息文本
    """
    ct = len(filtered_quakes)
    c1 = sum(1 for e in filtered_quakes if 4.7 <= e["magnitude"] <= 5.9)
    c2 = sum(1 for e in filtered_quakes if 6.0 <= e["magnitude"] <= 6.9)
    c3 = sum(1 for e in filtered_quakes if 7.0 <= e["magnitude"] <= 7.9)
    c4 = sum(1 for e in filtered_quakes if e["magnitude"] >= 8.0)
    mx = max(filtered_quakes, key=lambda e: e["magnitude"]) if filtered_quakes else None

    txt = (f"自1900年以来，本次地震震中{int(radius_km)}km范围内"
           f"共发生{ct}次4.7级以上地震，"
           f"其中4.7~5.9级地震{c1}次，6.0~6.9级地震{c2}次，"
           f"7.0~7.9级地震{c3}次，8.0级以上地震{c4}次。")
    if mx:
        y = mx.get("year", 0)
        m = mx.get("month", 0)
        d = mx.get("day", 0)
        if y > 0 and m > 0 and d > 0:
            date_s = f"{y}年{m}月{d}日"
        elif y > 0 and m > 0:
            date_s = f"{y}年{m}月"
        elif y > 0:
            date_s = f"{y}年"
        else:
            date_s = ""
        txt += f"最大地震为{date_s}{mx.get('location', '')}{mx['magnitude']}级地震"
    return txt


# ============================================================
# 布局创建函数
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale,
                        map_height_mm, ordered_layers=None, has_faults=True):
    """
    创建QGIS打印布局

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺
        map_height_mm (float): 地图高度（毫米）
        ordered_layers (list): 按渲染顺序排列的图层列表（第一项在最上层）
        has_faults (bool): 是否包含断裂线图例

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("历史地震分布图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

    # 设置页面尺寸
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm,
                                   QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    # 添加地图项
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top, QgsUnitTypes.LayoutMillimeters))
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

    # 设置地图渲染图层列表（setKeepLayerSet确保使用自定义列表，震中五角星在最上层）
    layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
    if layers_to_set:
        map_item.setLayers(layers_to_set)
        map_item.setKeepLayerSet(True)
    map_item.invalidateCache()

    # 经纬度网格
    _setup_map_grid(map_item, extent)
    # 指北针（地图右上角）
    _add_north_arrow(layout, map_height_mm)
    # 图例（右侧独立区域，含比例尺）
    _add_legend(layout, map_height_mm, has_faults, scale=scale, extent=extent,
                center_lat=latitude)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
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

    # 顶部和左侧显示经纬度标注
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
    添加指北针（地图右上角）

    参数:
        layout (QgsPrintLayout): 打印布局
        map_height_mm (float): 地图高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    # 创建指北针SVG并添加图片项
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_map_temp.svg")
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
    print("[信息] 指北针添加完成")


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """
    【已废弃】添加比例尺（地图右下角）
    比例尺已移至图例区底部，由 _add_legend 函数绘制。
    保留此函数以兼容旧调用，但不再被 create_print_layout 调用。

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图布局项
        scale (int): 比例尺
        extent (QgsRectangle): 地图范围
        center_lat (float): 中心纬度
        map_height_mm (float): 地图高度（毫米）
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

    # 比例尺背景白色矩形
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

    # 比例尺分母标签
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

    # 黑白交替刻度条
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


def _add_legend(layout, map_height_mm, has_faults=True, scale=None, extent=None, center_lat=None):
    """
    添加图例（位于右侧独立区域）
    - 图例左边框紧接底图右边框
    - 图例上下与底图对齐
    - 底部包含比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_height_mm (float): 底图高度（毫米）
        has_faults (bool): 是否包含断裂线图例
        scale (int): 比例尺分母（用于绘制比例尺）
        extent (QgsRectangle): 地图范围（用于计算比例尺）
        center_lat (float): 地图中心纬度（用于计算比例尺）
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

    # 中文图例项字体（SimSun，用于行政边界、断层等）
    item_format_cn = QgsTextFormat()
    item_format_cn.setFont(QFont("SimSun", LEGEND_ITEM_FONT_SIZE_PT))
    item_format_cn.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format_cn.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format_cn.setColor(QColor(0, 0, 0))

    # 英文/数字图例项字体（Times New Roman，用于震级数字和"~"）
    item_format_en = QgsTextFormat()
    item_format_en.setFont(QFont("Times New Roman", LEGEND_ITEM_FONT_SIZE_PT))
    item_format_en.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format_en.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format_en.setColor(QColor(0, 0, 0))

    # 图例背景矩形（白色实心，与地图等高）
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

    # 图例标题 "图  例"
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + 1.0,  # 1.0mm 上内边距
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(legend_width, 5.0,  # 标题行高 5.0mm
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # 图例项起始Y坐标（标题1mm上偏+5mm高度+1mm间距=7mm）
    current_y = legend_y + 7.0
    icon_x = legend_x + LEGEND_PADDING_MM
    icon_center_offset = LEGEND_ROW_HEIGHT_MM / 2.0
    text_x = icon_x + LEGEND_ICON_WIDTH_MM + LEGEND_ICON_TEXT_GAP_MM
    text_width = legend_width - LEGEND_PADDING_MM - LEGEND_ICON_WIDTH_MM - LEGEND_ICON_TEXT_GAP_MM - 1.0

    def _add_cn_label(label, y):
        """绘制中文图例文字标签（单列）"""
        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(label)
        lbl.setTextFormat(item_format_cn)
        lbl.attemptMove(QgsLayoutPoint(text_x, y, QgsUnitTypes.LayoutMillimeters))
        lbl.attemptResize(QgsLayoutSize(text_width, LEGEND_ROW_HEIGHT_MM,
                                        QgsUnitTypes.LayoutMillimeters))
        lbl.setHAlign(Qt.AlignLeft)
        lbl.setVAlign(Qt.AlignVCenter)
        lbl.setFrameEnabled(False)
        lbl.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl)

    def _add_mixed_label(num_text, cn_text, y):
        """绘制混合字体的震级标签：数字和"~"用Times New Roman，中文用SimSun"""
        # 数字部分宽度估算（Times New Roman 10pt，每字符约1.6mm，最小5.0mm）
        num_width = max(len(num_text) * 1.6, 5.0)
        cn_width = text_width - num_width

        num_lbl = QgsLayoutItemLabel(layout)
        num_lbl.setText(num_text)
        num_lbl.setTextFormat(item_format_en)
        num_lbl.attemptMove(QgsLayoutPoint(text_x, y, QgsUnitTypes.LayoutMillimeters))
        num_lbl.attemptResize(QgsLayoutSize(num_width, LEGEND_ROW_HEIGHT_MM,
                                            QgsUnitTypes.LayoutMillimeters))
        num_lbl.setHAlign(Qt.AlignLeft)
        num_lbl.setVAlign(Qt.AlignVCenter)
        num_lbl.setFrameEnabled(False)
        num_lbl.setBackgroundEnabled(False)
        layout.addLayoutItem(num_lbl)

        cn_lbl = QgsLayoutItemLabel(layout)
        cn_lbl.setText(cn_text)
        cn_lbl.setTextFormat(item_format_cn)
        cn_lbl.attemptMove(QgsLayoutPoint(text_x + num_width, y, QgsUnitTypes.LayoutMillimeters))
        cn_lbl.attemptResize(QgsLayoutSize(cn_width, LEGEND_ROW_HEIGHT_MM,
                                           QgsUnitTypes.LayoutMillimeters))
        cn_lbl.setHAlign(Qt.AlignLeft)
        cn_lbl.setVAlign(Qt.AlignVCenter)
        cn_lbl.setFrameEnabled(False)
        cn_lbl.setBackgroundEnabled(False)
        layout.addLayoutItem(cn_lbl)

    # ── 1~2. 两列基本图例项（震中位置、地级市、省界、市界、县界）──
    # 布局：左列第1行=震中位置, 右列第1行=地级市,
    #       左列第2行=省界, 右列第2行=市界,
    #       左列第3行=县界（右列第3行空）
    col_count = 2
    col_width = (legend_width - 2 * LEGEND_PADDING_MM) / col_count
    basic_icon_width = 4.0
    basic_icon_height = 2.5
    basic_icon_text_gap = 1.0

    basic_items = [
        ("震中", "star"),
        ("地级市",  "city_dot"),
        ("省界",    "solid_line"),
        ("市界",    "dash_city"),
        ("县界",    "dash_county"),
    ]

    basic_rows = (len(basic_items) + col_count - 1) // col_count  # = 3
    for idx, (label, draw_type) in enumerate(basic_items):
        row = idx // col_count
        col = idx % col_count
        item_x = legend_x + LEGEND_PADDING_MM + col * col_width
        item_y = current_y + row * LEGEND_ROW_HEIGHT_MM
        icon_center_y = item_y + LEGEND_ROW_HEIGHT_MM / 2.0

        if draw_type == "star":
            _draw_legend_star(layout, item_x, icon_center_y, basic_icon_width, basic_icon_height)
        elif draw_type == "city_dot":
            _draw_legend_city_dot(layout, item_x, icon_center_y, basic_icon_width)
        elif draw_type == "solid_line":
            _draw_legend_line(layout, item_x, icon_center_y, basic_icon_width,
                              PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM)
        elif draw_type == "dash_city":
            _draw_legend_dash_line(layout, item_x, icon_center_y, basic_icon_width,
                                   CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_county":
            _draw_legend_dash_line(layout, item_x, icon_center_y, basic_icon_width,
                                   COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)

        item_text_x = item_x + basic_icon_width + basic_icon_text_gap
        item_text_width = col_width - basic_icon_width - basic_icon_text_gap

        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(label)
        lbl.setTextFormat(item_format_cn)
        lbl.attemptMove(QgsLayoutPoint(item_text_x, item_y, QgsUnitTypes.LayoutMillimeters))
        lbl.attemptResize(QgsLayoutSize(item_text_width, LEGEND_ROW_HEIGHT_MM,
                                        QgsUnitTypes.LayoutMillimeters))
        lbl.setHAlign(Qt.AlignLeft)
        lbl.setVAlign(Qt.AlignVCenter)
        lbl.setFrameEnabled(False)
        lbl.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl)

    current_y += basic_rows * LEGEND_ROW_HEIGHT_MM

    # ── 3. 断裂线（可选，单列）──
    if has_faults:
        for label, color, line_width in [
            ("全新世断层",    FAULT_HOLOCENE_COLOR,       FAULT_HOLOCENE_WIDTH_MM),
            ("晚更新世断层",  FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM),
            ("早中更新世断层", FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM),
        ]:
            icon_center_y = current_y + icon_center_offset
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM, color, line_width)
            _add_cn_label(label, current_y)
            current_y += LEGEND_ROW_HEIGHT_MM

    # ── 4. 震级标题 ──
    mag_title_format = QgsTextFormat()
    mag_title_format.setFont(QFont("SimHei", 10))
    mag_title_format.setSize(10)
    mag_title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    mag_title_format.setColor(QColor(0, 0, 0))

    mag_title_label = QgsLayoutItemLabel(layout)
    mag_title_label.setText("震  级")
    mag_title_label.setTextFormat(mag_title_format)
    mag_title_label.attemptMove(QgsLayoutPoint(legend_x, current_y, QgsUnitTypes.LayoutMillimeters))
    mag_title_label.attemptResize(QgsLayoutSize(legend_width, LEGEND_ROW_HEIGHT_MM,
                                                QgsUnitTypes.LayoutMillimeters))
    mag_title_label.setHAlign(Qt.AlignHCenter)
    mag_title_label.setVAlign(Qt.AlignVCenter)
    mag_title_label.setFrameEnabled(False)
    mag_title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(mag_title_label)
    current_y += LEGEND_ROW_HEIGHT_MM

    # ── 5. 震级图例项（混合字体：数字/~用Times New Roman，中文用SimSun）──
    mag_items = [
        ("8.0",    "级以上", 4),
        ("7.0~7.9", "级",   3),
        ("6.0~6.9", "级",   2),
        ("4.7~5.9", "级",   1),
    ]
    for num_text, cn_text, lv in mag_items:
        icon_center_y = current_y + icon_center_offset
        _draw_legend_circle(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                            EARTHQUAKE_LEVEL_CONFIG[lv]["color"],
                            EARTHQUAKE_LEVEL_CONFIG[lv]["size_mm"])
        _add_mixed_label(num_text, cn_text, current_y)
        current_y += LEGEND_ROW_HEIGHT_MM

    n_basic = len(basic_items) + (3 if has_faults else 0)
    n_mag = len(mag_items)

    # ── 6. 比例尺（位于图例内容下方）──
    if scale is not None and extent is not None and center_lat is not None:
        lon_range_deg = extent.xMaximum() - extent.xMinimum()
        map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))
        km_per_mm = map_total_km / MAP_WIDTH_MM if MAP_WIDTH_MM > 0 else 1.0
        target_bar_km = MAP_WIDTH_MM * 0.18 * km_per_mm

        nice_values = [1, 2, 5, 10, 20, 50, 100, 200, 500]
        bar_km = nice_values[0]
        for nv in nice_values:
            if nv <= target_bar_km * 1.5:
                bar_km = nv
            else:
                break

        bar_length_mm = bar_km / km_per_mm if km_per_mm > 0 else 20.0
        bar_length_mm = max(bar_length_mm, 20.0)
        num_segments = 4

        # 标准尺寸
        std_bar_width = bar_length_mm + 16.0
        std_bar_height = 14.0

        # 图例区可用宽度（左右各留 2mm）
        avail_width = legend_width - 4.0
        if std_bar_width > avail_width:
            scale_factor = avail_width / std_bar_width
            std_bar_width = avail_width
            bar_length_mm *= scale_factor
            std_bar_height *= scale_factor
        else:
            scale_factor = 1.0

        # 比例尺垂直位置：距底部留 4mm 空间
        sb_height = std_bar_height
        sb_y = legend_y + legend_height - sb_height - 4.0
        sb_x = legend_x + (legend_width - std_bar_width) / 2.0

        # 比例尺分母文字
        scale_font_size = SCALE_FONT_SIZE_PT
        scale_tf = QgsTextFormat()
        scale_tf.setFont(QFont("Times New Roman", scale_font_size))
        scale_tf.setSize(scale_font_size)
        scale_tf.setSizeUnit(QgsUnitTypes.RenderPoints)
        scale_tf.setColor(QColor(0, 0, 0))

        lbl_scale = QgsLayoutItemLabel(layout)
        lbl_scale.setText(f"1:{scale:,}")
        lbl_scale.setTextFormat(scale_tf)
        lbl_scale.attemptMove(QgsLayoutPoint(sb_x, sb_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        lbl_scale.attemptResize(QgsLayoutSize(std_bar_width, 4.5 * scale_factor,
                                              QgsUnitTypes.LayoutMillimeters))
        lbl_scale.setHAlign(Qt.AlignHCenter)
        lbl_scale.setVAlign(Qt.AlignVCenter)
        lbl_scale.setFrameEnabled(False)
        lbl_scale.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_scale)

        # 黑白交替刻度条
        bar_start_x = sb_x + (std_bar_width - bar_length_mm) / 2.0
        bar_y = sb_y + 5.5 * scale_factor
        bar_h = 1.8 * scale_factor
        seg_width_mm = bar_length_mm / num_segments

        for i in range(num_segments):
            seg_shape = QgsLayoutItemShape(layout)
            seg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
            seg_x = bar_start_x + i * seg_width_mm
            seg_shape.attemptMove(QgsLayoutPoint(seg_x, bar_y, QgsUnitTypes.LayoutMillimeters))
            seg_shape.attemptResize(QgsLayoutSize(seg_width_mm, bar_h,
                                                  QgsUnitTypes.LayoutMillimeters))
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
        tick_tf = QgsTextFormat()
        tick_tf.setFont(QFont("Times New Roman", scale_font_size))
        tick_tf.setSize(scale_font_size)
        tick_tf.setSizeUnit(QgsUnitTypes.RenderPoints)
        tick_tf.setColor(QColor(0, 0, 0))

        label_y = bar_y + bar_h + 0.3
        label_h = 3.5 * scale_factor

        lbl_0 = QgsLayoutItemLabel(layout)
        lbl_0.setText("0")
        lbl_0.setTextFormat(tick_tf)
        lbl_0.attemptMove(QgsLayoutPoint(bar_start_x - 1.5, label_y,
                                         QgsUnitTypes.LayoutMillimeters))
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
            lbl_mid.setTextFormat(tick_tf)
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
        lbl_end.setTextFormat(tick_tf)
        end_x = bar_start_x + bar_length_mm - 4.0
        lbl_end.attemptMove(QgsLayoutPoint(end_x, label_y, QgsUnitTypes.LayoutMillimeters))
        lbl_end.attemptResize(QgsLayoutSize(14.0, label_h, QgsUnitTypes.LayoutMillimeters))
        lbl_end.setHAlign(Qt.AlignHCenter)
        lbl_end.setVAlign(Qt.AlignTop)
        lbl_end.setFrameEnabled(False)
        lbl_end.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_end)

        print(f"[信息] 比例尺添加到图例区完成，1:{scale:,}")

    print(f"[信息] 图例添加完成，共 {n_basic + n_mag + 1} 项（含震级标题）")


def _draw_legend_star(layout, x, center_y, width, height):
    """
    在图例中绘制红色五角星图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标宽度
        height (float): 图标高度
    """
    star_label = QgsLayoutItemLabel(layout)
    star_label.setText("★")
    star_format = QgsTextFormat()
    star_font_size = LEGEND_TITLE_FONT_SIZE_PT + 4
    star_format.setFont(QFont("SimSun", star_font_size))
    star_format.setSize(star_font_size)
    star_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    star_format.setColor(EPICENTER_COLOR)
    star_label.setTextFormat(star_format)
    star_label.attemptMove(QgsLayoutPoint(x, center_y - height / 2.0,
                                          QgsUnitTypes.LayoutMillimeters))
    star_label.attemptResize(QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters))
    star_label.setHAlign(Qt.AlignHCenter)
    star_label.setVAlign(Qt.AlignVCenter)
    star_label.setFrameEnabled(False)
    star_label.setBackgroundEnabled(False)
    layout.addLayoutItem(star_label)


def _draw_legend_city_dot(layout, x, center_y, width):
    """
    在图例中绘制地级市黑色实心圆点图标（双层圆圈结构）

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标区域宽度
    """
    # 使用图标区域的60%作为外圆大小
    icon_size = width * 0.6
    center_x = x + width / 2.0

    # 外层圆圈：白色填充+黑色边框
    outer_circle = QgsLayoutItemShape(layout)
    outer_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    outer_circle.attemptMove(QgsLayoutPoint(center_x - icon_size / 2.0,
                                            center_y - icon_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    outer_circle.attemptResize(QgsLayoutSize(icon_size, icon_size,
                                             QgsUnitTypes.LayoutMillimeters))
    outer_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': '0.15',
        'outline_width_unit': 'MM',
    })
    outer_circle.setSymbol(outer_symbol)
    outer_circle.setFrameEnabled(False)
    layout.addLayoutItem(outer_circle)

    # 内层圆点：黑色实心，大小为外圆的40%
    inner_size = icon_size * 0.4
    inner_circle = QgsLayoutItemShape(layout)
    inner_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    inner_circle.attemptMove(QgsLayoutPoint(center_x - inner_size / 2.0,
                                            center_y - inner_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    inner_circle.attemptResize(QgsLayoutSize(inner_size, inner_size,
                                             QgsUnitTypes.LayoutMillimeters))
    inner_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    inner_circle.setSymbol(inner_symbol)
    inner_circle.setFrameEnabled(False)
    layout.addLayoutItem(inner_circle)


def _draw_legend_line(layout, x, center_y, width, color, line_width_mm):
    """
    在图例中绘制实线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽（毫米）
    """
    line_height = line_width_mm
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    line_shape.attemptMove(QgsLayoutPoint(x, center_y - line_height / 2.0,
                                          QgsUnitTypes.LayoutMillimeters))
    line_shape.attemptResize(QgsLayoutSize(width, line_height, QgsUnitTypes.LayoutMillimeters))
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_style': 'no',
    })
    line_shape.setSymbol(line_symbol)
    line_shape.setFrameEnabled(False)
    layout.addLayoutItem(line_shape)


def _draw_legend_dash_line(layout, x, center_y, width, color, line_width_mm, dash_gap_mm):
    """
    在图例中绘制虚线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标总宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽（毫米）
        dash_gap_mm (float): 虚线间隔（毫米）
    """
    line_height = max(line_width_mm, 0.5)
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    dash_length_mm = max(dash_gap_mm * 3.5, 0.8)
    pattern_length = dash_length_mm + dash_gap_mm

    current_x = x
    while current_x < x + width:
        actual_dash = min(dash_length_mm, x + width - current_x)
        if actual_dash <= 0:
            break
        dash_shape = QgsLayoutItemShape(layout)
        dash_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_height / 2.0,
                                              QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(QgsLayoutSize(actual_dash, line_height,
                                               QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)
        current_x += pattern_length


def _draw_legend_circle(layout, x, center_y, width, color, size_mm):
    """
    在图例中绘制圆形图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标区域宽度
        color (QColor): 圆形填充颜色
        size_mm (float): 地图上的圆点大小（毫米），图例中适当缩放
    """
    # 图例中圆点大小限制在行高的80%以内
    circle_size = min(size_mm, LEGEND_ROW_HEIGHT_MM * 0.8)
    center_x = x + width / 2.0

    circle_shape = QgsLayoutItemShape(layout)
    circle_shape.setShapeType(QgsLayoutItemShape.Ellipse)
    circle_shape.attemptMove(QgsLayoutPoint(center_x - circle_size / 2.0,
                                            center_y - circle_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    circle_shape.attemptResize(QgsLayoutSize(circle_size, circle_size,
                                              QgsUnitTypes.LayoutMillimeters))
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    circle_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_color': '0,0,0,255',
        'outline_width': '0.15',
        'outline_width_unit': 'MM',
    })
    circle_shape.setSymbol(circle_symbol)
    circle_shape.setFrameEnabled(False)
    layout.addLayoutItem(circle_shape)


# ============================================================
# PNG导出函数
# ============================================================

def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出文件路径
        dpi (int): 输出分辨率（默认150）

    返回:
        str: 成功时返回输出文件路径，失败返回None
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
# 主函数
# ============================================================

def generate_earthquake_map(center_lon, center_lat, magnitude, csv_path,
                            output_path, csv_encoding="gbk",
                            basemap_path=None, annotation_path=None):
    """
    生成历史地震分布图（基于QGIS 3.40.15）

    包含内容：
    - 天地图矢量底图（vec_c）+ 矢量注记（cva_c）
    - 省界、市界、县界图层
    - 断裂线图层（全新世/晚更新世/早中更新世，不同线宽）
    - 历史地震圆点（按震级分4级，不同大小和颜色）
    - 震中五角星
    - 经纬度网格、指北针、比例尺
    - 图例（底图左下角，左/下边框与底图对齐）

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        magnitude (float): 震级
        csv_path (str): 历史地震CSV文件路径
        output_path (str): 输出PNG文件路径
        csv_encoding (str): CSV文件编码

    返回:
        str: 统计信息文本
    """
    logger.info('开始生成历史地震分布图: lon=%.4f lat=%.4f M=%.1f csv=%s output=%s',
                center_lon, center_lat, magnitude, csv_path, output_path)
    try:
        return _generate_earthquake_map_impl(
            center_lon, center_lat, magnitude, csv_path, output_path, csv_encoding,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成历史地震分布图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_map_impl(center_lon, center_lat, magnitude, csv_path,
                                   output_path, csv_encoding,
                                   basemap_path=None, annotation_path=None):
    """generate_earthquake_map 的实际实现（由 generate_earthquake_map 调用）。"""
    print("=" * 65)
    print("  历 史 地 震 分 布 图 生 成 工 具（QGIS版）")
    print("=" * 65)
    print(f"  震中: {center_lon}°E, {center_lat}°N, M{magnitude}")

    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    radius_km = config["radius_km"]
    scale = config["scale"]
    print(f"  范围: {config['map_size_km']}km, 半径: {radius_km}km, 比例尺: 1:{scale:,}\n")

    # [1/8] 读取历史地震CSV
    print("[1/8] 读取历史地震数据...")
    if not os.path.exists(csv_path):
        print(f"  *** CSV不存在: {csv_path} ***")
        return ""
    earthquakes = read_earthquake_csv(csv_path, encoding=csv_encoding)
    print()

    # [2/8] 筛选范围内地震
    print("[2/8] 筛选范围内地震...")
    filtered = filter_earthquakes(earthquakes, center_lon, center_lat, radius_km)
    # 将本次地震加入统计列表
    today = datetime.date.today()
    current_quake = {
        "time_str": today.strftime("%Y/%m/%d"),
        "lon": center_lon, "lat": center_lat, "depth": 0.0,
        "location": "", "magnitude": magnitude,
        "year": today.year, "month": today.month, "day": today.day,
        "distance": 0.0,
    }
    filtered.append(current_quake)
    print()

    # [3/8] 计算地图范围
    print("[3/8] 计算地图范围...")
    extent = calculate_extent(center_lon, center_lat, half_size_km)
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"  地图范围: {extent.toString()}")
    print(f"  地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm\n")

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 临时文件路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    temp_basemap_path = os.path.join(script_dir, "_temp_basemap_earthquake.png")
    temp_annotation_path = os.path.join(script_dir, "_temp_annotation_earthquake.png")
    svg_temp_path = os.path.join(script_dir, "_north_arrow_map_temp.svg")

    result_path = None
    try:
        # [4/8] 下载天地图矢量底图（vec_c）+ 矢量注记（cva_c）
        print("[4/8] 下载天地图矢量底图+矢量注记...")
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)
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
            annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px,
                                                                    temp_annotation_path)
        print()

        # [5/8] 加载行政边界图层
        print("[5/8] 加载行政边界图层...")
        county_layer = load_vector_layer(SHP_COUNTY_PATH, "县界")
        if county_layer:
            style_county_layer(county_layer)
            project.addMapLayer(county_layer)

        city_layer = load_vector_layer(SHP_CITY_PATH, "市界")
        if city_layer:
            style_city_layer(city_layer)
            project.addMapLayer(city_layer)

        province_layer = load_vector_layer(SHP_PROVINCE_PATH, "省界")
        if province_layer:
            style_province_layer(province_layer, center_lon, center_lat, extent)
            project.addMapLayer(province_layer)

        # 创建省份标注点图层（支持震中附近偏移）
        province_label_layer = None
        if province_layer:
            try:
                province_label_layer = create_province_label_layer(
                    province_layer, center_lon, center_lat, extent)
                if province_label_layer:
                    project.addMapLayer(province_label_layer, False)
                    print(f"[信息] 省份标注图层已添加，要素数量: {province_label_layer.featureCount()}")
                else:
                    print("[警告] 省份标注图层创建失败，回退到直接配置标注")
                    _setup_province_labels(province_layer)
            except Exception as exc:
                logger.warning('创建省份标注图层失败: %s', exc)
                try:
                    _setup_province_labels(province_layer)
                except Exception as fallback_exc:
                    logger.warning('回退标注配置也失败: %s', fallback_exc)

        # 创建地级市点图层
        city_point_layer = None
        try:
            city_point_layer = create_city_point_layer(extent)
            if city_point_layer:
                project.addMapLayer(city_point_layer)
        except Exception as exc:
            logger.warning('加载地级市点位图层失败，跳过: %s', exc)
            print(f"[警告] 加载地级市点位图层失败，跳过: {exc}")
        print()

        # [6/8] 解析断裂KMZ并创建QGIS图层
        print("[6/8] 解析断裂KMZ并创建图层...")
        kmz_abs_path = resolve_path(KMZ_FAULT_PATH)
        geo_extent_dict = {
            "min_lon": extent.xMinimum(), "max_lon": extent.xMaximum(),
            "min_lat": extent.yMinimum(), "max_lat": extent.yMaximum(),
        }
        fault_data = parse_kmz_faults(kmz_abs_path, geo_extent_dict)
        has_faults = any(len(v) > 0 for v in fault_data.values())

        fault_layers = {}
        # 按从旧到新的顺序添加到项目，确保全新世断层在最上层渲染
        for ftype in ["default", "early_pleistocene", "late_pleistocene", "holocene"]:
            lines = fault_data.get(ftype, [])
            if lines:
                fl = create_fault_layer(lines, ftype)
                if fl:
                    fault_layers[ftype] = fl
                    project.addMapLayer(fl)
        print()

        # [7/8] 创建地震圆点和震中图层
        print("[7/8] 创建地震圆点和震中图层...")
        earthquake_layer = None
        if filtered:
            earthquake_layer = create_earthquake_layer(filtered)
            if earthquake_layer:
                project.addMapLayer(earthquake_layer)

        epicenter_layer = create_epicenter_layer(center_lon, center_lat)
        if epicenter_layer:
            project.addMapLayer(epicenter_layer)

        if basemap_raster:
            project.addMapLayer(basemap_raster)
        if annotation_raster:
            project.addMapLayer(annotation_raster)
        print()

        # 按渲染顺序排列图层（列表第一项在最上层渲染）
        # 图层顺序：震中 -> 地级市 -> 注记 -> 地震点 -> 断裂线 -> 行政边界 -> 底图
        ordered_layers = []
        # 震中五角星（最顶层）
        if epicenter_layer:
            ordered_layers.append(epicenter_layer)
        # 地级市点（震中五角星下方）
        if city_point_layer:
            ordered_layers.append(city_point_layer)
        # 天地图矢量注记
        if annotation_raster:
            ordered_layers.append(annotation_raster)
        # 历史地震圆点
        if earthquake_layer:
            ordered_layers.append(earthquake_layer)
        # 断裂线（全新世在最上，早中更新世在下）
        for ftype in ["holocene", "late_pleistocene", "early_pleistocene", "default"]:
            if ftype in fault_layers:
                ordered_layers.append(fault_layers[ftype])
        # 行政边界（省份标注在省界上层，市界 -> 县界）
        if province_label_layer:
            ordered_layers.append(province_label_layer)
        if province_layer:
            ordered_layers.append(province_layer)
        if city_layer:
            ordered_layers.append(city_layer)
        if county_layer:
            ordered_layers.append(county_layer)
        # 天地图矢量底图（最底层）
        if basemap_raster:
            ordered_layers.append(basemap_raster)

        # [8/8] 创建打印布局并导出PNG
        print("[8/8] 创建布局并导出PNG...")
        layout = create_print_layout(
            project, center_lon, center_lat, magnitude,
            extent, scale, map_height_mm,
            ordered_layers=ordered_layers,
            has_faults=has_faults,
        )
        result_path = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    finally:
        # 清理临时文件
        if not basemap_path and os.path.exists(temp_basemap_path):
            try:
                os.remove(temp_basemap_path)
                pgw = temp_basemap_path.replace(".png", ".pgw")
                if os.path.exists(pgw):
                    os.remove(pgw)
            except OSError:
                pass
        if not annotation_path and os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw):
                    os.remove(pgw)
            except OSError:
                pass
        # 清理指北针临时SVG文件
        if os.path.exists(svg_temp_path):
            try:
                os.remove(svg_temp_path)
            except OSError:
                pass

    if result_path:
        print(f"  输出: {result_path}")

    stat_text = generate_statistics(filtered, radius_km)
    print("=" * 65)
    print("【统计信息】")
    print(stat_text)
    print("=" * 65)
    return stat_text


# ============================================================
# 脚本入口
# ============================================================

if __name__ == "__main__":
    # 震中经度（度）
    INPUT_LON = 122.06
    # 震中纬度（度）
    INPUT_LAT = 24.67
    # 震级（M）
    INPUT_MAGNITUDE = 7.6
    # CSV路径
    INPUT_CSV_PATH = r"../../data/geology/历史地震CSV文件.csv"
    # 输出路径
    OUTPUT_PATH = r"../../data/geology/output_earthquake_map.png"

    stat_result = generate_earthquake_map(
        center_lon=INPUT_LON,
        center_lat=INPUT_LAT,
        magnitude=INPUT_MAGNITUDE,
        csv_path=INPUT_CSV_PATH,
        output_path=OUTPUT_PATH,
        csv_encoding="gbk",
    )

    print('*' * 65)
    print(stat_result)
