# -*- coding: utf-8 -*-
"""
历史地震分布图生成脚本（基于Python + Pillow + requests + shapefile + KMZ）
功能：根据用户输入的震中经纬度、震级以及历史地震CSV文件，
      绘制震中附近一定范围内的历史地震分布图，叠加省界、市界、县界、
      断裂图层，带经纬度边框，并输出统计信息。

依赖安装：pip install Pillow requests pyshp lxml
作者：acao123
日期：2026-02-27

修改说明：
1. 底图继续使用矢量底图(vec_c)+矢量注记(cva_c)，添加海洋蓝色底色，
   提高注记zoom级别并使用LANCZOS重采样保持清晰
2. 震级圆点增大尺寸并加粗描边，确保可见
3. 省界改为灰色(比市界深一点)，线宽减小
4. 图例每个图标前增加左侧留白
5. 比例尺右侧展示完全，增加右边距
6. 指北针增加灰色透明背景
7. 8.0级以上圆点尺寸缩小，仅比7.0级稍大
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

# 矢量底图URL（继续使用vec_c）
TIANDITU_VEC_URL = (
    "http://t{s}.tianditu.gov.cn/vec_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 矢量注记URL（继续使用cva_c）
TIANDITU_CVA_URL = (
    "http://t{s}.tianditu.gov.cn/cva_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 是否叠加矢量注记图层
ENABLE_LABEL_OVERLAY = True

# 地图内容区域尺寸（不含经纬度边框）
MAP_WIDTH = 1400
MAP_HEIGHT = 1050

# 经纬度边框宽度（像素）
BORDER_LEFT = 80
BORDER_RIGHT = 80
BORDER_TOP = 60
BORDER_BOTTOM = 60

# 最终输出图片尺寸 = 地图 + 边框
OUTPUT_WIDTH = BORDER_LEFT + MAP_WIDTH + BORDER_RIGHT
OUTPUT_HEIGHT = BORDER_TOP + MAP_HEIGHT + BORDER_BOTTOM

OUTPUT_DPI = 150
OUTPUT_FORMAT = "png"
TILE_TIMEOUT = 20
TILE_RETRY = 3

# ============================================================
# 【地震圆点配置】
# 【修改2】增大所有圆点尺寸，透明度提高到255，确保在底图上清晰可见
# 【修改7】8.0级以上圆点从34→28，仅比7.0级(26)稍大
# ============================================================

COLOR_LEVEL_1 = (0, 255, 0, 255)       # 4.7~5.9级 - 绿色
COLOR_LEVEL_2 = (255, 255, 0, 255)     # 6.0~6.9级 - 黄色
COLOR_LEVEL_3 = (255, 165, 0, 255)     # 7.0~7.9级 - 橙色
COLOR_LEVEL_4 = (255, 0, 0, 255)       # 8.0级以上 - 红色

SIZE_LEVEL_1 = 18   # 4.7~5.9级（从14增大到18）
SIZE_LEVEL_2 = 22   # 6.0~6.9级（从20增大到22）
SIZE_LEVEL_3 = 26   # 7.0~7.9级（保持26）
SIZE_LEVEL_4 = 27   # 8.0级以上（从30缩小到28，仅比7.0级稍大）

EPICENTER_STAR_COLOR = (255, 0, 0, 255)
EPICENTER_STAR_SIZE = 30

# ============================================================
# 【行政边界线样式】
# 【修改3】省界改为深灰色，线宽减小；比市界深一点即可
# ============================================================

# 省界：深灰色实线（比市界深一点，不再纯黑粗线）
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
# 【断裂线样式 - 加粗 + 带黑色衬底】
# ============================================================

FAULT_HOLOCENE_COLOR = (255, 50, 50, 255)
FAULT_HOLOCENE_WIDTH = 5
FAULT_HOLOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_HOLOCENE_SHADOW_WIDTH = 7

FAULT_LATE_PLEISTOCENE_COLOR = (255, 0, 255, 255)
FAULT_LATE_PLEISTOCENE_WIDTH = 5
FAULT_LATE_PLEISTOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_LATE_PLEISTOCENE_SHADOW_WIDTH = 7

FAULT_EARLY_PLEISTOCENE_COLOR = (0, 255, 150, 255)
FAULT_EARLY_PLEISTOCENE_WIDTH = 5
FAULT_EARLY_PLEISTOCENE_SHADOW_COLOR = (0, 0, 0, 160)
FAULT_EARLY_PLEISTOCENE_SHADOW_WIDTH = 7

FAULT_DEFAULT_COLOR = (255, 200, 50, 220)
FAULT_DEFAULT_WIDTH = 4

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
        magnitude (float): 震级
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


def get_level_color(level):
    """根据等级返回颜色"""
    return {1: COLOR_LEVEL_1, 2: COLOR_LEVEL_2, 3: COLOR_LEVEL_3, 4: COLOR_LEVEL_4}.get(level, (128, 128, 128, 150))


def get_level_size(level):
    """根据等级返回圆点直径"""
    return {1: SIZE_LEVEL_1, 2: SIZE_LEVEL_2, 3: SIZE_LEVEL_3, 4: SIZE_LEVEL_4}.get(level, 6)


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


def km_to_degree_lon(km, latitude):
    """千米转经度差"""
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """千米转纬度差"""
    return km / 110.574


def geo_to_pixel(lon, lat, geo_extent, img_width, img_height):
    """
    经纬度转地图区域像素坐标（不含边框偏移）

    参数:
        lon, lat (float): 经纬度
        geo_extent (dict): 地理范围
        img_width, img_height (int): 地图区域尺寸
    返回:
        tuple: (px, py)
    """
    px = (lon - geo_extent["min_lon"]) / (geo_extent["max_lon"] - geo_extent["min_lon"]) * img_width
    py = (geo_extent["max_lat"] - lat) / (geo_extent["max_lat"] - geo_extent["min_lat"]) * img_height
    return int(round(px)), int(round(py))


def format_degree(value, is_lon=True):
    """
    将十进制度数格式化为 度°分' 格式（如 103°24'）

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
    else:
        suffix = "N" if value >= 0 else "S"
    return f"{degrees}°{minutes:02d}'"

# ============================================================
# 【天地图瓦片函数】
# 【修改1】继续使用矢量底图+矢量注记，但改善显示效果：
#   - 底色改为浅蓝色(模拟海洋)，解决海洋区域白色问题
#   - 提高zoom级别选择策略，优先更高zoom以获取更清晰的注记文字
#   - 注记层使用LANCZOS重采样提升清晰度
# ============================================================

def select_zoom_level(geo_extent, img_width, img_height):
    """
    【修改1】选择最优缩放级别。
    在满足瓦片数量限制前提下优先选择更高zoom，使注记文字更大更清晰。

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
        # 拼接图至少为目标尺寸即可，尽量选更高zoom
        if mosaic_w >= img_width and mosaic_h >= img_height:
            best_zoom = z
            # 限制瓦片总数不超过600块（放宽限制以获取更高zoom）
            if tx * ty > 600:
                best_zoom = z - 1 if z > 1 else z
                break
        else:
            best_zoom = z
    return max(3, min(best_zoom, 18))


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
    """下载瓦片（支持重试和服务器轮询）"""
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
    【修改1】获取天地图矢量底图+矢量注记。
    改进点：
      - 拼接图底色使用浅蓝色(170,211,223)模拟海洋，解决海洋区域白色问题
      - 矢量底图瓦片本身已包含陆地颜色，会覆盖掉浅蓝色底色
      - 注记层使用LANCZOS重采样保持清晰（而非NEAREST，避免锯齿）

    参数:
        center_lon, center_lat (float): 中心经纬度
        half_span_km (float): 半边长km
        scale_denom (int): 比例尺分母
        img_width, img_height (int): 地图区域尺寸
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

    # 【修改1】底色使用浅蓝色模拟海洋，矢量瓦片的陆地部分会覆盖此颜色
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

    # 裁剪底图，使用 LANCZOS 缩放
    cropped_vec = mosaic_vec.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_vec
    resized_vec = cropped_vec.resize((img_width, img_height), Image.LANCZOS)

    # 【修改1】注记也使用 LANCZOS 缩放，兼顾清晰度和平滑度
    if ENABLE_LABEL_OVERLAY:
        cropped_cva = mosaic_cva.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_cva
        resized_cva = cropped_cva.resize((img_width, img_height), Image.LANCZOS)
        result = Image.alpha_composite(resized_vec, resized_cva)
    else:
        result = resized_vec

    return result, geo_extent


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
        list: 筛选结果
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
# 【SHP读取】
# ============================================================

def read_shapefile_lines(shp_path, geo_extent):
    """
    读取SHP文件边界线段

    参数:
        shp_path (str): SHP路径
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
# 【绘线函数（实线/虚线/带衬底）】
# ============================================================

def draw_solid_lines(draw, lines, geo_extent, img_w, img_h, color, width):
    """
    绘制实线

    参数:
        draw: 绘图对象
        lines: 线段列表
        geo_extent: 地理范围
        img_w, img_h: 图片尺寸
        color: RGBA颜色
        width: 线宽
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_solid_lines_with_shadow(draw, lines, geo_extent, img_w, img_h,
                                 color, width, shadow_color, shadow_width):
    """
    绘制带黑色衬底的实线

    参数:
        draw: 绘图对象
        lines: 线段列表
        geo_extent: 地理范围
        img_w, img_h: 图片尺寸
        color: 主线颜色
        width: 主线宽度
        shadow_color: 衬底颜色
        shadow_width: 衬底宽度
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=shadow_color, width=shadow_width)
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)


def draw_dashed_lines(draw, lines, geo_extent, img_w, img_h, color, width, dash):
    """
    绘制虚线

    参数:
        draw: 绘图对象
        lines: 线段列表
        geo_extent: 地理范围
        img_w, img_h: 图片尺寸
        color: RGBA颜色
        width: 线宽
        dash: (线段长, 间隔长)
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, color, width, dash)


def draw_dashed_lines_with_shadow(draw, lines, geo_extent, img_w, img_h,
                                  color, width, dash, shadow_color, shadow_width):
    """
    绘制带衬底的虚线

    参数:
        draw: 绘图对象
        lines: 线段列表
        geo_extent: 地理范围
        img_w, img_h: 图片尺寸
        color: 主线颜色
        width: 主线宽度
        dash: (线段长, 间隔长)
        shadow_color: 衬底颜色
        shadow_width: 衬底宽度
    """
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, shadow_color, shadow_width, dash)
    for line in lines:
        pts = [geo_to_pixel(lon, lat, geo_extent, img_w, img_h) for lon, lat in line]
        if len(pts) >= 2:
            _draw_dashed_polyline(draw, pts, color, width, dash)


def _draw_dashed_polyline(draw, pts, color, width, dash):
    """
    绘制虚线折线

    参数:
        draw: 绘图对象
        pts: 像���坐标列表
        color: 颜色
        width: 线宽
        dash: (线段长, 间隔长)
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
# 【KMZ断裂解析】
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent):
    """
    解析KMZ断裂线

    参数:
        kmz_path (str): KMZ路径
        geo_extent (dict): ���理范围
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
    """解析KML断裂"""
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


def _classify_fault(name, style_url, description, style_colors):
    """判断断裂类型"""
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
    """提取LineString坐标"""
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
    """解析KML coordinates"""
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
    绘制断裂线（带黑色衬底）

    参数:
        draw: 绘图对象
        fault_data: 分类断裂线
        geo_extent: 地理范围
        img_w, img_h: 图片尺寸
    """
    sm = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH,
                     FAULT_HOLOCENE_SHADOW_COLOR, FAULT_HOLOCENE_SHADOW_WIDTH),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH,
                             FAULT_LATE_PLEISTOCENE_SHADOW_COLOR, FAULT_LATE_PLEISTOCENE_SHADOW_WIDTH),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH,
                              FAULT_EARLY_PLEISTOCENE_SHADOW_COLOR, FAULT_EARLY_PLEISTOCENE_SHADOW_WIDTH),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, (0, 0, 0, 100), FAULT_DEFAULT_WIDTH + 2),
    }
    for ftype, lines in fault_data.items():
        if not lines:
            continue
        c, w, sc, sw = sm.get(ftype, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, (0, 0, 0, 100),
                                      FAULT_DEFAULT_WIDTH + 2))
        draw_solid_lines_with_shadow(draw, lines, geo_extent, img_w, img_h, c, w, sc, sw)
        print(f"  绘制 {ftype}: {len(lines)} 条")


# ============================================================
# 【绘图函数】
# ============================================================

def draw_star(draw, cx, cy, radius, color, num_points=5):
    """
    绘制五角星

    参数:
        draw: 绘图对象
        cx, cy: 中心坐标
        radius: 外接圆半径
        color: RGBA颜色
        num_points: 角数
    """
    inner_r = radius * 0.382
    points = []
    for i in range(num_points * 2):
        angle = math.radians(i * 360.0 / (num_points * 2) - 90)
        r = radius if i % 2 == 0 else inner_r
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=color, outline=(0, 0, 0, 255))


def draw_earthquake_points(draw, filtered_quakes, geo_extent, img_w, img_h):
    """
    【修改2】绘制历史地震圆点（加粗描边，确保在矢量底图上清晰可见）

    参数:
        draw: 绘图对象
        filtered_quakes: 地震记录
        geo_extent: 地理范围
        img_w, img_h: 地图区域尺寸
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
            # 外层白色描边（加粗到3px，确保醒目）
            draw.ellipse([px - h - 3, py - h - 3, px + h + 3, py + h + 3],
                         fill=None, outline=(255, 255, 255, 255), width=3)
            # 填充色圆点 + 黑色内描边（加粗到2px）
            draw.ellipse([px - h, py - h, px + h, py + h],
                         fill=color, outline=(0, 0, 0, 255), width=2)
            count += 1
    print(f"  绘制地震点: {count}")
    return count


def draw_epicenter(draw, center_lon, center_lat, geo_extent, img_w, img_h):
    """
    绘制震中五角星（图层最高优先级）

    参数:
        draw: 绘图对象
        center_lon, center_lat: 震中经纬度
        geo_extent: 地理范围
        img_w, img_h: 地图区域尺寸
    """
    px, py = geo_to_pixel(center_lon, center_lat, geo_extent, img_w, img_h)
    draw_star(draw, px, py, EPICENTER_STAR_SIZE, EPICENTER_STAR_COLOR)
    print(f"  震中: ({px}, {py})")


# ============================================================
# 【经纬度边框绘制】
# ============================================================

def draw_coordinate_border(final_draw, geo_extent, final_img_width, final_img_height):
    """
    在最终图片上绘制经纬度刻度边框。

    参数:
        final_draw (ImageDraw): 最终图片的绘图对象
        geo_extent (dict): 地理范围
        final_img_width (int): 最终图片总宽度
        final_img_height (int): 最终图片总高度
    """
    try:
        font_coord = ImageFont.truetype(FONT_PATH_NORMAL, 13)
    except (IOError, OSError):
        font_coord = ImageFont.load_default()

    map_left = BORDER_LEFT
    map_top = BORDER_TOP
    map_right = BORDER_LEFT + MAP_WIDTH
    map_bottom = BORDER_TOP + MAP_HEIGHT

    draw_rect_outline(final_draw, map_left, map_top, map_right, map_bottom, (0, 0, 0, 255), 2)

    min_lon = geo_extent["min_lon"]
    max_lon = geo_extent["max_lon"]
    min_lat = geo_extent["min_lat"]
    max_lat = geo_extent["max_lat"]

    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat
    lon_step = _choose_tick_step(lon_range, target_min=4, target_max=6)
    lat_step = _choose_tick_step(lat_range, target_min=4, target_max=6)

    tick_len = 8

    # ========== 经度刻度（顶部和底部） ==========
    lon_start = math.ceil(min_lon / lon_step) * lon_step
    lon_val = lon_start
    while lon_val <= max_lon:
        frac = (lon_val - min_lon) / (max_lon - min_lon)
        px = map_left + int(frac * MAP_WIDTH)

        if map_left <= px <= map_right:
            final_draw.line([(px, map_top), (px, map_top - tick_len)],
                            fill=(0, 0, 0, 255), width=1)
            final_draw.line([(px, map_bottom), (px, map_bottom + tick_len)],
                            fill=(0, 0, 0, 255), width=1)

            label = format_degree(lon_val, is_lon=True)
            bb = final_draw.textbbox((0, 0), label, font=font_coord)
            tw = bb[2] - bb[0]
            final_draw.text((px - tw // 2, map_top - tick_len - 18),
                            label, fill=(0, 0, 0, 255), font=font_coord)
            final_draw.text((px - tw // 2, map_bottom + tick_len + 4),
                            label, fill=(0, 0, 0, 255), font=font_coord)

        lon_val += lon_step

    # ========== 纬度刻度（左侧和右侧） ==========
    lat_start = math.ceil(min_lat / lat_step) * lat_step
    lat_val = lat_start
    while lat_val <= max_lat:
        frac = (max_lat - lat_val) / (max_lat - min_lat)
        py = map_top + int(frac * MAP_HEIGHT)

        if map_top <= py <= map_bottom:
            final_draw.line([(map_left, py), (map_left - tick_len, py)],
                            fill=(0, 0, 0, 255), width=1)
            final_draw.line([(map_right, py), (map_right + tick_len, py)],
                            fill=(0, 0, 0, 255), width=1)

            label = format_degree(lat_val, is_lon=False)
            bb = final_draw.textbbox((0, 0), label, font=font_coord)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            final_draw.text((map_left - tick_len - tw - 6, py - th // 2),
                            label, fill=(0, 0, 0, 255), font=font_coord)
            final_draw.text((map_right + tick_len + 6, py - th // 2),
                            label, fill=(0, 0, 0, 255), font=font_coord)

        lat_val += lat_step


def draw_rect_outline(draw, x1, y1, x2, y2, color, width):
    """绘制矩形外框"""
    draw.line([(x1, y1), (x2, y1)], fill=color, width=width)
    draw.line([(x2, y1), (x2, y2)], fill=color, width=width)
    draw.line([(x2, y2), (x1, y2)], fill=color, width=width)
    draw.line([(x1, y2), (x1, y1)], fill=color, width=width)


def _choose_tick_step(range_deg, target_min=4, target_max=6):
    """根据地理范围选择合适的刻度间隔"""
    candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    for step in candidates:
        n_ticks = range_deg / step
        if target_min <= n_ticks <= target_max:
            return step
    best_step = range_deg / 5.0
    best_diff = float('inf')
    for step in candidates:
        n_ticks = range_deg / step
        diff = abs(n_ticks - 5)
        if diff < best_diff:
            best_diff = diff
            best_step = step
    return best_step


# ============================================================
# 【指北针和比例尺（绘制在地图区域内）】
# ============================================================

def draw_north_arrow(draw, x, y, size=55):
    """
    【修改6】绘制指北针（增加灰色透明圆形背景）

    参数:
        draw: 绘图对象
        x, y: 位置（指北针顶部中心）
        size: 大小
    """
    # 计算指北针整体中心（偏下一点，因为包含N字母在上方）
    center_x = x
    center_y = y + size // 2

    # 【修改6】绘制灰色半透明圆形背景
    bg_radius = int(size * 0.85)
    draw.ellipse(
        [center_x - bg_radius, center_y - bg_radius - 8,
         center_x + bg_radius, center_y + bg_radius - 8],
        fill=(220, 220, 220, 130),
        outline=(150, 150, 150, 200),
        width=1
    )

    # 指北针箭头
    top = (x, y)
    bl = (x - size // 4, y + size)
    br = (x + size // 4, y + size)
    cp = (x, int(y + size * 0.65))
    draw.polygon([top, bl, cp], fill=(0, 0, 0, 255))
    draw.polygon([top, br, cp], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    draw.polygon([top, bl, cp], outline=(0, 0, 0, 255))

    # N字母
    try:
        fn = ImageFont.truetype(FONT_PATH_TITLE, size // 3)
    except (IOError, OSError):
        fn = ImageFont.load_default()
    bb = draw.textbbox((0, 0), "N", font=fn)
    draw.text((x - (bb[2] - bb[0]) // 2, y - size // 3 - 8), "N", fill=(0, 0, 0, 255), font=fn)


def draw_scale_bar(draw, x, y, scale_denom, map_width, geo_extent, center_lat):
    """
    【修改5】绘制比例尺（修复右侧展示不全问题）
    - 增大右侧空间，确保末端数字完整显示
    - 添加自动左移逻辑，确保不超出地图区域

    参数:
        draw: 绘图对象
        x, y: 位置
        scale_denom: 比例尺分母
        map_width: 地图区域宽度
        geo_extent: 地理范围
        center_lat: 中心纬度
    """
    lr = geo_extent["max_lon"] - geo_extent["min_lon"]
    kpp = lr * 111.32 * math.cos(math.radians(center_lat)) / map_width
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    target = map_width * 0.15 * kpp
    bar_km = nice[0]
    for nd in nice:
        if nd <= target * 1.2:
            bar_km = nd
        else:
            break
    bar_px = int(bar_km / kpp)

    try:
        fs = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        fs = ImageFont.load_default()

    el = f"{bar_km} km"
    bb_el = draw.textbbox((0, 0), el, font=fs)
    el_w = bb_el[2] - bb_el[0]

    st = f"1:{scale_denom:,}"
    bb_st = draw.textbbox((0, 0), st, font=fs)
    st_w = bb_st[2] - bb_st[0]

    # 【修改5】右侧额外空间 = 末端标注完整宽度 + 充足间距
    right_extra = el_w + 20
    box_w = max(bar_px + right_extra, st_w + 24)

    # 【修改5】自动左移，确保整个比例尺在地图区域内
    actual_x = min(x, map_width - box_w - 16)
    actual_x = max(10, actual_x)  # 也不要太靠左

    # 背景框
    draw.rectangle([actual_x - 10, y - 30, actual_x + box_w + 10, y + 32],
                   fill=(255, 255, 255, 220), outline=(0, 0, 0, 200))

    # 黑白交替刻度条
    bh, ns_seg = 8, 4
    sw = bar_px // ns_seg
    for i in range(ns_seg):
        c = (0, 0, 0, 255) if i % 2 == 0 else (255, 255, 255, 255)
        draw.rectangle([actual_x + i * sw, y, actual_x + (i + 1) * sw, y + bh],
                       fill=c, outline=(0, 0, 0, 255))

    # 起点 "0"
    draw.text((actual_x, y + bh + 4), "0", fill=(0, 0, 0, 255), font=fs)

    # 【修改5】末端距离标注（紧跟在比例尺条右端后面，留4px间距）
    draw.text((actual_x + bar_px + 4, y + bh + 4), el, fill=(0, 0, 0, 255), font=fs)

    # 比例尺分母居中在条的上方
    draw.text((actual_x + bar_px // 2 - st_w // 2, y - 22), st, fill=(0, 0, 0, 255), font=fs)


# ============================================================
# 【图例 —— 左下角，每项增加前置留白】
# ============================================================

def draw_legend(draw, x, y, has_faults=True):
    """
    【修改4】绘制图例，每个图标前增加留白
    【修改7】8.0级以上圆点在图例中也使用调整后的尺寸

    参数:
        draw: 绘图对象
        x, y: 左上角坐标（地图区域内坐标）
        has_faults: 是否含断裂图例
    """
    try:
        ft = ImageFont.truetype(FONT_PATH_TITLE, 18)
        fi = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        ft = ImageFont.load_default()
        fi = ImageFont.load_default()

    item_h = 30
    n_items = 1 + 3 + (3 if has_faults else 0) + 4
    legend_h = 40 + n_items * item_h + 10
    # 【修改4】增加图例宽度，容纳左侧留白
    legend_w = 195

    draw.rectangle([x, y, x + legend_w, y + legend_h],
                   fill=(255, 255, 255, 240), outline=(0, 0, 0, 255), width=2)

    # 标题居中
    bb_title = draw.textbbox((0, 0), "图  例", font=ft)
    title_w = bb_title[2] - bb_title[0]
    draw.text((x + legend_w // 2 - title_w // 2, y + 10), "图  例", fill=(0, 0, 0, 255), font=ft)

    cy = y + 42
    # 【修改4】图标中心X增加左侧留白（从x+22 → x+32）
    icx = x + 32
    # 【修改4】线条图标起点（从x+6 → x+16）
    line_start = x + 16
    line_end = x + 48
    # 【修改4】文字起始X增加（从x+45 → x+58）
    tx = x + 58

    # === 震中位置（大五角星，第一行） ===
    draw_star(draw, icx, cy + 3, 14, EPICENTER_STAR_COLOR)
    draw.text((tx, cy - 5), "震中位置", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 省界（灰色实线，修改3后的颜色） ===
    draw.line([(line_start, cy + 3), (line_end, cy + 3)],
              fill=PROVINCE_BORDER_COLOR, width=PROVINCE_BORDER_WIDTH)
    draw.text((tx, cy - 5), "省界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 市界（灰色虚线） ===
    for dx in range(0, 32, 10):
        draw.line([(line_start + dx, cy + 3), (line_start + dx + 6, cy + 3)],
                  fill=CITY_BORDER_COLOR, width=CITY_BORDER_WIDTH)
    draw.text((tx, cy - 5), "市界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 县界（浅灰色虚线） ===
    for dx in range(0, 32, 8):
        draw.line([(line_start + dx, cy + 3), (line_start + dx + 4, cy + 3)],
                  fill=COUNTY_BORDER_COLOR, width=COUNTY_BORDER_WIDTH)
    draw.text((tx, cy - 5), "县界", fill=(0, 0, 0, 255), font=fi)
    cy += item_h

    # === 断裂线 ===
    if has_faults:
        draw.line([(line_start, cy + 3), (line_end, cy + 3)],
                  fill=FAULT_HOLOCENE_COLOR, width=FAULT_HOLOCENE_WIDTH)
        draw.text((tx, cy - 5), "全新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

        draw.line([(line_start, cy + 3), (line_end, cy + 3)],
                  fill=FAULT_LATE_PLEISTOCENE_COLOR, width=FAULT_LATE_PLEISTOCENE_WIDTH)
        draw.text((tx, cy - 5), "晚更新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

        draw.line([(line_start, cy + 3), (line_end, cy + 3)],
                  fill=FAULT_EARLY_PLEISTOCENE_COLOR, width=FAULT_EARLY_PLEISTOCENE_WIDTH)
        draw.text((tx, cy - 5), "早中更新世断层", fill=(0, 0, 0, 255), font=fi)
        cy += item_h

    # === 地震圆点（从大到小排列） ===
    for label, color, dot_size in [
        ("8.0级以上", COLOR_LEVEL_4, SIZE_LEVEL_4),
        ("7.0~7.9级", COLOR_LEVEL_3, SIZE_LEVEL_3),
        ("6.0~6.9级", COLOR_LEVEL_2, SIZE_LEVEL_2),
        ("4.7~5.9级", COLOR_LEVEL_1, SIZE_LEVEL_1),
    ]:
        h = dot_size // 2
        draw.ellipse([icx - h, cy + 3 - h, icx + h, cy + 3 + h],
                     fill=color, outline=(0, 0, 0, 180))
        draw.text((tx, cy - 5), label, fill=(0, 0, 0, 255), font=fi)
        cy += item_h

# ============================================================
# 【统计函数】
# ============================================================

def generate_statistics(filtered_quakes, radius_km):
    """
    生成统计信息

    参数:
        filtered_quakes (list): 筛选后地震记录
        radius_km (float): 半径
    返回:
        str: 统计文本
    """
    ct = len(filtered_quakes)
    c1 = sum(1 for e in filtered_quakes if 4.7 <= e["magnitude"] <= 5.9)
    c2 = sum(1 for e in filtered_quakes if 6.0 <= e["magnitude"] <= 6.9)
    c3 = sum(1 for e in filtered_quakes if 7.0 <= e["magnitude"] <= 7.9)
    c4 = sum(1 for e in filtered_quakes if e["magnitude"] >= 8.0)
    mx = max(filtered_quakes, key=lambda e: e["magnitude"]) if filtered_quakes else None

    txt = (f"自1900年以来，本次地震震中{int(radius_km)}km范围内"
           f"曾发生{ct}次4.7级以上地震，\n"
           f"其中4.7~5.9级地震{c1}次，6.0~6.9级地震{c2}次，"
           f"7.0~7.9级地震{c3}次，8.0级以上地震{c4}次。")
    if mx:
        y_s = str(mx.get("year", 0)) if mx.get("year", 0) > 0 else "未知"
        m_s = str(mx.get("month", 0)) if mx.get("month", 0) > 0 else "未知"
        d_s = str(mx.get("day", 0)) if mx.get("day", 0) > 0 else "未知"
        txt += f"\n最大地震为{y_s}年{m_s}月{d_s}日{mx.get('location', '')}{mx['magnitude']}级地震。"
    return txt


# ============================================================
# 【主函数】
# ============================================================

def generate_earthquake_map(center_lon, center_lat, magnitude, csv_path,
                            output_path, csv_encoding="gbk"):
    """
    生成历史地震分布图（含经纬度边框、行政边��、断裂线图层）

    修改说明：
        1. 矢量底图拼接底色改为浅蓝色(海洋色)，注记LANCZOS重采样更清晰
        2. 地震圆点增大+加粗描边，确保可见
        3. 省界改为灰色，比市界深一点
        4. 图例每项图标前增加留白
        5. 比例尺修复右侧展示不全
        6. 指北针增加灰色透明背景
        7. 8.0级以上圆点缩小，仅比7.0级稍大

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

    # [1/7] 读取CSV
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

    # [3/7] 底图（矢量底图 + 矢量注记）
    print("[3/7] 获取天地图矢量底图+矢量注记...")
    basemap, geo_extent = fetch_basemap(center_lon, center_lat, half_span_km,
                                        scale_denom, MAP_WIDTH, MAP_HEIGHT)
    print()

    # [4/7] 行政边界
    print("[4/7] 读取行政边界SHP...")
    print("  --- 省界 ---")
    province_lines = read_shapefile_lines(SHP_PROVINCE_PATH, geo_extent)
    print("  --- 市界 ---")
    city_lines = read_shapefile_lines(SHP_CITY_PATH, geo_extent)
    print("  --- 县界 ---")
    county_lines = read_shapefile_lines(SHP_COUNTY_PATH, geo_extent)
    print()

    # [5/7] 断裂
    print("[5/7] 读取断裂KMZ...")
    fault_data = parse_kmz_faults(KMZ_FAULT_PATH, geo_extent)
    has_faults = any(len(v) > 0 for v in fault_data.values())
    print()

    # [6/7] 绘制所有图层
    print("[6/7] 绘制图层要素...")
    map_img = basemap.convert("RGBA")

    # --- 图层1: 行政边界 ---
    bd_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_bd = ImageDraw.Draw(bd_layer)

    # 县界：浅灰色虚线（最细）
    if county_lines:
        print("  绘制县界...")
        draw_dashed_lines(draw_bd, county_lines, geo_extent,
                          MAP_WIDTH, MAP_HEIGHT,
                          COUNTY_BORDER_COLOR, COUNTY_BORDER_WIDTH,
                          COUNTY_BORDER_DASH)

    # 市界：灰色虚线（中等）
    if city_lines:
        print("  绘制市界...")
        draw_dashed_lines(draw_bd, city_lines, geo_extent,
                          MAP_WIDTH, MAP_HEIGHT,
                          CITY_BORDER_COLOR, CITY_BORDER_WIDTH,
                          CITY_BORDER_DASH)

    # 【修改3】省界：深灰色实线（比市界深一点，不再纯黑粗线）
    if province_lines:
        print("  绘制省界...")
        draw_solid_lines(draw_bd, province_lines, geo_extent,
                         MAP_WIDTH, MAP_HEIGHT,
                         PROVINCE_BORDER_COLOR, PROVINCE_BORDER_WIDTH)

    map_img = Image.alpha_composite(map_img, bd_layer)

    # --- 图层2: 断裂线（加粗+黑色衬底） ---
    if has_faults:
        ft_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
        draw_ft = ImageDraw.Draw(ft_layer)
        draw_fault_lines(draw_ft, fault_data, geo_extent, MAP_WIDTH, MAP_HEIGHT)
        map_img = Image.alpha_composite(map_img, ft_layer)

    # --- 图层3: 历史地震圆点（加粗描边，确保可见） ---
    eq_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_eq = ImageDraw.Draw(eq_layer)
    draw_earthquake_points(draw_eq, filtered, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    map_img = Image.alpha_composite(map_img, eq_layer)

    # --- 图层4: 震中五角星（最顶层） ---
    epi_layer = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw_epi = ImageDraw.Draw(epi_layer)
    draw_epicenter(draw_epi, center_lon, center_lat, geo_extent, MAP_WIDTH, MAP_HEIGHT)
    map_img = Image.alpha_composite(map_img, epi_layer)

    # --- 装饰要素（在地图区域内绘制） ---
    draw_map = ImageDraw.Draw(map_img)

    # 【修改6】右上角指北针（带灰色透明背景）
    draw_north_arrow(draw_map, MAP_WIDTH - 50, 20, size=50)

    # 【修改5】右下角比例尺（位置往左移更多，确保完整展示）
    draw_scale_bar(draw_map, MAP_WIDTH - 280, MAP_HEIGHT - 45,
                   scale_denom, MAP_WIDTH, geo_extent, center_lat)

    # 【修改4】左下角图例：图标前有留白
    item_h = 30
    n_items = 1 + 3 + (3 if has_faults else 0) + 4
    legend_h = 40 + n_items * item_h + 10
    legend_x = 0
    legend_y = MAP_HEIGHT - legend_h
    draw_legend(draw_map, legend_x, legend_y, has_faults=has_faults)

    # 顶部居中标题
    try:
        font_title = ImageFont.truetype(FONT_PATH_TITLE, 22)
    except (IOError, OSError):
        font_title = ImageFont.load_default()

    title_text = f"历史地震分布图（震中{int(radius_km)}km范围）"
    bbox_t = draw_map.textbbox((0, 0), title_text, font=font_title)
    title_w = bbox_t[2] - bbox_t[0]
    title_h = bbox_t[3] - bbox_t[1]
    title_x = MAP_WIDTH // 2 - title_w // 2
    title_y = 8
    draw_map.rectangle(
        [title_x - 12, title_y - 4, title_x + title_w + 12, title_y + title_h + 6],
        fill=(255, 255, 255, 210), outline=(0, 0, 0, 200)
    )
    draw_map.text((title_x, title_y), title_text, fill=(0, 0, 0, 255), font=font_title)
    print()

    # [7/7] 组装最终图片
    print("[7/7] 组装经纬度边框并保存...")

    final_img = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (255, 255, 255, 255))
    final_img.paste(map_img, (BORDER_LEFT, BORDER_TOP))

    final_draw = ImageDraw.Draw(final_img)
    draw_coordinate_border(final_draw, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)

    output_rgb = final_img.convert("RGB")
    output_rgb.save(output_path, dpi=(OUTPUT_DPI, OUTPUT_DPI), quality=95)
    fsize = os.path.getsize(output_path) / 1024
    print(f"  已保存: {output_path}")
    print(f"  大小: {fsize:.1f} KB")
    print(f"  尺寸: {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}px")
    print(f"  地图区域: {MAP_WIDTH}x{MAP_HEIGHT}px")
    print(f"  边框: 左={BORDER_LEFT} 右={BORDER_RIGHT} 上={BORDER_TOP} 下={BORDER_BOTTOM}px\n")

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

