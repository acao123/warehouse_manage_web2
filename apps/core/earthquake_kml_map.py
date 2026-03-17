# -*- coding: utf-8 -*-
"""
地震烈度图生成脚本（基于QGIS 3.40.15）
功能：根据用户输入的KML烈度圈文件，绘制地震烈度分布图，
      叠加天地图底图、省界、市界、县界、断裂图层，
      带经纬度边框、指北针、比例尺、图例、说明文字，并输出PNG图片。

依赖：QGIS 3.40.15 Python环境
作者：acao123
日期：2026-03-15
"""

import os
import sys
import math
import unicodedata
import zipfile
import logging
import requests
import re
from io import BytesIO
from lxml import etree
from datetime import datetime
from PIL import Image

# ============================================================
# Django settings 导入（可选）
# ============================================================
try:
    from django.conf import settings as _django_settings
    _DJANGO_AVAILABLE = True
except ImportError:
    _django_settings = None
    _DJANGO_AVAILABLE = False

from core.tianditu_basemap_downloader import (
    download_tianditu_basemap_tiles,
    download_tianditu_annotation_tiles,
)

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.earthquake_kml_map')
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
# 【配置常量区域】
# ============================================================

# 天地图密钥
TIANDITU_TK = (
    getattr(_django_settings, 'TIANDITU_TK', '1ef76ef90c6eb961cb49618f9b1a399d')
    if _DJANGO_AVAILABLE else '1ef76ef90c6eb961cb49618f9b1a399d'
)

# ============================================================
# 布局尺寸常量（参考 earthquake_map.py）
# ============================================================
# 输出图总宽度（毫米）
MAP_TOTAL_WIDTH_MM = 280.0
# 左边距（毫米）
BORDER_LEFT_MM = 4.0
# 上边距（毫米）
BORDER_TOP_MM = 4.0
# 下边距（毫米）
BORDER_BOTTOM_MM = 2.0
# 右边距（毫米）
BORDER_RIGHT_MM = 1.0

# 右侧说明文字区域宽度（毫米）
INFO_PANEL_WIDTH_MM = 55.0

# 底部图例区域高度（毫米）
LEGEND_HEIGHT_MM = 28.0

# 地图内容宽度（毫米）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - BORDER_RIGHT_MM - INFO_PANEL_WIDTH_MM

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
# 【SHP文件路径常量】（优先从 Django settings 读取）
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

# ============================================================
# 【字体路径常量】
# ============================================================
FONT_PATH_HEITI = "SimHei"
FONT_PATH_SONGTI = "SimSun"
FONT_PATH_TIMES = "Times New Roman"

# ============================================================
# 【字体大小常量】
# ============================================================
INFO_TEXT_FONT_SIZE_PT = 10
LEGEND_TITLE_FONT_SIZE_PT = 12
LEGEND_ITEM_FONT_SIZE_PT = 10
DATE_FONT_SIZE_PT = 10
INTENSITY_LABEL_FONT_SIZE_PT = 10
SCALE_FONT_SIZE_PT = 10
# 比例尺缩小时字体最小值（磅）
MIN_SCALE_FONT_SIZE_PT = 10

# ============================================================
# 【行政边界线样式】
# ============================================================
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

CITY_COLOR = QColor(100, 100, 100)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_GAP_MM = 0.3

COUNTY_COLOR = QColor(180, 180, 180)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_GAP_MM = 0.2

# ============================================================
# 【断裂线样式】
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
# 【烈度圈颜色配置】
# ============================================================
INTENSITY_COLORS = {
    4: QColor(0, 150, 255),
    5: QColor(0, 200, 100),
    6: QColor(255, 200, 0),
    7: QColor(255, 150, 0),
    8: QColor(255, 80, 0),
    9: QColor(255, 0, 0),
    10: QColor(200, 0, 50),
    11: QColor(150, 0, 100),
    12: QColor(100, 0, 150),
}

INTENSITY_LINE_WIDTH_MM = 0.6

# ============================================================
# 【震中标记样式】
# ============================================================
EPICENTER_STAR_SIZE_MM = 6.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 【工具函数】
# ============================================================

def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字

    参数:
        num (int): 阿拉伯数字（1-12）
    返回:
        str: 罗马数字字符串
    """
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


def get_scale_by_magnitude(magnitude):
    """
    根据震级动态获取比例尺分母

    参数:
        magnitude (float): 震级
    返回:
        int: 比例尺分母
    """
    if magnitude < 6.0:
        return 150000
    elif magnitude < 7.0:
        return 500000
    else:
        return 1500000


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
    return range_deg / 5.0


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


def calculate_polygon_area(coords):
    """
    使用鞋带公式计算多边形面积（平方千米）

    参数:
        coords (list): 坐标列表 [(lon, lat), ...]
    返回:
        float: 面积（平方千米）
    """
    if len(coords) < 3:
        return 0.0
    center_lat = sum(c[1] for c in coords) / len(coords)
    km_coords = []
    for lon, lat in coords:
        x = lon * 111.32 * math.cos(math.radians(center_lat))
        y = lat * 110.574
        km_coords.append((x, y))
    n = len(km_coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += km_coords[i][0] * km_coords[j][1]
        area -= km_coords[j][0] * km_coords[i][1]
    return abs(area) / 2.0


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
# 【KML烈度圈解析函数】
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件获取烈度圈数据

    参数:
        kml_path (str): KML文件路径
    返回:
        dict: {烈度值: [(lon, lat), ...], ...}
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
            name = _get_element_text(pm, 'name', nsmap, ns)
            intensity = _extract_intensity_from_name(name)
            if intensity is None:
                continue
            coords = _extract_linestring_coords(pm, nsmap, ns)
            if coords:
                intensity_data[intensity] = coords
                print(f"    烈度 {intensity}度: {len(coords)} 个坐标点")

    except Exception as e:
        print(f"  *** KML解析失败: {e} ***")

    return intensity_data


def _get_element_text(elem, tag, nsmap, ns):
    """获取KML元素的文本内容"""
    for pattern in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(pattern, nsmap) if 'kml:' in pattern else elem.find(pattern)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _extract_intensity_from_name(name):
    """从名称中提取烈度值"""
    if not name:
        return None
    match = re.search(r'(\d+)\s*度', name)
    if match:
        return int(match.group(1))
    try:
        return int(name.strip())
    except ValueError:
        return None


def _extract_linestring_coords(pm, nsmap, ns):
    """从Placemark中提取LineString坐标"""
    coords = []
    ls_elems = []
    for tag in [f'kml:LineString', f'{{{ns}}}LineString', 'LineString']:
        try:
            found = pm.findall('.//' + tag, nsmap) if 'kml:' in tag else pm.findall('.//' + tag)
            ls_elems.extend(found)
        except Exception:
            pass

    for ls in ls_elems:
        coord_text = ""
        for ctag in [f'kml:coordinates', f'{{{ns}}}coordinates', 'coordinates']:
            try:
                ce = ls.find(ctag, nsmap) if 'kml:' in ctag else ls.find(ctag)
            except Exception:
                ce = None
            if ce is not None and ce.text:
                coord_text = ce.text.strip()
                break
        if coord_text:
            coords = _parse_coordinates(coord_text)
    return coords


def _parse_coordinates(text):
    """解析KML coordinates文本"""
    coords = []
    for part in text.replace('\n', ' ').replace('\t', ' ').split():
        fields = part.strip().split(',')
        if len(fields) >= 2:
            try:
                lon = float(fields[0])
                lat = float(fields[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def calculate_geo_extent_from_intensity(intensity_data, margin_ratio=0.15):
    """
    根据烈度圈数据计算地理范围

    参数:
        intensity_data (dict): 烈度圈数据
        margin_ratio (float): 边距比例
    返回:
        QgsRectangle: 地理范围
    """
    all_lons = []
    all_lats = []
    for intensity, coords in intensity_data.items():
        for lon, lat in coords:
            all_lons.append(lon)
            all_lats.append(lat)

    if not all_lons:
        return None

    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)

    lon_margin = (max_lon - min_lon) * margin_ratio
    lat_margin = (max_lat - min_lat) * margin_ratio

    return QgsRectangle(
        min_lon - lon_margin,
        min_lat - lat_margin,
        max_lon + lon_margin,
        max_lat + lat_margin
    )


def calculate_epicenter(intensity_data):
    """
    根据烈度圈数据计算震中位置

    参数:
        intensity_data (dict): 烈度圈数据
    返回:
        tuple: (lon, lat) 震中经纬度
    """
    if not intensity_data:
        return None, None
    max_intensity = max(intensity_data.keys())
    coords = intensity_data[max_intensity]
    if not coords:
        return None, None
    center_lon = sum(c[0] for c in coords) / len(coords)
    center_lat = sum(c[1] for c in coords) / len(coords)
    return center_lon, center_lat


# ============================================================
# 【KMZ断裂解析函数】
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent):
    """
    解析KMZ断裂线

    参数:
        kmz_path (str): KMZ文件路径
        geo_extent (QgsRectangle): 地理范围
    返回:
        dict: {"holocene":[], "late_pleistocene":[], "early_pleistocene":[], "default":[]}
    """
    empty = {"holocene": [], "late_pleistocene": [], "early_pleistocene": [], "default": []}
    if not os.path.exists(kmz_path):
        print(f"  *** KMZ不存在: {kmz_path} ***")
        return empty

    el = (geo_extent.xMaximum() - geo_extent.xMinimum()) * 0.3
    ea = (geo_extent.yMaximum() - geo_extent.yMinimum()) * 0.3
    ext = (
        geo_extent.xMinimum() - el, geo_extent.xMaximum() + el,
        geo_extent.yMinimum() - ea, geo_extent.yMaximum() + ea
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
    print(f"  断裂统计: 全新世={len(result['holocene'])}, 晚更新世={len(result['late_pleistocene'])}, "
          f"早中更新世={len(result['early_pleistocene'])}, 其他={len(result['default'])}, 总计={total}")
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

    style_colors = _parse_kml_styles(root, nsmap, ns)
    folder_types = _parse_folder_structure(root, nsmap, ns)

    pms = root.findall('.//kml:Placemark', nsmap)
    if not pms:
        pms = root.findall('.//{' + ns + '}Placemark')
    if not pms:
        pms = root.findall('.//Placemark')

    for pm in pms:
        name = _get_element_text(pm, 'name', nsmap, ns)
        surl = _get_element_text(pm, 'styleUrl', nsmap, ns)
        desc = _get_element_text(pm, 'description', nsmap, ns)
        parent_folder_type = _get_parent_folder_type(pm, folder_types, nsmap, ns)
        ftype = _classify_fault_enhanced(name, surl, desc, style_colors, parent_folder_type)

        for lc in _extract_all_linestring_coords(pm, nsmap, ns):
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
    """解析KML的Folder结构"""
    folder_types = {}
    folders = root.findall('.//kml:Folder', nsmap)
    if not folders:
        folders = root.findall('.//{' + ns + '}Folder')
    if not folders:
        folders = root.findall('.//Folder')

    for folder in folders:
        folder_name = _get_element_text(folder, 'name', nsmap, ns)
        ftype = _classify_by_folder_name(folder_name)
        if ftype:
            folder_types[folder] = ftype
    return folder_types


def _classify_by_folder_name(folder_name):
    """根据Folder名称分类断层类型"""
    if not folder_name:
        return None
    name_lower = folder_name.lower()
    if "全新世" in folder_name or "holocene" in name_lower:
        return "holocene"
    if "晚更新世" in folder_name or "late pleistocene" in name_lower or "晚更新" in folder_name:
        return "late_pleistocene"
    if any(k in folder_name for k in ["早中更新世", "早更新世", "中更新世", "早-中更新世"]):
        return "early_pleistocene"
    return None


def _get_parent_folder_type(pm, folder_types, nsmap, ns):
    """获取Placemark所属Folder的断层类型"""
    parent = pm.getparent()
    while parent is not None:
        if parent in folder_types:
            return folder_types[parent]
        tag_name = parent.tag.split('}')[-1] if '}' in parent.tag else parent.tag
        if tag_name == 'Folder':
            folder_name = _get_element_text(parent, 'name', nsmap, ns)
            ftype = _classify_by_folder_name(folder_name)
            if ftype:
                return ftype
        parent = parent.getparent()
    return None


def _parse_kml_styles(root, nsmap, ns):
    """解析KML样式（含StyleMap）"""
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
                if ls is None:
                    continue
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
                    key_text = _get_element_text(pair, 'key', nsmap, ns)
                    if key_text == 'normal':
                        su = _get_element_text(pair, 'styleUrl', nsmap, ns)
                        if su in sc:
                            sc['#' + sid] = sc[su]
                        break
    return sc


def _classify_fault_enhanced(name, style_url, description, style_colors, parent_folder_type):
    """增强的断裂分类函数（含颜色推断）"""
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


def _extract_all_linestring_coords(pm, nsmap, ns):
    """提取所有LineString坐标"""
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
            pts = _parse_coordinates(ct)
            if pts:
                all_lines.append(pts)
    return all_lines

# ============================================================
# 【矢量图层加载与样式设置函数】
# ============================================================

def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）

    参数:
        shp_path (str): SHP文件路径
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
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """设置市界图层样式"""
    symbol = QgsFillSymbol()
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(CITY_COLOR)
    fill_sl.setStrokeWidth(CITY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.DashLine)
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
    print("[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """设置县界图层样式"""
    symbol = QgsFillSymbol()
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))
    fill_sl.setStrokeColor(COUNTY_COLOR)
    fill_sl.setStrokeWidth(COUNTY_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.DashLine)
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
    print("[信息] 县界图层样式设置完成")


# ============================================================
# 【QGIS矢量图层创建函数】
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


def create_intensity_layer(intensity_data):
    """
    创建烈度圈矢量图层

    参数:
        intensity_data (dict): 烈度圈数据 {烈度值: [(lon, lat), ...]}
    返回:
        QgsVectorLayer: 烈度圈图层
    """
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "烈度圈", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([
        QgsField("intensity", QVariant.Int),
        QgsField("label", QVariant.String),
    ])
    layer.updateFields()

    features = []
    for intensity, coords in intensity_data.items():
        if len(coords) < 2:
            continue
        points = [QgsPointXY(lon, lat) for lon, lat in coords]
        # 闭合曲线
        if points[0] != points[-1]:
            points.append(points[0])
        geom = QgsGeometry.fromPolylineXY(points)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttribute("intensity", intensity)
        feat.setAttribute("label", f"{int_to_roman(intensity)}")
        features.append(feat)

    provider.addFeatures(features)
    layer.updateExtents()

    # 按烈度分类渲染
    categories = []
    for intensity in sorted(intensity_data.keys()):
        color = INTENSITY_COLORS.get(intensity, QColor(255, 0, 0))
        line_sl = QgsSimpleLineSymbolLayer()
        line_sl.setColor(color)
        line_sl.setWidth(INTENSITY_LINE_WIDTH_MM)
        line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
        line_sl.setPenStyle(Qt.SolidLine)
        symbol = QgsLineSymbol()
        symbol.changeSymbolLayer(0, line_sl)
        category = QgsRendererCategory(intensity, symbol, f"{int_to_roman(intensity)}度")
        categories.append(category)

    renderer = QgsCategorizedSymbolRenderer("intensity", categories)
    layer.setRenderer(renderer)

    # 设置标注（沿线标注）
    settings = QgsPalLayerSettings()
    settings.fieldName = "label"
    settings.placement = Qgis.LabelPlacement.Curved

    text_format = QgsTextFormat()
    font = QFont(FONT_PATH_TIMES, INTENSITY_LABEL_FONT_SIZE_PT)
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

    layer.triggerRepaint()
    print(f"[信息] 创建烈度圈图层，共 {len(features)} 条")
    return layer


def create_fault_layer(fault_lines, fault_type):
    """
    创建断裂线矢量图层

    参数:
        fault_lines (list): 断裂线列表
        fault_type (str): 断裂类型
    返回:
        QgsVectorLayer或None
    """
    if not fault_lines:
        return None

    style_map = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH_MM, "全新世断层"),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM, "晚更新世断层"),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM, "早中更新世断层"),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH_MM, "其他断层"),
    }
    color, width_mm, layer_name = style_map.get(fault_type, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH_MM, "断层"))

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

    print(f"[信息] 创建{layer_name}图层，共 {len(features)} 条线")
    return layer


# ============================================================
# 【分析统计函数】
# ============================================================

def generate_analysis_text(intensity_data, areas):
    """
    生成分析文字

    参数:
        intensity_data (dict): 烈度圈数据
        areas (dict): 烈度面积统计
    返回:
        str: 分析文字
    """
    if not intensity_data:
        return ""
    max_intensity = max(intensity_data.keys())
    max_area = areas.get(max_intensity, 0)
    vi_above_area = sum(areas.get(i, 0) for i in intensity_data.keys() if i >= 6)

    analysis = (f"预计极震区地震烈度可达{int_to_roman(max_intensity)}度，"
                f"极震区面积估算为{max_area:.0f}平方千米，"
                f"地震烈度VI度以上区域面积达{vi_above_area:.0f}平方千米。")
    return analysis


def calculate_intensity_areas(intensity_data):
    """
    计算各烈度圈面积

    参数:
        intensity_data (dict): 烈度圈数据
    返回:
        dict: {烈度: 面积}
    """
    areas = {}
    for intensity, coords in intensity_data.items():
        area = calculate_polygon_area(coords)
        areas[intensity] = area
        print(f"    烈度 {intensity}度: 面积约 {area:.1f} 平方千米")
    return areas


# ============================================================
# 【布局创建函数】
# ============================================================

def calculate_map_height_from_extent(extent, map_width_mm):
    """
    根据地图范围和宽度计算地图高度

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


def create_print_layout(project, extent, scale, map_height_mm, description_text,
                        intensity_data, ordered_layers=None, has_faults=True):
    """
    创建QGIS打印布局

    参数:
        project (QgsProject): QGIS项目实例
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺
        map_height_mm (float): 地图高度（毫米）
        description_text (str): 说明文字
        intensity_data (dict): 烈度圈数据
        ordered_layers (list): 按渲染顺序排列的图层列表
        has_faults (bool): 是否包含断裂线
    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震烈度分布图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    # 计算输出总高度
    output_height_mm = BORDER_TOP_MM + map_height_mm + LEGEND_HEIGHT_MM + BORDER_BOTTOM_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm, QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    # 添加地图项
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, map_height_mm, QgsUnitTypes.LayoutMillimeters))
    map_item.setExtent(extent)
    map_item.setCrs(CRS_WGS84)
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM, QgsUnitTypes.LayoutMillimeters))
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setBackgroundEnabled(True)
    map_item.setBackgroundColor(QColor(255, 255, 255))
    layout.addLayoutItem(map_item)

    if ordered_layers:
        map_item.setLayers(ordered_layers)
    map_item.invalidateCache()

    # 经纬度网格
    _setup_map_grid(map_item, extent)

    # 指北针（地图右上角）
    _add_north_arrow(layout, map_left, map_top, MAP_WIDTH_MM)

    # 右侧说明文字面板
    _add_info_panel(layout, map_left + MAP_WIDTH_MM, map_top, INFO_PANEL_WIDTH_MM,
                    map_height_mm, description_text, scale, extent)

    # 底部图例
    _add_legend(layout, map_left, map_top + map_height_mm, MAP_WIDTH_MM + INFO_PANEL_WIDTH_MM,
                LEGEND_HEIGHT_MM, intensity_data, has_faults)

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
    annot_font = QFont(FONT_PATH_TIMES, LONLAT_FONT_SIZE_PT)
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


def _add_north_arrow(layout, map_left, map_top, map_width):
    """添加指北针（地图右上角）"""
    arrow_x = map_left + map_width - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM, QgsUnitTypes.LayoutMillimeters))
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

    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_kml_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    padding = 1.0
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x + padding, arrow_y + 0.5, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM - padding * 2,
                                            NORTH_ARROW_HEIGHT_MM - 1.0, QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)
    print("[信息] 指北针添加完成")


def _wrap_text_for_panel(text, panel_width_mm, font_size_pt, left_margin_mm=2.0, right_margin_mm=1.0):
    """
    根据面板宽度对混合文本（汉字、英文、数字、经纬度符号等）进行自动折行处理。

    参数:
        text (str): 原始文本
        panel_width_mm (float): 面板宽度（毫米）
        font_size_pt (float): 字体大小（磅）
        left_margin_mm (float): 左边距（毫米）
        right_margin_mm (float): 右边距（毫米）
    返回:
        str: 折行后的文本
    """
    # 全角字符（汉字等）宽度 ≈ 字号 × (25.4/72) mm/pt ≈ 字号 × 0.353mm/pt
    # 半角字符（数字、英文字母、标点符号，含 ° 等 ASCII 符号）宽度约为全角字符的一半
    full_char_width_mm = font_size_pt * 0.353
    half_char_width_mm = full_char_width_mm * 0.5
    available_mm = panel_width_mm - left_margin_mm - right_margin_mm

    def _char_width(ch):
        """根据 Unicode 东亚字符宽度属性返回显示宽度（毫米）。
        'W'(Wide) 和 'F'(Fullwidth) 为全角字符（汉字、全角符号等），其余为半角。"""
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('W', 'F'):
            return full_char_width_mm
        return half_char_width_mm

    result_lines = []
    for paragraph in text.split('\n'):
        if not paragraph:
            result_lines.append('')
            continue
        line = ''
        line_width = 0.0
        for char in paragraph:
            cw = _char_width(char)
            if line_width + cw > available_mm and line:
                result_lines.append(line)
                line = char
                line_width = cw
            else:
                line += char
                line_width += cw
        if line:
            result_lines.append(line)
    return '\n'.join(result_lines)


def _add_info_panel(layout, x, y, width, height, description_text, scale, extent):
    """
    添加右侧说明文字面板（含折行文字和比例尺）

    参数:
        layout: 布局对象
        x, y: 左上角坐标
        width, height: 宽高
        description_text: 说明文字
        scale: 比例尺分母
        extent: 地图范围（QgsRectangle）
    """
    # 白色背景
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters))
    bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    bg_shape.setSymbol(bg_symbol)
    layout.addLayoutItem(bg_shape)

    # 说明文字（右侧边框留 1mm ≥ 5px@150DPI，即 5×25.4/150≈0.85mm，取整为 1mm）
    LEFT_MARGIN_MM = 2.0
    RIGHT_MARGIN_MM = 2.0

    # 处理首行缩进并自动折行
    indent = "　　"  # 两个全角空格缩进
    indented_text = indent + description_text.replace("\n", "\n" + indent)
    wrapped_text = _wrap_text_for_panel(
        indented_text, width, INFO_TEXT_FONT_SIZE_PT,
        left_margin_mm=LEFT_MARGIN_MM, right_margin_mm=RIGHT_MARGIN_MM
    )

    text_format = QgsTextFormat()
    text_format.setFont(QFont(FONT_PATH_SONGTI, INFO_TEXT_FONT_SIZE_PT))
    text_format.setSize(INFO_TEXT_FONT_SIZE_PT)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(0, 0, 0))

    # 底部预留：比例尺区域 + 日期 ≈ 30mm
    BOTTOM_RESERVED_MM = 30.0
    text_area_height = max(5.0, height - BOTTOM_RESERVED_MM - 2.0)

    text_label = QgsLayoutItemLabel(layout)
    text_label.setText(wrapped_text)
    text_label.setTextFormat(text_format)
    text_label.attemptMove(QgsLayoutPoint(x + LEFT_MARGIN_MM, y + 2.0,
                                          QgsUnitTypes.LayoutMillimeters))
    text_label.attemptResize(QgsLayoutSize(width - LEFT_MARGIN_MM - RIGHT_MARGIN_MM,
                                           text_area_height,
                                           QgsUnitTypes.LayoutMillimeters))
    text_label.setHAlign(Qt.AlignLeft)
    text_label.setVAlign(Qt.AlignTop)
    text_label.setFrameEnabled(False)
    text_label.setBackgroundEnabled(False)
    text_label.setMode(QgsLayoutItemLabel.ModeFont)
    layout.addLayoutItem(text_label)

    # ----------------------------------------------------------------
    # 比例尺（参考 earthquake_map.py _add_scale_bar，适配面板宽度）
    # ----------------------------------------------------------------
    center_lat = (extent.yMaximum() + extent.yMinimum()) / 2.0
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

    # 面板内可用宽度（左右各留 2mm）
    avail_width = width - 4.0
    if std_bar_width > avail_width:
        scale_factor = avail_width / std_bar_width
        std_bar_width = avail_width
        bar_length_mm *= scale_factor
        std_bar_height *= scale_factor
    else:
        scale_factor = 1.0

    # 比例尺垂直位置：位于日期标签上方，紧靠底部
    DATE_SECTION_MM = 12.0
    sb_height = std_bar_height
    sb_y = y + height - DATE_SECTION_MM - sb_height - 2.0
    sb_x = x + (width - std_bar_width) / 2.0

    # 比例尺分母文字
    scale_font_size = max(MIN_SCALE_FONT_SIZE_PT, int(SCALE_FONT_SIZE_PT * scale_factor))
    scale_tf = QgsTextFormat()
    scale_tf.setFont(QFont(FONT_PATH_TIMES, scale_font_size))
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
    tick_font_size = max(MIN_SCALE_FONT_SIZE_PT, int(SCALE_FONT_SIZE_PT * scale_factor))
    tick_tf = QgsTextFormat()
    tick_tf.setFont(QFont(FONT_PATH_TIMES, tick_font_size))
    tick_tf.setSize(tick_font_size)
    tick_tf.setSizeUnit(QgsUnitTypes.RenderPoints)
    tick_tf.setColor(QColor(0, 0, 0))

    label_y = bar_y + bar_h + 0.3
    label_h = 3.5 * scale_factor

    lbl_0 = QgsLayoutItemLabel(layout)
    lbl_0.setText("0")
    lbl_0.setTextFormat(tick_tf)
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

    # ----------------------------------------------------------------
    # 制图日期
    # ----------------------------------------------------------------
    date_format = QgsTextFormat()
    date_format.setFont(QFont(FONT_PATH_SONGTI, DATE_FONT_SIZE_PT))
    date_format.setSize(DATE_FONT_SIZE_PT)
    date_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    date_format.setColor(QColor(0, 0, 0))

    current_date = datetime.now()
    date_text = f"制图日期：{current_date.year}年{current_date.month:02d}月{current_date.day:02d}日"

    date_label = QgsLayoutItemLabel(layout)
    date_label.setText(date_text)
    date_label.setTextFormat(date_format)
    date_label.attemptMove(QgsLayoutPoint(x + LEFT_MARGIN_MM, y + height - 10.0,
                                          QgsUnitTypes.LayoutMillimeters))
    date_label.attemptResize(QgsLayoutSize(width - LEFT_MARGIN_MM - RIGHT_MARGIN_MM, 8.0,
                                           QgsUnitTypes.LayoutMillimeters))
    date_label.setHAlign(Qt.AlignLeft)
    date_label.setVAlign(Qt.AlignVCenter)
    date_label.setFrameEnabled(False)
    date_label.setBackgroundEnabled(False)
    layout.addLayoutItem(date_label)

    print(f"[信息] 右侧说明面板添加完成，比例尺 1:{scale:,}")


def _add_legend(layout, x, y, width, height, intensity_data, has_faults=True):
    """
    添加底部图例（动态行列布局）
    布局规则：
      - 烈度圈 ≤ 3 个时：两行五列
      - 烈度圈 ≤ 8 个时：三行五列
      - 烈度圈 > 8 个时：三行五列，仅展示烈度最大的 8 个

    参数:
        layout: 布局对象
        x, y: 左上角坐标
        width, height: 宽高
        intensity_data: 烈度圈数据
        has_faults: 是否有断裂数据
    """
    # 图例背景
    bg_shape = QgsLayoutItemShape(layout)
    bg_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    bg_shape.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    bg_shape.attemptResize(QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters))
    bg_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': str(BORDER_WIDTH_MM),
        'outline_width_unit': 'MM',
    })
    bg_shape.setSymbol(bg_symbol)
    layout.addLayoutItem(bg_shape)

    # 图例标题
    title_format = QgsTextFormat()
    title_format.setFont(QFont(FONT_PATH_HEITI, LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(x, y + 1, QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(width, 6, QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # 确定行列数和烈度显示数量
    n_intensities = len(intensity_data)
    if n_intensities <= 3:
        rows = 2
    else:
        rows = 3
    cols = 5

    # 按烈度从大到小排序，超过 8 个时只保留最大的 8 个
    sorted_intensities = sorted(intensity_data.keys(), reverse=True)
    if n_intensities > 8:
        sorted_intensities = sorted_intensities[:8]

    # 收集图例项
    legend_items = []
    legend_items.append(("震中", "epicenter"))

    for intensity in sorted_intensities:
        roman = int_to_roman(intensity)
        legend_items.append((f"{roman}度区", "intensity", intensity))

    if has_faults:
        legend_items.append(("全新世断层", "fault_holocene"))
        legend_items.append(("晚更新世断层", "fault_late"))
        legend_items.append(("早中更新世断层", "fault_early"))

    legend_items.append(("省界", "province"))
    legend_items.append(("市界", "city"))
    legend_items.append(("县界", "county"))

    # 裁剪到可用格数
    legend_items = legend_items[:rows * cols]

    col_width = width / cols
    row_height = (height - 8) / rows
    start_y = y + 8

    item_format = QgsTextFormat()
    item_format.setFont(QFont(FONT_PATH_SONGTI, LEGEND_ITEM_FONT_SIZE_PT))
    item_format.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format.setColor(QColor(0, 0, 0))

    icon_width = 8.0
    text_gap = 1.5

    for idx, item in enumerate(legend_items):
        row = idx // cols
        col = idx % cols

        cell_x = x + col * col_width + 2
        cell_y = start_y + row * row_height + row_height / 2

        item_type = item[1]
        label = item[0]

        # 绘制图标
        if item_type == "epicenter":
            _draw_legend_star(layout, cell_x, cell_y, icon_width, row_height * 0.8)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "intensity":
            intensity = item[2]
            color = INTENSITY_COLORS.get(intensity, QColor(255, 0, 0))
            _draw_legend_line(layout, cell_x, cell_y, icon_width, color, INTENSITY_LINE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "fault_holocene":
            _draw_legend_line(layout, cell_x, cell_y, icon_width, FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "fault_late":
            _draw_legend_line(layout, cell_x, cell_y, icon_width, FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "fault_early":
            _draw_legend_line(layout, cell_x, cell_y, icon_width, FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "province":
            _draw_legend_line(layout, cell_x, cell_y, icon_width, PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "city":
            _draw_legend_dash_line(layout, cell_x, cell_y, icon_width, CITY_COLOR, CITY_LINE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        elif item_type == "county":
            _draw_legend_dash_line(layout, cell_x, cell_y, icon_width, COUNTY_COLOR, COUNTY_LINE_WIDTH_MM)
            text_x = cell_x + icon_width + text_gap
        else:
            text_x = cell_x

        # 绘制文字
        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(label)
        text_label.setTextFormat(item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, cell_y - 2.5, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(col_width - icon_width - 6, 5, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    print(f"[信息] 图例添加完成，{rows}行{cols}列，共 {len(legend_items)} 项")


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
    star_format.setFont(QFont(FONT_PATH_SONGTI, star_font_size))
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
    """绘制图例线条"""
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    line_shape.attemptMove(QgsLayoutPoint(x, center_y - line_width_mm / 2, QgsUnitTypes.LayoutMillimeters))
    line_shape.attemptResize(QgsLayoutSize(width, max(line_width_mm, 0.5), QgsUnitTypes.LayoutMillimeters))
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_style': 'no',
    })
    line_shape.setSymbol(line_symbol)
    line_shape.setFrameEnabled(False)
    layout.addLayoutItem(line_shape)


def _draw_legend_dash_line(layout, x, center_y, width, color, line_width_mm):
    """绘制图例虚线"""
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    dash_length = 1.5
    gap_length = 0.8
    current_x = x
    while current_x < x + width:
        actual_dash = min(dash_length, x + width - current_x)
        if actual_dash <= 0:
            break
        dash_shape = QgsLayoutItemShape(layout)
        dash_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_width_mm / 2, QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(QgsLayoutSize(actual_dash, max(line_width_mm, 0.5), QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)
        current_x += dash_length + gap_length


def _draw_legend_circle(layout, x, center_y, size, color):
    """绘制图例圆点"""
    circle_shape = QgsLayoutItemShape(layout)
    circle_shape.setShapeType(QgsLayoutItemShape.Ellipse)
    circle_shape.attemptMove(QgsLayoutPoint(x, center_y - size / 2, QgsUnitTypes.LayoutMillimeters))
    circle_shape.attemptResize(QgsLayoutSize(size, size, QgsUnitTypes.LayoutMillimeters))
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
# 【PNG导出函数】
# ============================================================

def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出文件路径
        dpi (int): 输出分辨率
    返回:
        str: 成功时返回输出文件路径
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
# 【主函数】
# ============================================================

def generate_earthquake_kml_map(kml_path, description_text, magnitude, output_path,
                                basemap_path=None, annotation_path=None):
    """
    生成地震烈度分布图（基于QGIS 3.40.15）

    参数:
        kml_path (str): KML烈度圈文件路径
        description_text (str): 说明文字（不超过450字）
        magnitude (float): 震级（用于确定比例尺）
        output_path (str): 输出PNG文件路径
    返回:
        dict: 包含分析结果的字典
    """
    logger.info('开始生成地震烈度分布图: kml=%s M=%.1f output=%s',
                kml_path, magnitude, output_path)
    try:
        return _generate_earthquake_kml_map_impl(
            kml_path, description_text, magnitude, output_path,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成地震烈度分布图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_kml_map_impl(kml_path, description_text, magnitude, output_path,
                                      basemap_path=None, annotation_path=None):
    """generate_earthquake_kml_map 的实际实现。"""
    print("=" * 65)
    print("  地 震 烈 度 分 布 图 生 成 工 具（QGIS版）")
    print("=" * 65)

    # [1/9] 解析KML文件
    print("\n[1/9] 解析KML烈度圈文件...")
    intensity_data = parse_intensity_kml(kml_path)
    if not intensity_data:
        print("  *** 无法解析烈度圈数据 ***")
        return None

    # [2/9] 计算地理范围和震中
    print("\n[2/9] 计算地理范围...")
    extent = calculate_geo_extent_from_intensity(intensity_data)
    center_lon, center_lat = calculate_epicenter(intensity_data)
    print(f"  震中: {center_lon:.4f}°E, {center_lat:.4f}°N")

    scale_denom = get_scale_by_magnitude(magnitude)
    print(f"  震级: M{magnitude}, 比例尺: 1:{scale_denom:,}")

    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"  地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # [3/9] 计算烈度面积
    print("\n[3/9] 计算烈度面积...")
    areas = calculate_intensity_areas(intensity_data)

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from core.qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 临时文件路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    temp_basemap_path = os.path.join(script_dir, "_temp_basemap_kml.png")
    temp_annotation_path = os.path.join(script_dir, "_temp_annotation_kml.png")
    svg_temp_path = os.path.join(script_dir, "_north_arrow_kml_temp.svg")

    result_path = None
    try:
        # [4/9] 下载天地图底图
        print("\n[4/9] 下载天地图矢量底图...")
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
            annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px, temp_annotation_path)

        # [5/9] 加载行政边界图层
        print("\n[5/9] 加载行政边界图层...")
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

        # [6/9] 解析断裂并创建图层
        print("\n[6/9] 解析断裂KMZ...")
        kmz_abs_path = resolve_path(KMZ_FAULT_PATH)
        fault_data = parse_kmz_faults(kmz_abs_path, extent)
        has_faults = any(len(v) > 0 for v in fault_data.values())

        fault_layers = {}
        for ftype in ["default", "early_pleistocene", "late_pleistocene", "holocene"]:
            lines = fault_data.get(ftype, [])
            if lines:
                fl = create_fault_layer(lines, ftype)
                if fl:
                    fault_layers[ftype] = fl
                    project.addMapLayer(fl)

        # [7/9] 创建烈度圈和震中图层
        print("\n[7/9] 创建烈度圈和震中图层...")
        intensity_layer = create_intensity_layer(intensity_data)
        if intensity_layer:
            project.addMapLayer(intensity_layer)

        epicenter_layer = create_epicenter_layer(center_lon, center_lat)
        if epicenter_layer:
            project.addMapLayer(epicenter_layer)

        if basemap_raster:
            project.addMapLayer(basemap_raster)
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # [8/9] 构建图层顺序
        print("\n[8/9] 构建图层顺序...")
        ordered_layers = []
        if epicenter_layer:
            ordered_layers.append(epicenter_layer)
        if annotation_raster:
            ordered_layers.append(annotation_raster)
        if intensity_layer:
            ordered_layers.append(intensity_layer)
        for ftype in ["holocene", "late_pleistocene", "early_pleistocene", "default"]:
            if ftype in fault_layers:
                ordered_layers.append(fault_layers[ftype])
        if province_layer:
            ordered_layers.append(province_layer)
        if city_layer:
            ordered_layers.append(city_layer)
        if county_layer:
            ordered_layers.append(county_layer)
        if basemap_raster:
            ordered_layers.append(basemap_raster)

        # 生成分析文字
        analysis_text = generate_analysis_text(intensity_data, areas)
        full_description = description_text + analysis_text

        # [9/9] 创建布局并导出
        print("\n[9/9] 创建布局并导出PNG...")
        layout = create_print_layout(
            project, extent, scale_denom, map_height_mm, full_description,
            intensity_data, ordered_layers=ordered_layers, has_faults=has_faults
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
        if os.path.exists(svg_temp_path):
            try:
                os.remove(svg_temp_path)
            except OSError:
                pass

    if result_path:
        fsize = os.path.getsize(result_path) / 1024
        print(f"\n  已保存: {result_path}")
        print(f"  大小: {fsize:.1f} KB")

    result = {
        "max_intensity": max(intensity_data.keys()),
        "max_intensity_area": areas.get(max(intensity_data.keys()), 0),
        "vi_above_area": sum(areas.get(i, 0) for i in intensity_data.keys() if i >= 6),
        "center_lon": center_lon,
        "center_lat": center_lat,
        "analysis_text": analysis_text,
    }

    print("\n" + "=" * 65)
    print("【分析结果】")
    print(f"  极震区烈度: {int_to_roman(result['max_intensity'])}度")
    print(f"  极震区面积: {result['max_intensity_area']:.0f} 平方千米")
    print(f"  VI度以上面积: {result['vi_above_area']:.0f} 平方千米")
    print("=" * 65)

    return result


# ============================================================
# 【测试方法】
# ============================================================

def test_generate_earthquake_kml_map():
    """测试地震烈度图生成功能"""
    test_kml_path = r"../../data/geology/test_intensity.kml"
    test_description = (
        "据中国地震台网正式测定：2026年01月26日14时56分甘肃甘南州迭部县"
        "(103.25°，34.06°)发生5.5级地震，震源深度10千米。"
        "综合考虑震中附近地质构造背景、地震波衰减特性，"
        "估计了本次地震的地震动预测图。"
    )
    test_magnitude = 5.5
    test_output_path = r"../../data/geology/output_earthquake_kml_map_qgis.png"

    if not os.path.exists(test_kml_path):
        _create_test_kml(test_kml_path)

    result = generate_earthquake_kml_map(
        kml_path=test_kml_path,
        description_text=test_description,
        magnitude=test_magnitude,
        output_path=test_output_path
    )

    if result:
        print("\n【测试通过】")
        print(f"  输出文件: {test_output_path}")
    else:
        print("\n【测试失败】")


def _create_test_kml(kml_path):
    """创建测试用KML文件"""
    center_lon, center_lat = 103.25, 34.06

    def _generate_circle_coords(clon, clat, radius_deg, num_points=36):
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = clon + radius_deg * math.cos(angle)
            lat = clat + radius_deg * 0.8 * math.sin(angle)
            coords.append((lon, lat))
        coords.append(coords[0])
        return coords

    kml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>5度</name>
<description></description>
<LineString><coordinates>
'''
    coords_5 = _generate_circle_coords(center_lon, center_lat, 0.5)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_5]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>6度</name>
<description></description>
<LineString><coordinates>
'''
    coords_6 = _generate_circle_coords(center_lon, center_lat, 0.3)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_6]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>7度</name>
<description></description>
<LineString><coordinates>
'''
    coords_7 = _generate_circle_coords(center_lon, center_lat, 0.15)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_7]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
</Document>
</kml>'''

    os.makedirs(os.path.dirname(kml_path), exist_ok=True)
    with open(kml_path, 'w', encoding='utf-8') as f:
        f.write(kml_content)
    print(f"  创建测试KML文件: {kml_path}")


# ============================================================
# 【脚本入口】
# ============================================================

if __name__ == "__main__":
    INPUT_KML_PATH = r"../../data/geology/n0432881302350072.kml"
    INPUT_DESCRIPTION = (
        "据中国地震台网正式测定： 2026年01月26日14时56分甘肃甘南州迭部县"
        "(103.25°，34.06°)发生5.5级地震，震源深度10千米。"
        "综合考虑震中附近地质构造背景、地震波衰减特性，"
        "估计了本次地震的地震动预测图。"
    )
    INPUT_MAGNITUDE = 7.5
    OUTPUT_PATH = r"../../data/geology/output_earthquake_kml_map_qgis.png"

    if not os.path.exists(INPUT_KML_PATH):
        print("KML文件不存在，运行测试模式...")
        test_generate_earthquake_kml_map()
    else:
        result = generate_earthquake_kml_map(
            kml_path=INPUT_KML_PATH,
            description_text=INPUT_DESCRIPTION,
            magnitude=INPUT_MAGNITUDE,
            output_path=OUTPUT_PATH
        )