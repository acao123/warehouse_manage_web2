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
# 输出图总宽度上限（毫米）
MAP_TOTAL_MAX_WIDTH_MM = 170.0
# 左边距（毫米）
BORDER_LEFT_MM = 4.0
# 上边距（毫米）
# 设为 8.0mm，为地图框上方的经纬度标注（10pt字体约3.5mm + QGIS默认间距约2-3mm）留出足够空间，
# 防止 QGIS 在空间不足时将地图框向下偏移，导致图例区底部留白。
BORDER_TOP_MM = 8.0
# 下边距（毫米）
BORDER_BOTTOM_MM = 2.0
# 右边距（毫米）
BORDER_RIGHT_MM = 1.0
# 图例区宽度（毫米），图例位于右侧独立区域
LEGEND_WIDTH_MM = 50.0
# 固定地图内容区高度（毫米）
MAP_HEIGHT_MM = 100.0
# 地图内容区最大宽度（总宽上限减去左右边距和图例区）
MAP_MAX_WIDTH_MM = MAP_TOTAL_MAX_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM
# 行间距倍数（保留供其他模块参考，说明文字不使用）
LINE_SPACING_FACTOR = 1.5

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
# 【说明文字区域布局常量】
# ============================================================
# 说明文字与图例区上边框的距离（毫米）
DESCRIPTION_TOP_MARGIN_MM = 2.0
# 说明文字与图例区左右边框的距离（毫米）
DESCRIPTION_HORIZONTAL_MARGIN_MM = 2.0

# ============================================================
# 【SHP文件路径常量】（优先从 Django settings 读取）
# ============================================================
_DEFAULT_BASE = "../../data/geology/"

SHP_PROVINCE_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '行政区划/省界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/省界.shp'
)
SHP_CITY_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/市界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/市界.shp'
)
SHP_COUNTY_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/县界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/县界.shp'
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
INFO_TEXT_FONT_SIZE_PT = 6
DESCRIPTION_FONT_SIZE_PT = 10  # 新增：说明文字字体大小
LEGEND_TITLE_FONT_SIZE_PT = 12
LEGEND_ITEM_FONT_SIZE_PT = 10
INTENSITY_LABEL_FONT_SIZE_PT = 10
SCALE_FONT_SIZE_PT = 10

# ============================================================
# 【图例布局常量】（参考 earthquake_map.py）
# ============================================================
# 图例项行高（毫米）
LEGEND_ROW_HEIGHT_MM = 6.0
# 图例内边距（毫米）
LEGEND_PADDING_MM = 2.0
# 图例图标宽度（毫米）
LEGEND_ICON_WIDTH_MM = 8.0
# 图标与文字间距（毫米）
LEGEND_ICON_TEXT_GAP_MM = 1.5
# 图例中最多显示的烈度项数（4行×3列）
MAX_INTENSITY_LEGEND_ITEMS = 12

# ============================================================
# 【行政边界线样式】
# ============================================================
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)
# 省份质心与震中坐标重合判断容差（约0.1米精度，用于浮点数相等比较）
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6

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
EPICENTER_STAR_SIZE_MM = 5.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ============================================================
# 【工具函数】
# ============================================================

def _auto_wrap_text(text, max_width_mm, font_size_pt, first_line_indent_chars=0):
    """
    根据指定宽度自动换行文本（基于字符宽度估算）。

    说明：
    - 若需首行缩进，调用方应在 text 开头拼接全角空格（如"　　"），
      函数将这些字符计入该行的宽度，自然占用首行空间，无需额外减少可用宽度。
    - first_line_indent_chars 保留作为兼容参数，函数内部不再用其缩减可用宽度。
    - max_width_mm 应已包含右侧1mm边距（即调用方传入的值为实际可用宽度）。

    参数:
        text (str): 原始文本（首行缩进字符已包含在内）
        max_width_mm (float): 每行最大可用宽度（毫米）
        font_size_pt (int/float): 字体大小（磅）
        first_line_indent_chars (int): 保留参数，当前版本不使用
    返回:
        str: 包含换行符的已换行文本
    """
    if not text:
        return ""

    # 1pt = 25.4/72 mm ≈ 0.353mm（四舍五入后的标准换算值）；SimSun 中文字符宽高接近正方形
    cn_char_width_mm = font_size_pt * 0.353
    # 英文/数字平均宽度约为中文的 0.6 倍
    en_char_width_mm = cn_char_width_mm * 0.6
    # 半角空格宽度约为英文字符的 0.5 倍
    space_width_mm = en_char_width_mm * 0.5

    # 不应出现在行首的标点（后置标点，跟随上文）
    trailing_punctuation = set('，。！？；：""''）》】、…')
    # 不应出现在行尾的标点（前置标点，引领下文）
    leading_punctuation = set('""''（《【')

    def _char_width(c):
        """估算单个字符的宽度（毫米）"""
        if c == ' ':
            return space_width_mm
        # CJK 统一汉字、扩展 A 区、兼容汉字、CJK 符号与标点（含全角空格 U+3000）、常用中文标点
        if ('\u4e00' <= c <= '\u9fff'
                or '\u3400' <= c <= '\u4dbf'
                or '\uf900' <= c <= '\ufaff'
                or '\u3000' <= c <= '\u303f'
                or c in '，。！？；：""''（）《》【】、…'):
            return cn_char_width_mm
        return en_char_width_mm

    result_lines = []

    for paragraph in text.split('\n'):
        if not paragraph:
            result_lines.append('')
            continue

        lines = []
        current_line = ""
        current_width = 0.0
        i = 0

        while i < len(paragraph):
            c = paragraph[i]
            cw = _char_width(c)

            if current_width + cw > max_width_mm and current_line:
                # 需要换行（此处 current_line 已确保非空，可安全访问 current_line[-1]）
                if c in trailing_punctuation:
                    # 后置标点随当前行末尾输出，再换行
                    current_line += c
                    lines.append(current_line)
                    current_line = ""
                    current_width = 0.0
                elif current_line[-1] in leading_punctuation:
                    # 当前行末尾是前置标点，将其移至下一行开头
                    last_char = current_line[-1]
                    current_line = current_line[:-1]
                    lines.append(current_line)
                    current_line = last_char + c
                    current_width = _char_width(last_char) + cw
                else:
                    # 正常换行
                    lines.append(current_line)
                    current_line = c
                    current_width = cw
            else:
                current_line += c
                current_width += cw

            i += 1

        if current_line:
            lines.append(current_line)

        result_lines.extend(lines)

    return '\n'.join(result_lines)

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
    基于WGS84椭球体参数，对每个顶点使用本地纬度修正

    参数:
        coords (list): 坐标列表 [(lon, lat), ...]
    返回:
        float: 面积（平方千米）
    """
    if len(coords) < 3:
        return 0.0

    # WGS84椭球体参数
    a = 6378.137   # 赤道半径（千米）
    b = 6356.7523  # 极半径（千米）
    e2 = 1 - (b / a) ** 2  # 第一偏心率的平方

    # 参考点取第一个点，避免绝对坐标的数值问题
    ref_lon, ref_lat = coords[0]

    km_coords = []
    for lon, lat in coords:
        lat_rad = math.radians(lat)
        sin_lat = math.sin(lat_rad)
        # 子午圈曲率半径 M
        M = a * (1 - e2) / (1 - e2 * sin_lat ** 2) ** 1.5
        # 卯酉圈曲率半径 N
        N = a / math.sqrt(1 - e2 * sin_lat ** 2)
        # 经度差转千米（使用本地纬度的卯酉圈半径）
        x = math.radians(lon - ref_lon) * N * math.cos(lat_rad)
        # 纬度差转千米（使用本地纬度的子午圈半径）
        y = math.radians(lat - ref_lat) * M
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


def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent, map_width_mm=None):
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移。

    当省份质心与震中坐标重合时，标注点向右下角偏移3mm，避免遮挡震中五角星标识。

    参数:
        province_layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float): 震中经度（度）
        epicenter_lat (float): 震中纬度（度）
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量（mm转度）
        map_width_mm (float): 地图内容区宽度（毫米），用于经度偏移计算

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
    if map_width_mm is None:
        map_width_mm = MAP_MAX_WIDTH_MM
    lon_offset_deg = offset_mm / map_width_mm * map_width_deg   # 向右偏移（经度增大）
    lat_offset_deg = offset_mm / MAP_HEIGHT_MM * map_height_deg  # 向下偏移（纬度减小）

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
        if (abs(cx - epicenter_lon) < PROVINCE_EPICENTER_COINCIDENCE_TOL
                and abs(cy - epicenter_lat) < PROVINCE_EPICENTER_COINCIDENCE_TOL):
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
                f"极震区面积估算为{format_area(max_area)}平方千米，"
                f"地震烈度VI度以上区域面积达{format_area(vi_above_area)}平方千米。")
    return analysis


def format_area(num):
    if num > 1:
        return f"{num:.0f}"
    else:
        return f"{num:.2f}"

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

def calculate_map_dimensions_from_extent(extent):
    """
    根据地图范围计算地图宽度和高度

    高度固定为 MAP_HEIGHT_MM（100mm），宽度根据经纬度范围的实际纵横比
    （含纬度余弦修正）动态计算，但不超过 MAP_MAX_WIDTH_MM。

    参数:
        extent (QgsRectangle): 地图范围
    返回:
        tuple: (map_width_mm, map_height_mm)
    """
    lon_range = extent.width()
    lat_range = extent.height()
    center_lat = (extent.yMaximum() + extent.yMinimum()) / 2.0
    # 考虑纬度修正：经度方向的实际距离比纬度方向短
    lon_km = lon_range * 111.32 * math.cos(math.radians(center_lat))
    lat_km = lat_range * 111.32
    aspect_ratio = lon_km / lat_km if lat_km > 0 else 1.0

    map_height_mm = MAP_HEIGHT_MM  # 固定100mm
    map_width_mm = map_height_mm * aspect_ratio

    if map_width_mm > MAP_MAX_WIDTH_MM:
        map_width_mm = MAP_MAX_WIDTH_MM

    return map_width_mm, map_height_mm


def adjust_extent_to_match_aspect_ratio(extent, map_width_mm, map_height_mm):
    """
    调整地理范围使其宽高比与地图项的mm宽高比一致。
    这样QGIS的map_item.setExtent()不会自动扩展范围，
    确保主图区高度精确等于 map_height_mm。
    """
    center_lat = (extent.yMaximum() + extent.yMinimum()) / 2.0
    cos_lat = math.cos(math.radians(center_lat))

    lon_range = extent.width()
    lat_range = extent.height()

    # 地图项的目标宽高比（mm）
    target_aspect = map_width_mm / map_height_mm
    # 当前地理范围的实际宽高比（经纬度余弦修正后）
    current_aspect = (lon_range * cos_lat) / lat_range if lat_range > 0 else target_aspect

    center_lon = (extent.xMaximum() + extent.xMinimum()) / 2.0

    if current_aspect < target_aspect:
        # 经度范围不够宽，需要扩展经度
        new_lon_range = target_aspect * lat_range / cos_lat
        new_lat_range = lat_range
    else:
        # 纬度范围不够高，需要扩展纬度
        new_lat_range = lon_range * cos_lat / target_aspect
        new_lon_range = lon_range

    return QgsRectangle(
        center_lon - new_lon_range / 2.0,
        center_lat - new_lat_range / 2.0,
        center_lon + new_lon_range / 2.0,
        center_lat + new_lat_range / 2.0
    )


def round_scale_denominator(raw_scale):
    """
    将比例尺分母圆整为前两位有效数字，其余补0（标准四舍五入）。
    例如：
        1234567 -> 1200000
        987654  -> 990000
        56789   -> 57000
        1500    -> 1500
        350     -> 350
    """
    if raw_scale <= 0:
        return 1
    int_scale = int(raw_scale)
    digits = len(str(int_scale))
    if digits <= 2:
        return int_scale
    factor = 10 ** (digits - 2)
    return (int_scale + factor // 2) // factor * factor


def create_print_layout(project, extent, scale, map_height_mm, description_text,
                        intensity_data, map_width_mm=None, ordered_layers=None, has_faults=True):
    """
    创建QGIS打印布局

    参数:
        project (QgsProject): QGIS项目实例
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺
        map_height_mm (float): 地图高度（毫米）
        description_text (str): 说明文字
        intensity_data (dict): 烈度圈数据
        map_width_mm (float): 地图宽度（毫米），默认使用 MAP_MAX_WIDTH_MM
        ordered_layers (list): 按渲染顺序排列的图层列表
        has_faults (bool): 是否包含断裂线
    返回:
        QgsPrintLayout: 打印布局对象
    """
    if map_width_mm is None:
        map_width_mm = MAP_MAX_WIDTH_MM

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震烈度分布图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    # 计算输出总高度：上边距 + 地图高度 + 下边距
    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM
    # 计算输出总宽度：左边距 + 地图宽度 + 图例宽度 + 右边距
    output_width_mm = BORDER_LEFT_MM + map_width_mm + LEGEND_WIDTH_MM + BORDER_RIGHT_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(output_width_mm, output_height_mm, QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    # 添加地图项
    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(map_width_mm, map_height_mm, QgsUnitTypes.LayoutMillimeters))
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
        map_item.setKeepLayerSet(True)
    map_item.invalidateCache()

    # 经纬度网格
    _setup_map_grid(map_item, extent)

    # 指北针（地图右上角）
    _add_north_arrow(layout, map_left, map_top, map_width_mm)

    # 右侧独立图例区（与地图等高，含比例尺）
    center_lat = (extent.yMaximum() + extent.yMinimum()) / 2.0
    _add_legend(layout, map_height_mm, has_faults, scale=scale, extent=extent,
                center_lat=center_lat, intensity_data=intensity_data,
                description_text=description_text, map_width_mm=map_width_mm)

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


def _add_legend(layout, map_height_mm, has_faults=True, scale=None, extent=None,
                center_lat=None, intensity_data=None, description_text=None, map_width_mm=None):
    """
    添加图例（位于右侧独立区域，与地图等高）
    - 图例左边框紧接底图右边框
    - 图例上下与底图对齐
    - 包含：基本项两列、断裂线单列（可选）、烈度标题、烈度三列、底部比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_height_mm (float): 底图高度（毫米）
        has_faults (bool): 是否包含断裂线图例
        scale (int): 比例尺分母（用于绘制比例尺）
        extent (QgsRectangle): 地图范围（用于计算比例尺）
        center_lat (float): 地图中心纬度（用于计算比例尺）
        intensity_data (dict): 烈度圈数据 {烈度值: [(lon, lat), ...]}
        description_text (str): 说明文字，显示在比例尺上方
        map_width_mm (float): 地图内容区宽度（毫米），用于定位图例和计算比例尺
    """
    if map_width_mm is None:
        map_width_mm = MAP_MAX_WIDTH_MM
    legend_x = BORDER_LEFT_MM + map_width_mm
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

    # 公共文本格式
    title_format = QgsTextFormat()
    title_format.setFont(QFont(FONT_PATH_HEITI, LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    # 中文图例项字体（SimSun）
    item_format_cn = QgsTextFormat()
    item_format_cn.setFont(QFont(FONT_PATH_SONGTI, LEGEND_ITEM_FONT_SIZE_PT))
    item_format_cn.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format_cn.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format_cn.setColor(QColor(0, 0, 0))

    # 英文/数字图例项字体（Times New Roman，用于罗马数字）
    item_format_en = QgsTextFormat()
    item_format_en.setFont(QFont(FONT_PATH_TIMES, LEGEND_ITEM_FONT_SIZE_PT))
    item_format_en.setSize(LEGEND_ITEM_FONT_SIZE_PT)
    item_format_en.setSizeUnit(QgsUnitTypes.RenderPoints)
    item_format_en.setColor(QColor(0, 0, 0))

    # 说明文字区高度（默认25mm，有说明文字时按实际行数动态计算）
    INFO_TEXT_AREA_HEIGHT_MM = 25.0

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

    # ── 上部：说明文字区（固定65mm高，位于图例区顶部）──
    if description_text:
        # 计算可用宽度（减去左右边距各2mm，再减去右边框1mm间距）
        available_width_mm = legend_width - DESCRIPTION_HORIZONTAL_MARGIN_MM * 2

        # 首行缩进：在文本开头拼接两个全角空格，函数处理时自然占用首行空间
        indented_source = "　　" + description_text
        indented_text = _auto_wrap_text(indented_source, available_width_mm, DESCRIPTION_FONT_SIZE_PT)

        # 动态计算说明文字区高度（依据实际换行行数）
        # SimSun 10pt 实际行高约 5mm（0.5mm/pt），不使用行距倍数
        line_height_mm = DESCRIPTION_FONT_SIZE_PT * 0.4
        num_lines = len(indented_text.split('\n'))
        INFO_TEXT_AREA_HEIGHT_MM = DESCRIPTION_TOP_MARGIN_MM + num_lines * line_height_mm + 3.0  # 3mm 底部内边距

        # 创建说明文字格式（SimSun字体）
        desc_format = QgsTextFormat()
        desc_format.setFont(QFont(FONT_PATH_SONGTI, DESCRIPTION_FONT_SIZE_PT))
        desc_format.setSize(DESCRIPTION_FONT_SIZE_PT)
        desc_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        desc_format.setColor(QColor(0, 0, 0))

        desc_label = QgsLayoutItemLabel(layout)
        desc_label.setMode(QgsLayoutItemLabel.ModeFont)  # 使用纯文本模式（非HTML）
        desc_label.setText(indented_text)
        desc_label.setTextFormat(desc_format)
        desc_label.attemptMove(QgsLayoutPoint(legend_x + DESCRIPTION_HORIZONTAL_MARGIN_MM,
                                              legend_y + DESCRIPTION_TOP_MARGIN_MM,
                                              QgsUnitTypes.LayoutMillimeters))
        desc_label.attemptResize(QgsLayoutSize(legend_width - DESCRIPTION_HORIZONTAL_MARGIN_MM * 2,  # -1.0mm 右侧安全间距
                                               INFO_TEXT_AREA_HEIGHT_MM - DESCRIPTION_TOP_MARGIN_MM + 2.0,  # +2.0mm 确保最后一行完整显示
                                               QgsUnitTypes.LayoutMillimeters))
        desc_label.setHAlign(Qt.AlignLeft)
        desc_label.setVAlign(Qt.AlignTop)
        desc_label.setFrameEnabled(False)
        desc_label.setBackgroundEnabled(False)
        layout.addLayoutItem(desc_label)
        print(f"[信息] 说明文字添加到图例区完成（字体: SimSun {DESCRIPTION_FONT_SIZE_PT}pt，已自动换行）")

    # ── 分隔线（位于说明文字区底部）──
    sep_shape = QgsLayoutItemShape(layout)
    sep_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    sep_shape.attemptMove(QgsLayoutPoint(legend_x, legend_y + INFO_TEXT_AREA_HEIGHT_MM,
                                         QgsUnitTypes.LayoutMillimeters))
    sep_shape.attemptResize(QgsLayoutSize(legend_width, BORDER_WIDTH_MM,
                                          QgsUnitTypes.LayoutMillimeters))
    sep_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    sep_shape.setSymbol(sep_symbol)
    sep_shape.setFrameEnabled(False)
    layout.addLayoutItem(sep_shape)

    # 图例区起始Y坐标（分隔线底部）
    legend_items_y = legend_y + INFO_TEXT_AREA_HEIGHT_MM + BORDER_WIDTH_MM

    # 图例标题 "图  例"
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_items_y + 1.0,
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(legend_width, 5.0,
                                            QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # 图例项起始Y坐标（标题1mm上偏+5mm高度+1mm间距=7mm）
    current_y = legend_items_y + 7.0
    icon_x = legend_x + LEGEND_PADDING_MM
    icon_center_offset = LEGEND_ROW_HEIGHT_MM / 2.0
    text_x_single = icon_x + LEGEND_ICON_WIDTH_MM + LEGEND_ICON_TEXT_GAP_MM
    text_width_single = legend_width - LEGEND_PADDING_MM - LEGEND_ICON_WIDTH_MM - LEGEND_ICON_TEXT_GAP_MM - 1.0

    def _add_cn_label(label, y):
        """绘制中文图例文字标签（单列）"""
        lbl = QgsLayoutItemLabel(layout)
        lbl.setText(label)
        lbl.setTextFormat(item_format_cn)
        lbl.attemptMove(QgsLayoutPoint(text_x_single, y, QgsUnitTypes.LayoutMillimeters))
        lbl.attemptResize(QgsLayoutSize(text_width_single, LEGEND_ROW_HEIGHT_MM,
                                        QgsUnitTypes.LayoutMillimeters))
        lbl.setHAlign(Qt.AlignLeft)
        lbl.setVAlign(Qt.AlignVCenter)
        lbl.setFrameEnabled(False)
        lbl.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl)

    # ── 1. 基本图例项（两列布局）──
    # 第1行左列：震中（红色五角星），右列：省界（灰色实线）
    # 第2行左列：市界（灰色虚线），右列：县界（浅灰色虚线）
    col_count = 2
    col_width = (legend_width - 2 * LEGEND_PADDING_MM) / col_count
    basic_icon_width = 4.0
    basic_icon_height = 2.5
    basic_icon_text_gap = 1.0

    basic_items = [
        ("震中",  "star"),
        ("省界",  "solid_province"),
        ("市界",  "dash_city"),
        ("县界",  "dash_county"),
    ]

    basic_rows = (len(basic_items) + col_count - 1) // col_count  # = 2
    for idx, (label, draw_type) in enumerate(basic_items):
        row = idx // col_count
        col = idx % col_count
        item_x = legend_x + LEGEND_PADDING_MM + col * col_width
        item_y = current_y + row * LEGEND_ROW_HEIGHT_MM
        icon_center_y = item_y + LEGEND_ROW_HEIGHT_MM / 2.0

        if draw_type == "star":
            _draw_legend_star(layout, item_x, icon_center_y, basic_icon_width, basic_icon_height)
        elif draw_type == "solid_province":
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

    # ── 2. 断裂线（可选，单列）──
    if has_faults:
        for label, color, line_width in [
            ("全新世断层",     FAULT_HOLOCENE_COLOR,         FAULT_HOLOCENE_WIDTH_MM),
            ("晚更新世断层",   FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH_MM),
            ("早中更新世断层", FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH_MM),
        ]:
            icon_center_y = current_y + icon_center_offset
            _draw_legend_line(layout, icon_x, icon_center_y, LEGEND_ICON_WIDTH_MM, color, line_width)
            _add_cn_label(label, current_y)
            current_y += LEGEND_ROW_HEIGHT_MM

    # ── 3. 地震烈度标题（居中）──
    int_title_format = QgsTextFormat()
    int_title_format.setFont(QFont(FONT_PATH_HEITI, 10))
    int_title_format.setSize(10)
    int_title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    int_title_format.setColor(QColor(0, 0, 0))

    int_title_label = QgsLayoutItemLabel(layout)
    int_title_label.setText("地震烈度")
    int_title_label.setTextFormat(int_title_format)
    int_title_label.attemptMove(QgsLayoutPoint(legend_x, current_y, QgsUnitTypes.LayoutMillimeters))
    int_title_label.attemptResize(QgsLayoutSize(legend_width, 5,
                                                QgsUnitTypes.LayoutMillimeters))
    int_title_label.setHAlign(Qt.AlignHCenter)
    int_title_label.setVAlign(Qt.AlignVCenter)
    int_title_label.setFrameEnabled(False)
    int_title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(int_title_label)
    current_y += 5

    # ── 4. 烈度图例项（三列布局，罗马数字，最多 MAX_INTENSITY_LEGEND_ITEMS 个）──
    if intensity_data:
        sorted_intensities = sorted(intensity_data.keys(), reverse=True)
        if len(sorted_intensities) > MAX_INTENSITY_LEGEND_ITEMS:
            sorted_intensities = sorted_intensities[:MAX_INTENSITY_LEGEND_ITEMS]

        int_col_count = 4
        int_col_width = (legend_width - 2 * LEGEND_PADDING_MM) / int_col_count
        int_icon_width = 4.0
        int_icon_text_gap = 1.0
        int_rows = (len(sorted_intensities) + int_col_count - 1) // int_col_count

        for idx, intensity in enumerate(sorted_intensities):
            row = idx // int_col_count
            col = idx % int_col_count
            item_x = legend_x + LEGEND_PADDING_MM + col * int_col_width
            item_y = current_y + row * LEGEND_ROW_HEIGHT_MM
            icon_center_y = item_y + LEGEND_ROW_HEIGHT_MM / 2.0

            color = INTENSITY_COLORS.get(intensity, QColor(255, 0, 0))
            _draw_legend_line(layout, item_x, icon_center_y, int_icon_width, color,
                              INTENSITY_LINE_WIDTH_MM)

            roman = int_to_roman(intensity)
            text_start_x = item_x + int_icon_width + int_icon_text_gap
            # 罗马数字宽度估算：Times New Roman 10pt，每字符约1.6mm，最小5.0mm
            roman_width = max(len(roman) * 1.6, 5.0)
            du_width = int_col_width - int_icon_width - int_icon_text_gap - roman_width

            # 罗马数字（Times New Roman）
            num_lbl = QgsLayoutItemLabel(layout)
            num_lbl.setText(roman)
            num_lbl.setTextFormat(item_format_en)
            num_lbl.attemptMove(QgsLayoutPoint(text_start_x, item_y,
                                               QgsUnitTypes.LayoutMillimeters))
            num_lbl.attemptResize(QgsLayoutSize(roman_width, LEGEND_ROW_HEIGHT_MM,
                                                QgsUnitTypes.LayoutMillimeters))
            num_lbl.setHAlign(Qt.AlignLeft)
            num_lbl.setVAlign(Qt.AlignVCenter)
            num_lbl.setFrameEnabled(False)
            num_lbl.setBackgroundEnabled(False)
            layout.addLayoutItem(num_lbl)

            # "度"字（SimSun）
            # cn_lbl = QgsLayoutItemLabel(layout)
            # cn_lbl.setText("度")
            # cn_lbl.setTextFormat(item_format_cn)
            # cn_lbl.attemptMove(QgsLayoutPoint(text_start_x + roman_width, item_y,
            #                                   QgsUnitTypes.LayoutMillimeters))
            # cn_lbl.attemptResize(QgsLayoutSize(max(du_width, 4.0), LEGEND_ROW_HEIGHT_MM,
            #                                    QgsUnitTypes.LayoutMillimeters))
            # cn_lbl.setHAlign(Qt.AlignLeft)
            # cn_lbl.setVAlign(Qt.AlignVCenter)
            # cn_lbl.setFrameEnabled(False)
            # cn_lbl.setBackgroundEnabled(False)
            # layout.addLayoutItem(cn_lbl)

        current_y += int_rows * LEGEND_ROW_HEIGHT_MM

    # ── 5. 比例尺（位于图例区底部）──
    if scale is not None and extent is not None and center_lat is not None:
        lon_range_deg = extent.xMaximum() - extent.xMinimum()
        map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))
        km_per_mm = map_total_km / map_width_mm if map_width_mm > 0 else 1.0
        target_bar_km = map_width_mm * 0.18 * km_per_mm

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

        std_bar_width = bar_length_mm + 16.0
        std_bar_height = 9.0

        # 图例区可用宽度（左右各留 2mm）
        avail_width = legend_width - 4.0
        if std_bar_width > avail_width:
            scale_factor = avail_width / std_bar_width
            std_bar_width = avail_width
            bar_length_mm *= scale_factor
            std_bar_height *= scale_factor
        else:
            scale_factor = 1.0

        # 比例尺垂直位置：距图例区底部留 4mm 空间
        sb_height = std_bar_height
        sb_y = legend_y + legend_height - sb_height - 4.0
        sb_x = legend_x + (legend_width - std_bar_width) / 2.0

        # 比例尺分母文字
        scale_tf = QgsTextFormat()
        scale_tf.setFont(QFont(FONT_PATH_TIMES, SCALE_FONT_SIZE_PT))
        scale_tf.setSize(SCALE_FONT_SIZE_PT)
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
        tick_tf.setFont(QFont(FONT_PATH_TIMES, SCALE_FONT_SIZE_PT))
        tick_tf.setSize(SCALE_FONT_SIZE_PT)
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

    n_intensity = len(intensity_data) if intensity_data else 0
    print(f"[信息] 图例添加完成，烈度项 {n_intensity} 个，断裂线 {'有' if has_faults else '无'}")


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
    line_shape.attemptResize(QgsLayoutSize(width, max(line_height, 0.5),
                                           QgsUnitTypes.LayoutMillimeters))
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_style': 'no',
    })
    line_shape.setSymbol(line_symbol)
    line_shape.setFrameEnabled(False)
    layout.addLayoutItem(line_shape)


def _draw_legend_dash_line(layout, x, center_y, width, color, line_width_mm, dash_gap_mm=0.8):
    """
    在图例中绘制虚线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标总宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽（毫米）
        dash_gap_mm (float): 虚线间隔（毫米），默认0.8
    """
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_height = max(line_width_mm, 0.5)
    # 短划长度 = 间隔 × 3.5（约使实线部分占75%），最小0.8mm
    dash_length_mm = max(dash_gap_mm * 3.5, 0.8)
    pattern_length = dash_length_mm + dash_gap_mm
    current_x = x
    while current_x < x + width:
        actual_dash = min(dash_length_mm, x + width - current_x)
        if actual_dash <= 0:
            break
        dash_shape = QgsLayoutItemShape(layout)
        dash_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_height / 2,
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
    print(f"  震级: M{magnitude}, 比例尺(震级估算): 1:{scale_denom:,}")

    map_width_mm, map_height_mm = calculate_map_dimensions_from_extent(extent)
    # 调整 extent 使其宽高比与 map_item 的 mm 尺寸严格一致，
    # 避免 QGIS 自动扩展 extent 导致实际渲染高度偏离 map_height_mm
    extent = adjust_extent_to_match_aspect_ratio(extent, map_width_mm, map_height_mm)
    # 动态计算比例尺：基于调整后 extent 的纬度方向（高度固定）
    lat_range_deg = extent.yMaximum() - extent.yMinimum()
    if lat_range_deg > 0:
        scale_denom = int((lat_range_deg * 111320.0) / (MAP_HEIGHT_MM / 1000.0))
    scale_denom = round_scale_denominator(scale_denom)
    print(f"  地图尺寸: {map_width_mm:.1f}mm x {map_height_mm:.1f}mm")
    print(f"  动态比例尺: 1:{scale_denom:,}")

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
        width_px = int(map_width_mm / 25.4 * OUTPUT_DPI)
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
            style_province_layer(province_layer, center_lon, center_lat, extent)
            project.addMapLayer(province_layer)

        # 创建省份标注点图层
        province_label_layer = None
        if province_layer:
            try:
                province_label_layer = create_province_label_layer(
                    province_layer, center_lon, center_lat, extent, map_width_mm=map_width_mm)
                if province_label_layer:
                    # False: 不自动将图层添加到图层树，由 ordered_layers 手动控制渲染顺序
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
        # 省份标注在省界上层
        if province_label_layer:
            ordered_layers.append(province_label_layer)
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
            intensity_data, map_width_mm=map_width_mm,
            ordered_layers=ordered_layers, has_faults=has_faults
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
        " 综合考虑震中附近地质构造背景、地震波衰减特性，"
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