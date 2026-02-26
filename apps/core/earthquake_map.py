# -*- coding: utf-8 -*-
"""
历史地震分布图生成脚本（基于Python + Pillow + requests + shapefile + KMZ）
功能：根据用户输入的震中经纬度、震级以及历史地震CSV文件，
      绘制震中附近一定范围内的历史地震分布图，叠加省界、市界、县界、
      断裂图层，并输出统计信息。

依赖安装：pip install Pillow requests pyshp lxml
作者：acao123
日期：2026-02-26
"""

import os
import sys
import csv
import math
import time
import zipfile
import requests
from io import BytesIO
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

try:
    import shapefile
except ImportError:
    print("*** 请安装pyshp库: pip install pyshp ***")
    sys.exit(1)

# ============================================================
# 【配置常量区域】
# ============================================================

TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

TIANDITU_IMG_URL = (
    "http://t{s}.tianditu.gov.cn/img_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=img&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

TIANDITU_CIA_URL = (
    "http://t{s}.tianditu.gov.cn/cia_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cia&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

ENABLE_LABEL_OVERLAY = True
OUTPUT_WIDTH = 1600
OUTPUT_HEIGHT = 1200
OUTPUT_DPI = 150
OUTPUT_FORMAT = "png"
TILE_TIMEOUT = 20
TILE_RETRY = 3

# 地震圆点颜色（RGBA）
COLOR_LEVEL_1 = (0, 255, 0, 240)       # 4.7~5.9级 - 绿色
COLOR_LEVEL_2 = (255, 255, 0, 220)     # 6.0~6.9级 - 黄色
COLOR_LEVEL_3 = (255, 165, 0, 230)     # 7.0~7.9级 - 橙色
COLOR_LEVEL_4 = (255, 0, 0, 240)       # 8.0级以上 - 红色

# 地震圆点直径（像素）
SIZE_LEVEL_1 = 14
SIZE_LEVEL_2 = 20
SIZE_LEVEL_3 = 26
SIZE_LEVEL_4 = 34

# 震中五角星（最大，图层最高优先级）
EPICENTER_STAR_COLOR = (255, 0, 0, 255)
EPICENTER_STAR_SIZE = 30

# ============================================================
# 【行政边界线样式 - 加粗加亮版本，确保卫星底图上清晰可见】
# ============================================================

# 省界 - 亮紫色粗实线（带黑色描边衬底）
PROVINCE_BORDER_COLOR = (180, 0, 255, 255)     # 亮紫色，完全不透明
PROVINCE_BORDER_WIDTH = 5                       # 粗线
PROVINCE_BORDER_SHADOW_COLOR = (0, 0, 0, 180)  # 黑色描边衬底
PROVINCE_BORDER_SHADOW_WIDTH = 7                # 衬底比主线宽

# 市界 - 亮粉色粗虚线（带黑色描边衬底）
CITY_BORDER_COLOR = (255, 100, 200, 255)        # 亮粉色
CITY_BORDER_WIDTH = 3
CITY_BORDER_DASH = (12, 6)
CITY_BORDER_SHADOW_COLOR = (0, 0, 0, 150)
CITY_BORDER_SHADOW_WIDTH = 5

# 县界 - 亮黄色细虚线（带黑色描边衬底）
COUNTY_BORDER_COLOR = (255, 255, 100, 230)      # 亮黄色（灰色在底图上不可见）
COUNTY_BORDER_WIDTH = 2
COUNTY_BORDER_DASH = (8, 4)
COUNTY_BORDER_SHADOW_COLOR = (0, 0, 0, 120)
COUNTY_BORDER_SHADOW_WIDTH = 4

# ============================================================
# 【断裂线样式 - 加粗加亮版本】
# ============================================================

# 全新世断层 - 亮红色粗实线
FAULT_HOLOCENE_COLOR = (255, 50, 50, 255)
FAULT_HOLOCENE_WIDTH = 3
FAULT_HOLOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_HOLOCENE_SHADOW_WIDTH = 5

# 晚更新世断层 - 亮品红粗实线
FAULT_LATE_PLEISTOCENE_COLOR = (255, 0, 255, 255)
FAULT_LATE_PLEISTOCENE_WIDTH = 3
FAULT_LATE_PLEISTOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_LATE_PLEISTOCENE_SHADOW_WIDTH = 5

# 早中更新世断层 - 亮青绿色粗实线
FAULT_EARLY_PLEISTOCENE_COLOR = (0, 255, 150, 255)
FAULT_EARLY_PLEISTOCENE_WIDTH = 3
FAULT_EARLY_PLEISTOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_EARLY_PLEISTOCENE_SHADOW_WIDTH = 5

# 未分类断层
FAULT_DEFAULT_COLOR = (255, 200, 50, 220)
FAULT_DEFAULT_WIDTH = 2

# ============================================================
# 【文件路径】
# ============================================================

SHP_PROVINCE_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
SHP_CITY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
SHP_COUNTY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"
KMZ_FAULT_PATH = r"../../data/geology/断层/全国六代图断裂.KMZ"

# 字体
FONT_PATH_TITLE = "C:/Windows/Fonts/simhei.ttf"
FONT_PATH_NORMAL = "C:/Windows/Fonts/simsun.ttc"

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

def get_range_params(magnitude):
    """
    根据震级确定绘图范围参数

    参数:
        magnitude (float): 震级（M）
    返回:
        tuple: (半径km, 地图边长km, 比例尺分母)
    """
    if magnitude < 6.0:
        return 15, 30, 150000
    elif magnitude < 7.0:
        return 50, 100, 500000
    else:
        return 150, 300, 1500000


def get_earthquake_level(mag):
    """
    根据震级返回等级分档(1-4)，0=不在范围

    参数:
        mag (float): 震级
    返回:
        int: 1=4.7~5.9  2=6.0~6.9  3=7.0~7.9  4=8.0+  0=不在范围
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


def get_level_color(level):
    """根据等级返回颜色"""
    return {1: COLOR_LEVEL_1, 2: COLOR_LEVEL_2, 3: COLOR_LEVEL_3, 4: COLOR_LEVEL_4}.get(level, (128, 128, 128, 150))


def get_level_size(level):
    """根据等级返回圆点直径"""
    return {1: SIZE_LEVEL_1, 2: SIZE_LEVEL_2, 3: SIZE_LEVEL_3, 4: SIZE_LEVEL_4}.get(level, 6)


def haversine_distance(lon1, lat1, lon2, lat2):
    """
    Haversine公式计算两点球面距离

    参数:
        lon1, lat1, lon2, lat2 (float): 经纬度（度）
    返回:
        float: 距离（千米）
    """
    R = 6371.0
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lat2 - lat1)
    dn = math.radians(lon2 - lon1)
    a = math.sin(dl / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dn / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def km_to_degree_lon(km, latitude):
    """千米转经度差"""
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """千米转纬度差"""
    return km / 110.574


def geo_to_pixel(lon, lat, geo_extent, img_width, img_height):
    """
    经纬度转图片像素坐标

    参数:
        lon, lat (float): 经纬度
        geo_extent (dict): 地理范围
        img_width, img_height (int): 图片尺寸
    返回:
        tuple: (px, py)
    """
    px = (lon - geo_extent["min_lon"]) / (geo_extent["max_lon"] - geo_extent["min_lon"]) * img_width
    py = (geo_extent["max_lat"] - lat) / (geo_extent["max_lat"] - geo_extent["min_lat"]) * img_height
    return int(round(px)), int(round(py))


# ============================================================
# 【天地图瓦片函数】
# ============================================================

def select_zoom_level(geo_extent, img_width, img_height):
    """选择最优缩放级别"""
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]
    best_zoom = 1
    for z in range(1, 19):
        m = TIANDITU_MATRIX[z]
        tx = math.ceil(lon_range / m["tile_span_lon"]) + 1
        ty = math.ceil(lat_range / m["tile_span_lat"]) + 1
        if tx * 256 >= img_width and ty * 256 >= img_height:
            best_zoom = z
            if tx * ty > 400:
                best_zoom = z - 1 if z > 1 else z
                break
        else:
            best_zoom = z
    return max(3, min(best_zoom, 16))


def lonlat_to_tile_epsg4326(lon, lat, zoom):
    """经纬度转瓦片行列号"""
    m = TIANDITU_MATRIX[zoom]
    col = int(math.floor((lon + 180.0) / m["tile_span_lon"]))
    row = int(math.floor((90.0 - lat) / m["tile_span_lat"]))
    return max(0, min(col, m["n_cols"] - 1)), max(0, min(row, m["n_rows"] - 1))


def tile_to_lonlat_epsg4326(tile_col, tile_row, zoom):
    """瓦片行列号转左上角经纬度"""
    m = TIANDITU_MATRIX[zoom]
    return -180.0 + tile_col * m["tile_span_lon"], 90.0 - tile_row * m["tile_span_lat"]


def download_tile_with_retry(url_template, zoom, tile_col, tile_row, retries=TILE_RETRY):
    """下载瓦片（支持重试）"""
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


def fetch_basemap(center_lon, center_lat, half_span_km, scale_denom, img_width, img_height):
    """
    获取天地图底图

    参数:
        center_lon, center_lat (float): 中心经纬度
        half_span_km (float): 半边长km
        scale_denom (int): 比例尺分母
        img_width, img_height (int): 输出尺寸
    返回:
        tuple: (PIL.Image, geo_extent)
    """
    delta_lon = km_to_degree_lon(half_span_km, center_lat)
    delta_lat = km_to_degree_lat(half_span_km)
    geo_extent = {
        "min_lon": center_lon - delta_lon, "max_lon": center_lon + delta_lon,
        "min_lat": center_lat - delta_lat, "max_lat": center_lat + delta_lat,
    }
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
    mosaic = Image.new("RGBA", (mw, mh), (200, 200, 200, 255))
    mo_lon, mo_lat = tile_to_lonlat_epsg4326(col_min, row_min, zoom)
    me_lon, me_lat = tile_to_lonlat_epsg4326(col_max + 1, row_max + 1, zoom)

    dl_ok, dl_fail = 0, 0
    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            px, py = (col - col_min) * ts, (row - row_min) * ts
            tile = download_tile_with_retry(TIANDITU_IMG_URL, zoom, col, row)
            if tile:
                mosaic.paste(tile.convert("RGBA"), (px, py))
                dl_ok += 1
            else:
                dl_fail += 1
            if ENABLE_LABEL_OVERLAY:
                lbl = download_tile_with_retry(TIANDITU_CIA_URL, zoom, col, row)
                if lbl:
                    tmp = Image.new("RGBA", mosaic.size, (0, 0, 0, 0))
                    tmp.paste(lbl.convert("RGBA"), (px, py))
                    mosaic = Image.alpha_composite(mosaic, tmp)
        print(f"    下载: {(col - col_min + 1) / ntx * 100:.0f}%")

    print(f"  瓦片完成: 成功={dl_ok}, 失败={dl_fail}")

    def g2m(lon, lat):
        return ((lon - mo_lon) / (me_lon - mo_lon) * mw,
                (mo_lat - lat) / (mo_lat - me_lat) * mh)

    cl, ct = g2m(geo_extent["min_lon"], geo_extent["max_lat"])
    cr, cb = g2m(geo_extent["max_lon"], geo_extent["min_lat"])
    cl, ct = max(0, int(round(cl))), max(0, int(round(ct)))
    cr, cb = min(mw, int(round(cr))), min(mh, int(round(cb)))
    cropped = mosaic.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic
    return cropped.resize((img_width, img_height), Image.LANCZOS), geo_extent


# ============================================================
# 【CSV读取】
# ============================================================

def read_earthquake_csv(csv_path, encoding="gbk"):
    """
    读取历史地震CSV

    参数:
        csv_path (str): 路径
        encoding (str): 编码
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
    筛选范围内地震

    参数:
        earthquakes (list): 全部记录
        center_lon, center_lat (float): 震中
        radius_km (float): 半径
        min_magnitude (float): 最小震级
    返回:
        list: 筛选结果（含distance字段）
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
# 【SHP行政边界读取与绘制 —— 带黑色描边衬底增强可见性】
# ============================================================

def read_shapefile_lines(shp_path, geo_extent):
    """
    读取SHP文件，提取地理范围内的边界线段。
    使用30%的范围扩展来避免边界处线段遗漏。

    参数:
        shp_path (str): SHP文件路径
        geo_extent (dict): 地理范围
    返回:
        list: [[(lon,lat), ...], ...]
    """
    if not os.path.exists(shp_path):
        print(f"  *** SHP不存在: {shp_path} ***")
        return []
    try:
        sf = shapefile.Reader(shp_path)
    except Exception as e:
        print(f"  *** SHP读取失败: {e} ***")
        return []

    # 扩展30%范围（比之前的10%更大，避免遗漏）
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


def draw_solid_lines(draw, lines, geo_extent, img_w, img_h, color, width):
    """
    绘制实线

    参数:
        draw (ImageDraw): 绘图对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
        color (tuple): RGBA颜色
        width (int): 线宽
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_solid_lines_with_shadow(draw, lines, geo_extent, img_w, img_h,
                                 color, width, shadow_color, shadow_width):
    """
    绘制带黑色衬底描边的实线（先画粗的黑色底线，再画彩色主线）

    参数:
        draw (ImageDraw): 绘图对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
        color (tuple): 主线RGBA颜色
        width (int): 主线宽度
        shadow_color (tuple): 衬底RGBA颜色
        shadow_width (int): 衬底宽度（应大于主线宽度）
    """
    # 第一遍：画黑色衬底
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=shadow_color, width=shadow_width)
    # 第二遍：画彩色主线
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_dashed_lines(draw, lines, geo_extent, img_w, img_h, color, width, dash):
    """
    绘制虚线

    参数:
        draw (ImageDraw): 绘图对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
        color (tuple): RGBA颜色
        width (int): 线宽
        dash (tuple): (线段长, 间隔长)
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, color, width, dash)


def draw_dashed_lines_with_shadow(draw, lines, geo_extent, img_w, img_h,
                                  color, width, dash, shadow_color, shadow_width):
    """
    绘制带黑色衬底的虚线

    参数:
        draw (ImageDraw): 绘图对象
        lines (list): 线段列表
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
        color (tuple): 主线RGBA颜色
        width (int): 主线宽度
        dash (tuple): (线段长, 间隔长)
        shadow_color (tuple): 衬底RGBA颜色
        shadow_width (int): 衬底宽度
    """
    # 第一遍：衬底虚线（更粗）
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, shadow_color, shadow_width, dash)
    # 第二遍：主线虚线
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, color, width, dash)


def _draw_dashed_polyline(draw, pts, color, width, dash):
    """
    绘制虚线折线

    参数:
        draw (ImageDraw): 绘图对象
        pts (list): 像素坐标列表
        color (tuple): RGBA颜色
        width (int): 线宽
        dash (tuple): (线段长, 间隔长)
    """
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


# ============================================================
# 【KMZ断裂数据解析】
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent):
    """
    解析KMZ断裂线，按类型分类

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
                print("  *** KMZ中无KML ***")
                return empty
            for kn in kml_files:
                print(f"  解析: {kn}")
                _parse_kml_faults(zf.read(kn), ext, result)
    except zipfile.BadZipFile:
        print("  尝试作为KML解析...")
        try:
            with open(kmz_path, 'rb') as f:
                _parse_kml_faults(f.read(), ext, result)
        except Exception as e:
            print(f"  *** 失败: {e} ***")
    except Exception as e:
        print(f"  *** KMZ失败: {e} ***")

    total = sum(len(v) for v in result.values())
    print(f"  断裂线: 全新世={len(result['holocene'])}, 晚更新世={len(result['late_pleistocene'])}, "
          f"早中更新世={len(result['early_pleistocene'])}, 其他={len(result['default'])}, 总={total}")
    return result


def _parse_kml_faults(kml_data, ext, result):
    """
    解析KML断裂Placemark

    参数:
        kml_data (bytes): KML数据
        ext (tuple): (min_lon, max_lon, min_lat, max_lat) 扩展边界
        result (dict): 分类结果（就地修改）
    """
    try:
        root = etree.fromstring(kml_data)
    except Exception as e:
        print(f"    解析错误: {e}")
        return

    ns = root.nsmap.get(None, 'http://www.opengis.net/kml/2.2')
    nsmap = {'kml': ns}
    sc = _parse_kml_styles(root, nsmap, ns)

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
        ftype = _classify_fault(name, surl, desc, sc)

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


def _ft(elem, tag, nsmap, ns):
    """查找KML元素文本（兼容多种命名空间）"""
    for p in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(p, nsmap) if 'kml:' in p else elem.find(p)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _parse_kml_styles(root, nsmap, ns):
    """
    解析KML样式，建立ID到颜色映射

    参数:
        root: KML根元素
        nsmap (dict): 命名空间
        ns (str): 默认命名空间URI
    返回:
        dict: {"#id": "abgr_str"}
    """
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


def _classify_fault(name, style_url, description, style_colors):
    """
    判断断裂类型

    参数:
        name, style_url, description (str): 名称/样式/描述
        style_colors (dict): 样式映射
    返回:
        str: 类型标识
    """
    combined = (name + " " + description).lower()
    if "全新世" in name or "全新世" in description or "holocene" in combined:
        return "holocene"
    if "晚更新世" in name or "晚更新世" in description or "late pleistocene" in combined:
        return "late_pleistocene"
    if any(k in name or k in description for k in ["早中更新世", "早更新世", "中更新世"]):
        return "early_pleistocene"
    if "early pleistocene" in combined or "middle pleistocene" in combined:
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
    """
    从Placemark提取LineString坐标

    参数:
        pm: Placemark元素
        nsmap (dict): 命名空间
        ns (str): 默认命名空间
    返回:
        list: [[(lon,lat),...], ...]
    """
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
    """解析KML coordinates文本为[(lon,lat),...]"""
    pts = []
    for p in text.replace('\n', ' ').replace('\t', ' ').split():
        f = p.strip().split(',')
        if len(f) >= 2:
            try:
                pts.append((float(f[0]), float(f[1])))
            except ValueError:
                continue
    return pts


def draw_fault_lines(draw, fault_data, geo_extent, img_w, img_h):
    """
    绘制断裂线（带黑色衬底描边）

    参数:
        draw (ImageDraw): 绘图对象
        fault_data (dict): 分类断裂线
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
    """
    style_map = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH,
                     FAULT_HOLOCENE_SHADOW_COLOR, FAULT_HOLOCENE_SHADOW_WIDTH),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH,
                             FAULT_LATE_PLEISTOCENE_SHADOW_COLOR, FAULT_LATE_PLEISTOCENE_SHADOW_WIDTH),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH,
                              FAULT_EARLY_PLEISTOCENE_SHADOW_COLOR, FAULT_EARLY_PLEISTOCENE_SHADOW_WIDTH),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, (0, 0, 0, 100), 3),
    }
    for ftype, lines in fault_data.items():
        if not lines:
            continue
        c, w, sc, sw = style_map.get(ftype, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, (0, 0, 0, 100), 3))
        draw_solid_lines_with_shadow(draw, lines, geo_extent, img_w, img_h, c, w, sc, sw)
        print(f"  绘制 {ftype} 断裂: {len(lines)} 条")

# ============================================================
# 【绘图函数】
# ============================================================

def draw_star(draw, cx, cy, radius, color, num_points=5):
    """
    绘制五角星

    参数:
        draw (ImageDraw): 绘图对象
        cx, cy (int): 中心像素坐标
        radius (int): 外接圆半径
        color (tuple): RGBA颜色
        num_points (int): 角数
    """
    inner_radius = radius * 0.382
    points = []
    for i in range(num_points * 2):
        angle = math.radians(i * 360.0 / (num_points * 2) - 90)
        r = radius if i % 2 == 0 else inner_radius
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=color, outline=(0, 0, 0, 255))


def draw_north_arrow(draw, x, y, size=60):
    """
    绘制指北针

    参数:
        draw (ImageDraw): 绘图对象
        x, y (int): 位置
        size (int): 大小
    """
    top = (x, y)
    bl = (x - size // 4, y + size)
    br = (x + size // 4, y + size)
    cp = (x, int(y + size * 0.65))
    draw.polygon([top, bl, cp], fill=(0, 0, 0, 255))
    draw.polygon([top, br, cp], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    draw.polygon([top, bl, cp], outline=(0, 0, 0, 255))
    try:
        fn = ImageFont.truetype(FONT_PATH_TITLE, size // 3)
    except (IOError, OSError):
        fn = ImageFont.load_default()
    bb = draw.textbbox((0, 0), "N", font=fn)
    draw.text((x - (bb[2] - bb[0]) // 2, y - size // 3 - 8), "N", fill=(0, 0, 0, 255), font=fn)


def draw_scale_bar(draw, x, y, scale_denom, img_width, geo_extent, center_lat):
    """
    绘制比例尺

    参数:
        draw (ImageDraw): 绘图对象
        x, y (int): 位置
        scale_denom (int): 比例尺分母
        img_width (int): 图片宽度
        geo_extent (dict): 地理范围
        center_lat (float): 中心纬度
    """
    lr = geo_extent["max_lon"] - geo_extent["min_lon"]
    kpp = lr * 111.32 * math.cos(math.radians(center_lat)) / img_width
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    target = img_width * 0.15 * kpp
    bar_km = nice[0]
    for nd in nice:
        if nd <= target * 1.2:
            bar_km = nd
        else:
            break
    bar_px = int(bar_km / kpp)

    draw.rectangle([x - 8, y - 28, x + bar_px + 65, y + 28],
                   fill=(255, 255, 255, 220), outline=(0, 0, 0, 200))
    bh, ns = 8, 4
    sw = bar_px // ns
    for i in range(ns):
        c = (0, 0, 0, 255) if i % 2 == 0 else (255, 255, 255, 255)
        draw.rectangle([x + i * sw, y, x + (i + 1) * sw, y + bh], fill=c, outline=(0, 0, 0, 255))
    try:
        fs = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        fs = ImageFont.load_default()
    draw.text((x, y + bh + 3), "0", fill=(0, 0, 0, 255), font=fs)
    el = f"{bar_km} km"
    bb = draw.textbbox((0, 0), el, font=fs)
    draw.text((x + bar_px - (bb[2] - bb[0]) // 2, y + bh + 3), el, fill=(0, 0, 0, 255), font=fs)
    st = f"1:{scale_denom:,}"
    bb2 = draw.textbbox((0, 0), st, font=fs)
    draw.text((x + bar_px // 2 - (bb2[2] - bb2[0]) // 2, y - 20), st, fill=(0, 0, 0, 255), font=fs)


def draw_legend(draw, x, y, has_faults=True):
    """
    绘制图例（参照样例图样式）
    第一行：震中位置（大五角星，图例中最大的图标）

    参数:
        draw (ImageDraw): 绘图对象
        x, y (int): 左上角坐标
        has_faults (bool): 是否含断裂图例
    """
    try:
        ft = ImageFont.truetype(FONT_PATH_TITLE, 18)
        fi = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        ft = ImageFont.load_default()
        fi = ImageFont.load_default()

    item_h = 25
    n_items = 1 + 3 + (3 if has_faults else 0) + 4  # 震中+3边界+(3断裂)+4地震
    legend_h = 35 + n_items * item_h + 10
    legend_w = 200

    draw.rectangle([x, y, x + legend_w, y + legend_h],
                   fill=(255, 255, 255, 240), outline=(0, 0, 0, 255), width=2)

    draw.text((x + 60, y + 8), "图  例", fill=(0, 0, 0, 255), font=ft)

    cy = y + 38
    icx = x + 22
    tx = x + 45

    # === 第一行：震中位置（大五角星，图例最大图标） ===
    draw_star(draw, icx, cy + 2, 14, EPICENTER_STAR_COLOR)
    draw.text((tx, cy - 7), "震中位置", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 行政边界 ===
    # 省界（实线）
    draw.line([(x + 6, cy + 2), (x + 38, cy + 2)],
              fill=PROVINCE_BORDER_COLOR, width=PROVINCE_BORDER_WIDTH)
    draw.text((tx, cy - 7), "省界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # 市界（虚线）
    for dx in range(0, 30, 10):
        draw.line([(x + 6 + dx, cy + 2), (x + 6 + dx + 6, cy + 2)],
                  fill=CITY_BORDER_COLOR, width=CITY_BORDER_WIDTH)
    draw.text((tx, cy - 7), "市界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # 县界（虚线）
    for dx in range(0, 30, 8):
        draw.line([(x + 6 + dx, cy + 2), (x + 6 + dx + 4, cy + 2)],
                  fill=COUNTY_BORDER_COLOR, width=COUNTY_BORDER_WIDTH)
    draw.text((tx, cy - 7), "县界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 断裂线 ===
    if has_faults:
        draw.line([(x + 6, cy + 2), (x + 38, cy + 2)],
                  fill=FAULT_HOLOCENE_COLOR, width=FAULT_HOLOCENE_WIDTH)
        draw.text((tx, cy - 7), "全新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

        draw.line([(x + 6, cy + 2), (x + 38, cy + 2)],
                  fill=FAULT_LATE_PLEISTOCENE_COLOR, width=FAULT_LATE_PLEISTOCENE_WIDTH)
        draw.text((tx, cy - 7), "晚更新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

        draw.line([(x + 6, cy + 2), (x + 38, cy + 2)],
                  fill=FAULT_EARLY_PLEISTOCENE_COLOR, width=FAULT_EARLY_PLEISTOCENE_WIDTH)
        draw.text((tx, cy - 7), "早中更新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

    # === 地震圆点 ===
    for label, color, dot_size in [
        ("8.0级以上", COLOR_LEVEL_4, SIZE_LEVEL_4),
        ("7.0~7.9级", COLOR_LEVEL_3, SIZE_LEVEL_3),
        ("6.0~6.9级", COLOR_LEVEL_2, SIZE_LEVEL_2),
        ("4.7~5.9级", COLOR_LEVEL_1, SIZE_LEVEL_1),
    ]:
        h = dot_size // 2
        draw.ellipse([icx - h, cy + 2 - h, icx + h, cy + 2 + h],
                     fill=color, outline=(0, 0, 0, 180))
        draw.text((tx, cy - 7), label, fill=(0, 0, 0, 255), font=fi)
        cy += item_h


def draw_earthquake_points(draw, filtered_quakes, geo_extent, img_w, img_h):
    """
    绘制历史地震圆点（带白色描边）

    参数:
        draw (ImageDraw): 绘图对象
        filtered_quakes (list): 筛选后地震记录
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
    返回:
        int: 绘制数量
    """
    sorted_q = sorted(filtered_quakes, key=lambda e: e["magnitude"])
    count = 0
    for eq in sorted_q:
        lv = get_earthquake_level(eq["magnitude"])
        if lv == 0:
            continue
        color = get_level_color(lv)
        size = get_level_size(lv)
        px, py = geo_to_pixel(eq["lon"], eq["lat"], geo_extent, img_w, img_h)
        if 0 <= px <= img_w and 0 <= py <= img_h:
            h = size // 2
            draw.ellipse([px - h - 2, py - h - 2, px + h + 2, py + h + 2],
                         fill=None, outline=(255, 255, 255, 220), width=2)
            draw.ellipse([px - h, py - h, px + h, py + h],
                         fill=color, outline=(0, 0, 0, 200))
            count += 1
    print(f"  绘制地震点: {count}")
    return count


def draw_epicenter(draw, center_lon, center_lat, geo_extent, img_w, img_h):
    """
    绘制震中五角星（图层最高优先级，最后绘制）

    参数:
        draw (ImageDraw): 绘图对象
        center_lon, center_lat (float): 震中经纬度
        geo_extent (dict): 地理范围
        img_w, img_h (int): 图片尺寸
    """
    px, py = geo_to_pixel(center_lon, center_lat, geo_extent, img_w, img_h)
    draw_star(draw, px, py, EPICENTER_STAR_SIZE, EPICENTER_STAR_COLOR)
    print(f"  震中: ({px}, {py})")


# ============================================================
# 【统��】
# ============================================================

def generate_statistics(filtered_quakes, radius_km):
    """
    生成统计文本

    参数:
        filtered_quakes (list): 地震记录
        radius_km (float): 半径
    返回:
        str: 统计信息
    """
    ct = len(filtered_quakes)
    c1 = sum(1 for e in filtered_quakes if 4.7 <= e["magnitude"] <= 5.9)
    c2 = sum(1 for e in             filtered_quakes if 6.0 <= e["magnitude"] <= 6.9)
    c3 = sum(1 for e in filtered_quakes if 7.0 <= e["magnitude"] <= 7.9)
    c4 = sum(1 for e in filtered_quakes if e["magnitude"] >= 8.0)
    mx = max(filtered_quakes, key=lambda e: e["magnitude"]) if filtered_quakes else None

    txt = (f"自1900年以来，本次地震震中{int(radius_km)}km范围内"
           f"曾发生{ct}次4.7级以上地震，\n"
           f"其中4.7~5.9级地震{c1}次，6.0~6.9级地震{c2}次，"
           f"7.0~7.9级地震{c3}次，8.0级以上地震{c4}次。")
    if mx:
        y = str(mx.get("year", 0)) if mx.get("year", 0) > 0 else "未知"
        m = str(mx.get("month", 0)) if mx.get("month", 0) > 0 else "未知"
        d = str(mx.get("day", 0)) if mx.get("day", 0) > 0 else "未知"
        txt += f"\n最大地震为{y}年{m}月{d}日{mx.get('location', '')}{mx['magnitude']}级地震。"
    return txt


# ============================================================
# 【主函数】
# ============================================================

def generate_earthquake_map(center_lon, center_lat, magnitude, csv_path,
                            output_path, csv_encoding="gbk"):
    """
    生成历史地震分布图（含行政边界和断裂线图层）

    绘制图层顺序（从底到顶）：
        1. 天地图底图
        2. 县界（亮黄色虚线+黑色衬底）
        3. 市界（亮粉色虚线+黑色衬底）
        4. 省界（亮紫色实线+黑色衬底）
        5. 断裂线（红/品红/青绿+黑色衬底）
        6. 历史地震圆点（带白色描边）
        7. 震中五角星（最顶层，优先级最高）
        8. 装饰要素（指北针、比例尺、图例、标题）

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        magnitude (float): 震级
        csv_path (str): CSV路径
        output_path (str): 输出路径
        csv_encoding (str): CSV编码
    返回:
        str: 统计信息
    """
    print("=" * 65)
    print("  历 史 地 震 分 布 图 生 成 工 具")
    print("=" * 65)
    print(f"  震中: {center_lon}°E, {center_lat}°N, M{magnitude}")

    radius_km, span_km, scale_denom = get_range_params(magnitude)
    half_span_km = span_km / 2.0
    print(f"  范围: {radius_km}km, 比例尺: 1:{scale_denom:,}\n")

    # [1/7] ���取CSV
    print("[1/7] 读取历史地震数据...")
    if not os.path.exists(csv_path):
        print(f"  *** CSV不存在: {csv_path} ***")
        return ""
    earthquakes = read_earthquake_csv(csv_path, encoding=csv_encoding)
    print()

    # [2/7] 筛选
    print("[2/7] 筛选范围内地震...")
    filtered = filter_earthquakes(earthquakes, center_lon, center_lat, radius_km)
    print()

    # [3/7] 底图
    print("[3/7] 获取天地图底图...")
    basemap, geo_extent = fetch_basemap(center_lon, center_lat, half_span_km,
                                        scale_denom, OUTPUT_WIDTH, OUTPUT_HEIGHT)
    print()

    # [4/7] 读取行政边界
    print("[4/7] 读取行政边界SHP...")
    print("  --- 省界 ---")
    province_lines = read_shapefile_lines(SHP_PROVINCE_PATH, geo_extent)
    print("  --- 市界 ---")
    city_lines = read_shapefile_lines(SHP_CITY_PATH, geo_extent)
    print("  --- 县界 ---")
    county_lines = read_shapefile_lines(SHP_COUNTY_PATH, geo_extent)
    print()

    # [5/7] 读取断裂
    print("[5/7] 读取断裂KMZ...")
    fault_data = parse_kmz_faults(KMZ_FAULT_PATH, geo_extent)
    has_faults = any(len(v) > 0 for v in fault_data.values())
    print()

    # [6/7] 绘制所有图层
    print("[6/7] 绘制图层要素...")
    result_img = basemap.convert("RGBA")

    # --- 图层1: 行政边界（带黑色衬底描边，确保在卫星底图上清晰可见） ---
    boundary_layer = Image.new("RGBA", result_img.size, (0, 0, 0, 0))
    draw_bd = ImageDraw.Draw(boundary_layer)

    # 县界（最底层边界，亮黄色虚线+黑色衬底）
    if county_lines:
        print("  绘制县界（亮黄色虚线+黑色衬底）...")
        draw_dashed_lines_with_shadow(draw_bd, county_lines, geo_extent,
                                      OUTPUT_WIDTH, OUTPUT_HEIGHT,
                                      COUNTY_BORDER_COLOR, COUNTY_BORDER_WIDTH,
                                      COUNTY_BORDER_DASH,
                                      COUNTY_BORDER_SHADOW_COLOR, COUNTY_BORDER_SHADOW_WIDTH)

    # 市界（中层边界，亮粉色虚线+黑色衬底）
    if city_lines:
        print("  绘制市界（亮粉色虚线+黑色衬底）...")
        draw_dashed_lines_with_shadow(draw_bd, city_lines, geo_extent,
                                      OUTPUT_WIDTH, OUTPUT_HEIGHT,
                                      CITY_BORDER_COLOR, CITY_BORDER_WIDTH,
                                      CITY_BORDER_DASH,
                                      CITY_BORDER_SHADOW_COLOR, CITY_BORDER_SHADOW_WIDTH)

    # 省界（最顶层边界，亮紫色实线+黑色衬底）
    if province_lines:
        print("  绘制省界（亮紫色实线+黑色衬底）...")
        draw_solid_lines_with_shadow(draw_bd, province_lines, geo_extent,
                                     OUTPUT_WIDTH, OUTPUT_HEIGHT,
                                     PROVINCE_BORDER_COLOR, PROVINCE_BORDER_WIDTH,
                                     PROVINCE_BORDER_SHADOW_COLOR, PROVINCE_BORDER_SHADOW_WIDTH)

    result_img = Image.alpha_composite(result_img, boundary_layer)

    # --- 图层2: 断裂线（带黑色衬底描边） ---
    if has_faults:
        fault_layer = Image.new("RGBA", result_img.size, (0, 0, 0, 0))
        draw_ft = ImageDraw.Draw(fault_layer)
        draw_fault_lines(draw_ft, fault_data, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)
        result_img = Image.alpha_composite(result_img, fault_layer)

    # --- 图层3: 历史地震圆点 ---
    eq_layer = Image.new("RGBA", result_img.size, (0, 0, 0, 0))
    draw_eq = ImageDraw.Draw(eq_layer)
    draw_earthquake_points(draw_eq, filtered, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)
    result_img = Image.alpha_composite(result_img, eq_layer)

    # --- 图层4: 震中五角星（最顶层，优先级最高，不被任何图层遮挡） ---
    epi_layer = Image.new("RGBA", result_img.size, (0, 0, 0, 0))
    draw_epi = ImageDraw.Draw(epi_layer)
    draw_epicenter(draw_epi, center_lon, center_lat, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)
    result_img = Image.alpha_composite(result_img, epi_layer)

    # --- 装饰要素 ---
    draw_final = ImageDraw.Draw(result_img)

    # 右上角指北针
    draw_north_arrow(draw_final, OUTPUT_WIDTH - 55, 25, size=55)

    # 右下角比例尺
    draw_scale_bar(draw_final, OUTPUT_WIDTH - 280, OUTPUT_HEIGHT - 50,
                   scale_denom, OUTPUT_WIDTH, geo_extent, center_lat)

    # 左下角图例
    legend_y = OUTPUT_HEIGHT - 380 if has_faults else OUTPUT_HEIGHT - 300
    draw_legend(draw_final, 12, legend_y, has_faults=has_faults)

    # 顶部居中标题
    try:
        font_title = ImageFont.truetype(FONT_PATH_TITLE, 22)
    except (IOError, OSError):
        font_title = ImageFont.load_default()

    title_text = f"历史地震分布图（震中{int(radius_km)}km范围）"
    bbox_t = draw_final.textbbox((0, 0), title_text, font=font_title)
    title_w = bbox_t[2] - bbox_t[0]
    title_h = bbox_t[3] - bbox_t[1]
    title_x = OUTPUT_WIDTH // 2 - title_w // 2
    title_y = 8
    draw_final.rectangle(
        [title_x - 12, title_y - 4, title_x + title_w + 12, title_y + title_h + 6],
        fill=(255, 255, 255, 210), outline=(0, 0, 0, 200)
    )
    draw_final.text((title_x, title_y), title_text, fill=(0, 0, 0, 255), font=font_title)
    print()

    # [7/7] 保存
    print("[7/7] 保存输出图片...")
    output_rgb = result_img.convert("RGB")
    output_rgb.save(output_path, dpi=(OUTPUT_DPI, OUTPUT_DPI), quality=95)
    fsize = os.path.getsize(output_path) / 1024
    print(f"  已保存: {output_path}")
    print(f"  大小: {fsize:.1f} KB, 尺寸: {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}px\n")

    stat_text = generate_statistics(filtered, radius_km)
    print("=" * 65)
    print("【统计信息】")
    print(stat_text)
    print("=" * 65)
    return stat_text


# ============================================================
# 【脚本入口】
# ============================================================

if __name__ == "__main__":
    # 震中经度（度）
    INPUT_LON = 122.06
    # 震中纬度（度）
    INPUT_LAT = 24.67
    # 震级（M）
    INPUT_MAGNITUDE = 6.6
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
        csv_encoding="gbk"
    )