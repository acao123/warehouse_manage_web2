# -*- coding: utf-8 -*-
"""
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
	4.指北针的位置和样式参考”earthquake_kml_map.py“
    5.图例位置：放在地图的右侧，图例左边框与地图右边框重合，图例下边框与地图下边框平行，
	内容从属性表yanxing字段中获取，图例布局按照”制图布局参考图3.png“图例格式放置
	6.比例尺的位置和样式参考”earthquake_kml_map.py“
	7.地图框上侧和左侧标注经纬度，形式为X°X′N，X°X′E；经度最多6个，纬度最多5个，经纬度的字体是8pt。
	8.从”地级市点位数据.shp“文件属性表中获取市的名称，和点位信息，代表市的位置符号更改，具体样式为：黑色空圈内为一个实心黑圆，加一个圆形的白色背景；整体大小为市名称大小的三分之一；市的名称字体是9pt，颜色是黑色，加白边，该图层位于地质构造图tif文件的上方显示
	9.烈度的加载参考earthquake_kml_map.py，需要展示在地图上
	10.代表震中位置的红色五角星外面加白边，内部为纯红色，大小为8pt字体的三分之二。
	11.调用 “地质构造图tif文件”的时候，不要改动内部的色块
	说明： 地质构造图tif文件位置：../../data/geology/图3/group.tif
		  省界shp文件位置：../../data/geology/行政区划/省界.shp
	      市界shp文件位置：../../data/geology/行政区划/市界.shp
	      县界shp文件位置：../../data/geology/行政区划/县界.shp
	      地级市点位数据.shp文件位置：../../data/geology/2023地级市点位数据/地级市点位数据.shp
	12.注释是中文注释，要求方法和参数需要有中文注释
	13.指省界颜色改为:R=160 G=160 B=160，0.4mm，市界颜色改为:R=160 G=160 B=160，0.24mm，虚线间隔为0.3，县界颜色改为:R=160 G=160 B=160，0.14mm虚线间隔为0.3
	14.代码需要无bug可运行，并写出测试方法
	输出图布局参考：制图布局参考图3.png，
	15.基于QGIS3.40.15 python环境，所以生产时一定需要参考对应版本API,生成的代码不能有bug
	注意：”制图布局参考图3.png“值提供布局和样式参考。
	输出完整代码，代码可能比较长，分四个部分输出，我会放到一个python文件中
地震地质构造图生成脚本（基于Python + Pillow + rasterio + shapefile）
"""
import os
import sys
import re
import math
import struct
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

try:
    import shapefile
except ImportError:
    print("*** 请安装pyshp库: pip install pyshp ***")
    sys.exit(1)

try:
    import rasterio
    HAS_RASTERIO = True
except ImportError:
    HAS_RASTERIO = False

TIF_PATH = r"../../data/geology/图3/group.tif"
SHP_PROVINCE_PATH = (
    r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    r"/全国省份行政区划数据/省级行政区划/省.shp"
)
SHP_CITY_PATH = (
    r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    r"/全国市级行政区划数据/市级行政区划/市.shp"
)
SHP_COUNTY_PATH = (
    r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别"
    r"/全国县级行政区划数据/县级行政区划/县.shp"
)
SHP_CITY_POINTS_PATH = r"../../data/geology/2023地级市点位数据/地级市点位数据.shp"

FONT_PATH_HEITI = "C:/Windows/Fonts/simhei.ttf"
FONT_PATH_SONGTI = "C:/Windows/Fonts/simsun.ttc"
FONT_PATH_TIMES = "C:/Windows/Fonts/times.ttf"
FONT_FALLBACKS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]

OUTPUT_DPI = 150
MM_PX = OUTPUT_DPI / 25.4
TOTAL_WIDTH_PX = int(200 * MM_PX)
MAP_BORDER_WIDTH = max(2, int(round(0.35 * MM_PX)))
LEGEND_WIDTH = int(34 * MM_PX)
BORDER_LEFT = int(9 * MM_PX)
BORDER_TOP = int(4 * MM_PX)
BORDER_BOTTOM = int(5 * MM_PX)
MAP_WIDTH = TOTAL_WIDTH_PX - BORDER_LEFT - LEGEND_WIDTH
MAP_HEIGHT = MAP_WIDTH
OUTPUT_WIDTH = TOTAL_WIDTH_PX
OUTPUT_HEIGHT = BORDER_TOP + MAP_HEIGHT + BORDER_BOTTOM

PT_TO_PX = OUTPUT_DPI / 72.0
PROVINCE_LABEL_FONT_SIZE = max(10, int(8 * PT_TO_PX))
CITY_LABEL_FONT_SIZE = max(11, int(9 * PT_TO_PX))
COORD_FONT_SIZE = max(10, int(8 * PT_TO_PX))
SCALE_FONT_SIZE = max(10, int(8 * PT_TO_PX))
LEGEND_TITLE_FONT_SIZE = max(12, int(10 * PT_TO_PX))
LEGEND_ITEM_FONT_SIZE = max(10, int(8 * PT_TO_PX))
INTENSITY_LABEL_FONT_SIZE = max(11, int(9 * PT_TO_PX))
EPICENTER_STAR_RADIUS = max(6, int(8 * PT_TO_PX * 2 / 3))

PROVINCE_BORDER_COLOR = (160, 160, 160, 255)
PROVINCE_BORDER_WIDTH = max(2, int(round(0.4 * MM_PX)))
CITY_BORDER_COLOR = (160, 160, 160, 255)
CITY_BORDER_WIDTH = max(1, int(round(0.24 * MM_PX)))
CITY_BORDER_DASH = (8, 4)
COUNTY_BORDER_COLOR = (160, 160, 160, 200)
COUNTY_BORDER_WIDTH = max(1, int(round(0.14 * MM_PX)))
COUNTY_BORDER_DASH = (6, 4)
PROVINCE_LABEL_COLOR = (77, 77, 77, 255)

INTENSITY_COLORS = {
    4:  (0, 150, 255, 255),
    5:  (0, 200, 100, 255),
    6:  (255, 200, 0, 255),
    7:  (255, 150, 0, 255),
    8:  (255, 80, 0, 255),
    9:  (255, 0, 0, 255),
    10: (200, 0, 50, 255),
    11: (150, 0, 100, 255),
    12: (100, 0, 150, 255),
}
INTENSITY_LINE_WIDTH = max(2, int(round(0.5 * MM_PX)))
EPICENTER_COLOR = (255, 0, 0, 255)

def load_font(font_path, size, fallback_path=None):
    """加载字体，失败时依次尝试备用字体"""
    candidates = [font_path, fallback_path] + FONT_FALLBACKS
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except (IOError, OSError):
                continue
    return ImageFont.load_default()


def get_range_params(magnitude):
    """根据震级确定绘图范围参数，返回(半径km, 地图边长km, 比例尺分母)"""
    if magnitude < 6.0:
        return 15, 30, 150000
    elif magnitude < 7.0:
        return 50, 100, 500000
    else:
        return 150, 300, 1500000


def km_to_degree_lon(km, latitude):
    """千米转经度差"""
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """千米转纬度差"""
    return km / 110.574


def geo_to_pixel(lon, lat, geo_extent, img_width, img_height):
    """经纬度坐标转地图区域像素坐标"""
    px = ((lon - geo_extent["min_lon"]) /
          (geo_extent["max_lon"] - geo_extent["min_lon"]) * img_width)
    py = ((geo_extent["max_lat"] - lat) /
          (geo_extent["max_lat"] - geo_extent["min_lat"]) * img_height)
    return int(round(px)), int(round(py))


def format_degree(value, is_lon=True):
    """将十进制度数格式化为度分格式。
    经度格式：X°X′E 或 X°X′W（保留方向后缀）；
    纬度格式：X°X′（不加 N/S 后缀）。

    参数:
        value (float): 十进制度数
        is_lon (bool): True=经度，False=纬度
    返回:
        str: 格式化字符串
    """
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes = int((abs_val - degrees) * 60)
    if is_lon:
        suffix = "E" if value >= 0 else "W"
        return "%d\u00b0%02d\u2032%s" % (degrees, minutes, suffix)
    else:
        return "%d\u00b0%02d\u2032" % (degrees, minutes)


def int_to_roman(num):
    """将阿拉伯数字转换为罗马数字"""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = ""
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            result += syms[i]
            num -= val[i]
        i += 1
    return result


def _choose_tick_step(range_deg, target_min=4, target_max=6):
    """根据地理范围选择合适的刻度间隔"""
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


def calculate_polygon_centroid(coords):
    """计算多边形质心"""
    if not coords:
        return None, None
    n = len(coords)
    return sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n


def calculate_polygon_area(coords):
    """使用鞋带公式计算多边形面积（平方千米）"""
    if len(coords) < 3:
        return 0.0
    center_lat = sum(c[1] for c in coords) / len(coords)
    km_coords = [
        (lon * 111.32 * math.cos(math.radians(center_lat)), lat * 110.574)
        for lon, lat in coords
    ]
    n = len(km_coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += km_coords[i][0] * km_coords[j][1]
        area -= km_coords[j][0] * km_coords[i][1]
    return abs(area) / 2.0

def read_dbf_file(dbf_path):
    """读取DBF文件，返回(字段名列表, 记录列表)"""
    if not os.path.exists(dbf_path):
        return [], []
    try:
        with open(dbf_path, "rb") as f:
            header = f.read(32)
            if len(header) < 32:
                return [], []
            num_records = struct.unpack("<I", header[4:8])[0]
            header_size = struct.unpack("<H", header[8:10])[0]
            record_size = struct.unpack("<H", header[10:12])[0]
            fields = []
            while f.tell() < header_size - 1:
                fd = f.read(32)
                if not fd or fd[0:1] == b"\r":
                    break
                raw_name = fd[0:11].rstrip(b"\x00")
                name = ""
                for enc in ("gbk", "utf-8", "latin-1"):
                    try:
                        name = raw_name.decode(enc).strip("\x00")
                        break
                    except (UnicodeDecodeError, LookupError):
                        pass
                fields.append((name, fd[16]))
            f.seek(header_size)
            records = []
            for _ in range(num_records):
                raw = f.read(record_size)
                if not raw or raw[0:1] == b"\x1a":
                    break
                record = {}
                pos = 1
                for fname, flen in fields:
                    raw_val = raw[pos:pos + flen]
                    val = ""
                    for enc in ("gbk", "utf-8", "latin-1"):
                        try:
                            val = raw_val.decode(enc).strip()
                            break
                        except (UnicodeDecodeError, LookupError):
                            pass
                    record[fname] = val
                    pos += flen
                records.append(record)
        return [f[0] for f in fields], records
    except (IOError, OSError, struct.error, ValueError) as e:
        print("  DBF读取失败: %s" % e)
        return [], []


def _read_color_from_vat_dbf(tif_path):
    """从 .vat.dbf 属性表读取每个Value对应的RGB颜色，返回字典 {value: (r, g, b, 255)}。

    参数:
        tif_path (str): TIF文件路径
    返回:
        dict: {像素值(int): (R, G, B, 255)} 的颜色映射字典
    """
    base = os.path.splitext(tif_path)[0]
    candidate_dbf = [tif_path + ".vat.dbf", base + ".vat.dbf",
                     base + ".VAT.dbf", base + ".dbf"]
    dbf_path = None
    for p in candidate_dbf:
        if os.path.exists(p):
            dbf_path = p
            break
    if dbf_path is None:
        return {}
    fields, records = read_dbf_file(dbf_path)
    if not records:
        return {}
    fl = {f.lower(): f for f in fields}
    value_field = fl.get("value") or fl.get("val") or (fields[0] if fields else None)
    red_field = fl.get("red") or fl.get("r")
    green_field = fl.get("green") or fl.get("g")
    blue_field = fl.get("blue") or fl.get("b")
    if not (value_field and red_field and green_field and blue_field):
        return {}
    color_map = {}
    for rec in records:
        try:
            value = int(float(rec.get(value_field, 0) or 0))
        except (ValueError, TypeError):
            continue
        try:
            r = int(float(rec.get(red_field, 0) or 0))
            g = int(float(rec.get(green_field, 0) or 0))
            b = int(float(rec.get(blue_field, 0) or 0))
            color_map[value] = (r, g, b, 255)
        except (ValueError, TypeError):
            pass
    return color_map


def _parse_qml_colors(tif_path):
    """从QGIS QML样式文件解析每个Value对应的颜色，返回字典 {value: (r, g, b, 255)}。
    支持 paletteEntry 和 item 两种节点格式，颜色字符串支持 #RRGGBB 和 #AARRGGBB。

    参数:
        tif_path (str): TIF文件路径（自动查找同目录下同名.qml文件）
    返回:
        dict: {像素值(int): (R, G, B, 255)} 的颜色映射字典
    """
    base = os.path.splitext(tif_path)[0]
    qml_candidates = [
        base + ".qml",
        tif_path + ".qml",
        os.path.join(os.path.dirname(tif_path), "group.qml"),
    ]
    qml_path = None
    for p in qml_candidates:
        if os.path.exists(p):
            qml_path = p
            break
    if qml_path is None:
        return {}
    try:
        from lxml import etree as _ET
        with open(qml_path, "rb") as f:
            root = _ET.fromstring(f.read())
        color_map = {}
        # 遍历所有 paletteEntry 和 item 节点
        for entry in root.iter():
            if entry.tag in ("paletteEntry", "item"):
                val_str = entry.get("value")
                color_str = entry.get("color")
                if val_str is None or not color_str:
                    continue
                try:
                    val = int(float(val_str))
                except (ValueError, TypeError):
                    continue
                color_str = color_str.strip()
                if color_str.startswith("#"):
                    hex_c = color_str[1:]
                    if len(hex_c) == 6:
                        # #RRGGBB
                        r = int(hex_c[0:2], 16)
                        g = int(hex_c[2:4], 16)
                        b = int(hex_c[4:6], 16)
                        color_map[val] = (r, g, b, 255)
                    elif len(hex_c) == 8:
                        # #AARRGGBB（QGIS格式，前两位为Alpha）
                        r = int(hex_c[2:4], 16)
                        g = int(hex_c[4:6], 16)
                        b = int(hex_c[6:8], 16)
                        color_map[val] = (r, g, b, 255)
        print("  QML颜色映射解析完成，共 %d 个条目" % len(color_map))
        return color_map
    except (IOError, OSError, ValueError, TypeError) as e:
        print("  QML解析失败: %s" % e)
        return {}


def _generate_auto_colors(unique_values):
    """当无法从任何来源获取颜色映射时，为每个唯一值自动生成可区分的颜色。

    参数:
        unique_values (list): 唯一像素值列表
    返回:
        dict: {像素值(int): (R, G, B, 255)} 的颜色映射字典
    """
    import colorsys
    color_map = {}
    n = len(unique_values)
    for i, v in enumerate(sorted(unique_values)):
        if v == 0:
            # 0值通常为背景/无数据，使用白色
            color_map[v] = (255, 255, 255, 255)
            continue
        hue = (i * 0.618033988749895) % 1.0  # 黄金角分布，确保相邻颜色差异大
        saturation = 0.5 + (i % 3) * 0.15
        lightness = 0.4 + (i % 5) * 0.08
        r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
        color_map[v] = (int(r * 255), int(g * 255), int(b * 255), 255)
    return color_map


def _parse_qml_colors_by_label(tif_path):
    """从QML样式文件解析每个label对应的颜色，返回 {label_str: (r, g, b, 255)}。

    参数:
        tif_path (str): TIF文件路径
    返回:
        dict: {标签名(str): (R, G, B, 255)}
    """
    base = os.path.splitext(tif_path)[0]
    qml_candidates = [
        base + ".qml",
        tif_path + ".qml",
        os.path.join(os.path.dirname(tif_path), "group.qml"),
    ]
    qml_path = None
    for p in qml_candidates:
        if os.path.exists(p):
            qml_path = p
            break
    if qml_path is None:
        return {}
    try:
        from lxml import etree as _ET
        with open(qml_path, "rb") as f:
            root = _ET.fromstring(f.read())
        label_color_map = {}
        # 遍历所有 paletteEntry 和 item 节点，按 label 属性匹配颜色
        for entry in root.iter():
            if entry.tag in ("paletteEntry", "item"):
                label = entry.get("label")
                color_str = entry.get("color")
                if not label or not color_str:
                    continue
                label = label.strip()
                color_str = color_str.strip()
                if color_str.startswith("#"):
                    hex_c = color_str[1:]
                    if len(hex_c) == 6:
                        r = int(hex_c[0:2], 16)
                        g = int(hex_c[2:4], 16)
                        b = int(hex_c[4:6], 16)
                        label_color_map[label] = (r, g, b, 255)
                    elif len(hex_c) == 8:
                        # #AARRGGBB（QGIS格式，前两位为Alpha）
                        r = int(hex_c[2:4], 16)
                        g = int(hex_c[4:6], 16)
                        b = int(hex_c[6:8], 16)
                        label_color_map[label] = (r, g, b, 255)
        print("  QML label颜色映射解析完成，共 %d 个条目" % len(label_color_map))
        return label_color_map
    except (IOError, OSError, ValueError, TypeError) as e:
        print("  QML label解析失败: %s" % e)
        return {}


def read_tif_attribute_table(tif_path):
    """读取TIF属性表，提取岩性(yanxing)和分组(grouping)字段信息，返回[(value, color_rgba, yanxing_name),...]。

    颜色获取优先级：
    1. 从QML样式文件中按 Value 获取颜色（_parse_qml_colors）
    2. 从QML样式文件中按 Grouping(label) 获取颜色（_parse_qml_colors_by_label）
    3. 从属性表的 Red/Green/Blue 字段获取颜色
    4. 自动生成颜色（与地图底图中同一个 Value 的颜色一致，_generate_auto_colors）
    """
    result = []
    base = os.path.splitext(tif_path)[0]
    candidate_dbf = [tif_path + ".vat.dbf", base + ".vat.dbf",
                     base + ".VAT.dbf", base + ".dbf"]
    dbf_path = None
    for p in candidate_dbf:
        if os.path.exists(p):
            dbf_path = p
            break
    if dbf_path is None:
        print("  未找到TIF属性表文件（.vat.dbf）")
        return result
    print("  读取属性表: %s" % dbf_path)
    fields, records = read_dbf_file(dbf_path)
    if not records:
        return result
    print("  属性表字段: %s" % fields)
    fl = {f.lower(): f for f in fields}
    value_field = fl.get("value") or fl.get("val") or (fields[0] if fields else None)
    yanxing_field = None
    for k in ["yanxing", "yanxing1", "yx", "lithology"]:
        if k in fl:
            yanxing_field = fl[k]
            break
    # 读取 Grouping 字段（图层名，用于颜色匹配）
    grouping_field = fl.get("grouping") or fl.get("group")
    if yanxing_field is None:
        print("  未找到yanxing字段")
        return result
    # 优先从QML样式文件获取颜色映射（按Value索引）
    qml_color_map = _parse_qml_colors(tif_path)
    if qml_color_map:
        print("  使用QML样式文件颜色（按Value），共 %d 个条目" % len(qml_color_map))
    else:
        print("  未找到QML按Value颜色，尝试按label获取")
    # 从QML按label/Grouping获取颜色（第二优先级）
    qml_label_color_map = _parse_qml_colors_by_label(tif_path)
    red_field = fl.get("red") or fl.get("r")
    green_field = fl.get("green") or fl.get("g")
    blue_field = fl.get("blue") or fl.get("b")
    # 预先收集所有唯一Value，以便自动生成颜色时与地图底图保持一致
    all_values = []
    for rec in records:
        try:
            v = int(float(rec.get(value_field, 0) or 0))
        except (ValueError, TypeError):
            v = 0
        all_values.append(v)
    # 若所有颜色获取方式均失败，则自动生成颜色（与_load_tif_rasterio逻辑一致）
    auto_color_map = {}
    if not qml_color_map and not qml_label_color_map:
        if not (red_field and green_field and blue_field):
            auto_color_map = _generate_auto_colors(list(set(all_values)))
            print("  使用自动生成颜色，共 %d 个分类值" % len(auto_color_map))
    seen = set()
    for rec in records:
        try:
            value = int(float(rec.get(value_field, 0) or 0))
        except (ValueError, TypeError):
            value = 0
        yanxing = rec.get(yanxing_field, "").strip()
        if not yanxing or yanxing in seen:
            continue
        seen.add(yanxing)
        # 读取Grouping字段值（图层名）
        grouping = ""
        if grouping_field:
            grouping = rec.get(grouping_field, "").strip()
        # 颜色获取：优先QML按Value，其次QML按label/Grouping，再次属性表RGB，最后自动生成
        color = (128, 128, 128, 255)
        if qml_color_map and value in qml_color_map:
            color = qml_color_map[value]
        elif qml_label_color_map and grouping and grouping in qml_label_color_map:
            color = qml_label_color_map[grouping]
        elif qml_label_color_map and yanxing in qml_label_color_map:
            color = qml_label_color_map[yanxing]
        else:
            try:
                if red_field and green_field and blue_field:
                    r = int(float(rec.get(red_field, 128) or 128))
                    g = int(float(rec.get(green_field, 128) or 128))
                    b = int(float(rec.get(blue_field, 128) or 128))
                    color = (r, g, b, 255)
                elif auto_color_map and value in auto_color_map:
                    color = auto_color_map[value]
            except (ValueError, TypeError):
                if auto_color_map and value in auto_color_map:
                    color = auto_color_map[value]
        result.append((value, color, yanxing))
    print("  读取到 %d 个岩性图例项" % len(result))
    return result


def _get_tif_geo_extent_from_tags(img):
    """从PIL Image的TIFF标签中提取地理范围"""
    try:
        tags = img.tag_v2 if hasattr(img, "tag_v2") else {}
        if 33922 in tags and 33550 in tags:
            tp = tags[33922]
            ps = tags[33550]
            if len(tp) >= 6 and len(ps) >= 2:
                ox = float(tp[3])
                oy = float(tp[4])
                xs = float(ps[0])
                ys = float(ps[1])
                w, h = img.size
                return {"min_lon": ox, "max_lon": ox + xs * w,
                        "min_lat": oy - ys * h, "max_lat": oy}
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        print("  TIFF标签解析失败: %s" % e)
    return None


def load_tif_to_image(tif_path, geo_extent, img_width, img_height):
    """
    加载地质构造TIF文件，裁剪至指定地理范围并缩放到目标尺寸，保持原始色彩渲染
    """
    fallback = Image.new("RGBA", (img_width, img_height), (230, 230, 230, 255))
    if not os.path.exists(tif_path):
        print("  *** TIF文件不存在: %s ***" % tif_path)
        return fallback
    if HAS_RASTERIO:
        try:
            return _load_tif_rasterio(tif_path, geo_extent, img_width, img_height)
        except (IOError, OSError, ValueError, RuntimeError) as e:
            print("  rasterio读取失败: %s" % e)
    try:
        return _load_tif_pillow(tif_path, geo_extent, img_width, img_height)
    except (IOError, OSError, ValueError) as e:
        print("  PIL读取TIF失败: %s" % e)
    return fallback


def _load_tif_rasterio(tif_path, geo_extent, img_width, img_height):
    """使用rasterio加载并裁剪TIF文件（保持原始颜色）"""
    import rasterio
    from rasterio.windows import from_bounds as _fb
    from rasterio.enums import Resampling
    import numpy as np
    with rasterio.open(tif_path) as src:
        print("  TIF信息: 波段=%d, 尺寸=%dx%d" % (src.count, src.width, src.height))
        window = None
        try:
            window = _fb(geo_extent["min_lon"], geo_extent["min_lat"],
                         geo_extent["max_lon"], geo_extent["max_lat"], src.transform)
        except (ValueError, RuntimeError) as e:
            print("  裁剪窗口计算失败: %s" % e)
        out_shape = (src.count, img_height, img_width)
        if window is not None:
            data = src.read(window=window, out_shape=out_shape, resampling=Resampling.lanczos)
        else:
            data = src.read(out_shape=out_shape, resampling=Resampling.lanczos)
        if src.count == 1:
            # 分类栅格必须使用最近邻重采样，避免插值产生无效分类值
            if window is not None:
                data_nearest = src.read(window=window, out_shape=out_shape, resampling=Resampling.nearest)
            else:
                data_nearest = src.read(out_shape=out_shape, resampling=Resampling.nearest)
            band = data_nearest[0]
            # 获取颜色映射：优先 .vat.dbf，其次 QML，再次内嵌colormap，最后自动生成
            color_map = _read_color_from_vat_dbf(tif_path)
            if not color_map:
                color_map = _parse_qml_colors(tif_path)
            if not color_map:
                try:
                    cmap = src.colormap(1)
                    if cmap:
                        for v, rgba_c in cmap.items():
                            color_map[int(v)] = tuple(list(rgba_c)[:3]) + (255,)
                except (KeyError, ValueError, AttributeError):
                    pass
            if not color_map:
                # 所有颜色获取方式都失败，自动生成颜色
                unique_vals = [int(v) for v in np.unique(band)]
                color_map = _generate_auto_colors(unique_vals)
                print("  使用自动生成颜色，共 %d 个分类值" % len(color_map))
            # 使用字典映射逐像素赋色，避免 % 256 引起的颜色错误
            rgba_arr = np.zeros((band.shape[0], band.shape[1], 4), dtype="uint8")
            rgba_arr[:, :, 3] = 255  # 默认完全不透明
            for v in np.unique(band):
                v_int = int(v)
                c = color_map.get(v_int, (128, 128, 128, 255))
                mask = (band == v)
                rgba_arr[mask, 0] = c[0]
                rgba_arr[mask, 1] = c[1]
                rgba_arr[mask, 2] = c[2]
                rgba_arr[mask, 3] = c[3] if len(c) > 3 else 255
        elif src.count == 3:
            r, g, b = data[0], data[1], data[2]
            alpha = np.full((img_height, img_width), 255, dtype="uint8")
            rgba_arr = np.stack([r.astype("uint8"), g.astype("uint8"),
                                 b.astype("uint8"), alpha], axis=-1)
        elif src.count >= 4:
            r, g, b, a = data[0], data[1], data[2], data[3]
            rgba_arr = np.stack([r.astype("uint8"), g.astype("uint8"),
                                 b.astype("uint8"), a.astype("uint8")], axis=-1)
        else:
            return Image.new("RGBA", (img_width, img_height), (200, 200, 200, 255))
        return Image.fromarray(rgba_arr, "RGBA").resize((img_width, img_height), Image.LANCZOS)


def _load_tif_pillow(tif_path, geo_extent, img_width, img_height):
    """使用PIL加载TIF文件（通过TIFF地理标签获取空间参考）"""
    img = Image.open(tif_path)
    print("  TIF信息（PIL）: 模式=%s, 尺寸=%s" % (img.mode, img.size))
    tif_extent = _get_tif_geo_extent_from_tags(img)
    if tif_extent is not None:
        tif_w, tif_h = img.size
        lon_r = tif_extent["max_lon"] - tif_extent["min_lon"]
        lat_r = tif_extent["max_lat"] - tif_extent["min_lat"]
        if lon_r > 0 and lat_r > 0:
            px1 = int((geo_extent["min_lon"] - tif_extent["min_lon"]) / lon_r * tif_w)
            px2 = int((geo_extent["max_lon"] - tif_extent["min_lon"]) / lon_r * tif_w)
            py1 = int((tif_extent["max_lat"] - geo_extent["max_lat"]) / lat_r * tif_h)
            py2 = int((tif_extent["max_lat"] - geo_extent["min_lat"]) / lat_r * tif_h)
            px1 = max(0, min(px1, tif_w - 1))
            px2 = max(1, min(px2, tif_w))
            py1 = max(0, min(py1, tif_h - 1))
            py2 = max(1, min(py2, tif_h))
            if px2 > px1 and py2 > py1:
                img = img.crop((px1, py1, px2, py2))
    return img.convert("RGBA").resize((img_width, img_height), Image.LANCZOS)

def read_shapefile_lines(shp_path, geo_extent):
    """读取SHP文件的边界线段（省市县界）"""
    if not os.path.exists(shp_path):
        print("  *** SHP不存在: %s ***" % shp_path)
        return []
    try:
        sf = shapefile.Reader(shp_path, encoding="gbk")
    except (shapefile.ShapefileException, UnicodeDecodeError):
        try:
            sf = shapefile.Reader(shp_path)
        except (shapefile.ShapefileException, IOError) as e:
            print("  *** SHP读取失败: %s ***" % e)
            return []
    el = (geo_extent["max_lon"] - geo_extent["min_lon"]) * 0.3
    ea = (geo_extent["max_lat"] - geo_extent["min_lat"]) * 0.3
    ext = (geo_extent["min_lon"] - el, geo_extent["max_lon"] + el,
           geo_extent["min_lat"] - ea, geo_extent["max_lat"] + ea)
    all_lines = []
    shapes = sf.shapes()
    print("  SHP %s: %d 个要素" % (os.path.basename(shp_path), len(shapes)))
    for shape in shapes:
        if hasattr(shape, "bbox") and len(shape.bbox) >= 4:
            if (shape.bbox[2] < ext[0] or shape.bbox[0] > ext[1] or
                    shape.bbox[3] < ext[2] or shape.bbox[1] > ext[3]):
                continue
        parts = list(shape.parts) if hasattr(shape, "parts") else [0]
        points = shape.points if hasattr(shape, "points") else []
        if not points:
            continue
        for i in range(len(parts)):
            si = parts[i]
            ei = parts[i + 1] if i + 1 < len(parts) else len(points)
            pp = points[si:ei]
            if len(pp) < 2:
                continue
            cur = []
            for pt in pp:
                if ext[0] <= pt[0] <= ext[1] and ext[2] <= pt[1] <= ext[3]:
                    cur.append((pt[0], pt[1]))
                else:
                    if len(cur) >= 2:
                        all_lines.append(cur)
                    cur = []
            if len(cur) >= 2:
                all_lines.append(cur)
    print("  提取到 %d 条线段" % len(all_lines))
    return all_lines


def read_province_polygons(shp_path, geo_extent):
    """读取省界SHP的多边形及省份名称和质心位置"""
    if not os.path.exists(shp_path):
        print("  *** 省界SHP不存在: %s ***" % shp_path)
        return []
    try:
        sf = shapefile.Reader(shp_path, encoding="gbk")
    except (shapefile.ShapefileException, UnicodeDecodeError):
        try:
            sf = shapefile.Reader(shp_path)
        except (shapefile.ShapefileException, IOError) as e:
            print("  *** 省界SHP读取失败: %s ***" % e)
            return []
    fields = [f[0] for f in sf.fields[1:]]
    print("  省界字段: %s" % fields)
    name_field_idx = None
    for cand in ["NAME", "Name", "name", "省", "PROV", "PROVINCE", "NAME_1", "FULLNAME"]:
        if cand in fields:
            name_field_idx = fields.index(cand)
            break
    if name_field_idx is None and fields:
        name_field_idx = 0
    el = (geo_extent["max_lon"] - geo_extent["min_lon"]) * 0.2
    ea = (geo_extent["max_lat"] - geo_extent["min_lat"]) * 0.2
    results = []
    for sr in sf.shapeRecords():
        shape = sr.shape
        rec = sr.record
        if hasattr(shape, "bbox") and len(shape.bbox) >= 4:
            if (shape.bbox[2] < geo_extent["min_lon"] - el or
                    shape.bbox[0] > geo_extent["max_lon"] + el or
                    shape.bbox[3] < geo_extent["min_lat"] - ea or
                    shape.bbox[1] > geo_extent["max_lat"] + ea):
                continue
        name = ""
        if name_field_idx is not None:
            try:
                raw = rec[name_field_idx]
                if isinstance(raw, bytes):
                    for enc in ("gbk", "utf-8", "latin-1"):
                        try:
                            name = raw.decode(enc).strip()
                            break
                        except (UnicodeDecodeError, LookupError):
                            pass
                else:
                    name = str(raw).strip()
            except (IndexError, TypeError):
                name = ""
        parts = list(shape.parts) if hasattr(shape, "parts") else [0]
        points = shape.points if hasattr(shape, "points") else []
        if not points:
            continue
        max_part_len = 0
        centroid_lon, centroid_lat = None, None
        for pi in range(len(parts)):
            si = parts[pi]
            ei = parts[pi + 1] if pi + 1 < len(parts) else len(points)
            ring = points[si:ei]
            if len(ring) > max_part_len:
                max_part_len = len(ring)
                cx, cy = calculate_polygon_centroid([(p[0], p[1]) for p in ring])
                centroid_lon, centroid_lat = cx, cy
        if (centroid_lon is not None and
                geo_extent["min_lon"] <= centroid_lon <= geo_extent["max_lon"] and
                geo_extent["min_lat"] <= centroid_lat <= geo_extent["max_lat"]):
            results.append((name, centroid_lon, centroid_lat))
    print("  省份标注: %d 个" % len(results))
    return results


def read_city_points(shp_path, geo_extent):
    """读取地级市点位数据SHP，返回[(name, lon, lat),...]"""
    if not os.path.exists(shp_path):
        print("  *** 地级市点位SHP不存在: %s ***" % shp_path)
        return []
    try:
        sf = shapefile.Reader(shp_path, encoding="gbk")
    except (shapefile.ShapefileException, UnicodeDecodeError):
        try:
            sf = shapefile.Reader(shp_path)
        except (shapefile.ShapefileException, IOError) as e:
            print("  *** 地级市SHP读取失败: %s ***" % e)
            return []
    fields = [f[0] for f in sf.fields[1:]]
    name_field_idx = None
    for cand in ["NAME", "Name", "name", "城市", "CITY", "city", "CITYNAME", "地级市"]:
        if cand in fields:
            name_field_idx = fields.index(cand)
            break
    if name_field_idx is None and fields:
        name_field_idx = 0
    el = (geo_extent["max_lon"] - geo_extent["min_lon"]) * 0.1
    ea = (geo_extent["max_lat"] - geo_extent["min_lat"]) * 0.1
    results = []
    for sr in sf.shapeRecords():
        shape = sr.shape
        rec = sr.record
        pts = shape.points if hasattr(shape, "points") else []
        if not pts:
            continue
        lon, lat = float(pts[0][0]), float(pts[0][1])
        if not (geo_extent["min_lon"] - el <= lon <= geo_extent["max_lon"] + el and
                geo_extent["min_lat"] - ea <= lat <= geo_extent["max_lat"] + ea):
            continue
        name = ""
        if name_field_idx is not None:
            try:
                raw = rec[name_field_idx]
                if isinstance(raw, bytes):
                    for enc in ("gbk", "utf-8", "latin-1"):
                        try:
                            name = raw.decode(enc).strip()
                            break
                        except (UnicodeDecodeError, LookupError):
                            pass
                else:
                    name = str(raw).strip()
            except (IndexError, TypeError):
                pass
        results.append((name, lon, lat))
    print("  地级市点位: %d 个（范围内）" % len(results))
    return results

def parse_intensity_kml(kml_path):
    """解析KML文件获取烈度圈坐标数据，返回{烈度值: [(lon,lat),...]}"""
    intensity_data = {}
    if not os.path.exists(kml_path):
        print("  *** KML文件不存在: %s ***" % kml_path)
        return intensity_data
    try:
        with open(kml_path, "rb") as f:
            kml_content = f.read()
        root = etree.fromstring(kml_content)
        ns = root.nsmap.get(None, "http://www.opengis.net/kml/2.2")
        nsmap = {"kml": ns}
        pms = root.findall(".//kml:Placemark", nsmap)
        if not pms:
            pms = root.findall(".//{%s}Placemark" % ns)
        if not pms:
            pms = root.findall(".//Placemark")
        print("  找到 %d 个Placemark" % len(pms))
        for pm in pms:
            name = _kml_get_text(pm, "name", nsmap, ns)
            intensity = _extract_intensity_from_name(name)
            if intensity is None:
                continue
            coords = _extract_kml_linestring_coords(pm, nsmap, ns)
            if coords:
                intensity_data[intensity] = coords
                print("    烈度 %d度: %d 个坐标点" % (intensity, len(coords)))
    except (etree.XMLSyntaxError, etree.XPathError, IOError, OSError) as e:
        print("  *** KML解析失败: %s ***" % e)
    return intensity_data


def _kml_get_text(elem, tag, nsmap, ns):
    """获取KML元素文本内容"""
    for pattern in ["kml:%s" % tag, "{%s}%s" % (ns, tag), tag]:
        try:
            e = elem.find(pattern, nsmap) if "kml:" in pattern else elem.find(pattern)
        except (etree.XPathError, TypeError, AttributeError):
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _extract_intensity_from_name(name):
    """从Placemark名称提取烈度值（如'4度'->4）"""
    if not name:
        return None
    m = re.search(r"(\d+)\s*度", name)
    if m:
        return int(m.group(1))
    try:
        return int(name.strip())
    except ValueError:
        return None


def _extract_kml_linestring_coords(pm, nsmap, ns):
    """从Placemark提取LineString坐标"""
    ls_elems = []
    for tag in ["kml:LineString", "{%s}LineString" % ns, "LineString"]:
        try:
            found = pm.findall(".//" + tag, nsmap) if "kml:" in tag else pm.findall(".//" + tag)
            ls_elems.extend(found)
        except (etree.XPathError, TypeError, AttributeError):
            pass
    for ls in ls_elems:
        coord_text = ""
        for ctag in ["kml:coordinates", "{%s}coordinates" % ns, "coordinates"]:
            try:
                ce = ls.find(ctag, nsmap) if "kml:" in ctag else ls.find(ctag)
            except (etree.XPathError, TypeError, AttributeError):
                ce = None
            if ce is not None and ce.text:
                coord_text = ce.text.strip()
                break
        if coord_text:
            return _parse_kml_coords(coord_text)
    return []


def _parse_kml_coords(text):
    """解析KML坐标文本，返回[(lon,lat),...]"""
    coords = []
    for part in text.replace("\n", " ").replace("\t", " ").split():
        fs = part.strip().split(",")
        if len(fs) >= 2:
            try:
                coords.append((float(fs[0]), float(fs[1])))
            except ValueError:
                continue
    return coords

def draw_solid_lines(draw, lines, geo_extent, img_w, img_h, color, width):
    """绘制实线（省界）"""
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_dashed_lines(draw, lines, geo_extent, img_w, img_h, color, width, dash):
    """绘制虚线（市界、县界）"""
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, color, width, dash)


def _draw_dashed_polyline(draw, pts, color, width, dash):
    """绘制虚线折线"""
    dl, gl = dash
    tp = dl + gl
    acc = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        sl = math.sqrt(dx * dx + dy * dy)
        if sl < 1:
            continue
        ux, uy = dx / sl, dy / sl
        pos = 0.0
        while pos < sl:
            pp = acc % tp
            if pp < dl:
                step = min(dl - pp, sl - pos)
                sx, sy = x1 + ux * pos, y1 + uy * pos
                ex, ey = x1 + ux * (pos + step), y1 + uy * (pos + step)
                draw.line([(sx, sy), (ex, ey)], fill=color, width=width)
            else:
                step = min(tp - pp, sl - pos)
            pos += step
            acc += step


def draw_text_with_white_halo(draw, text, x, y, font, text_color,
                               halo_color=(255, 255, 255, 255)):
    """绘制带白色光晕效果的文字"""
    for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1),
                   (0, 1), (1, -1), (1, 0), (1, 1)]:
        draw.text((x + dx, y + dy), text, fill=halo_color, font=font)
    draw.text((x, y), text, fill=text_color, font=font)


def draw_province_labels(draw, province_data, geo_extent, img_w, img_h):
    """绘制省份名称标注（位于省界内，带白色描边，字体8pt，颜色R=77 G=77 B=77）"""
    font = load_font(FONT_PATH_SONGTI, PROVINCE_LABEL_FONT_SIZE, FONT_PATH_HEITI)
    for name, clon, clat in province_data:
        if not name:
            continue
        px, py = geo_to_pixel(clon, clat, geo_extent, img_w, img_h)
        if not (0 <= px <= img_w and 0 <= py <= img_h):
            continue
        bbox = draw.textbbox((0, 0), name, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw_text_with_white_halo(draw, name, px - tw // 2, py - th // 2,
                                  font, PROVINCE_LABEL_COLOR)


def draw_city_points(draw, city_data, geo_extent, img_w, img_h):
    """绘制地级市点位（黑色空圈内一个实心圆 + 白色背景）和市名称（9pt黑色加白边）"""
    name_font = load_font(FONT_PATH_SONGTI, CITY_LABEL_FONT_SIZE, FONT_PATH_HEITI)
    symbol_r = max(3, CITY_LABEL_FONT_SIZE // 3)
    for name, lon, lat in city_data:
        px, py = geo_to_pixel(lon, lat, geo_extent, img_w, img_h)
        if not (-symbol_r * 2 <= px <= img_w + symbol_r * 2 and
                -symbol_r * 2 <= py <= img_h + symbol_r * 2):
            continue
        bg_r = symbol_r + 2
        draw.ellipse([px - bg_r, py - bg_r, px + bg_r, py + bg_r],
                     fill=(255, 255, 255, 220))
        draw.ellipse([px - symbol_r, py - symbol_r, px + symbol_r, py + symbol_r],
                     fill=None, outline=(0, 0, 0, 255), width=1)
        inner_r = max(1, symbol_r // 2)
        draw.ellipse([px - inner_r, py - inner_r, px + inner_r, py + inner_r],
                     fill=(0, 0, 0, 255))
        if name:
            bbox = draw.textbbox((0, 0), name, font=name_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw_text_with_white_halo(draw, name, px + symbol_r + 3, py - th // 2,
                                      name_font, (0, 0, 0, 255))


def draw_intensity_circles(draw, intensity_data, geo_extent, img_w, img_h):
    """绘制烈度圈（不同烈度不同颜色，返回面积字典）"""
    areas = {}
    for intensity in sorted(intensity_data.keys()):
        coords = intensity_data[intensity]
        if not coords:
            continue
        area = calculate_polygon_area(coords)
        areas[intensity] = area
        color = INTENSITY_COLORS.get(intensity, (255, 0, 0, 255))
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in coords]
        if len(pts) >= 2:
            draw.line(pts + [pts[0]], fill=color, width=INTENSITY_LINE_WIDTH)
        print("    烈度 %d度: 面积约 %.1f 平方千米" % (intensity, area))
    return areas


def draw_intensity_labels(draw, intensity_data, geo_extent, img_w, img_h):
    """绘制烈度圈罗马数字标注（放置在烈度圈最南端）"""
    font = load_font(FONT_PATH_TIMES, INTENSITY_LABEL_FONT_SIZE)
    for intensity, coords in intensity_data.items():
        if not coords:
            continue
        color = INTENSITY_COLORS.get(intensity, (255, 0, 0, 255))
        min_lat_idx = min(range(len(coords)), key=lambda i: coords[i][1])
        px, py = geo_to_pixel(coords[min_lat_idx][0], coords[min_lat_idx][1],
                              geo_extent, img_w, img_h)
        roman = int_to_roman(intensity)
        bbox = draw.textbbox((0, 0), roman, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.rectangle([px - tw // 2 - 3, py - 2, px + tw // 2 + 3, py + th + 2],
                       fill=(255, 255, 255, 220))
        draw.text((px - tw // 2, py), roman, fill=color, font=font)


def _star_polygon(cx, cy, outer_r, inner_r):
    """计算五角星多边形顶点坐标"""
    pts = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        r = outer_r if i % 2 == 0 else inner_r
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def draw_epicenter_star(draw, center_lon, center_lat, geo_extent, img_w, img_h):
    """绘制震中五角星（红色，外面加白边），大小为8pt字体的2/3"""
    px, py = geo_to_pixel(center_lon, center_lat, geo_extent, img_w, img_h)
    r = EPICENTER_STAR_RADIUS
    inner_r = int(r * 0.382)
    draw.polygon(_star_polygon(px, py, r + 2, inner_r + 1), fill=(255, 255, 255, 255))
    draw.polygon(_star_polygon(px, py, r, inner_r), fill=EPICENTER_COLOR,
                 outline=(200, 200, 200, 200))
    print("  震中五角星: (%d, %d), 半径=%dpx" % (px, py, r))


def draw_north_arrow(draw, map_right, map_top, size=50):
    """
    绘制指北针（上边和地图上边框对齐，右侧和地图右边框对齐）
    样式：白色背景，黑色0.35mm边框，左半箭头黑色，右半箭头白色
    """
    box_w = size + 16
    box_h = size + 30
    bg_x2 = map_right
    bg_x1 = bg_x2 - box_w
    bg_y1 = map_top
    bg_y2 = bg_y1 + box_h
    draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2],
                   fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)
    cx = (bg_x1 + bg_x2) // 2
    aty = bg_y1 + 24
    aby = bg_y1 + size + 16
    aw = max(6, size // 4)
    acp = aby - size // 3
    draw.polygon([(cx, aty), (cx - aw, aby), (cx, acp)], fill=(0, 0, 0, 255))
    draw.polygon([(cx, aty), (cx + aw, aby), (cx, acp)],
                 fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    draw.line([(cx, aty), (cx - aw, aby)], fill=(0, 0, 0, 255), width=1)
    draw.line([(cx + aw, aby), (cx, aty)], fill=(0, 0, 0, 255), width=1)
    fn = load_font(FONT_PATH_TIMES, max(12, size // 3))
    bbox = draw.textbbox((0, 0), "N", font=fn)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, bg_y1 + 5), "N", fill=(0, 0, 0, 255), font=fn)


def draw_scale_bar(draw, map_right, map_bottom, scale_denom,
                   map_width, geo_extent, center_lat):
    """
    绘制线段比例尺（右下角，右边框和下边框对齐）
    样式：白色背景，黑色0.35mm边框，上方显示比例文字（如 1：500,000），
          下方为黑白交替线段和公里数标注，字体8pt
    """
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    km_per_pixel = lon_range * 111.32 * math.cos(math.radians(center_lat)) / map_width
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    target_km = map_width * 0.18 * km_per_pixel
    bar_km = nice[0]
    for nv in nice:
        if nv <= target_km * 1.5:
            bar_km = nv
        else:
            break
    bar_px = max(20, int(bar_km / km_per_pixel))
    font = load_font(FONT_PATH_TIMES, SCALE_FONT_SIZE, FONT_PATH_SONGTI)
    end_label = "%d km" % bar_km
    zero_label = "0"
    bb_end = draw.textbbox((0, 0), end_label, font=font)
    bb_zero = draw.textbbox((0, 0), zero_label, font=font)
    end_w = bb_end[2] - bb_end[0]
    zero_w = bb_zero[2] - bb_zero[0]
    text_h = bb_end[3] - bb_end[1]
    bar_h = max(8, int(2.0 * MM_PX))
    pad = 5
    # 比例文字，如 1:500,000（英文冒号，千分位分隔符）
    ratio_text = "1:%s" % "{:,}".format(scale_denom)
    bb_ratio = draw.textbbox((0, 0), ratio_text, font=font)
    ratio_w = bb_ratio[2] - bb_ratio[0]
    ratio_h = bb_ratio[3] - bb_ratio[1]
    box_total_w = max(zero_w // 2 + bar_px + end_w + 10 + pad * 2, ratio_w + pad * 2)
    box_total_h = ratio_h + 2 + bar_h + text_h + pad * 2 + 4
    bx2 = map_right
    by2 = map_bottom
    bx1 = bx2 - box_total_w
    by1 = by2 - box_total_h
    draw.rectangle([bx1, by1, bx2, by2],
                   fill=(255, 255, 255, 240), outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)
    # 绘制比例文字（居中显示在比例尺上方）
    ratio_x = bx1 + (box_total_w - ratio_w) // 2
    draw.text((ratio_x, by1 + pad), ratio_text, fill=(0, 0, 0, 255), font=font)
    # 比例尺线段起始Y坐标（在比例文字下方）
    bar_y = by1 + pad + ratio_h + 2
    bar_x = bx1 + pad + zero_w // 2
    seg_w = bar_px // 4
    for i in range(4):
        c = (0, 0, 0, 255) if i % 2 == 0 else (255, 255, 255, 255)
        draw.rectangle([bar_x + i * seg_w, bar_y,
                        bar_x + (i + 1) * seg_w, bar_y + bar_h],
                       fill=c, outline=(0, 0, 0, 255))
    ty = bar_y + bar_h + 3
    draw.text((bar_x - zero_w // 2, ty), zero_label, fill=(0, 0, 0, 255), font=font)
    draw.text((bar_x + bar_px + 3, ty), end_label, fill=(0, 0, 0, 255), font=font)


def draw_coordinate_border(draw, geo_extent, map_left, map_top, map_width, map_height):
    """
    绘制经纬度刻度线和标注（上侧经度最多6个，左侧纬度最多5个）
    格式：X°X'E（经度），X°X'N（纬度），字体8pt
    """
    font = load_font(FONT_PATH_TIMES, COORD_FONT_SIZE, FONT_PATH_SONGTI)
    map_right = map_left + map_width
    map_bottom = map_top + map_height
    draw.rectangle([map_left, map_top, map_right, map_bottom],
                   outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)
    min_lon = geo_extent["min_lon"]
    max_lon = geo_extent["max_lon"]
    min_lat = geo_extent["min_lat"]
    max_lat = geo_extent["max_lat"]
    tick_len = max(4, int(1.5 * MM_PX))
    lon_step = _choose_tick_step(max_lon - min_lon, target_min=3, target_max=6)
    lon_start = math.ceil(min_lon / lon_step) * lon_step
    lon_values = []
    v = lon_start
    while v <= max_lon + 1e-9:
        lon_values.append(v)
        v += lon_step
    if len(lon_values) > 6:
        lon_values = lon_values[:6]
    for lon_val in lon_values:
        frac = (lon_val - min_lon) / (max_lon - min_lon)
        px = map_left + int(frac * map_width)
        if map_left <= px <= map_right:
            draw.line([(px, map_top), (px, map_top - tick_len)], fill=(0, 0, 0, 255), width=1)
            label = format_degree(lon_val, is_lon=True)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((px - tw // 2, map_top - tick_len - th - 2),
                      label, fill=(0, 0, 0, 255), font=font)
    lat_step = _choose_tick_step(max_lat - min_lat, target_min=3, target_max=5)
    lat_start = math.ceil(min_lat / lat_step) * lat_step
    lat_values = []
    v = lat_start
    while v <= max_lat + 1e-9:
        lat_values.append(v)
        v += lat_step
    if len(lat_values) > 5:
        lat_values = lat_values[:5]
    for lat_val in lat_values:
        frac = (max_lat - lat_val) / (max_lat - min_lat)
        py = map_top + int(frac * map_height)
        if map_top <= py <= map_bottom:
            draw.line([(map_left, py), (map_left - tick_len, py)], fill=(0, 0, 0, 255), width=1)
            label = format_degree(lat_val, is_lon=False)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((map_left - tick_len - tw - 3, py - th // 2),
                      label, fill=(0, 0, 0, 255), font=font)

def draw_legend_panel(draw, x, y, width, height, intensity_data, yanxing_list):
    """
    绘制右侧图例面板
    上部：固定图例（震中位置、烈度、省界、市界、县界、居民地）
    下部：岩性图例（从TIF属性表yanxing字段获取）
    图例左边框与地图右边框重合，图例下边框与地图下边框平行
    """
    draw.rectangle([x, y, x + width, y + height],
                   fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)
    title_font = load_font(FONT_PATH_HEITI, LEGEND_TITLE_FONT_SIZE, FONT_PATH_SONGTI)
    item_font = load_font(FONT_PATH_SONGTI, LEGEND_ITEM_FONT_SIZE, FONT_PATH_HEITI)
    pad = max(6, int(1.5 * MM_PX))
    icon_w = max(18, int(4.5 * MM_PX))
    icon_h = max(6, int(1.5 * MM_PX))
    gap = max(3, int(0.8 * MM_PX))
    line_h = LEGEND_ITEM_FONT_SIZE + 6
    cx = x + pad
    cy = y + pad
    bb_t = draw.textbbox((0, 0), "图  例", font=title_font)
    tw = bb_t[2] - bb_t[0]
    draw.text((x + width // 2 - tw // 2, cy), "图  例", fill=(0, 0, 0, 255), font=title_font)
    cy += bb_t[3] - bb_t[1] + pad
    draw.line([(x + pad, cy), (x + width - pad, cy)], fill=(180, 180, 180, 255), width=1)
    cy += gap + 2

    def _draw_item(symbol_fn, label):
        nonlocal cy
        if cy + line_h > y + height - pad:
            return
        symbol_fn(cy + line_h // 2)
        bb = draw.textbbox((0, 0), label, font=item_font)
        th = bb[3] - bb[1]
        draw.text((cx + icon_w + gap, cy + line_h // 2 - th // 2),
                  label, fill=(0, 0, 0, 255), font=item_font)
        cy += line_h

    def _epi(ic_y):
        r_ = max(4, LEGEND_ITEM_FONT_SIZE // 2)
        ir_ = max(2, int(r_ * 0.382))
        icx = cx + icon_w // 2
        draw.polygon(_star_polygon(icx, ic_y, r_ + 1, ir_ + 1), fill=(255, 255, 255, 255))
        draw.polygon(_star_polygon(icx, ic_y, r_, ir_), fill=EPICENTER_COLOR)
    _draw_item(_epi, "震中位置")

    for intensity in sorted(intensity_data.keys(), reverse=True):
        color = INTENSITY_COLORS.get(intensity, (200, 100, 0, 255))
        roman = int_to_roman(intensity)
        def _int_line(ic_y, col=color):
            draw.line([(cx, ic_y), (cx + icon_w, ic_y)], fill=col, width=INTENSITY_LINE_WIDTH)
        _draw_item(_int_line, "%s度区" % roman)

    def _prov(ic_y):
        draw.line([(cx, ic_y), (cx + icon_w, ic_y)],
                  fill=PROVINCE_BORDER_COLOR, width=PROVINCE_BORDER_WIDTH)
    _draw_item(_prov, "省界")

    def _city_line(ic_y):
        dd, dg = CITY_BORDER_DASH
        for dx in range(0, icon_w, dd + dg):
            draw.line([(cx + dx, ic_y), (min(cx + dx + dd, cx + icon_w), ic_y)],
                      fill=CITY_BORDER_COLOR, width=CITY_BORDER_WIDTH)
    _draw_item(_city_line, "市界")

    def _county_line(ic_y):
        dd, dg = COUNTY_BORDER_DASH
        for dx in range(0, icon_w, dd + dg):
            draw.line([(cx + dx, ic_y), (min(cx + dx + dd, cx + icon_w), ic_y)],
                      fill=COUNTY_BORDER_COLOR, width=COUNTY_BORDER_WIDTH)
    _draw_item(_county_line, "县界")

    def _residential(ic_y):
        icx = cx + icon_w // 2
        r_ = max(3, LEGEND_ITEM_FONT_SIZE // 3)
        ir_ = max(1, r_ // 2)
        draw.ellipse([icx - r_ - 2, ic_y - r_ - 2, icx + r_ + 2, ic_y + r_ + 2],
                     fill=(255, 255, 255, 220))
        draw.ellipse([icx - r_, ic_y - r_, icx + r_, ic_y + r_],
                     fill=None, outline=(0, 0, 0, 255), width=1)
        draw.ellipse([icx - ir_, ic_y - ir_, icx + ir_, ic_y + ir_], fill=(0, 0, 0, 255))
    _draw_item(_residential, "居民地")

    if yanxing_list and cy + line_h < y + height - pad:
        cy += gap
        draw.line([(x + pad, cy), (x + width - pad, cy)], fill=(180, 180, 180, 255), width=1)
        cy += gap + 2
        bb_yx = draw.textbbox((0, 0), "岩性", font=title_font)
        draw.text((x + width // 2 - (bb_yx[2] - bb_yx[0]) // 2, cy),
                  "岩性", fill=(0, 0, 0, 255), font=title_font)
        cy += bb_yx[3] - bb_yx[1] + gap
        for _val, color_rgba, yanxing_name in yanxing_list:
            if cy + line_h > y + height - pad:
                break
            ic_y = cy + line_h // 2
            draw.rectangle([cx, ic_y - icon_h // 2, cx + icon_w, ic_y + icon_h // 2],
                            fill=color_rgba[:3] + (255,), outline=(80, 80, 80, 200))
            bb = draw.textbbox((0, 0), yanxing_name, font=item_font)
            th = bb[3] - bb[1]
            draw.text((cx + icon_w + gap, ic_y - th // 2),
                      yanxing_name, fill=(0, 0, 0, 255), font=item_font)
            cy += line_h


def generate_geological_map(center_lon, center_lat, magnitude, kml_path, output_path):
    """
    生成地震地质构造图

    参数:
        center_lon (float): 震中经度（度）
        center_lat (float): 震中纬度（度）
        magnitude (float): 震级（M）
        kml_path (str): 烈度圈KML文件路径（可为None）
        output_path (str): 输出PNG文件路径
    返回:
        dict: 包含生成信息的字典
    """
    print("=" * 65)
    print("  地 震 地 质 构 造 图 生 成 工 具")
    print("=" * 65)
    print("  震中: %.4f°E, %.4f°N, M%s" % (center_lon, center_lat, magnitude))

    print("\n[1/9] 计算地理范围...")
    radius_km, span_km, scale_denom = get_range_params(magnitude)
    half_km = span_km / 2.0
    delta_lon = km_to_degree_lon(half_km, center_lat)
    delta_lat = km_to_degree_lat(half_km)
    geo_extent = {
        "min_lon": center_lon - delta_lon,
        "max_lon": center_lon + delta_lon,
        "min_lat": center_lat - delta_lat,
        "max_lat": center_lat + delta_lat,
    }
    print("  范围: %dkm, 比例尺: 1:%d" % (span_km, scale_denom))

    print("\n[2/9] 加载地质构造TIF底图...")
    tif_img = load_tif_to_image(TIF_PATH, geo_extent, MAP_WIDTH, MAP_HEIGHT)

    print("\n[3/9] 读取TIF属性表（岩性图例）...")
    yanxing_list = read_tif_attribute_table(TIF_PATH)

    print("\n[4/9] 读取行政边界SHP...")
    print("  --- 省界 ---")
    province_lines = read_shapefile_lines(SHP_PROVINCE_PATH, geo_extent)
    print("  --- 省界多边形（名称标注）---")
    province_polygons = read_province_polygons(SHP_PROVINCE_PATH, geo_extent)
    print("  --- 市界 ---")
    city_lines = read_shapefile_lines(SHP_CITY_PATH, geo_extent)
    print("  --- 县界 ---")
    county_lines = read_shapefile_lines(SHP_COUNTY_PATH, geo_extent)

    print("\n[5/9] 读取地级市点位数据...")
    city_points = read_city_points(SHP_CITY_POINTS_PATH, geo_extent)

    intensity_data = {}
    if kml_path and os.path.exists(kml_path):
        print("\n[6/9] 解析烈度圈KML...")
        intensity_data = parse_intensity_kml(kml_path)
    else:
        print("\n[6/9] 跳过烈度圈（未提供KML文件或文件不存在）")

    print("\n[7/9] 绘制地图图层...")

    # 基础：地质构造TIF（保持原始颜色渲染）
    map_img = tif_img.convert("RGBA")

    # 图层1：行政边界
    bd_layer = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
    draw_bd = ImageDraw.Draw(bd_layer)
    if county_lines:
        print("  绘制县界...")
        draw_dashed_lines(draw_bd, county_lines, geo_extent,
                          MAP_WIDTH, MAP_HEIGHT, COUNTY_BORDER_COLOR,
                          COUNTY_BORDER_WIDTH, COUNTY_BORDER_DASH)
    if city_lines:
        print("  绘制市界...")
        draw_dashed_lines(draw_bd, city_lines, geo_extent,
                          MAP_WIDTH, MAP_HEIGHT, CITY_BORDER_COLOR,
                          CITY_BORDER_WIDTH, CITY_BORDER_DASH)
    if province_lines:
        print("  绘制省界...")
        draw_solid_lines(draw_bd, province_lines, geo_extent,
                         MAP_WIDTH, MAP_HEIGHT, PROVINCE_BORDER_COLOR, PROVINCE_BORDER_WIDTH)
    map_img = Image.alpha_composite(map_img, bd_layer)

    # 图层2：省份名称
    if province_polygons:
        print("  绘制省份名称...")
        pl = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
        draw_pl = ImageDraw.Draw(pl)
        draw_province_labels(draw_pl, province_polygons, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        map_img = Image.alpha_composite(map_img, pl)

    # 图层3：地级市点位（位于TIF上方）
    if city_points:
        print("  绘制地级市点位...")
        cl = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
        draw_cl = ImageDraw.Draw(cl)
        draw_city_points(draw_cl, city_points, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        map_img = Image.alpha_composite(map_img, cl)

    # 图层4：烈度圈（含罗马数字标注）
    areas = {}
    if intensity_data:
        print("  绘制烈度圈...")
        il = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
        draw_il = ImageDraw.Draw(il)
        areas = draw_intensity_circles(draw_il, intensity_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        draw_intensity_labels(draw_il, intensity_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        map_img = Image.alpha_composite(map_img, il)

    # 图层5：震中五角星（最顶层）
    print("  绘制震中五角星...")
    el = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
    draw_el = ImageDraw.Draw(el)
    draw_epicenter_star(draw_el, center_lon, center_lat, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    map_img = Image.alpha_composite(map_img, el)

    # 地图内装饰要素（指北针 + 比例尺）
    draw_map = ImageDraw.Draw(map_img)
    print("  绘制指北针...")
    north_size = max(40, int(12 * MM_PX))
    draw_north_arrow(draw_map, MAP_WIDTH, 0, size=north_size)
    print("  绘制比例尺...")
    draw_scale_bar(draw_map, MAP_WIDTH, MAP_HEIGHT,
                   scale_denom, MAP_WIDTH, geo_extent, center_lat)

    print("\n[8/9] 组装最终图片...")
    final_img = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (255, 255, 255, 255))
    map_left = BORDER_LEFT
    map_top = BORDER_TOP
    final_img.paste(map_img, (map_left, map_top))
    final_draw = ImageDraw.Draw(final_img)

    # 经纬度坐标边框（上侧和左侧）
    draw_coordinate_border(final_draw, geo_extent, map_left, map_top, MAP_WIDTH, MAP_HEIGHT)

    # 右侧图例面板（左边框 = 地图右边框，下边框 = 地图下边框）
    draw_legend_panel(final_draw, map_left + MAP_WIDTH, map_top, LEGEND_WIDTH, MAP_HEIGHT,
                      intensity_data, yanxing_list)

    print("\n[9/9] 保存输出...")
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    output_rgb = final_img.convert("RGB")
    output_rgb.save(output_path, dpi=(OUTPUT_DPI, OUTPUT_DPI), quality=95)
    fsize = os.path.getsize(output_path) / 1024
    print("  已保存: %s" % output_path)
    print("  大小: %.1f KB" % fsize)
    print("  总尺寸: %dx%dpx (%.1fmm x %.1fmm)" %
          (OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_WIDTH / MM_PX, OUTPUT_HEIGHT / MM_PX))
    print("  比例尺: 1:%d" % scale_denom)

    result = {
        "output_path": output_path,
        "center_lon": center_lon,
        "center_lat": center_lat,
        "magnitude": magnitude,
        "scale_denom": scale_denom,
        "geo_extent": geo_extent,
        "intensity_areas": areas,
        "yanxing_count": len(yanxing_list),
        "city_count": len(city_points),
    }
    if intensity_data:
        max_i = max(intensity_data.keys())
        result["max_intensity"] = max_i
        result["max_intensity_area"] = areas.get(max_i, 0)
    print("\n" + "=" * 65)
    return result


def _create_test_kml(kml_path, center_lon, center_lat, magnitude):
    """创建测试用KML烈度圈文件"""
    if magnitude < 6.0:
        radii = {5: 0.15, 6: 0.08, 7: 0.04}
    elif magnitude < 7.0:
        radii = {5: 0.5, 6: 0.3, 7: 0.15, 8: 0.07}
    else:
        radii = {5: 1.5, 6: 0.9, 7: 0.5, 8: 0.25, 9: 0.10}

    def _circle(clon, clat, r, n=36):
        pts = []
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            pts.append("%.6f,%.6f,0" % (clon + r * math.cos(a), clat + r * 0.8 * math.sin(a)))
        return " ".join(pts)

    pms = ""
    for deg, r in radii.items():
        pms += ("<Placemark><name>%d度</name>\n"
                "<LineString><coordinates>%s</coordinates></LineString>\n"
                "</Placemark>\n") % (deg, _circle(center_lon, center_lat, r))

    kml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
           '<Document>\n' + pms + '</Document>\n</kml>')

    out_dir = os.path.dirname(os.path.abspath(kml_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(kml_path, "w", encoding="utf-8") as f:
        f.write(kml)
    print("  创建测试KML: %s" % kml_path)


def test_get_range_params():
    """测试比例尺参数获取函数"""
    print("\n===== 测试: test_get_range_params =====")
    r, s, d = get_range_params(5.5)
    assert r == 15 and s == 30 and d == 150000, "M5.5参数错误: %d,%d,%d" % (r, s, d)
    r, s, d = get_range_params(6.5)
    assert r == 50 and s == 100 and d == 500000, "M6.5参数错误"
    r, s, d = get_range_params(7.5)
    assert r == 150 and s == 300 and d == 1500000, "M7.5参数错误"
    print("  【通过】get_range_params测试全部通过")


def test_format_degree():
    """测试经纬度格式化函数"""
    print("\n===== 测试: test_format_degree =====")
    result = format_degree(103.25, is_lon=True)
    assert "103" in result and "E" in result, "经度格式错误: %s" % result
    result = format_degree(34.06, is_lon=False)
    # 纬度不再附加 N/S 后缀，只显示 X°X′
    assert "34" in result and "N" not in result and "S" not in result, "纬度格式错误: %s" % result
    result = format_degree(-120.5, is_lon=True)
    assert "120" in result and "W" in result, "西经格式错误: %s" % result
    print("  【通过】format_degree测试全部通过")


def test_int_to_roman():
    """测试罗马数字转换函数"""
    print("\n===== 测试: test_int_to_roman =====")
    cases = {1: "I", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X", 12: "XII"}
    for n, expected in cases.items():
        got = int_to_roman(n)
        assert got == expected, "int_to_roman(%d) = %s, 期望 %s" % (n, got, expected)
    print("  【通过】int_to_roman测试全部通过")


def test_parse_intensity_kml():
    """测试KML烈度圈解析函数"""
    import tempfile
    print("\n===== 测试: test_parse_intensity_kml =====")
    kml_content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
                   '<Document>\n'
                   '<Placemark><name>6度</name>\n'
                   '<LineString><coordinates>'
                   '103.1,34.0,0 103.2,34.1,0 103.3,34.0,0 103.2,33.9,0 103.1,34.0,0'
                   '</coordinates></LineString>\n'
                   '</Placemark>\n'
                   '<Placemark><name>7度</name>\n'
                   '<LineString><coordinates>'
                   '103.15,34.0,0 103.25,34.08,0 103.35,34.0,0 103.25,33.92,0 103.15,34.0,0'
                   '</coordinates></LineString>\n'
                   '</Placemark>\n'
                   '</Document>\n</kml>')
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".kml", encoding="utf-8", delete=False) as f:
        f.write(kml_content)
        tmpfile = f.name
    try:
        data = parse_intensity_kml(tmpfile)
        assert 6 in data, "应解析出6度烈度圈"
        assert 7 in data, "应解析出7度烈度圈"
        assert len(data[6]) >= 4, "6度烈度圈应有>=4个坐标点"
        print("  【通过】parse_intensity_kml测试全部通过")
    finally:
        os.unlink(tmpfile)


def test_generate_geological_map():
    """
    完整流程测试：生成地震地质构造图
    测试用例：甘肃甘南州迭部县附近5.5级地震
    """
    print("\n===== 测试: test_generate_geological_map =====")
    test_lon = 103.25
    test_lat = 34.06
    test_magnitude = 5.5
    test_kml = "/tmp/test_geological/test_intensity.kml"
    test_out = "/tmp/test_geological/output_geological_map.png"
    os.makedirs("/tmp/test_geological", exist_ok=True)
    if not os.path.exists(test_kml):
        _create_test_kml(test_kml, test_lon, test_lat, test_magnitude)
    result = generate_geological_map(
        center_lon=test_lon, center_lat=test_lat, magnitude=test_magnitude,
        kml_path=test_kml, output_path=test_out)
    assert result is not None, "生成结果不应为None"
    assert result["center_lon"] == test_lon
    assert result["scale_denom"] == 150000, "M5.5应对应1:150000"
    assert os.path.exists(test_out), "输出文件不存在"
    fsize = os.path.getsize(test_out)
    assert fsize > 1024, "输出文件过小: %d bytes" % fsize
    print("\n  【测试通过】输出: %s (%.1fKB)" % (test_out, fsize / 1024))
    return result


def run_all_tests():
    """运行所有单元测试，返回bool表示是否全部通过"""
    print("\n" + "=" * 65)
    print("  运 行 所 有 测 试")
    print("=" * 65)
    passed = True
    for fn in [test_get_range_params, test_format_degree,
               test_int_to_roman, test_parse_intensity_kml]:
        try:
            fn()
        except AssertionError as e:
            print("\n【失败】%s: %s" % (fn.__name__, e))
            passed = False
    if passed:
        print("\n所有单元测试通过！")
    return passed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="地震地质构造图生成工具")
    parser.add_argument("--test", action="store_true", help="运行所有单元测试")
    parser.add_argument("--full-test", action="store_true", help="运行完整生成测试")
    parser.add_argument("--lon", type=float, default=118.18, help="震中经度")
    parser.add_argument("--lat", type=float, default=39.63, help="震中纬度")
    parser.add_argument("--mag", type=float, default=7.8, help="震级")
    parser.add_argument("--kml", type=str, default=r"../../data/geology/n0432881302350072.kml",
                        help="烈度圈KML文件路径")
    parser.add_argument("--output", type=str,
                        default=r"../../data/geology/output_geological_map.png",
                        help="输出PNG文件路径")
    args = parser.parse_args()

    if args.test or args.full_test:
        ok = run_all_tests()
        if args.full_test:
            test_generate_geological_map()
        if not ok:
            sys.exit(1)
    else:
        generate_geological_map(
            center_lon=args.lon,
            center_lat=args.lat,
            magnitude=args.mag,
            kml_path=args.kml,
            output_path=args.output
        )
