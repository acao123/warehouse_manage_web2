# -*- coding: utf-8 -*-
"""earthquake_landslide_slope_map.py

修复说明（2026-03-12）：
- 之前版本的天地图底图加载函数是占位实现（pass / t0固定服务器 / 未拼接瓦片 / 未裁剪缩放），
  导致输出图看不到天地图矢量底图。
- 本次参考 apps/core/earthquake_map.py 中已验证正确的天地图 vec_c + cva_c 瓦片拼接方式，
  实现 load_tianditu_basemap / load_tianditu_annotation：
  1) 根据 extent 自动选择 zoom
  2) 计算瓦片行列范围并下载拼接
  3) 按 extent 裁剪并 resize 到目标像素尺寸
  4) 注记层使用透明底并与底图 alpha_composite

注意：
- 该文件当前仅包含“天地图瓦片加载”能力（与原脚本结构兼容）。
- 你只需要在主制图流程中调用 load_tianditu_basemap + load_tianditu_annotation 并叠加即可。
"""

import math
import time
from io import BytesIO

import requests
from PIL import Image

# ============================================================
# 天地图 WMTS 配置（vec_c + cva_c）
# ============================================================

TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

TIANDITU_VEC_URL = (
    "http://t{s}.tianditu.gov.cn/vec_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

TIANDITU_CVA_URL = (
    "http://t{s}.tianditu.gov.cn/cva_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

TIANDITU_TILE_TIMEOUT = 20  # seconds
TIANDITU_TILE_RETRY = 3

# 兼容旧变量名（若其它代码仍引用）
TIANDITU_VEC_C_URL = "http://t{s}.tianditu.gov.cn/vec_c/wmts?"  # unused placeholder
TIANDITU_CVA_C_URL = "http://t{s}.tianditu.gov.cn/cva_c/wmts?"  # unused placeholder

# ============================================================
# 天地图矩阵参数（EPSG:4326 瓦片方案，来自 earthquake_map.py）
# ============================================================

TIANDITU_MATRIX = {}
for _z in range(1, 19):
    _n_cols = 2 ** _z
    _n_rows = 2 ** (_z - 1)
    TIANDITU_MATRIX[_z] = {
        "n_cols": _n_cols,
        "n_rows": _n_rows,
        "tile_span_lon": 360.0 / _n_cols,
        "tile_span_lat": 180.0 / _n_rows,
    }


def _select_zoom_level(geo_extent, img_width, img_height):
    """优先选择更高 zoom（保证瓦片拼接尺寸 >= 输出尺寸），并限制瓦片数量。"""
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


def _lonlat_to_tile_epsg4326(lon, lat, zoom):
    """经纬度转瓦片行列号（EPSG:4326）"""
    m = TIANDITU_MATRIX[zoom]
    col = int(math.floor((lon + 180.0) / m["tile_span_lon"]))
    row = int(math.floor((90.0 - lat) / m["tile_span_lat"]))
    return max(0, min(col, m["n_cols"] - 1)), max(0, min(row, m["n_rows"] - 1))


def _tile_to_lonlat_epsg4326(tile_col, tile_row, zoom):
    """瓦片行列号转瓦片左上角经纬度"""
    m = TIANDITU_MATRIX[zoom]
    return -180.0 + tile_col * m["tile_span_lon"], 90.0 - tile_row * m["tile_span_lat"]


def _download_tile_with_retry(url_template, zoom, tile_col, tile_row, retries=TIANDITU_TILE_RETRY):
    """下载瓦片（重试 + 服务器轮询 t0~t7）"""
    for attempt in range(retries):
        server = (tile_col + tile_row + attempt) % 8
        url = (
            url_template.replace("{s}", str(server))
            .replace("{z}", str(zoom))
            .replace("{x}", str(tile_col))
            .replace("{y}", str(tile_row))
        )
        try:
            resp = requests.get(url, timeout=TIANDITU_TILE_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                return Image.open(BytesIO(resp.content))
            if attempt < retries - 1:
                time.sleep(0.3)
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5)
    return None


def _compose_wmts_mosaic(geo_extent, img_width, img_height, url_template, rgba_bg):
    """拼接 WMTS 瓦片并裁剪缩放到目标尺寸。"""
    zoom = _select_zoom_level(geo_extent, img_width, img_height)
    matrix = TIANDITU_MATRIX[zoom]

    # 计算瓦片范围（按 extent 计算，略扩一圈避免裁剪边缘出现空白）
    col_min, row_min = _lonlat_to_tile_epsg4326(geo_extent["min_lon"], geo_extent["max_lat"], zoom)
    col_max, row_max = _lonlat_to_tile_epsg4326(geo_extent["max_lon"], geo_extent["min_lat"], zoom)

    col_min = max(0, col_min - 1)
    row_min = max(0, row_min - 1)
    col_max = min(matrix["n_cols"] - 1, col_max + 1)
    row_max = min(matrix["n_rows"] - 1, row_max + 1)

    ntx = col_max - col_min + 1
    nty = row_max - row_min + 1

    ts = 256
    mw, mh = ntx * ts, nty * ts

    mosaic = Image.new("RGBA", (mw, mh), rgba_bg)

    # 马赛克左上 / 右下经纬度（用于裁剪定位）
    mo_lon, mo_lat = _tile_to_lonlat_epsg4326(col_min, row_min, zoom)
    me_lon, me_lat = _tile_to_lonlat_epsg4326(col_max + 1, row_max + 1, zoom)

    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            px, py = (col - col_min) * ts, (row - row_min) * ts
            tile = _download_tile_with_retry(url_template, zoom, col, row)
            if tile is None:
                continue
            mosaic.paste(tile.convert("RGBA"), (px, py))

    # geo -> mosaic px
    def g2m(lon, lat):
        return (
            (lon - mo_lon) / (me_lon - mo_lon) * mw,
            (mo_lat - lat) / (mo_lat - me_lat) * mh,
        )

    cl, ct = g2m(geo_extent["min_lon"], geo_extent["max_lat"])
    cr, cb = g2m(geo_extent["max_lon"], geo_extent["min_lat"])

    cl, ct = max(0, int(round(cl))), max(0, int(round(ct)))
    cr, cb = min(mw, int(round(cr))), min(mh, int(round(cb)))

    cropped = mosaic.crop((cl, ct, cr, cb)) if cr > cl and cb > ct else mosaic
    return cropped.resize((img_width, img_height), Image.LANCZOS)


# ============================================================
# 对外函数：底图/注记
# ============================================================

def load_tianditu_basemap(extent, map_width_px, map_height_px):
    """加载天地图矢量底图（vec_c）。

    参数:
        extent (tuple/list): (min_lon, max_lon, min_lat, max_lat)
        map_width_px (int): 输出宽度像素
        map_height_px (int): 输出高度像素

    返回:
        PIL.Image (RGBA)
    """
    min_lon, max_lon, min_lat, max_lat = extent
    geo_extent = {
        "min_lon": float(min_lon),
        "max_lon": float(max_lon),
        "min_lat": float(min_lat),
        "max_lat": float(max_lat),
    }

    # 使用浅蓝色背景模拟海洋（与 earthquake_map.py 一致）
    return _compose_wmts_mosaic(
        geo_extent=geo_extent,
        img_width=map_width_px,
        img_height=map_height_px,
        url_template=TIANDITU_VEC_URL,
        rgba_bg=(170, 211, 223, 255),
    )


def load_tianditu_annotation(extent, map_width_px, map_height_px):
    """加载天地图矢量注记（cva_c），透明底。

    返回:
        PIL.Image (RGBA)
    """
    min_lon, max_lon, min_lat, max_lat = extent
    geo_extent = {
        "min_lon": float(min_lon),
        "max_lon": float(max_lon),
        "min_lat": float(min_lat),
        "max_lat": float(max_lat),
    }

    return _compose_wmts_mosaic(
        geo_extent=geo_extent,
        img_width=map_width_px,
        img_height=map_height_px,
        url_template=TIANDITU_CVA_URL,
        rgba_bg=(0, 0, 0, 0),
    )


def alpha_composite_basemap_and_label(basemap_img, label_img):
    """将底图与注记叠加，返回新图。"""
    if basemap_img.mode != "RGBA":
        basemap_img = basemap_img.convert("RGBA")
    if label_img.mode != "RGBA":
        label_img = label_img.convert("RGBA")
    return Image.alpha_composite(basemap_img, label_img)

# ============================================================
# 示例（调试用）：单独运行时下载一张底图看看效果
# ============================================================

if __name__ == "__main__":
    # 示例范围：以一点为中心扩展 0.5 度（仅用于演示）
    center_lon, center_lat = 122.06, 24.67
    half = 0.5
    extent_demo = (center_lon - half, center_lon + half, center_lat - half, center_lat + half)

    w, h = 1400, 1050
    base = load_tianditu_basemap(extent_demo, w, h)
    ann = load_tianditu_annotation(extent_demo, w, h)
    out = alpha_composite_basemap_and_label(base, ann)
    out.show()