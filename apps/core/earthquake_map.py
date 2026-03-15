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
import requests
from io import BytesIO
from lxml import etree
from PIL import Image

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
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

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
# 地图内容宽度（图例叠加在底图内部，不占额外列）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - BORDER_RIGHT_MM

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
LONLAT_FONT_SIZE_PT = 8

# ============================================================
# 行政边界样式
# ============================================================
# 省界：深灰色实线
PROVINCE_COLOR = QColor(60, 60, 60)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

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
FAULT_HOLOCENE_COLOR = QColor(255, 50, 50)
FAULT_HOLOCENE_WIDTH_MM = 0.5
# 晚更新世断层：品红色，中等
FAULT_LATE_PLEISTOCENE_COLOR = QColor(255, 0, 255)
FAULT_LATE_PLEISTOCENE_WIDTH_MM = 0.35
# 早中更新世断层：绿色，最细
FAULT_EARLY_PLEISTOCENE_COLOR = QColor(0, 200, 100)
FAULT_EARLY_PLEISTOCENE_WIDTH_MM = 0.2
# 其他断层
FAULT_DEFAULT_COLOR = QColor(255, 200, 50)
FAULT_DEFAULT_WIDTH_MM = 0.25

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
# 图例配置（底图左下角）
# ============================================================
# 图例宽度（毫米）
LEGEND_WIDTH_MM = 42.0
# 图例标题字体大小（磅）
LEGEND_TITLE_FONT_SIZE_PT = 10
# 图例项目字体大小（磅）
LEGEND_ITEM_FONT_SIZE_PT = 8
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
# 数据文件路径（相对于脚本所在目录）
# ============================================================
SHP_PROVINCE_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国省份行政区划数据/省级行政区划/省.shp"
)
SHP_CITY_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国市级行政区划数据/市级行政区划/市.shp"
)
SHP_COUNTY_PATH = (
    "../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    "/全国县级行政区划数据/县级行政区划/县.shp"
)
KMZ_FAULT_PATH = "../../data/geology/断层/全国六代图断裂.KMZ"

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
            if gg > 150 and rr < 100 and bb < 100:
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
# 天地图瓦片下载函数
# ============================================================

def download_tianditu_basemap_tiles(extent, width_px, height_px, output_path):
    """
    下载天地图矢量底图瓦片（vec_c）并拼接为本地栅格图像

    参数:
        extent (QgsRectangle): 渲染范围（WGS84）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        output_path (str): 输出文件路径

    返回:
        QgsRasterLayer或None
    """
    tk = TIANDITU_TK
    lon_range = extent.xMaximum() - extent.xMinimum()
    zoom = int(math.log2(360 / lon_range * width_px / 256))
    zoom = max(1, min(zoom, 18))
    print(f"[信息] 下载天地图矢量底图瓦片，缩放级别: {zoom}")

    def lon_to_tile_x(lon, z):
        """经度转瓦片X坐标"""
        n = 2 ** z
        x = int((lon + 180.0) / 360.0 * n)
        return max(0, min(n - 1, x))

    def lat_to_tile_y(lat, z):
        """纬度转瓦片Y坐标（天地图c系列）"""
        n = 2 ** (z - 1)
        y = int((90.0 - lat) / 180.0 * n)
        return max(0, min(n - 1, y))

    def tile_x_to_lon(x, z):
        """瓦片X坐标转经度（左边界）"""
        n = 2 ** z
        return x / n * 360.0 - 180.0

    def tile_y_to_lat(y, z):
        """瓦片Y坐标转纬度（上边界）"""
        n = 2 ** (z - 1)
        return 90.0 - y / n * 180.0

    tile_x_min = lon_to_tile_x(extent.xMinimum(), zoom)
    tile_x_max = lon_to_tile_x(extent.xMaximum(), zoom)
    tile_y_min = lat_to_tile_y(extent.yMaximum(), zoom)
    tile_y_max = lat_to_tile_y(extent.yMinimum(), zoom)

    if tile_y_min > tile_y_max:
        tile_y_min, tile_y_max = tile_y_max, tile_y_min

    num_tiles_x = tile_x_max - tile_x_min + 1
    num_tiles_y = tile_y_max - tile_y_min + 1
    print(f"[信息] 需要下载 {num_tiles_x * num_tiles_y} 个底图瓦片 ({num_tiles_x} x {num_tiles_y})")

    tile_size = 256
    mosaic_width = num_tiles_x * tile_size
    mosaic_height = num_tiles_y * tile_size
    # 底色使用浅蓝色模拟海洋，陆地由矢量瓦片覆盖
    mosaic = Image.new('RGB', (mosaic_width, mosaic_height), (170, 211, 223))

    downloaded = 0
    failed = 0
    servers = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']

    for ty in range(tile_y_min, tile_y_max + 1):
        for tx in range(tile_x_min, tile_x_max + 1):
            server = servers[(tx + ty) % len(servers)]
            vec_url = (
                f"http://{server}.tianditu.gov.cn/vec_c/wmts?"
                f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                f"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
                f"&FORMAT=tiles&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                f"&tk={tk}"
            )
            try:
                resp = requests.get(vec_url, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 0:
                    tile_img = Image.open(BytesIO(resp.content)).convert('RGB')
                    paste_x = (tx - tile_x_min) * tile_size
                    paste_y = (ty - tile_y_min) * tile_size
                    mosaic.paste(tile_img, (paste_x, paste_y))
                    downloaded += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"[警告] 底图瓦片下载异常: {tx},{ty} - {e}")

    print(f"[信息] 底图瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

    if downloaded == 0:
        print("[错误] 没有成功下载任何底图瓦片")
        return None

    actual_lon_min = tile_x_to_lon(tile_x_min, zoom)
    actual_lon_max = tile_x_to_lon(tile_x_max + 1, zoom)
    actual_lat_max = tile_y_to_lat(tile_y_min, zoom)
    actual_lat_min = tile_y_to_lat(tile_y_max + 1, zoom)

    crop_left = int((extent.xMinimum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
    crop_right = int((extent.xMaximum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
    crop_top = int((actual_lat_max - extent.yMaximum()) / (actual_lat_max - actual_lat_min) * mosaic_height)
    crop_bottom = int((actual_lat_max - extent.yMinimum()) / (actual_lat_max - actual_lat_min) * mosaic_height)

    crop_left = max(0, min(mosaic_width - 1, crop_left))
    crop_right = max(crop_left + 1, min(mosaic_width, crop_right))
    crop_top = max(0, min(mosaic_height - 1, crop_top))
    crop_bottom = max(crop_top + 1, min(mosaic_height, crop_bottom))

    cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
    final_image = cropped.resize((width_px, height_px), Image.LANCZOS)
    final_image.save(output_path, 'PNG')
    print(f"[信息] 底图已保存: {output_path}")

    # 生成World文件，使GDAL能正确关联地理坐标
    world_file_path = output_path.replace(".png", ".pgw")
    x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
    y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
    with open(world_file_path, 'w') as f:
        f.write(f"{x_res}\n0\n0\n{-y_res}\n{extent.xMinimum()}\n{extent.yMaximum()}\n")

    raster_layer = QgsRasterLayer(output_path, "天地图底图", "gdal")
    if raster_layer.isValid():
        print("[信息] 成功加载底图栅格图层")
        return raster_layer
    else:
        print("[错误] 无法加载底图栅格图层")
        return None


def download_tianditu_annotation_tiles(extent, width_px, height_px, output_path):
    """
    下载天地图矢量注记瓦片（cva_c）并拼接为本地栅格图像（透明背景）

    参数:
        extent (QgsRectangle): 渲染范围（WGS84）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        output_path (str): 输出文件路径

    返回:
        QgsRasterLayer或None
    """
    tk = TIANDITU_TK
    lon_range = extent.xMaximum() - extent.xMinimum()
    zoom = int(math.log2(360 / lon_range * width_px / 256))
    zoom = max(1, min(zoom, 18))
    print(f"[信息] 下载天地图注记瓦片，缩放级别: {zoom}")

    def lon_to_tile_x(lon, z):
        """经度转瓦片X坐标"""
        n = 2 ** z
        x = int((lon + 180.0) / 360.0 * n)
        return max(0, min(n - 1, x))

    def lat_to_tile_y(lat, z):
        """纬度转瓦片Y坐标（天地图c系列）"""
        n = 2 ** (z - 1)
        y = int((90.0 - lat) / 180.0 * n)
        return max(0, min(n - 1, y))

    def tile_x_to_lon(x, z):
        """瓦片X坐标转经度（左边界）"""
        n = 2 ** z
        return x / n * 360.0 - 180.0

    def tile_y_to_lat(y, z):
        """瓦片Y坐标转纬度（上边界）"""
        n = 2 ** (z - 1)
        return 90.0 - y / n * 180.0

    tile_x_min = lon_to_tile_x(extent.xMinimum(), zoom)
    tile_x_max = lon_to_tile_x(extent.xMaximum(), zoom)
    tile_y_min = lat_to_tile_y(extent.yMaximum(), zoom)
    tile_y_max = lat_to_tile_y(extent.yMinimum(), zoom)

    if tile_y_min > tile_y_max:
        tile_y_min, tile_y_max = tile_y_max, tile_y_min

    num_tiles_x = tile_x_max - tile_x_min + 1
    num_tiles_y = tile_y_max - tile_y_min + 1
    total_tiles = num_tiles_x * num_tiles_y
    print(f"[信息] 需要下载 {total_tiles} 个注记瓦片 ({num_tiles_x} x {num_tiles_y})")

    tile_size = 256
    mosaic_width = num_tiles_x * tile_size
    mosaic_height = num_tiles_y * tile_size
    # 使用RGBA模式，支持透明背景
    mosaic = Image.new('RGBA', (mosaic_width, mosaic_height), (0, 0, 0, 0))

    downloaded = 0
    failed = 0
    servers = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']

    for ty in range(tile_y_min, tile_y_max + 1):
        for tx in range(tile_x_min, tile_x_max + 1):
            server = servers[(tx + ty) % len(servers)]
            cva_url = (
                f"http://{server}.tianditu.gov.cn/cva_c/wmts?"
                f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                f"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
                f"&FORMAT=tiles&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                f"&tk={tk}"
            )
            try:
                resp = requests.get(cva_url, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 0:
                    tile_cva = Image.open(BytesIO(resp.content)).convert('RGBA')
                    paste_x = (tx - tile_x_min) * tile_size
                    paste_y = (ty - tile_y_min) * tile_size
                    mosaic.paste(tile_cva, (paste_x, paste_y), tile_cva)
                    downloaded += 1
                else:
                    failed += 1
                    print(f"[警告] 注记瓦片下载失败: {tx},{ty} - 状态码: {resp.status_code}")
            except Exception as e:
                failed += 1
                print(f"[警告] 注记瓦片下载异常: {tx},{ty} - {e}")

    print(f"[信息] 注记瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

    if downloaded == 0:
        print("[错误] 没有成功下载任何注记瓦片")
        return None

    actual_lon_min = tile_x_to_lon(tile_x_min, zoom)
    actual_lon_max = tile_x_to_lon(tile_x_max + 1, zoom)
    actual_lat_max = tile_y_to_lat(tile_y_min, zoom)
    actual_lat_min = tile_y_to_lat(tile_y_max + 1, zoom)

    crop_left = int((extent.xMinimum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
    crop_right = int((extent.xMaximum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
    crop_top = int((actual_lat_max - extent.yMaximum()) / (actual_lat_max - actual_lat_min) * mosaic_height)
    crop_bottom = int((actual_lat_max - extent.yMinimum()) / (actual_lat_max - actual_lat_min) * mosaic_height)

    crop_left = max(0, min(mosaic_width - 1, crop_left))
    crop_right = max(crop_left + 1, min(mosaic_width, crop_right))
    crop_top = max(0, min(mosaic_height - 1, crop_top))
    crop_bottom = max(crop_top + 1, min(mosaic_height, crop_bottom))

    cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
    final_image = cropped.resize((width_px, height_px), Image.LANCZOS)
    final_image.save(output_path, 'PNG')
    print(f"[信息] 注记底图已保存: {output_path}")

    # 生成World文件
    world_file_path = output_path.replace(".png", ".pgw")
    x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
    y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
    with open(world_file_path, 'w') as f:
        f.write(f"{x_res}\n0\n0\n{-y_res}\n{extent.xMinimum()}\n{extent.yMaximum()}\n")

    raster_layer = QgsRasterLayer(output_path, "天地图注记", "gdal")
    if raster_layer.isValid():
        print("[信息] 成功加载注记栅格图层")
        return raster_layer
    else:
        print("[错误] 无法加载注记栅格图层")
        return None


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


def style_province_layer(layer):
    """
    设置省界图层样式（深灰色实线+省名标注）

    参数:
        layer (QgsVectorLayer): 省界图层
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
           f"共发生{ct}次4.7级以上地震（含本次），"
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

    # 设置地图渲染图层列表
    layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
    if layers_to_set:
        map_item.setLayers(layers_to_set)
    map_item.invalidateCache()

    # 经纬度网格
    _setup_map_grid(map_item, extent)
    # 指北针（地图右上角）
    _add_north_arrow(layout, map_height_mm)
    # 比例尺（地图右下角）
    _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)
    # 图例（底图左下角）
    _add_legend(layout, map_left, map_top, map_height_mm, has_faults)

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

    # 指北针背景白色矩形
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
    添加比例尺（地图右下角）

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


def _add_legend(layout, map_left_mm, map_top_mm, map_height_mm, has_faults=True):
    """
    添加图例（位于底图左下角）
    - 图例左边框与底图左边框对齐
    - 图例下边框与底图下边框对齐

    参数:
        layout (QgsPrintLayout): 打印布局
        map_left_mm (float): 底图左边界X坐标（毫米）
        map_top_mm (float): 底图顶部Y坐标（毫米）
        map_height_mm (float): 底图高度（毫米）
        has_faults (bool): 是否包含断裂线图例
    """
    # 构建图例项列表（顺序与原来保持一致）
    legend_items = []
    legend_items.append(("震中位置", "star"))
    legend_items.append(("省界", "solid_line_province"))
    legend_items.append(("市界", "dash_line_city"))
    legend_items.append(("县界", "dash_line_county"))
    if has_faults:
        legend_items.append(("全新世断层", "solid_line_holocene"))
        legend_items.append(("晚更新世断层", "solid_line_late_pleistocene"))
        legend_items.append(("早中更新世断层", "solid_line_early_pleistocene"))
    # 地震等级从大到小排列
    legend_items.append(("8.0级以上", "circle_lv4"))
    legend_items.append(("7.0~7.9级", "circle_lv3"))
    legend_items.append(("6.0~6.9级", "circle_lv2"))
    legend_items.append(("4.7~5.9级", "circle_lv1"))

    n_items = len(legend_items)
    # 计算图例总高度
    title_height_mm = 6.0
    legend_height_mm = title_height_mm + n_items * LEGEND_ROW_HEIGHT_MM + LEGEND_PADDING_MM * 2

    # 图例位置：左边框=底图左边框，下边框=底图下边框
    legend_x = map_left_mm
    legend_y = map_top_mm + map_height_mm - legend_height_mm

    # 图例背景矩形（半透明白色）
    legend_bg = QgsLayoutItemShape(layout)
    legend_bg.setShapeType(QgsLayoutItemShape.Rectangle)
    legend_bg.attemptMove(QgsLayoutPoint(legend_x, legend_y, QgsUnitTypes.LayoutMillimeters))
    legend_bg.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM, legend_height_mm,
                                           QgsUnitTypes.LayoutMillimeters))
    legend_bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,230',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    legend_bg.setSymbol(legend_bg_symbol)
    legend_bg.setFrameEnabled(True)
    legend_bg.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend_bg)

    # 图例标题 "图  例"
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + LEGEND_PADDING_MM,
                                           QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(LEGEND_WIDTH_MM, title_height_mm - 1.0,
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # 图例项文本格式
    item_format = QgsTextFormat()
    item_format.setFont(QFont("SimSun", LEGEND_ITEM_FONT_SIZE_PT))
    item_format.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format.setColor(QColor(0, 0, 0))

    item_start_y = legend_y + LEGEND_PADDING_MM + title_height_mm
    icon_x = legend_x + LEGEND_PADDING_MM
    icon_center_offset = LEGEND_ROW_HEIGHT_MM / 2.0

    for idx, (label, draw_type) in enumerate(legend_items):
        item_y = item_start_y + idx * LEGEND_ROW_HEIGHT_MM
        icon_center_y = item_y + icon_center_offset

        # 根据类型绘制对应图标
        if draw_type == "star":
            _draw_legend_star(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                              LEGEND_ROW_HEIGHT_MM * 0.8)
        elif draw_type == "solid_line_province":
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                              PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM)
        elif draw_type == "dash_line_city":
            _draw_legend_dash_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                   CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_line_county":
            _draw_legend_dash_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                   COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)
        elif draw_type == "solid_line_holocene":
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                              FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH_MM)
        elif draw_type == "solid_line_late_pleistocene":
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                              FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM)
        elif draw_type == "solid_line_early_pleistocene":
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                              FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM)
        elif draw_type == "circle_lv4":
            _draw_legend_circle(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                EARTHQUAKE_LEVEL_CONFIG[4]["color"],
                                EARTHQUAKE_LEVEL_CONFIG[4]["size_mm"])
        elif draw_type == "circle_lv3":
            _draw_legend_circle(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                EARTHQUAKE_LEVEL_CONFIG[3]["color"],
                                EARTHQUAKE_LEVEL_CONFIG[3]["size_mm"])
        elif draw_type == "circle_lv2":
            _draw_legend_circle(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                EARTHQUAKE_LEVEL_CONFIG[2]["color"],
                                EARTHQUAKE_LEVEL_CONFIG[2]["size_mm"])
        elif draw_type == "circle_lv1":
            _draw_legend_circle(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM,
                                EARTHQUAKE_LEVEL_CONFIG[1]["color"],
                                EARTHQUAKE_LEVEL_CONFIG[1]["size_mm"])

        # 绘制文字标签
        text_x = icon_x + LEGEND_ICON_WIDTH_MM + LEGEND_ICON_TEXT_GAP_MM
        text_width = LEGEND_WIDTH_MM - LEGEND_PADDING_MM - LEGEND_ICON_WIDTH_MM - LEGEND_ICON_TEXT_GAP_MM - 1.0
        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(label)
        text_label.setTextFormat(item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, LEGEND_ROW_HEIGHT_MM,
                                               QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    print(f"[信息] 图例添加完成，共 {n_items} 项")


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
    line_height = max(line_width_mm, 0.5)
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
                            output_path, csv_encoding="gbk"):
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

    # 初始化QGIS应用
    if not QgsApplication.instance():
        qgs_app = QgsApplication([], False)
        qgs_app.initQgis()
        print("[信息] QGIS应用初始化完成")

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
        basemap_raster = download_tianditu_basemap_tiles(extent, width_px, height_px, temp_basemap_path)
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
            style_province_layer(province_layer)
            project.addMapLayer(province_layer)
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
        # 图层顺序：震中 -> 注记 -> 地震点 -> 断裂线 -> 行政边界 -> 底图
        ordered_layers = []
        # 震中五角星（最顶层）
        if epicenter_layer:
            ordered_layers.append(epicenter_layer)
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
        # 行政边界（省界 -> 市界 -> 县界）
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
        for tmp_path in [temp_basemap_path, temp_annotation_path]:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    pgw = tmp_path.replace(".png", ".pgw")
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
    INPUT_MAGNITUDE = 8.8
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
