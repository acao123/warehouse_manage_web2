# -*- coding: utf-8 -*-
"""
历史地震分布图生成脚本（基于Python + matplotlib + requests + shapefile + KMZ）
功能：根据用户输入的震中经纬度、震级以及历史地震CSV文件，
      绘制震中附近一定范围内的历史地震分布图，叠加省界、市界、县界、
      断裂图层，带经纬度边框，并输出为 Adobe Illustrator 兼容格式(.ai/.pdf/.eps/.svg)。

      矢量要素（行政边界、断裂线、地震圆点、五角星、图例、比例尺、指北针）
      全部以矢量形式输出，底图瓦片作为嵌入位图。

依赖安装：pip install matplotlib requests pyshp lxml Pillow numpy
作者：acao123
日期：2026-02-27

输出格式说明：
    - .ai  → 实际输出为EPS格式（Adobe Illustrator原生支持打开编辑EPS）
    - .pdf → PDF矢量格式（AI可直接打开编辑）
    - .eps → EPS矢量格式（AI可直接打开编辑）
    - .svg → SVG矢量格式（AI可直接打开编辑）
    - .png → 传统位图格式（兼容保留）
"""

import os
import sys
import csv
import math
import time
import zipfile
import requests
import numpy as np
from io import BytesIO
from lxml import etree
from PIL import Image

import matplotlib
matplotlib.use('Agg')  # 无GUI后端
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
from matplotlib.patches import FancyArrowPatch, Circle, Rectangle, Polygon
from matplotlib.collections import LineCollection
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import matplotlib.font_manager as fm

try:
    import shapefile
except ImportError:
    print("*** 请安装pyshp库: pip install pyshp ***")
    sys.exit(1)

# ============================================================
# 【字体配置 - matplotlib中文支持】
# ============================================================

# 尝试加载中文字体
_FONT_CANDIDATES_TITLE = [
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_CANDIDATES_NORMAL = [
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/STSong.ttf",
]

FONT_PROP_TITLE = None
FONT_PROP_NORMAL = None

for fp in _FONT_CANDIDATES_TITLE:
    if os.path.exists(fp):
        FONT_PROP_TITLE = fm.FontProperties(fname=fp)
        break
if FONT_PROP_TITLE is None:
    FONT_PROP_TITLE = fm.FontProperties(family='sans-serif')

for fp in _FONT_CANDIDATES_NORMAL:
    if os.path.exists(fp):
        FONT_PROP_NORMAL = fm.FontProperties(fname=fp)
        break
if FONT_PROP_NORMAL is None:
    FONT_PROP_NORMAL = fm.FontProperties(family='serif')

# 配置matplotlib默认字体（备用）
plt.rcParams['font.sans-serif'] = ['SimHei', 'WenQuanYi Zen Hei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ============================================================
# 【配置常量区域】
# ============================================================

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

# 图片尺寸（英寸），用于matplotlib figure
FIG_WIDTH_INCH = 15.6    # 约1560px @ 100dpi
FIG_HEIGHT_INCH = 11.7   # 约1170px @ 100dpi

OUTPUT_DPI = 150
TILE_TIMEOUT = 20
TILE_RETRY = 3

# ============================================================
# 【地震圆点配置 - matplotlib使用点(point)为单位】
# ============================================================

# 颜色（归一化到0-1，带alpha）
COLOR_LEVEL_1 = (0.0, 1.0, 0.0, 1.0)       # 4.7~5.9级 - 绿色
COLOR_LEVEL_2 = (1.0, 1.0, 0.0, 1.0)       # 6.0~6.9级 - 黄色
COLOR_LEVEL_3 = (1.0, 0.647, 0.0, 1.0)     # 7.0~7.9级 - 橙色
COLOR_LEVEL_4 = (1.0, 0.0, 0.0, 1.0)       # 8.0级以上 - 红色

# matplotlib scatter 的 s 参数是面积(point^2)
SIZE_LEVEL_1 = 120    # 4.7~5.9级
SIZE_LEVEL_2 = 200    # 6.0~6.9级
SIZE_LEVEL_3 = 320    # 7.0~7.9级
SIZE_LEVEL_4 = 380    # 8.0级以上（仅比7.0级稍大）

EPICENTER_STAR_COLOR = (1.0, 0.0, 0.0, 1.0)
EPICENTER_STAR_SIZE = 500  # 五角星面积

# ============================================================
# 【行政边界线样式 - matplotlib格式】
# 省界：深灰色实线（比市界深一点）
# ============================================================

PROVINCE_BORDER_COLOR = (0.235, 0.235, 0.235, 1.0)  # (60,60,60)/255
PROVINCE_BORDER_WIDTH = 1.2

CITY_BORDER_COLOR = (0.392, 0.392, 0.392, 1.0)      # (100,100,100)/255
CITY_BORDER_WIDTH = 0.6
CITY_BORDER_DASH = (8, 4)

COUNTY_BORDER_COLOR = (0.627, 0.627, 0.627, 0.86)   # (160,160,160)/255
COUNTY_BORDER_WIDTH = 0.4
COUNTY_BORDER_DASH = (5, 3)

# ============================================================
# 【断裂线样式】
# ============================================================

FAULT_HOLOCENE_COLOR = (1.0, 0.196, 0.196, 1.0)
FAULT_HOLOCENE_WIDTH = 2.0

FAULT_LATE_PLEISTOCENE_COLOR = (1.0, 0.0, 1.0, 1.0)
FAULT_LATE_PLEISTOCENE_WIDTH = 2.0

FAULT_EARLY_PLEISTOCENE_COLOR = (0.0, 1.0, 0.588, 1.0)
FAULT_EARLY_PLEISTOCENE_WIDTH = 2.0

FAULT_DEFAULT_COLOR = (1.0, 0.784, 0.196, 0.86)
FAULT_DEFAULT_WIDTH = 1.5

# ============================================================
# 【文件路径】
# ============================================================

SHP_PROVINCE_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
SHP_CITY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
SHP_COUNTY_PATH = r"../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"
KMZ_FAULT_PATH = r"../../data/geology/断层/全国六代图断裂.KMZ"

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
    """根据震级确定绘图范围参数"""
    if magnitude < 6.0:
        return 15, 30, 150000
    elif magnitude < 7.0:
        return 50, 100, 500000
    else:
        return 150, 300, 1500000


def get_earthquake_level(mag):
    """根据震级返回等级(1-4)，0=不在范围"""
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
    return {1: COLOR_LEVEL_1, 2: COLOR_LEVEL_2,
            3: COLOR_LEVEL_3, 4: COLOR_LEVEL_4}.get(level, (0.5, 0.5, 0.5, 0.6))


def get_level_size(level):
    """根据等级返回散点面积"""
    return {1: SIZE_LEVEL_1, 2: SIZE_LEVEL_2,
            3: SIZE_LEVEL_3, 4: SIZE_LEVEL_4}.get(level, 30)


def haversine_distance(lon1, lat1, lon2, lat2):
    """Haversine公式计算球面距离(km)"""
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


def format_degree(value, is_lon=True):
    """将十进制度数格式化为 度°分' 格式"""
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes = int((abs_val - degrees) * 60)
    if is_lon:
        suffix = "E" if value >= 0 else "W"
    else:
        suffix = "N" if value >= 0 else "S"
    return f"{degrees}°{minutes:02d}'{suffix}"

# ============================================================
# 【天地图瓦片函数】
# ============================================================

def select_zoom_level(geo_extent, target_width=1400, target_height=1050):
    """选择最优缩放级别"""
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]
    best_zoom = 1
    for z in range(1, 19):
        m = TIANDITU_MATRIX[z]
        tx = math.ceil(lon_range / m["tile_span_lon"]) + 1
        ty = math.ceil(lat_range / m["tile_span_lat"]) + 1
        mosaic_w = tx * 256
        mosaic_h = ty * 256
        if mosaic_w >= target_width and mosaic_h >= target_height:
            best_zoom = z
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


def fetch_basemap_array(center_lon, center_lat, half_span_km, geo_extent):
    """
    获取天地图矢量底图+矢量注记，返回numpy数组和地理范围。
    底色使用浅蓝色模拟海洋。

    返回:
        tuple: (numpy_array [H,W,4], geo_extent)
    """
    target_w = int(FIG_WIDTH_INCH * OUTPUT_DPI)
    target_h = int(FIG_HEIGHT_INCH * OUTPUT_DPI)

    print(f"  地理范围: 经度[{geo_extent['min_lon']:.4f}, {geo_extent['max_lon']:.4f}], "
          f"纬度[{geo_extent['min_lat']:.4f}, {geo_extent['max_lat']:.4f}]")

    zoom = select_zoom_level(geo_extent, target_w, target_h)
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
            tile = download_tile_with_retry(TIANDITU_VEC_URL, zoom, col, row)
            if tile:
                mosaic_vec.paste(tile.convert("RGBA"), (px, py))
                dl_ok += 1
            else:
                dl_fail += 1
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

    cropped_vec = mosaic_vec.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_vec
    resized_vec = cropped_vec.resize((target_w, target_h), Image.LANCZOS)

    if ENABLE_LABEL_OVERLAY:
        cropped_cva = mosaic_cva.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic_cva
        resized_cva = cropped_cva.resize((target_w, target_h), Image.LANCZOS)
        result = Image.alpha_composite(resized_vec, resized_cva)
    else:
        result = resized_vec

    # 转为numpy数组供matplotlib imshow使用
    return np.array(result.convert("RGBA")), geo_extent


# ============================================================
# 【CSV读取】
# ============================================================

def read_earthquake_csv(csv_path, encoding="gbk"):
    """读取历史地震CSV"""
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
    """筛选范围内地震"""
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
    """读取SHP文件边界线段，返回 [[(lon,lat),...], ...]"""
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
# 【KMZ断裂解析】
# ============================================================

def parse_kmz_faults(kmz_path, geo_extent):
    """解析KMZ断裂线"""
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

# ============================================================
# 【matplotlib矢量绘图函数】
# ============================================================

def plot_lines_on_ax(ax, lines, color, linewidth, linestyle='-', zorder=2, label=None):
    """
    在matplotlib axes上绘制线段（矢量）

    参数:
        ax: matplotlib Axes对象
        lines: [[(lon,lat),...], ...] 线段列表
        color: 颜色
        linewidth: 线宽
        linestyle: 线型 '-'实线, '--'虚线, ':'点线, '-.'点划线
        zorder: 图层顺序
        label: 图例标签（仅第一条线使用）
    """
    first = True
    for line in lines:
        if len(line) < 2:
            continue
        lons = [p[0] for p in line]
        lats = [p[1] for p in line]
        if first and label:
            ax.plot(lons, lats, color=color, linewidth=linewidth,
                    linestyle=linestyle, zorder=zorder, label=label, solid_capstyle='round')
            first = False
        else:
            ax.plot(lons, lats, color=color, linewidth=linewidth,
                    linestyle=linestyle, zorder=zorder, solid_capstyle='round')


def plot_fault_lines(ax, fault_data):
    """
    绘制断裂线（矢量，带黑色衬底）

    参数:
        ax: matplotlib Axes对象
        fault_data: 分类断裂线字典
    """
    style_map = {
        "holocene": (FAULT_HOLOCENE_COLOR, FAULT_HOLOCENE_WIDTH, "全新世断层"),
        "late_pleistocene": (FAULT_LATE_PLEISTOCENE_COLOR, FAULT_LATE_PLEISTOCENE_WIDTH, "晚更新世断层"),
        "early_pleistocene": (FAULT_EARLY_PLEISTOCENE_COLOR, FAULT_EARLY_PLEISTOCENE_WIDTH, "早中更新世断层"),
        "default": (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, None),
    }
    # 先绘制黑色衬底
    for ftype, lines in fault_data.items():
        if not lines:
            continue
        _, w, _ = style_map.get(ftype, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, None))
        plot_lines_on_ax(ax, lines, color=(0, 0, 0, 0.5), linewidth=w + 1.5,
                         linestyle='-', zorder=4)

    # 再绘制彩色主线
    for ftype, lines in fault_data.items():
        if not lines:
            continue
        c, w, lbl = style_map.get(ftype, (FAULT_DEFAULT_COLOR, FAULT_DEFAULT_WIDTH, None))
        plot_lines_on_ax(ax, lines, color=c, linewidth=w,
                         linestyle='-', zorder=5, label=lbl)
        print(f"  绘制 {ftype}: {len(lines)} 条")


def plot_earthquake_points(ax, filtered_quakes):
    """
    绘制历史地震圆点（矢量散点）

    参数:
        ax: matplotlib Axes对象
        filtered_quakes: 地震记录列表
    返回:
        int: 绘制数量
    """
    sorted_q = sorted(filtered_quakes, key=lambda e: e["magnitude"])
    count = 0
    # 按等级分组绘制，提高效率
    groups = {1: ([], [], []), 2: ([], [], []), 3: ([], [], []), 4: ([], [], [])}
    for eq in sorted_q:
        lv = get_earthquake_level(eq["magnitude"])
        if lv == 0:
            continue
        if lv in groups:
            groups[lv][0].append(eq["lon"])
            groups[lv][1].append(eq["lat"])
            groups[lv][2].append(eq["magnitude"])
            count += 1

    level_labels = {1: "4.7~5.9级", 2: "6.0~6.9级", 3: "7.0~7.9级", 4: "8.0级以上"}

    for lv in [1, 2, 3, 4]:
        lons, lats, _ = groups[lv]
        if not lons:
            continue
        color = get_level_color(lv)
        size = get_level_size(lv)
        ax.scatter(lons, lats, s=size, c=[color], edgecolors='black',
                   linewidths=1.5, zorder=7, label=level_labels[lv],
                   marker='o', alpha=1.0)

    print(f"  绘制地震点: {count}")
    return count


def plot_epicenter_star(ax, center_lon, center_lat):
    """
    绘制震中五角星（矢量）

    参数:
        ax: matplotlib Axes对象
        center_lon, center_lat: 震中经纬度
    """
    ax.scatter([center_lon], [center_lat], s=EPICENTER_STAR_SIZE,
               c=[EPICENTER_STAR_COLOR], edgecolors='black', linewidths=1.5,
               zorder=10, marker='*', label='震中位置')
    print(f"  震中: ({center_lon}, {center_lat})")


def draw_north_arrow_on_ax(ax, geo_extent):
    """
    在axes上绘制指北针（矢量，带灰色透明背景）

    参数:
        ax: matplotlib Axes对象
        geo_extent: 地理范围
    """
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]

    # 指北针位于右上角
    cx = geo_extent["max_lon"] - lon_range * 0.04
    cy = geo_extent["max_lat"] - lat_range * 0.08
    arrow_h = lat_range * 0.08
    arrow_w = lon_range * 0.015

    # 灰色透明背景圆
    bg_radius_x = lon_range * 0.035
    bg_radius_y = lat_range * 0.07
    bg = mpatches.Ellipse((cx, cy), width=bg_radius_x * 2, height=bg_radius_y * 2,
                           facecolor=(0.86, 0.86, 0.86, 0.5),
                           edgecolor=(0.6, 0.6, 0.6, 0.7),
                           linewidth=0.5, zorder=11)
    ax.add_patch(bg)

    # 左半（黑色填充）
    left_tri = Polygon(
        [(cx, cy + arrow_h * 0.45),
         (cx - arrow_w, cy - arrow_h * 0.35),
         (cx, cy - arrow_h * 0.05)],
        closed=True, facecolor='black', edgecolor='black', linewidth=0.5, zorder=12)
    ax.add_patch(left_tri)

    # 右半（白色填充）
    right_tri = Polygon(
        [(cx, cy + arrow_h * 0.45),
         (cx + arrow_w, cy - arrow_h * 0.35),
         (cx, cy - arrow_h * 0.05)],
        closed=True, facecolor='white', edgecolor='black', linewidth=0.5, zorder=12)
    ax.add_patch(right_tri)

    # N字母
    ax.text(cx, cy + arrow_h * 0.55, 'N', fontsize=10, fontweight='bold',
            ha='center', va='bottom', zorder=13, fontproperties=FONT_PROP_TITLE)


def draw_scale_bar_on_ax(ax, geo_extent, center_lat, scale_denom):
    """
    在axes上绘制比例尺（矢量，确保右侧完��）

    参数:
        ax: matplotlib Axes对象
        geo_extent: 地理范围
        center_lat: 中心纬度
        scale_denom: 比例尺分母
    """
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]

    # 比例尺位于右下角
    bar_y = geo_extent["min_lat"] + lat_range * 0.05
    bar_right = geo_extent["max_lon"] - lon_range * 0.03

    # 计算比例尺长度(km)和对应经度差
    km_per_deg = 111.32 * math.cos(math.radians(center_lat))
    target_km = lon_range * km_per_deg * 0.15
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    bar_km = nice[0]
    for nd in nice:
        if nd <= target_km * 1.2:
            bar_km = nd
        else:
            break

    bar_deg = bar_km / km_per_deg
    bar_left = bar_right - bar_deg

    # 白色背景框
    pad_x = lon_range * 0.01
    pad_y = lat_range * 0.015
    bg_rect = Rectangle(
        (bar_left - pad_x * 2, bar_y - pad_y * 2.5),
        bar_deg + pad_x * 5, pad_y * 6,
        facecolor=(1, 1, 1, 0.85), edgecolor=(0, 0, 0, 0.7),
        linewidth=0.5, zorder=11)
    ax.add_patch(bg_rect)

    # 黑白交替刻度条（4段）
    bar_height = lat_range * 0.008
    seg_deg = bar_deg / 4
    for i in range(4):
        c = 'black' if i % 2 == 0 else 'white'
        rect = Rectangle(
            (bar_left + i * seg_deg, bar_y),
            seg_deg, bar_height,
            facecolor=c, edgecolor='black', linewidth=0.5, zorder=12)
        ax.add_patch(rect)

    # 起点"0"
    ax.text(bar_left, bar_y - pad_y * 0.5, '0', fontsize=7,
            ha='center', va='top', zorder=13, fontproperties=FONT_PROP_NORMAL)

    # 终点距离
    ax.text(bar_left + bar_deg, bar_y - pad_y * 0.5, f'{bar_km} km', fontsize=7,
            ha='center', va='top', zorder=13, fontproperties=FONT_PROP_NORMAL)

    # 比例尺分母
    ax.text(bar_left + bar_deg / 2, bar_y + bar_height + pad_y * 0.5,
            f'1:{scale_denom:,}', fontsize=7,
            ha='center', va='bottom', zorder=13, fontproperties=FONT_PROP_NORMAL)


def build_legend(ax, has_faults=True):
    """
    构建图例（矢量，左下角，每项前有留白）

    参数:
        ax: matplotlib Axes对象
        has_faults: 是否含断裂图例
    """
    handles = []
    labels = []

    # 震中位置
    h_epi = mlines.Line2D([], [], color=EPICENTER_STAR_COLOR[:3], marker='*',
                           markersize=14, linestyle='None', markeredgecolor='black',
                           markeredgewidth=0.8)
    handles.append(h_epi)
    labels.append('震中位置')

    # 省界
    h_prov = mlines.Line2D([], [], color=PROVINCE_BORDER_COLOR[:3],
                            linewidth=PROVINCE_BORDER_WIDTH, linestyle='-')
    handles.append(h_prov)
    labels.append('省界')

    # 市界
    h_city = mlines.Line2D([], [], color=CITY_BORDER_COLOR[:3],
                            linewidth=CITY_BORDER_WIDTH, linestyle='--')
    handles.append(h_city)
    labels.append('市界')

    # 县界
    h_county = mlines.Line2D([], [], color=COUNTY_BORDER_COLOR[:3],
                              linewidth=COUNTY_BORDER_WIDTH, linestyle=':')
    handles.append(h_county)
    labels.append('县界')

    # 断裂线
    if has_faults:
        h_hol = mlines.Line2D([], [], color=FAULT_HOLOCENE_COLOR[:3],
                                linewidth=FAULT_HOLOCENE_WIDTH, linestyle='-')
        handles.append(h_hol)
        labels.append('全新世断层')

        h_late = mlines.Line2D([], [], color=FAULT_LATE_PLEISTOCENE_COLOR[:3],
                                 linewidth=FAULT_LATE_PLEISTOCENE_WIDTH, linestyle='-')
        handles.append(h_late)
        labels.append('晚更新世断层')

        h_early = mlines.Line2D([], [], color=FAULT_EARLY_PLEISTOCENE_COLOR[:3],
                                  linewidth=FAULT_EARLY_PLEISTOCENE_WIDTH, linestyle='-')
        handles.append(h_early)
        labels.append('早中更新世断层')

    # 地震圆点（从大到小）
    for lbl, color, sz in [
        ("8.0级以上", COLOR_LEVEL_4, 10),
        ("7.0~7.9级", COLOR_LEVEL_3, 9),
        ("6.0~6.9级", COLOR_LEVEL_2, 8),
        ("4.7~5.9级", COLOR_LEVEL_1, 7),
    ]:
        h = mlines.Line2D([], [], color=color[:3], marker='o', markersize=sz,
                           linestyle='None', markeredgecolor='black', markeredgewidth=0.8)
        handles.append(h)
        labels.append(lbl)

    legend = ax.legend(
        handles, labels,
        loc='lower left',
        fontsize=8,
        frameon=True,
        framealpha=0.92,
        edgecolor='black',
        fancybox=False,
        borderpad=1.0,        # 图例内边距
        handletextpad=1.2,    # 图标与文字间距（留白）
        handlelength=2.5,     # 图标长度
        labelspacing=0.8,     # 行间距
        prop=FONT_PROP_NORMAL,
        title='图  例',
        title_fontproperties=FONT_PROP_TITLE,
    )
    legend.get_title().set_fontsize(11)
    legend.set_zorder(15)


# ============================================================
# 【统计函数】
# ============================================================

def generate_statistics(filtered_quakes, radius_km):
    """生成统计信息"""
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
# 【主函数 - 输出AI/PDF/EPS/SVG/PNG】
# ============================================================

def generate_earthquake_map(center_lon, center_lat, magnitude, csv_path,
                            output_path, csv_encoding="gbk"):
    """
    生成历史地震分布图，输出为Adobe Illustrator兼容格式。

    支持的输出格式（由output_path后缀决定）：
        .ai  → 输出EPS格式（AI原生打开编辑）
        .pdf → PDF矢量格式
        .eps → EPS矢量格式
        .svg → SVG矢量格式
        .png → 位图格式（兼容保留）

    所有矢量要素（边界线、断裂线、地震圆点、五角星、图例、
    比例尺、指北针、标题、刻度）均为矢量输出，底图瓦片为嵌入位图。

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        magnitude (float): 震级
        csv_path (str): CSV路径
        output_path (str): 输出路径（后缀决定格式：.ai/.pdf/.eps/.svg/.png）
        csv_encoding (str): CSV编码
    返回:
        str: 统计信息
    """
    print("=" * 65)
    print("  历 史 地 震 分 布 图 生 成 工 具 (AI矢量版)")
    print("=" * 65)
    print(f"  震中: {center_lon}°E, {center_lat}°N, M{magnitude}")

    radius_km, span_km, scale_denom = get_range_params(magnitude)
    half_span_km = span_km / 2.0
    print(f"  范围: {radius_km}km, 比例尺: 1:{scale_denom:,}\n")

    # 计算地理范围
    delta_lon = km_to_degree_lon(half_span_km, center_lat)
    delta_lat = km_to_degree_lat(half_span_km)
    geo_extent = {
        "min_lon": center_lon - delta_lon, "max_lon": center_lon + delta_lon,
        "min_lat": center_lat - delta_lat, "max_lat": center_lat + delta_lat,
    }

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

    # [3/7] 底图
    print("[3/7] 获取天地图矢量底图+矢量注记...")
    basemap_array, geo_extent = fetch_basemap_array(
        center_lon, center_lat, half_span_km, geo_extent)
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

    # [6/7] 使用matplotlib绑制所有图层
    print("[6/7] 使用matplotlib绘制矢量要素...")

    fig, ax = plt.subplots(1, 1, figsize=(FIG_WIDTH_INCH, FIG_HEIGHT_INCH))
    fig.subplots_adjust(left=0.06, right=0.94, top=0.94, bottom=0.06)

    # --- 底图（位图，作为背景嵌入） ---
    ax.imshow(basemap_array,
              extent=[geo_extent["min_lon"], geo_extent["max_lon"],
                      geo_extent["min_lat"], geo_extent["max_lat"]],
              aspect='auto', zorder=0, interpolation='lanczos')

    # --- 行政边界（矢量线条） ---
    if county_lines:
        print("  绘制县界...")
        plot_lines_on_ax(ax, county_lines, COUNTY_BORDER_COLOR, COUNTY_BORDER_WIDTH,
                         linestyle=':', zorder=2)

    if city_lines:
        print("  绘制市界...")
        plot_lines_on_ax(ax, city_lines, CITY_BORDER_COLOR, CITY_BORDER_WIDTH,
                         linestyle='--', zorder=3)

    if province_lines:
        print("  绘制省界...")
        plot_lines_on_ax(ax, province_lines, PROVINCE_BORDER_COLOR, PROVINCE_BORDER_WIDTH,
                         linestyle='-', zorder=3)

    # --- 断裂线（矢量，带衬底） ---
    if has_faults:
        plot_fault_lines(ax, fault_data)

    # --- 历史地震圆点（矢量散点） ---
    plot_earthquake_points(ax, filtered)

    # --- 震中五角星（矢量） ---
    plot_epicenter_star(ax, center_lon, center_lat)

    # --- 设置坐标轴范围和刻度 ---
    ax.set_xlim(geo_extent["min_lon"], geo_extent["max_lon"])
    ax.set_ylim(geo_extent["min_lat"], geo_extent["max_lat"])

    # 经纬度刻度（约5个）
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]
    lon_step = _choose_tick_step_mpl(lon_range)
    lat_step = _choose_tick_step_mpl(lat_range)

    lon_ticks = []
    v = math.ceil(geo_extent["min_lon"] / lon_step) * lon_step
    while v <= geo_extent["max_lon"]:
        lon_ticks.append(v)
        v += lon_step
    lat_ticks = []
    v = math.ceil(geo_extent["min_lat"] / lat_step) * lat_step
    while v <= geo_extent["max_lat"]:
        lat_ticks.append(v)
        v += lat_step

    ax.set_xticks(lon_ticks)
    ax.set_yticks(lat_ticks)
    ax.set_xticklabels([format_degree(v, is_lon=True) for v in lon_ticks],
                        fontsize=8, fontproperties=FONT_PROP_NORMAL)
    ax.set_yticklabels([format_degree(v, is_lon=False) for v in lat_ticks],
                        fontsize=8, fontproperties=FONT_PROP_NORMAL)

    ax.tick_params(axis='both', which='both', direction='out', length=5, width=1,
                   top=True, right=True, labeltop=True, labelright=True)
    # 顶部和右侧也显示刻度标签
    ax.xaxis.set_tick_params(labeltop=True)
    ax.yaxis.set_tick_params(labelright=True)

    # 设置第二组刻度标签（顶部和右侧）
    secax_x = ax.secondary_xaxis('top')
    secax_x.set_xticks(lon_ticks)
    secax_x.set_xticklabels([format_degree(v, is_lon=True) for v in lon_ticks],
                             fontsize=8, fontproperties=FONT_PROP_NORMAL)
    secax_x.tick_params(direction='out', length=5, width=1)

    secax_y = ax.secondary_yaxis('right')
    secax_y.set_yticks(lat_ticks)
    secax_y.set_yticklabels([format_degree(v, is_lon=False) for v in lat_ticks],
                             fontsize=8, fontproperties=FONT_PROP_NORMAL)
    secax_y.tick_params(direction='out', length=5, width=1)

    # 边框
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)
        spine.set_color('black')

    # --- 装饰要素（矢量） ---
    # 指北针
    draw_north_arrow_on_ax(ax, geo_extent)

    # 比例尺
    draw_scale_bar_on_ax(ax, geo_extent, center_lat, scale_denom)

    # 图例
    build_legend(ax, has_faults=has_faults)

    # 标题
    title_text = f"历史地震分布图（震中{int(radius_km)}km范围）"
    ax.set_title(title_text, fontsize=16, fontweight='bold',
                 fontproperties=FONT_PROP_TITLE, pad=15,
                 bbox=dict(boxstyle='round,pad=0.4', facecolor='white',
                           edgecolor='black', alpha=0.85))

    print()

    # [7/7] 保存
    print("[7/7] 保存输出文件...")

    # 判断输出格式
    _, ext = os.path.splitext(output_path)
    ext = ext.lower()

    save_kwargs = {'dpi': OUTPUT_DPI, 'bbox_inches': 'tight', 'pad_inches': 0.3}

    if ext == '.ai':
        # AI格式 → 实际保存为EPS（Adobe Illustrator原生支持打开EPS）
        # 确保输出路径后缀为.ai
        save_kwargs['format'] = 'eps'
        fig.savefig(output_path, **save_kwargs)
        print(f"  输出格式: Adobe Illustrator (EPS兼容)")
        print(f"  说明: 文件以EPS格式保存为.ai后缀，AI可直接打开编辑")
    elif ext == '.pdf':
        save_kwargs['format'] = 'pdf'
        fig.savefig(output_path, **save_kwargs)
        print(f"  输出格式: PDF矢量格式（AI可直接打开编辑）")
    elif ext == '.eps':
        save_kwargs['format'] = 'eps'
        fig.savefig(output_path, **save_kwargs)
        print(f"  输出格式: EPS矢量格式（AI可直接打开编辑）")
    elif ext == '.svg':
        save_kwargs['format'] = 'svg'
        fig.savefig(output_path, **save_kwargs)
        print(f"  输出格式: SVG矢量格式（AI可直接打开编辑）")
    elif ext == '.png':
        fig.savefig(output_path, **save_kwargs)
        print(f"  输出格式: PNG位图格式")
    else:
        # 默认保存为PDF（AI最佳兼容）
        output_path_pdf = output_path + '.pdf'
        save_kwargs['format'] = 'pdf'
        fig.savefig(output_path_pdf, **save_kwargs)
        output_path = output_path_pdf
        print(f"  输出格式: PDF矢量格式（默认，AI可直接打开编辑）")

    plt.close(fig)

    if os.path.exists(output_path):
        fsize = os.path.getsize(output_path) / 1024
        print(f"  已保存: {output_path}")
        print(f"  大小: {fsize:.1f} KB")
    else:
        print(f"  *** 保存失败 ***")

    print()
    stat_text = generate_statistics(filtered, radius_km)
    print("=" * 65)
    print("【统计信息】")
    print(stat_text)
    print("=" * 65)
    return stat_text


def _choose_tick_step_mpl(range_deg, target_min=4, target_max=6):
    """选择合适的刻度间隔"""
    candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
    for step in candidates:
        n_ticks = range_deg / step
        if target_min <= n_ticks <= target_max:
            return step
    best_step = range_deg / 5.0
    best_diff = float('inf')
    for step in candidates:
        diff = abs(range_deg / step - 5)
        if diff < best_diff:
            best_diff = diff
            best_step = step
    return best_step


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

    # 输出路径 —— 修改后缀即可切换格式：
    #   .ai  → Adobe Illustrator格式（EPS兼容，AI直接打开编辑）
    #   .pdf → PDF矢量格式（AI直接打开编辑，推荐）
    #   .eps → EPS矢量格式
    #   .svg → SVG矢量格式
    #   .png → 传统位图格式
    OUTPUT_PATH = r"../../data/geology/output_earthquake_map.ai"

    stat_result = generate_earthquake_map(
        center_lon=INPUT_LON,
        center_lat=INPUT_LAT,
        magnitude=INPUT_MAGNITUDE,
        csv_path=INPUT_CSV_PATH,
        output_path=OUTPUT_PATH,
        csv_encoding="gbk"
    )