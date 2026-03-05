'''
你好，你是一名优秀的程序员和地质专家。
基于QGIS 3.40.15使用python将信息系统GIS中的.kml添加底图+省、市、县界+断裂，然后根据要求输出png图
说明：
	实现步骤如下：
    (1).kml文件的xml格式打开是这样的：
	<?xml version="1.0" encoding="UTF-8"?>
	<kml xmlns="http://www.opengis.net/kml/2.2">
	<Document>
	<Placemark><name>4度</name>
	<description></description>
	<LineString><coordinates>
	114.78551111594,39.444015151372,0 114.78440837926,39.4460985825,0 114.78327632395,39.448172529053,0 114.78211508894,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>5度</name>
	<description></description>
	<LineString><coordinates>
	114.54659841049,39.369129104779,0 114.54620255623,39.369877072372,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>6度</name>
	<description></description>
	<LineString><coordinates>
	114.42167259312,39.329939431831,0 114.42160044682,39.330076196429,0 114.421526587,...
	</coordinates></LineString>
	</Placemark>
	</Document>
	</kml>
    从该xml中可以获取到烈度 比如说4度，5度，6度(在qgis中显示为烈度圈)，
    需要获取所有的烈度(烈度圈一定是一圈套一圈，越外层烈度依次递减)
	2）底图(使用天地图矢量底图+矢量注记)：
		TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"
		# 矢量底图URL
		TIANDITU_VEC_URL = (
			"http://t{s}.tianditu.gov.cn/vec_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)

		# 矢量注记URL
		TIANDITU_CVA_URL = (
			"http://t{s}.tianditu.gov.cn/cva_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)
	3）指北针放在底图的右上角，白色背景，指针左侧是黑色右侧是白色，上边和底图对齐，右侧和底图对齐，参考制图布局参考图2.png
	4）烈度使用罗马数字(I（1）、V（5）、X（10）...)
	5）字体字号(所有字体，英文用times New Roman，中文用宋体，图例两个字用黑体)
	6）右上角说明说明文字为用户输出+分析得出(文字不会超过450字)，注意说明文字字号可以使用常量设置，左右缩进 文字不能超过输出的画布，首字缩进2个字符
		用户输入：据中国地震台网正式测定:2026年01月26日14时56分甘隶甘南州选部县(103.25”,34.06’)发生5.5级地震,震源深度10千米。综合考虑震中附近地质构造背景、地震波衰减特性，估计了本次地震的地震动预测图。预计极震区地震烈度可达X度，极震区面积估算为X平方千米,地震烈度VI度以上区域面积达X平方千米。

		需要分析的是极震区地震烈度可达X度为最大烈度，极震区面积估算为X平方千米为最大烈度面积，
		地震烈度VI度以上区域面积达X平方千米为烈度VI度以上区域的面积
	7）比例尺使用线段比例尺，放在图右下位置，下面为制图时间：XX年XX月XX日(当前时间)
	说明：省界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp
	      市界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp
	      县界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp
		  全国六代图断裂位置:../../data/geology/断层/全国六代图断裂.KMZ
	8）比例尺使用线段比例尺，根据用户传入的震级动态调整比例尺比值。
	说明：震级M＜6时，比例尺设置为1：150000，震级6≤M＜7时，比例尺设置为1：500000；震级M≥7时，比例尺设置为1：1500000

	省界、市界、县界、全国六代图断裂位置使用常量，kml文件、说明文字通过传参
	注释是中文注释，要求方法和参数需要有中文注释
	代码需要无bug可运行，并写出测试方法，代码分四部分输出。
	图例参考：代码earthquake_map.py，但是在该制图图例在图正下方，图例标题 三行四列布局 超过12个图例不展示，烈度图例用线段表示
	布局参考图：制图布局参考图2.png

┌─────────────────────────────────┐─────────────────┤
│                         N(指北针)│  说明文字（9pt宋体│
│                                 │  首行2字缩进      │
│          地图框                  │   总字数≤450字）
│  (含天地图底图+省市县界+           │                  │
│   断裂+烈度圈+震中)               │                  │
│                                 │                  │
│                                 │  比例尺（动态档位） │
│                                 │  制图日期         │
├─────────────────────────────────┴──────────────────┤
│           图例（三行四列，黑体标题"图  例"）            │
└────────────────────────────────────────────────────┤


'''
'''
你好，你是一名优秀的程序员和地质专家。
基于QGIS 3.40.15使用python将信息系统GIS中的.kml添加底图+省、市、县界+断裂，然后根据要求输出png图
说明：
	实现步骤如下：
    (1).kml文件的xml格式打开是这样的：
	<?xml version="1.0" encoding="UTF-8"?>
	<kml xmlns="http://www.opengis.net/kml/2.2">
	<Document>
	<Placemark><name>4度</name>
	<description></description>
	<LineString><coordinates>
	114.78551111594,39.444015151372,0 114.78440837926,39.4460985825,0 114.78327632395,39.448172529053,0 114.78211508894,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>5度</name>
	<description></description>
	<LineString><coordinates>
	114.54659841049,39.369129104779,0 114.54620255623,39.369877072372,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>6度</name>
	<description></description>
	<LineString><coordinates>
	114.42167259312,39.329939431831,0 114.42160044682,39.330076196429,0 114.421526587,...
	</coordinates></LineString>
	</Placemark>
	</Document>
	</kml>
    从该xml中可以获取到烈度 比如说4度，5度，6度(在qgis中显示为烈度圈)，
    需要获取所有的烈度(烈度圈一定是一圈套一圈，越外层烈度依次递减)
	2）底图(使用天地图矢量底图+矢量注记)：
		TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"
		# 矢量底图URL
		TIANDITU_VEC_URL = (
			"http://t{s}.tianditu.gov.cn/vec_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)

		# 矢量注记URL
		TIANDITU_CVA_URL = (
			"http://t{s}.tianditu.gov.cn/cva_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)
	3）指北针放在底图的右上角，白色背景，指针左侧是黑色右侧是白色，上边和底图对齐，右侧和底图对齐，参考制图布局参考图2.png
	4）烈度使用罗马数字(I（1）、V（5）、X（10）...)
	5）字体字号(所有字体，英文用times New Roman，中文用宋体，图例两个字用黑体)
	6）右上角说明说明文字为用户输出+分析得出(文字不会超过450字)，注意说明文字字号可以使用常量设置，左右缩进 文字不能超过输出的画布，首字缩进2个字符
		用户输入：据中国地震台网正式测定:2026年01月26日14时56分甘隶甘南州选部县(103.25",34.06')发生5.5级地震,震源深度10千米。综合考虑震中附近地质构造背景、地震波衰减特性，估计了本次地震的地震动预测图。预计极震区地震烈度可达X度，极震区面积估算为X平方千米,地震烈度VI度以上区域面积达X平方千米。

		需要分析的是极震区地震烈度可达X度为最大烈度，极震区面积估算为X平方千米为最大烈度面积，
		地震烈度VI度以上区域面积达X平方千米为烈度VI度以上区域的面积
	7）比例尺使用线段比例尺，放在图右下位置，下面为制图时间：XX年XX月XX日(当前时间)
	说明：省界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp
	      市界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp
	      县界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp
		  全国六代图断裂位置:../../data/geology/断层/全国六代图断裂.KMZ
	8）比例尺使用线段比例尺，根据用户传入的震级动态调整比例尺比值。
	说明：震级M＜6时，比例尺设置为1：150000，震级6≤M＜7时，比例尺设置为1：500000；震级M≥7时，比例尺设置为1：1500000

	省界、市界、县界、全国六代图断裂位置使用常量，kml文件、说明文字通过传参
	注释是中文注释，要求方法和参数需要有中文注释
	代码需要无bug可运行，并写出测试方法，代码分四部分输出。
	图例参考：代码earthquake_map.py，但是在该制图图例在图正下方，图例标题 三行四列布局 超过12个图例不展示，烈度图例用线段表示
	布局参考图：制图布局参考图2.png

┌─────────────────────────────────┐─────────────────┤
│                         N(指北针)│  说明文字（9pt宋体│
│                                 │  首行2字缩进      │
│          地图框                  │   总字数≤450字）
│  (含天地图底图+省市县界+           │                  │
│   断裂+烈度圈+震中)               │                  │
│                                 │                  │
│                                 │  比例尺（动态档位） │
│                                 │  制图日期         │
├─────────────────────────────────┴──────────────────┤
│           图例（三行四列，黑体标题"图  例"）            │
└────────────────────────────────────────────────────┤


'''
# -*- coding: utf-8 -*-
"""
地震烈度图生成脚本（基于Python + Pillow + QGIS风格布局）
功能：根据用户输入的KML烈度圈文件，绘制地震烈度分布图，
      叠加天地图底图、省界、市界、县界、断裂图层，
      带经纬度边框、指北针、比例尺、图例、说明文字，并输出PNG图片。

依赖安装：pip install Pillow requests pyshp lxml
作者：acao123
日期：2026-03-05
"""

import os
import sys
import math
import time
import zipfile
import requests
import re
from io import BytesIO
from lxml import etree
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

try:
    import shapefile
except ImportError:
    print("*** 请安装pyshp库: pip install pyshp ***")
    sys.exit(1)

# ============================================================
# 【配置常量区域】
# ============================================================

# 天地图密钥
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

# 矢量底图URL
TIANDITU_VEC_URL = (
        "http://t{s}.tianditu.gov.cn/vec_c/wmts?"
        "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
        "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        "&tk=" + TIANDITU_TK
)

# 矢量注记URL
TIANDITU_CVA_URL = (
        "http://t{s}.tianditu.gov.cn/cva_c/wmts?"
        "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
        "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        "&tk=" + TIANDITU_TK
)

# 是否叠加矢量注记图层
ENABLE_LABEL_OVERLAY = True

# 地图内容区域尺寸
MAP_WIDTH = 1200
MAP_HEIGHT = 900

# 右侧说明文字区域宽度
INFO_PANEL_WIDTH = 300

# 底部图例区域高度
LEGEND_HEIGHT = 150

# 经纬度边框宽度（像素）
BORDER_LEFT = 60
BORDER_RIGHT = 10
BORDER_TOP = 40
BORDER_BOTTOM = 40

# 最终输出图片尺寸
OUTPUT_WIDTH = BORDER_LEFT + MAP_WIDTH + INFO_PANEL_WIDTH + BORDER_RIGHT
OUTPUT_HEIGHT = BORDER_TOP + MAP_HEIGHT + LEGEND_HEIGHT + BORDER_BOTTOM

OUTPUT_DPI = 150
TILE_TIMEOUT = 20
TILE_RETRY = 3

# 地图边框线宽（像素）- 用于统一地图框和图例框的线宽
MAP_BORDER_WIDTH = 2

# ============================================================
# 【SHP文件路径常量】
# ============================================================

SHP_PROVINCE_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
SHP_CITY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
SHP_COUNTY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"
KMZ_FAULT_PATH = r"../../data/geology/断层/全国六代图断裂.KMZ"

# ============================================================
# 【字体路径常量】
# ============================================================

# 黑体（图例标题用）
FONT_PATH_HEITI = "C:/Windows/Fonts/simhei.ttf"
# 宋体（中文正文用）
FONT_PATH_SONGTI = "C:/Windows/Fonts/simsun.ttc"
# Times New Roman（英文用）
FONT_PATH_TIMES = "C:/Windows/Fonts/times.ttf"

# ============================================================
# 【字体大小常量】
# ============================================================

# 说明文字字号常量
INFO_TEXT_FONT_SIZE = 18

# 图例标题"图 例"字体大小
LEGEND_TITLE_FONT_SIZE = 20

# 图例内容文字字体大小
LEGEND_ITEM_FONT_SIZE = 16

# 制图日期字体大小
DATE_FONT_SIZE = 18

# ============================================================
# 【行政边界线样式】
# ============================================================

# 省界：深灰色实线
PROVINCE_BORDER_COLOR = (60, 60, 60, 255)
PROVINCE_BORDER_WIDTH = 2

# 市界：灰色虚线
CITY_BORDER_COLOR = (100, 100, 100, 255)
CITY_BORDER_WIDTH = 1
CITY_BORDER_DASH = (12, 6)

# 县界：浅灰色虚线
COUNTY_BORDER_COLOR = (160, 160, 160, 220)
COUNTY_BORDER_WIDTH = 1
COUNTY_BORDER_DASH = (8, 4)

# ============================================================
# 【断裂线样式】- 统一图层和图例颜色
# ============================================================

# 全新世断层：红色
FAULT_HOLOCENE_COLOR = (255, 0, 0, 255)
FAULT_HOLOCENE_WIDTH = 2

# 晚更新世断层：紫红色
FAULT_LATE_PLEISTOCENE_COLOR = (255, 0, 255, 255)
FAULT_LATE_PLEISTOCENE_WIDTH = 2

# 早中更新世断层：青绿色
FAULT_EARLY_PLEISTOCENE_COLOR = (0, 200, 150, 255)
FAULT_EARLY_PLEISTOCENE_WIDTH = 2

# 默认断层颜色
FAULT_DEFAULT_COLOR = (200, 150, 50, 220)
FAULT_DEFAULT_WIDTH = 2

# ============================================================
# 【烈度圈颜色配置】- 使用红色系，外圈浅，内圈深
# ============================================================

INTENSITY_COLORS = {
    4: (255, 200, 200, 180),  # IV度 - 浅红
    5: (255, 150, 150, 200),  # V度 - 较浅红
    6: (255, 100, 100, 220),  # VI度 - 中红
    7: (255, 50, 50, 230),  # VII度 - 较深红
    8: (220, 0, 0, 240),  # VIII度 - 深红
    9: (180, 0, 0, 250),  # IX度 - 更深红
    10: (140, 0, 0, 255),  # X度 - 最深红
    11: (100, 0, 0, 255),  # XI度
    12: (60, 0, 0, 255),  # XII度
}

# 烈度圈线宽
INTENSITY_LINE_WIDTH = 3

# ============================================================
# 【震中标记样式】
# ============================================================

EPICENTER_COLOR = (255, 0, 0, 255)
EPICENTER_SIZE = 12

# 天地图矩阵参数
TIANDITU_MATRIX = {}
for _z in range(1, 19):
    _n_cols = 2 ** _z
    _n_rows = 2 ** (_z - 1)
    TIANDITU_MATRIX[_z] = {
        "n_cols": _n_cols, "n_rows": _n_rows,
        "tile_span_lon": 360.0 / _n_cols, "tile_span_lat": 180.0 / _n_rows,
    }


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
    说明:
        震级M＜6时，比例尺设置为1：150000
        震级6≤M＜7时，比例尺设置为1：500000
        震级M≥7时，比例尺设置为1：1500000
    """
    if magnitude < 6.0:
        return 150000
    elif magnitude < 7.0:
        return 500000
    else:
        return 1500000


def km_to_degree_lon(km, latitude):
    """
    千米转经度差

    参数:
        km (float): 距离（千米）
        latitude (float): 纬度
    返回:
        float: 经度差
    """
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """
    千米转纬度差

    参数:
        km (float): 距离（千米）
    返回:
        float: 纬度差
    """
    return km / 110.574


def geo_to_pixel(lon, lat, geo_extent, img_width, img_height):
    """
    经纬度转地图区域像素坐标

    参数:
        lon (float): 经度
        lat (float): 纬度
        geo_extent (dict): 地理范围字典
        img_width (int): 图片宽度
        img_height (int): 图片高度
    返回:
        tuple: (px, py) 像素坐标
    """
    px = (lon - geo_extent["min_lon"]) / (geo_extent["max_lon"] - geo_extent["min_lon"]) * img_width
    py = (geo_extent["max_lat"] - lat) / (geo_extent["max_lat"] - geo_extent["min_lat"]) * img_height
    return int(round(px)), int(round(py))


def format_degree(value, is_lon=True):
    """
    将十进制度数格式化为 度°分' 格式

    参数:
        value (float): 十进制度数
        is_lon (bool): True表示经度，False表示纬度
    返回:
        str: 格式化字符串
    """
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes = int((abs_val - degrees) * 60)
    if is_lon:
        suffix = "E" if value >= 0 else "W"
    else:
        suffix = "N" if value >= 0 else "S"
    return f"{degrees}°{minutes:02d}'"


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

    # 计算多边形中心纬度用于面积换算
    center_lat = sum(c[1] for c in coords) / len(coords)

    # 将经纬度转换为近似平面坐标（千米）
    km_coords = []
    for lon, lat in coords:
        x = lon * 111.32 * math.cos(math.radians(center_lat))
        y = lat * 110.574
        km_coords.append((x, y))

    # 鞋带公式
    n = len(km_coords)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += km_coords[i][0] * km_coords[j][1]
        area -= km_coords[j][0] * km_coords[i][1]

    return abs(area) / 2.0


def load_font(font_path, size, fallback_path=None):
    """
    加载字体，失败时使用备用字体

    参数:
        font_path (str): 主字体路径
        size (int): 字号
        fallback_path (str): 备用字体路径
    返回:
        ImageFont: 字体对象
    """
    try:
        return ImageFont.truetype(font_path, size)
    except (IOError, OSError):
        if fallback_path:
            try:
                return ImageFont.truetype(fallback_path, size)
            except (IOError, OSError):
                pass
        return ImageFont.load_default()


# ============================================================
# 【天地图瓦片函数】
# ============================================================

def select_zoom_level(geo_extent, img_width, img_height):
    """
    选择最优缩放级别

    参数:
        geo_extent (dict): 地理范围
        img_width (int): 目标宽度
        img_height (int): 目标高度
    返回:
        int: 缩放级别
    """
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]
    best_zoom = 1
    for z in range(1, 19):
        m = TIANDITU_MATRIX[z]
        tx = math.ceil(lon_range / m["tile_span_lon"]) + 1
        ty = math.ceil(lat_range / m["tile_span_lat"]) + 1
        mosaic_w = tx * 256
        mosaic_h = ty * 256
        if mosaic_w >= img_width and mosaic_h >= img_height:
            best_zoom = z
            if tx * ty > 600:
                best_zoom = z - 1 if z > 1 else z
                break
        else:
            best_zoom = z
    return max(3, min(best_zoom, 18))


def lonlat_to_tile_epsg4326(lon, lat, zoom):
    """
    经纬度转瓦片行列号

    参数:
        lon (float): 经度
        lat (float): 纬度
        zoom (int): 缩放级别
    返回:
        tuple: (col, row) 瓦片列号和行号
    """
    m = TIANDITU_MATRIX[zoom]
    col = int(math.floor((lon + 180.0) / m["tile_span_lon"]))
    row = int(math.floor((90.0 - lat) / m["tile_span_lat"]))
    return max(0, min(col, m["n_cols"] - 1)), max(0, min(row, m["n_rows"] - 1))


def tile_to_lonlat_epsg4326(tile_col, tile_row, zoom):
    """
    瓦片行列号转左上角经纬度

    参数:
        tile_col (int): 瓦片列号
        tile_row (int): 瓦片行号
        zoom (int): 缩放级别
    返回:
        tuple: (lon, lat) 经纬度
    """
    m = TIANDITU_MATRIX[zoom]
    return -180.0 + tile_col * m["tile_span_lon"], 90.0 - tile_row * m["tile_span_lat"]


def download_tile_with_retry(url_template, zoom, tile_col, tile_row, retries=TILE_RETRY):
    """
    下载瓦片（支持重试和服务器轮询）

    参数:
        url_template (str): URL模板
        zoom (int): 缩放级别
        tile_col (int): 瓦片列号
        tile_row (int): 瓦片行号
        retries (int): 重试次数
    返回:
        PIL.Image: 瓦片图片，失败返回None
    """
    for attempt in range(retries):
        server = (tile_col + tile_row + attempt) % 8
        url = (url_template.replace("{s}", str(server)).replace("{z}", str(zoom))
               .replace("{x}", str(tile_col)).replace("{y}", str(tile_row)))
        try:
            resp = requests.get(url, timeout=TILE_TIMEOUT)
            if resp.status_code == 200 and len(resp.content) > 0:
                return Image.open(BytesIO(resp.content))
            elif attempt < retries - 1:
                time.sleep(0.3)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5)
            else:
                print(f"    瓦片失败: z={zoom} col={tile_col} row={tile_row}: {e}")
    return None

def fetch_basemap(geo_extent, img_width, img_height):
    """
    获取天地图矢量底图+矢量注记

    参数:
        geo_extent (dict): 地理范围
        img_width (int): 地图区域宽度
        img_height (int): 地图区域高度
    返回:
        PIL.Image: 底图图片
    """
    print(f"  地理范围: 经度[{geo_extent['min_lon']:.4f}, {geo_extent['max_lon']:.4f}], "
          f"纬度[{geo_extent['min_lat']:.4f}, {geo_extent['max_lat']:.4f}]")

    zoom = select_zoom_level(geo_extent, img_width, img_height)
    print(f"  缩放级别: zoom={zoom}")
    matrix = TIANDITU_MATRIX[zoom]

    col_min, row_min = lonlat_to_tile_epsg4326(geo_extent["min_lon"], geo_extent["max_lat"], zoom)
    col_max, row_max = lonlat_to_tile_epsg4326(geo_extent["max_lon"], geo_extent["min_lat"], zoom)
    col_min = max(0, col_min - 1)
    row_min = max(0, row_min - 1)
    col_max = min(matrix["n_cols"] - 1, col_max + 1)
    row_max = min(matrix["n_rows"] - 1, row_max + 1)
    ntx = col_max - col_min + 1
    nty = row_max - row_min + 1
    print(f"  瓦片: {ntx}x{nty}={ntx * nty}块")

    ts = 256
    mw, mh = ntx * ts, nty * ts

    # 底色使用浅蓝色模拟海洋
    mosaic_vec = Image.new("RGBA", (mw, mh), (170, 211, 223, 255))
    mosaic_cva = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))

    mo_lon, mo_lat = tile_to_lonlat_epsg4326(col_min, row_min, zoom)
    me_lon, me_lat = tile_to_lonlat_epsg4326(col_max + 1, row_max + 1, zoom)

    dl_ok, dl_fail = 0, 0
    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            px, py = (col - col_min) * ts, (row - row_min) * ts
            # 矢量底图
            tile = download_tile_with_retry(TIANDITU_VEC_URL, zoom, col, row)
            if tile:
                mosaic_vec.paste(tile.convert("RGBA"), (px, py))
                dl_ok += 1
            else:
                dl_fail += 1
            # 矢量注记
            if ENABLE_LABEL_OVERLAY:
                lbl = download_tile_with_retry(TIANDITU_CVA_URL, zoom, col, row)
                if lbl:
                    mosaic_cva.paste(lbl.convert("RGBA"), (px, py))
        print(f"    下载: {(col - col_min + 1) / ntx * 100:.0f}%")

    print(f"  瓦片完成: 成功={dl_ok}, 失败={dl_fail}")

    def g2m(lon, lat):
        return ((lon - mo_lon) / (me_lon - mo_lon) * mw,
                (mo_lat - lat) / (mo_lat - me_lat) * mh)

    cl, ct = g2m(geo_extent["min_lon"], geo_extent["max_lat"])
    cr, cb = g2m(geo_extent["max_lon"], geo_extent["min_lat"])
    cl, ct = max(0, int(round(cl))), max(0, int(round(ct)))
    cr, cb = min(mw, int(round(cr))), min(mh, int(round(cb)))

    # 裁剪底图
    cropped_vec = mosaic_vec.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_vec
    resized_vec = cropped_vec.resize((img_width, img_height), Image.LANCZOS)

    # 注记使用LANCZOS缩放
    if ENABLE_LABEL_OVERLAY:
        cropped_cva = mosaic_cva.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_cva
        resized_cva = cropped_cva.resize((img_width, img_height), Image.LANCZOS)
        result = Image.alpha_composite(resized_vec, resized_cva)
    else:
        result = resized_vec

    return result


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
    说明:
        KML文件中的name标签包含烈度信息，如"4度"、"5度"等
        coordinates标签包含烈度圈的坐标点
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

        # 查找所有Placemark
        placemarks = root.findall('.//kml:Placemark', nsmap)
        if not placemarks:
            placemarks = root.findall('.//{' + ns + '}Placemark')
        if not placemarks:
            placemarks = root.findall('.//Placemark')

        print(f"  找到 {len(placemarks)} 个Placemark")

        for pm in placemarks:
            # 获取烈度名称
            name = _get_element_text(pm, 'name', nsmap, ns)

            # 从名称中提取烈度值（如"4度" -> 4）
            intensity = _extract_intensity_from_name(name)
            if intensity is None:
                continue

            # 获取坐标
            coords = _extract_linestring_coords(pm, nsmap, ns)
            if coords:
                intensity_data[intensity] = coords
                print(f"    烈度 {intensity}度: {len(coords)} 个坐标点")

    except Exception as e:
        print(f"  *** KML解析失败: {e} ***")

    return intensity_data


def _get_element_text(elem, tag, nsmap, ns):
    """
    获取KML元素的文本内容

    参数:
        elem: XML元素
        tag (str): 标签名
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        str: 文本内容
    """
    for pattern in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(pattern, nsmap) if 'kml:' in pattern else elem.find(pattern)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _extract_intensity_from_name(name):
    """
    从名称中提取烈度值

    参数:
        name (str): 名称字符串，如"4度"、"5度"
    返回:
        int: 烈度值，无法解析返回None
    """
    if not name:
        return None

    # 匹配数字+度的模式
    match = re.search(r'(\d+)\s*度', name)
    if match:
        return int(match.group(1))

    # 尝试直接解析数字
    try:
        return int(name.strip())
    except ValueError:
        return None


def _extract_linestring_coords(pm, nsmap, ns):
    """
    从Placemark中提取LineString坐标

    参数:
        pm: Placemark元素
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        list: [(lon, lat), ...]
    """
    coords = []

    # 查找LineString元素
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
    """
    解析KML coordinates文本

    参数:
        text (str): 坐标文本，格式如 "lon,lat,alt lon,lat,alt ..."
    返回:
        list: [(lon, lat), ...]
    """
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
        dict: 地理范围字典
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

    # 添加边距
    lon_margin = (max_lon - min_lon) * margin_ratio
    lat_margin = (max_lat - min_lat) * margin_ratio

    return {
        "min_lon": min_lon - lon_margin,
        "max_lon": max_lon + lon_margin,
        "min_lat": min_lat - lat_margin,
        "max_lat": max_lat + lat_margin,
    }


def calculate_epicenter(intensity_data):
    """
    根据烈度圈数据计算震中位置（取最大烈度圈的中心）

    参数:
        intensity_data (dict): 烈度圈数据
    返回:
        tuple: (lon, lat) 震中经纬度
    """
    if not intensity_data:
        return None, None

    # 获取最大烈度
    max_intensity = max(intensity_data.keys())
    coords = intensity_data[max_intensity]

    if not coords:
        return None, None

    # 计算中心点
    center_lon = sum(c[0] for c in coords) / len(coords)
    center_lat = sum(c[1] for c in coords) / len(coords)

    return center_lon, center_lat


# ============================================================
# 【SHP文件读取函数】
# ============================================================

def read_shapefile_lines(shp_path, geo_extent):
    """
    读取SHP文件边界线段

    参数:
        shp_path (str): SHP文件路径
        geo_extent (dict): 地理范围
    返回:
        list: [[(lon,lat),...], ...]
    """
    if not os.path.exists(shp_path):
        print(f"  *** SHP不存在: {shp_path} ***")
        return []
    try:
        sf = shapefile.Reader(shp_path)
    except Exception as e:
        print(f"  *** SHP读取失败: {e} ***")
        return []

    el = (geo_extent["max_lon"] - geo_extent["min_lon"]) * 0.3
    ea = (geo_extent["max_lat"] - geo_extent["min_lat"]) * 0.3
    ext = (geo_extent["min_lon"] - el, geo_extent["max_lon"] + el,
           geo_extent["min_lat"] - ea, geo_extent["max_lat"] + ea)

    all_lines = []
    shapes = sf.shapes()
    print(f"  SHP {os.path.basename(shp_path)}: {len(shapes)} 个要素")

    for shape in shapes:
        if hasattr(shape, 'bbox') and len(shape.bbox) >= 4:
            if (shape.bbox[2] < ext[0] or shape.bbox[0] > ext[1] or
                    shape.bbox[3] < ext[2] or shape.bbox[1] > ext[3]):
                continue
        parts = list(shape.parts) if hasattr(shape, 'parts') else [0]
        points = shape.points if hasattr(shape, 'points') else []
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

    print(f"  提取到 {len(all_lines)} 条线段")
    return all_lines


# ============================================================
# 【KMZ断裂解析函数】
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent):
    """
    解析KMZ断裂线

    参数:
        kmz_path (str): KMZ文件路径
        geo_extent (dict): 地理范围
    返回:
        dict: {"holocene":[], "late_pleistocene":[], "early_pleistocene":[], "default":[]}
    """
    empty = {"holocene": [], "late_pleistocene": [], "early_pleistocene": [], "default": []}
    if not os.path.exists(kmz_path):
        print(f"  *** KMZ不存在: {kmz_path} ***")
        return empty

    el = (geo_extent["max_lon"] - geo_extent["min_lon"]) * 0.3
    ea = (geo_extent["max_lat"] - geo_extent["min_lat"]) * 0.3
    ext = (geo_extent["min_lon"] - el, geo_extent["max_lon"] + el,
           geo_extent["min_lat"] - ea, geo_extent["max_lat"] + ea)

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
    pms = root.findall('.//kml:Placemark', nsmap)
    if not pms:
        pms = root.findall('.//{' + ns + '}Placemark')
    if not pms:
        pms = root.findall('.//Placemark')

    for pm in pms:
        name = _get_element_text(pm, 'name', nsmap, ns)
        surl = _get_element_text(pm, 'styleUrl', nsmap, ns)
        desc = _get_element_text(pm, 'description', nsmap, ns)
        ftype = _classify_fault(name, surl, desc, sc)
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


def _parse_kml_styles(root, nsmap, ns):
    """解析KML样式"""
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
    return sc


def _classify_fault(name, style_url, description, style_colors):
    """分类断裂类型"""
    combined = (name + " " + description).lower()
    if "全新世" in name or "全新世" in description or "holocene" in combined:
        return "holocene"
    if "晚更新世" in name or "晚更新世" in description or "late pleistocene" in combined:
        return "late_pleistocene"
    if any(k in name or k in description for k in ["早中更新世", "早更新世", "中更新世"]):
        return "early_pleistocene"
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
# 【绘制函数】
# ============================================================

def draw_solid_lines(draw, lines, geo_extent, img_w, img_h, color, width):
    """
    绘制实线

    参数:
        draw: ImageDraw对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
        color (tuple): RGBA颜色
        width (int): 线宽
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_dashed_lines(draw, lines, geo_extent, img_w, img_h, color, width, dash):
    """
    绘制虚线

    参数:
        draw: ImageDraw对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
        color (tuple): RGBA颜色
        width (int): 线宽
        dash (tuple): (线段长, 间隔长)
    """
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


def draw_fault_lines(draw, fault_data, geo_extent, img_w, img_h):
    """
    绘制断裂线（颜色与图例一致）

    参数:
        draw: ImageDraw对象
        fault_data (dict): 断裂数据
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
    """
    # 使用与图例一致的颜色常量
    style_map = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH),
    }

    for ftype, lines in fault_data.items():
        if not lines:
            continue
        color, width = style_map.get(ftype, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH))
        draw_solid_lines(draw, lines, geo_extent, img_w, img_h, color, width)


def draw_intensity_circles(draw, intensity_data, geo_extent, img_w, img_h):
    """
    绘制烈度圈（线条形式）

    参数:
        draw: ImageDraw对象
        intensity_data (dict): 烈度圈数据
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
    返回:
        dict: 烈度面积统计 {烈度: 面积}
    """
    areas = {}

    # 按烈度从小到大排序绘制（外圈先画）
    sorted_intensities = sorted(intensity_data.keys())

    for intensity in sorted_intensities:
        coords = intensity_data[intensity]
        if not coords:
            continue

        # 计算面积
        area = calculate_polygon_area(coords)
        areas[intensity] = area

        # 获取颜色
        color = INTENSITY_COLORS.get(intensity, (255, 0, 0, 200))

        # 转换为像素坐标
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in coords]

        if len(pts) >= 2:
            # 绘制闭合曲线
            pts_closed = pts + [pts[0]]  # 闭合
            draw.line(pts_closed, fill=color, width=INTENSITY_LINE_WIDTH)

        print(f"    烈度 {intensity}度: 面积约 {area:.1f} 平方千米")

    return areas


def draw_intensity_labels(draw, intensity_data, geo_extent, img_w, img_h):
    """
    绘制烈度标注（使用罗马数字）

    参数:
        draw: ImageDraw对象
        intensity_data (dict): 烈度圈数据
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
    """
    font = load_font(FONT_PATH_TIMES, 16)

    for intensity, coords in intensity_data.items():
        if not coords:
            continue

        # 在烈度圈的左下方标注
        # 找到纬度最低的点附近
        min_lat_idx = min(range(len(coords)), key=lambda i: coords[i][1])
        label_lon, label_lat = coords[min_lat_idx]

        # 稍微偏移
        px, py = geo_to_pixel(label_lon, label_lat, geo_extent, img_w, img_h)

        # 罗马数字标注
        roman = int_to_roman(intensity)
        label = f"{roman}"

        # 白色背景
        bbox = draw.textbbox((px, py), label, font=font)
        draw.rectangle([bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1],
                       fill=(255, 255, 255, 220))
        draw.text((px, py), label, fill=(200, 0, 0, 255), font=font)


def draw_epicenter(draw, center_lon, center_lat, geo_extent, img_w, img_h):
    """
    绘制震中标记（实心圆点）

    参数:
        draw: ImageDraw对象
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        geo_extent (dict): 地理范围
        img_w (int): 图片宽度
        img_h (int): 图片高度
    """
    px, py = geo_to_pixel(center_lon, center_lat, geo_extent, img_w, img_h)
    r = EPICENTER_SIZE // 2

    # 绘制实心圆点
    draw.ellipse([px - r, py - r, px + r, py + r],
                 fill=EPICENTER_COLOR, outline=(0, 0, 0, 255), width=1)

    print(f"  震中: ({px}, {py})")


def draw_north_arrow(draw, x, y, size=50):
    """
    绘制指北针（白色背景，左黑右白）

    参数:
        draw: ImageDraw对象
        x (int): 中心X坐标
        y (int): 上边缘Y坐标
        size (int): 指北针大小
    """
    # 白色背景
    bg_padding = 8
    bg_x1 = x - size // 2 - bg_padding
    bg_y1 = y
    bg_x2 = x + size // 2 + bg_padding
    bg_y2 = y + size + bg_padding + 20

    draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2],
                   fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=1)

    # 指北针箭头
    center_x = x
    arrow_top = y + 20
    arrow_bottom = y + size + 10
    arrow_width = size // 4

    # 左半边（黑色）
    left_points = [
        (center_x, arrow_top),
        (center_x - arrow_width, arrow_bottom),
        (center_x, arrow_bottom - size // 3)
    ]
    draw.polygon(left_points, fill=(0, 0, 0, 255))

    # 右半边（白色带黑边）
    right_points = [
        (center_x, arrow_top),
        (center_x + arrow_width, arrow_bottom),
        (center_x, arrow_bottom - size // 3)
    ]
    draw.polygon(right_points, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))

    # N字母
    font = load_font(FONT_PATH_TIMES, size // 3)
    bbox = draw.textbbox((0, 0), "N", font=font)
    text_w = bbox[2] - bbox[0]
    draw.text((center_x - text_w // 2, y + 5), "N", fill=(0, 0, 0, 255), font=font)


def draw_scale_bar(draw, x, y, scale_denom, map_width, geo_extent, center_lat):
    """
    绘制线段比例尺

    参数:
        draw: ImageDraw对象
        x (int): 起始X坐标
        y (int): Y坐标
        scale_denom (int): 比例尺分母
        map_width (int): 地图宽度
        geo_extent (dict): 地理范围
        center_lat (float): 中心纬度
    """
    # 计算每像素代表的千米数
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    km_per_pixel = lon_range * 111.32 * math.cos(math.radians(center_lat)) / map_width

    # 选择合适的比例尺长度
    nice_values = [1, 2, 5, 10, 15, 20, 50, 100]
    target_km = map_width * 0.12 * km_per_pixel
    bar_km = nice_values[0]
    for nv in nice_values:
        if nv <= target_km * 1.5:
            bar_km = nv
        else:
            break

    bar_px = int(bar_km / km_per_pixel)

    font = load_font(FONT_PATH_SONGTI, 11)

    # 绘制黑白交替的比例尺条
    bar_height = 6
    num_segments = 4
    seg_width = bar_px // num_segments

    for i in range(num_segments):
        color = (0, 0, 0, 255) if i % 2 == 0 else (255, 255, 255, 255)
        draw.rectangle([x + i * seg_width, y, x + (i + 1) * seg_width, y + bar_height],
                       fill=color, outline=(0, 0, 0, 255))

    # 标注
    draw.text((x, y + bar_height + 2), "0", fill=(0, 0, 0, 255), font=font)

    mid_label = str(bar_km // 2)
    mid_bbox = draw.textbbox((0, 0), mid_label, font=font)
    draw.text((x + bar_px // 2 - (mid_bbox[2] - mid_bbox[0]) // 2, y + bar_height + 2),
              mid_label, fill=(0, 0, 0, 255), font=font)

    end_label = f"{bar_km}"
    end_bbox = draw.textbbox((0, 0), end_label, font=font)
    draw.text((x + bar_px - (end_bbox[2] - end_bbox[0]) // 2, y + bar_height + 2),
              end_label, fill=(0, 0, 0, 255), font=font)

    # 千米单位
    unit_label = "千米"
    draw.text((x + bar_px + 8, y + bar_height + 2), unit_label, fill=(0, 0, 0, 255), font=font)


def draw_coordinate_border(draw, geo_extent, map_left, map_top, map_width, map_height):
    """
    绘制经纬度刻度边框

    参数:
        draw: ImageDraw对象
        geo_extent (dict): 地理范围
        map_left (int): 地图区域左边界
        map_top (int): 地图区域上边界
        map_width (int): 地图宽度
        map_height (int): 地图高度
    """
    font = load_font(FONT_PATH_TIMES, 11)

    map_right = map_left + map_width
    map_bottom = map_top + map_height

    # 绘制边框（使用统一的边框线宽常量）
    draw.rectangle([map_left, map_top, map_right, map_bottom], outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)

    min_lon = geo_extent["min_lon"]
    max_lon = geo_extent["max_lon"]
    min_lat = geo_extent["min_lat"]
    max_lat = geo_extent["max_lat"]

    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    # 选择刻度间隔
    lon_step = _choose_tick_step(lon_range)
    lat_step = _choose_tick_step(lat_range)

    tick_len = 6

    # 经度刻度
    lon_start = math.ceil(min_lon / lon_step) * lon_step
    lon_val = lon_start
    while lon_val <= max_lon:
        frac = (lon_val - min_lon) / (max_lon - min_lon)
        px = map_left + int(frac * map_width)

        if map_left <= px <= map_right:
            draw.line([(px, map_top), (px, map_top - tick_len)], fill=(0, 0, 0, 255), width=1)
            draw.line([(px, map_bottom), (px, map_bottom + tick_len)], fill=(0, 0, 0, 255), width=1)

            label = format_degree(lon_val, is_lon=True)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            draw.text((px - tw // 2, map_top - tick_len - 14), label, fill=(0, 0, 0, 255), font=font)
            draw.text((px - tw // 2, map_bottom + tick_len + 2), label, fill=(0, 0, 0, 255), font=font)

        lon_val += lon_step

    # 纬度刻度
    lat_start = math.ceil(min_lat / lat_step) * lat_step
    lat_val = lat_start
    while lat_val <= max_lat:
        frac = (max_lat - lat_val) / (max_lat - min_lat)
        py = map_top + int(frac * map_height)

        if map_top <= py <= map_bottom:
            draw.line([(map_left, py), (map_left - tick_len, py)], fill=(0, 0, 0, 255), width=1)
            draw.line([(map_right, py), (map_right + tick_len, py)], fill=(0, 0, 0, 255), width=1)

            label = format_degree(lat_val, is_lon=False)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((map_left - tick_len - tw - 4, py - th // 2), label, fill=(0, 0, 0, 255), font=font)

        lat_val += lat_step


def _choose_tick_step(range_deg, target_min=4, target_max=6):
    """选择合适的刻度间隔"""
    candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    for step in candidates:
        n_ticks = range_deg / step
        if target_min <= n_ticks <= target_max:
            return step
    return range_deg / 5.0


# ============================================================
# 【右侧说明文字绘制】
# ============================================================

def draw_info_panel(draw, x, y, width, height, description_text,
                    scale_denom, geo_extent, center_lat, map_width, map_top):
    """
    绘制右侧说明文字区域（指北针在地图框右上角）

    参数:
        draw: ImageDraw对象
        x (int): 区域左上角X坐标
        y (int): 区域左上角Y坐标
        width (int): 区域宽度
        height (int): 区域高度
        description_text (str): 说明文字（不超过450字）
        scale_denom (int): 比例尺分母
        geo_extent (dict): 地理范围
        center_lat (float): 中心纬度
        map_width (int): 地图宽度
        map_top (int): 地图框顶部Y坐标
    """
    # 白色背景
    draw.rectangle([x, y, x + width, y + height], fill=(255, 255, 255, 255))

    # 指北针放在地图框右上角（上边和地图框上边对齐，右侧和地图框右边对齐）
    # 地图框右边界是 x（即 BORDER_LEFT + MAP_WIDTH），地图框顶部是 map_top
    north_arrow_size = 45
    north_arrow_x = x - 10 - north_arrow_size // 2  # 在地图框内，靠近右边框
    north_arrow_y = map_top + 5  # 与地图框顶部对齐，稍微下移一点
    draw_north_arrow(draw, north_arrow_x, north_arrow_y, size=north_arrow_size)

    # 说明文字（9pt宋体，首行2字缩进）
    font = load_font(FONT_PATH_SONGTI, INFO_TEXT_FONT_SIZE)

    text_x = x + 10
    text_y = y + 10
    text_width = width - 20

    # 处理文字换行和首行缩进
    lines = _wrap_text_with_indent(description_text, font, text_width, draw)

    line_height = INFO_TEXT_FONT_SIZE + 4
    for i, line in enumerate(lines):
        if text_y + line_height > y + height - 80:
            break
        draw.text((text_x, text_y), line, fill=(0, 0, 0, 255), font=font)
        text_y += line_height

    # 比例尺（右下角位置）
    scale_x = x + 20
    scale_y = y + height - 70
    draw_scale_bar(draw, scale_x, scale_y, scale_denom, map_width, geo_extent, center_lat)

    # 制图时间（使用常量字体大小）
    current_date = datetime.now()
    date_text = f"{current_date.year}年{current_date.month:02d}月{current_date.day:02d}日"

    date_font = load_font(FONT_PATH_SONGTI, DATE_FONT_SIZE)
    draw.text((scale_x, scale_y + 30), date_text, fill=(0, 0, 0, 255), font=date_font)


def _wrap_text_with_indent(text, font, max_width, draw, indent_chars=2):
    """
    文字换行处理，首行缩进

    参数:
        text (str): 原始文字
        font: 字体对象
        max_width (int): 最大宽度
        draw: ImageDraw对象
        indent_chars (int): 缩进字符数
    返回:
        list: 换行后的文字列表
    """
    lines = []
    paragraphs = text.split('\n')

    indent = '　' * indent_chars  # 全角空格缩进

    for p_idx, para in enumerate(paragraphs):
        if not para.strip():
            continue

        # 首段首行缩进
        if p_idx == 0 or para.strip():
            current_line = indent
        else:
            current_line = ""

        for char in para:
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line = test_line
            else:
                if current_line.strip():
                    lines.append(current_line)
                current_line = char

        if current_line.strip():
            lines.append(current_line)

    return lines


# ============================================================
# 【底部图例绘制】- 修改：整体居中，具体图例项左对齐
# ============================================================

def draw_legend(draw, x, y, width, height, intensity_data, has_faults=True):
    """
    绘制底部图例（三行四列布局，最多12个图例项）
    整体在单元格内居中对齐，但每个具体图例项内部是左对齐（图标在左，文字在右）

    参数:
        draw: ImageDraw对象
        x (int): 图例区域左上角X坐标
        y (int): 图例区域左上角Y坐标
        width (int): 图例区域宽度
        height (int): 图例区域高度
        intensity_data (dict): 烈度圈数据
        has_faults (bool): 是否有断裂数据
    """
    # 白色背景，边框粗细与地图框一致
    draw.rectangle([x, y, x + width, y + height],
                   fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=MAP_BORDER_WIDTH)

    # 图例标题（黑体，居中，使用常量字体大小）
    title_font = load_font(FONT_PATH_HEITI, LEGEND_TITLE_FONT_SIZE)
    title = "图  例"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = bbox[2] - bbox[0]
    draw.text((x + width // 2 - title_w // 2, y + 8), title, fill=(0, 0, 0, 255), font=title_font)

    # 图例项字体（使用常量字体大小）
    item_font = load_font(FONT_PATH_SONGTI, LEGEND_ITEM_FONT_SIZE)

    cols = 4
    rows = 3
    col_width = width // cols
    row_height = (height - 35) // rows

    start_y = y + 35

    # 收集图例项
    legend_items = []

    # 震中
    legend_items.append(("epicenter", "震中"))

    # 烈度圈（用线段表示，使用罗马数字）
    sorted_intensities = sorted(intensity_data.keys(), reverse=True)
    for intensity in sorted_intensities:
        roman = int_to_roman(intensity)
        legend_items.append(("intensity", f"{roman}度区", intensity))

    # 断裂（使用与图层一致的颜色）
    if has_faults:
        legend_items.append(("fault_holocene", "全新世断层"))
        legend_items.append(("fault_late", "晚更新世断层"))
        legend_items.append(("fault_early", "早中更新世断层"))

    # 行政边界
    legend_items.append(("province", "省界"))
    legend_items.append(("city", "市界"))
    legend_items.append(("county", "县界"))

    # 只显示前12个
    legend_items = legend_items[:12]

    # 图标宽度和间距常量
    icon_width = 25  # 图标宽度
    text_gap = 5     # 图标和文字之间的间距

    # 首先计算所有图例项的宽度，找出最大宽度用于统一对齐
    item_widths = []
    for item in legend_items:
        item_type = item[0]
        label = item[1]

        # 计算文字宽度
        text_bbox = draw.textbbox((0, 0), label, font=item_font)
        text_width = text_bbox[2] - text_bbox[0]

        # 计算整个图例项的总宽度
        if item_type == "epicenter":
            total_width = 10 + text_gap + text_width  # 圆点直径10
        else:
            total_width = icon_width + text_gap + text_width

        item_widths.append(total_width)

    for idx, item in enumerate(legend_items):
        row = idx // cols
        col = idx % cols

        # 计算当前单元格的位置
        cell_left = x + col * col_width
        cell_center_x = cell_left + col_width // 2
        item_y = start_y + row * row_height + row_height // 2

        item_type = item[0]
        label = item[1]

        # 获取当前项的宽度
        total_width = item_widths[idx]

        # 计算文字高度用于垂直居中
        text_bbox = draw.textbbox((0, 0), label, font=item_font)
        text_height = text_bbox[3] - text_bbox[1]

        # 【关键修改】整体居中：计算图例项的起始X位置，使整体在单元格内居中
        # 但图例项内部保持左对齐（图标在左，文字紧跟在右）
        item_start_x = cell_center_x - total_width // 2

        # 绘制图例图标（从item_start_x开始，保持内部左对齐）
        if item_type == "epicenter":
            # 震中：红色实心圆
            r = 5
            draw.ellipse([item_start_x, item_y - r, item_start_x + r * 2, item_y + r],
                         fill=EPICENTER_COLOR, outline=(0, 0, 0, 255))
            text_x = item_start_x + r * 2 + text_gap

        elif item_type == "intensity":
            # 烈度圈：线段
            intensity = item[2]
            color = INTENSITY_COLORS.get(intensity, (255, 0, 0, 200))
            draw.line([(item_start_x, item_y), (item_start_x + icon_width, item_y)],
                      fill=color, width=INTENSITY_LINE_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "fault_holocene":
            # 全新世断层：使用与图层一致的颜色
            draw.line([(item_start_x, item_y), (item_start_x + icon_width, item_y)],
                      fill=FAULT_HOLOCENE_COLOR, width=FAULT_HOLOCENE_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "fault_late":
            # 晚更新世断层：使用与图层一致的颜色
            draw.line([(item_start_x, item_y), (item_start_x + icon_width, item_y)],
                      fill=FAULT_LATE_PLEISTOCENE_COLOR, width=FAULT_LATE_PLEISTOCENE_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "fault_early":
            # 早中更新世断层：使用与图层一致的颜色
            draw.line([(item_start_x, item_y), (item_start_x + icon_width, item_y)],
                      fill=FAULT_EARLY_PLEISTOCENE_COLOR, width=FAULT_EARLY_PLEISTOCENE_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "province":
            # 省界：实线
            draw.line([(item_start_x, item_y), (item_start_x + icon_width, item_y)],
                      fill=PROVINCE_BORDER_COLOR, width=PROVINCE_BORDER_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "city":
            # 市界：虚线
            for dx in range(0, icon_width, 8):
                draw.line([(item_start_x + dx, item_y), (item_start_x + dx + 5, item_y)],
                          fill=CITY_BORDER_COLOR, width=CITY_BORDER_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        elif item_type == "county":
            # 县界：虚线
            for dx in range(0, icon_width, 6):
                draw.line([(item_start_x + dx, item_y), (item_start_x + dx + 3, item_y)],
                          fill=COUNTY_BORDER_COLOR, width=COUNTY_BORDER_WIDTH)
            text_x = item_start_x + icon_width + text_gap

        else:
            text_x = item_start_x

        # 绘制文字标签（垂直居中，水平紧跟图标）
        draw.text((text_x, item_y - text_height // 2), label, fill=(0, 0, 0, 255), font=item_font)


# ============================================================
# 【分析统计函数】
# ============================================================

def generate_analysis_text(intensity_data, areas):
    """
    生成分析文字（极震区烈度、面积等）

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

    # 计算VI度以上区域面积
    vi_above_area = sum(areas.get(i, 0) for i in intensity_data.keys() if i >= 6)

    analysis = (f"预计极震区地震烈度可达{int_to_roman(max_intensity)}度，"
                f"极震区面积估算为{max_area:.0f}平方千米，"
                f"地震烈度VI度以上区域面积达{vi_above_area:.0f}平方千米。")

    return analysis

# ============================================================
# 【主函数】
# ============================================================

def generate_earthquake_kml_map(kml_path, description_text, magnitude, output_path):
    """
    生成地震烈度分布图

    参数:
        kml_path (str): KML烈度圈文件路径
        description_text (str): 说明文字（不超过450字）
        magnitude (float): 震级（用于确定比例尺）
        output_path (str): 输出PNG文件路径
    返回:
        dict: 包含分析结果的字典
    说明:
        1. 解析KML文件获取烈度圈数据
        2. 加载天地图底图
        3. 叠加省、市、县界和断裂
        4. 绘制烈度圈（线条形式，罗马数字标注）
        5. 绘制震中标记
        6. 添加指北针、比例尺、图例、说明文字
        7. 输出PNG图片
    """
    print("=" * 65)
    print("  地 震 烈 度 分 布 图 生 成 工 具")
    print("=" * 65)

    # [1/8] 解析KML文件
    print("\n[1/8] 解析KML烈度圈文件...")
    intensity_data = parse_intensity_kml(kml_path)
    if not intensity_data:
        print("  *** 无法解析烈度圈数据 ***")
        return None

    # [2/8] 计算地理范围和震中
    print("\n[2/8] 计算地理范围...")
    geo_extent = calculate_geo_extent_from_intensity(intensity_data)
    center_lon, center_lat = calculate_epicenter(intensity_data)
    print(f"  震中: {center_lon:.4f}°E, {center_lat:.4f}°N")

    # 获取比例尺
    scale_denom = get_scale_by_magnitude(magnitude)
    print(f"  震级: M{magnitude}, 比例尺: 1:{scale_denom:,}")

    # [3/8] 获取底图
    print("\n[3/8] 获取天地图底图...")
    basemap = fetch_basemap(geo_extent, MAP_WIDTH, MAP_HEIGHT)

    # [4/8] 读取行政边界
    print("\n[4/8] 读取行政边界SHP...")
    print("  --- 省界 ---")
    province_lines = read_shapefile_lines(SHP_PROVINCE_PATH, geo_extent)
    print("  --- 市界 ---")
    city_lines = read_shapefile_lines(SHP_CITY_PATH, geo_extent)
    print("  --- 县界 ---")
    county_lines = read_shapefile_lines(SHP_COUNTY_PATH, geo_extent)

    # [5/8] 读取断裂
    print("\n[5/8] 读取断裂KMZ...")
    fault_data = parse_kmz_faults(KMZ_FAULT_PATH, geo_extent)
    has_faults = any(len(v) > 0 for v in fault_data.values())

    # [6/8] 绘制图层
    print("\n[6/8] 绘制图层要素...")
    map_img = basemap.convert("RGBA")

    # 行政边界图层
    bd_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_bd = ImageDraw.Draw(bd_layer)

    if county_lines:
        print("  绘制县界...")
        draw_dashed_lines(draw_bd, county_lines, geo_extent, MAP_WIDTH, MAP_HEIGHT,
                          COUNTY_BORDER_COLOR, COUNTY_BORDER_WIDTH, COUNTY_BORDER_DASH)

    if city_lines:
        print("  绘制市界...")
        draw_dashed_lines(draw_bd, city_lines, geo_extent, MAP_WIDTH, MAP_HEIGHT,
                          CITY_BORDER_COLOR, CITY_BORDER_WIDTH, CITY_BORDER_DASH)

    if province_lines:
        print("  绘制省界...")
        draw_solid_lines(draw_bd, province_lines, geo_extent, MAP_WIDTH, MAP_HEIGHT,
                         PROVINCE_BORDER_COLOR, PROVINCE_BORDER_WIDTH)

    map_img = Image.alpha_composite(map_img, bd_layer)

    # 断裂图层（颜色与图例一致）
    if has_faults:
        ft_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
        draw_ft = ImageDraw.Draw(ft_layer)
        draw_fault_lines(draw_ft, fault_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        map_img = Image.alpha_composite(map_img, ft_layer)

    # 烈度圈图层
    intensity_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_intensity = ImageDraw.Draw(intensity_layer)
    print("  绘制烈度圈...")
    areas = draw_intensity_circles(draw_intensity, intensity_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    draw_intensity_labels(draw_intensity, intensity_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    map_img = Image.alpha_composite(map_img, intensity_layer)

    # 震中图层
    epi_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_epi = ImageDraw.Draw(epi_layer)
    print("  绘制震中...")
    draw_epicenter(draw_epi, center_lon, center_lat, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    map_img = Image.alpha_composite(map_img, epi_layer)

    # [7/8] 组装最终图片
    print("\n[7/8] 组装最终图片...")

    # 创建最终画布
    final_img = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (255, 255, 255, 255))

    # 粘贴地图
    final_img.paste(map_img, (BORDER_LEFT, BORDER_TOP))

    final_draw = ImageDraw.Draw(final_img)

    # 绘制经纬度边框
    draw_coordinate_border(final_draw, geo_extent, BORDER_LEFT, BORDER_TOP, MAP_WIDTH, MAP_HEIGHT)

    # 生成分析文字
    analysis_text = generate_analysis_text(intensity_data, areas)
    full_description = description_text + analysis_text

    # 绘制右侧说明文字区域（传入地图框顶部位置）
    info_panel_x = BORDER_LEFT + MAP_WIDTH
    info_panel_y = BORDER_TOP
    draw_info_panel(final_draw, info_panel_x, info_panel_y, INFO_PANEL_WIDTH, MAP_HEIGHT,
                    full_description, scale_denom, geo_extent, center_lat, MAP_WIDTH, BORDER_TOP)

    # 绘制底部图例
    legend_x = BORDER_LEFT
    legend_y = BORDER_TOP + MAP_HEIGHT
    legend_width = MAP_WIDTH + INFO_PANEL_WIDTH
    draw_legend(final_draw, legend_x, legend_y, legend_width, LEGEND_HEIGHT,
                intensity_data, has_faults)

    # [8/8] 保存输出
    print("\n[8/8] 保存输出...")
    output_rgb = final_img.convert("RGB")
    output_rgb.save(output_path, dpi=(OUTPUT_DPI, OUTPUT_DPI), quality=95)

    fsize = os.path.getsize(output_path) / 1024
    print(f"  已保存: {output_path}")
    print(f"  大小: {fsize:.1f} KB")
    print(f"  尺寸: {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}px")

    # 返回分析结果
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
    """
    测试地震烈度图生成功能

    说明:
        测试数据使用甘肃甘南州迭部县5.5级地震
        测试KML文件路径、说明文字、震级、输出路径
    """
    # 测试KML文件路径
    test_kml_path = r"../../data/geology/test_intensity.kml"

    # 测试说明文字（用户输入）
    test_description = (
        "据中国地震台网正式测定：2026年01月26日14时56分甘肃甘南州迭部县"
        "(103.25°，34.06°)发生5.5级地震，震源深度10千米。"
        "综合考虑震中附近地质构造背景、地震波衰减特性，"
        "估计了本次地震的地震动预测图。"
    )

    # 测试震级
    test_magnitude = 5.5

    # 测试输出路径
    test_output_path = r"../../data/geology/output_earthquake_kml_map.png"

    # 创建测试KML文件（如果不存在）
    if not os.path.exists(test_kml_path):
        _create_test_kml(test_kml_path)

    # 执行生成
    result = generate_earthquake_kml_map(
        kml_path=test_kml_path,
        description_text=test_description,
        magnitude=test_magnitude,
        output_path=test_output_path
    )

    # 验证结果
    if result:
        print("\n【测试通过】")
        print(f"  输出文件: {test_output_path}")
        assert result["max_intensity"] > 0, "最大烈度应大于0"
        assert result["center_lon"] is not None, "震中经度不应为空"
        assert result["center_lat"] is not None, "震中纬度不应为空"
    else:
        print("\n【测试失败】")


def _create_test_kml(kml_path):
    """
    创建测试用KML文件

    参数:
        kml_path (str): KML文件保存路径
    """
    # 甘肃甘南州迭部县附近的模拟烈度圈
    center_lon, center_lat = 103.25, 34.06

    kml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>5度</name>
<description></description>
<LineString><coordinates>
'''

    # 生成5度圈（最外圈，半径约50km）
    coords_5 = _generate_circle_coords(center_lon, center_lat, 0.5)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_5]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>6度</name>
<description></description>
<LineString><coordinates>
'''

    # 生成6度圈
    coords_6 = _generate_circle_coords(center_lon, center_lat, 0.3)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_6]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>7度</name>
<description></description>
<LineString><coordinates>
'''

    # 生成7度圈（最内圈）
    coords_7 = _generate_circle_coords(center_lon, center_lat, 0.15)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_7]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
</Document>
</kml>'''

    # 确保目录存在
    os.makedirs(os.path.dirname(kml_path), exist_ok=True)

    with open(kml_path, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    print(f"  创建测试KML文件: {kml_path}")


def _generate_circle_coords(center_lon, center_lat, radius_deg, num_points=36):
    """
    生成近似圆形的坐标点

    参数:
        center_lon (float): 中心经度
        center_lat (float): 中心纬度
        radius_deg (float): 半径（度）
        num_points (int): 点数
    返回:
        list: [(lon, lat), ...]
    """
    coords = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        lon = center_lon + radius_deg * math.cos(angle)
        lat = center_lat + radius_deg * 0.8 * math.sin(angle)  # 椭圆形
        coords.append((lon, lat))
    coords.append(coords[0])  # 闭合
    return coords


# ============================================================
# 【脚本入口】
# ============================================================

if __name__ == "__main__":
    # 用户输入参数
    INPUT_KML_PATH = r"../../data/geology/n0432881302350072.kml"

    INPUT_DESCRIPTION = (
        "据中国地震台网正式测定：2026年01月26日14时56分甘肃甘南州迭部县"
        "(103.25°，34.06°)发生5.5级地震，震源深度10千米。"
        "综合考虑震中附近地质构造背景、地震波衰减特性，"
        "估计了本次地震的地震动预测图。"
    )

    INPUT_MAGNITUDE = 5.5

    OUTPUT_PATH = r"../../data/geology/output_earthquake_kml_map.png"

    # 如果KML文件不存在，运行测试
    if not os.path.exists(INPUT_KML_PATH):
        print("KML文件不存在，运行测试模式...")
        test_generate_earthquake_kml_map()
    else:
        # 正式生成
        result = generate_earthquake_kml_map(
            kml_path=INPUT_KML_PATH,
            description_text=INPUT_DESCRIPTION,
            magnitude=INPUT_MAGNITUDE,
            output_path=OUTPUT_PATH
        )