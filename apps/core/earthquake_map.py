# -*- coding: utf-8 -*-
"""
历史地震分布图生成脚本（基于Python + Pillow + requests）
功能：根据用户输入的震中经纬度、震级以及历史地震CSV文件，
      绘制震中附近一定范围内的历史地震分布图，并输出统计信息。

使用方法：
    直接运行脚本，修改底部的输入参数即可。
    依赖安装：pip install Pillow requests

作者：acao123
日期：2026-02-26
"""

import os
import sys
import csv
import math
import time
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ============================================================
# 【配置常量区域】- 可自由修改
# ============================================================

# 天地图WMTS服务地址（影像底图）- 使用EPSG:4326 (c矩阵集)
# tk为天地图开发者密钥，可替换为自己的密钥
TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"

# 天地图影像底图URL模板（支持t0-t7多服务器轮询）
TIANDITU_IMG_URL = (
    "http://t{s}.tianditu.gov.cn/img_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=img&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 天地图标注服务地址（地名标注）
TIANDITU_CIA_URL = (
    "http://t{s}.tianditu.gov.cn/cia_c/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&LAYER=cia&STYLE=default&TILEMATRIXSET=c"
    "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
    "&tk=" + TIANDITU_TK
)

# 是否叠加地名标注图层
ENABLE_LABEL_OVERLAY = True

# 输出图片尺寸（像素）
OUTPUT_WIDTH = 1600
OUTPUT_HEIGHT = 1200

# 输出图片DPI
OUTPUT_DPI = 150

# 输出图片格式（png / jpg / tiff，均可插入Word文档）
OUTPUT_FORMAT = "png"

# 瓦片下载超时时间（秒）
TILE_TIMEOUT = 20
# 瓦片下载失败重试次数
TILE_RETRY = 3

# 地震圆点颜色配置（RGBA格式）
# 4.7~5.9级 - 纯绿色（高饱和度，卫星底图上更醒目）
COLOR_LEVEL_1 = (0, 255, 0, 240)
# 6.0~6.9级 - 黄色
COLOR_LEVEL_2 = (255, 255, 0, 220)
# 7.0~7.9级 - 橙色
COLOR_LEVEL_3 = (255, 165, 0, 230)
# 8.0级以上 - 红色
COLOR_LEVEL_4 = (255, 0, 0, 240)

# 地震圆点直径配置（像素）- 增大各级别尺寸确保可见
SIZE_LEVEL_1 = 14   # 4.7~5.9级
SIZE_LEVEL_2 = 20   # 6.0~6.9级
SIZE_LEVEL_3 = 26   # 7.0~7.9级
SIZE_LEVEL_4 = 34   # 8.0级以上

# 震中五角星颜色和大小
EPICENTER_STAR_COLOR = (255, 0, 0, 255)  # 红色
EPICENTER_STAR_SIZE = 24                  # 五角星外接圆半径（像素）

# 字体设置（根据操作系统修改路径）
# Windows系统
FONT_PATH_TITLE = "C:/Windows/Fonts/simhei.ttf"     # 黑体
FONT_PATH_NORMAL = "C:/Windows/Fonts/simsun.ttc"     # 宋体
# Linux系统可使用以下路径（取消注释）：
# FONT_PATH_TITLE = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
# FONT_PATH_NORMAL = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
# macOS系统可使用：
# FONT_PATH_TITLE = "/System/Library/Fonts/PingFang.ttc"
# FONT_PATH_NORMAL = "/System/Library/Fonts/PingFang.ttc"


# ============================================================
# 【天地图EPSG:4326瓦片矩阵参数】
# 天地图c矩阵集基于EPSG:4326 (WGS84经纬度)
# 原点在(-180, 90)，即左上角
# zoom=1时：2列x1行，每瓦片覆盖180°x180°
# zoom=n时：2^n列 x 2^(n-1)行，每瓦片覆盖 360/2^n ° x 180/2^(n-1) °
# 瓦片大小：256x256像素
# ============================================================

# 天地图EPSG:4326矩阵集预定义参数
TIANDITU_MATRIX = {}
for _z in range(1, 19):
    _n_cols = 2 ** _z
    _n_rows = 2 ** (_z - 1)
    _tile_span_lon = 360.0 / _n_cols   # 每瓦片经度跨度
    _tile_span_lat = 180.0 / _n_rows   # 每瓦片纬度跨度
    TIANDITU_MATRIX[_z] = {
        "n_cols": _n_cols,
        "n_rows": _n_rows,
        "tile_span_lon": _tile_span_lon,
        "tile_span_lat": _tile_span_lat,
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
    根据震级返回地震等级分档（1-4）

    参数:
        mag (float): 震级

    返回:
        int: 等级分档，0表示不在统计范围内
             1: 4.7~5.9级
             2: 6.0~6.9级
             3: 7.0~7.9级
             4: 8.0级以上
    """
    if 4.7 <= mag <= 5.9:
        return 1
    elif 6.0 <= mag <= 6.9:
        return 2
    elif 7.0 <= mag <= 7.9:
        return 3
    elif mag >= 8.0:
        return 4
    else:
        return 0


def get_level_color(level):
    """
    根据地震等级分档返回对应颜色

    参数:
        level (int): 等级分档(1-4)

    返回:
        tuple: RGBA颜色元组
    """
    color_map = {
        1: COLOR_LEVEL_1,
        2: COLOR_LEVEL_2,
        3: COLOR_LEVEL_3,
        4: COLOR_LEVEL_4,
    }
    return color_map.get(level, (128, 128, 128, 150))


def get_level_size(level):
    """
    根据地震等级分档返回对应圆点直径

    参数:
        level (int): 等级分档(1-4)

    返回:
        int: 圆点直径（像素）
    """
    size_map = {
        1: SIZE_LEVEL_1,
        2: SIZE_LEVEL_2,
        3: SIZE_LEVEL_3,
        4: SIZE_LEVEL_4,
    }
    return size_map.get(level, 6)


def haversine_distance(lon1, lat1, lon2, lat2):
    """
    使用Haversine公式计算两点之间的球面距离

    参数:
        lon1 (float): 第一个点的经度（度）
        lat1 (float): 第一个点的纬度（度）
        lon2 (float): 第二个点的经度（度）
        lat2 (float): 第二个点的纬度（度）

    返回:
        float: 两点之间的距离（千米）
    """
    R = 6371.0  # 地球平均半径，单位：千米
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def km_to_degree_lon(km, latitude):
    """
    将千米转换为经度差（度），考虑纬度影响

    参数:
        km (float): 距离（千米）
        latitude (float): 当前纬度（度）

    返回:
        float: 经度差（度）
    """
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """
    将千米转换为纬度差（度）

    参数:
        km (float): 距离（千米）

    返回:
        float: 纬度差（度）
    """
    return km / 110.574


# ============================================================
# 【天地图瓦片相关函数】
# ============================================================

def select_zoom_level(geo_extent, img_width, img_height):
    """
    根据地理范围和目标图片大小选择最优的天地图缩放级别。
    选取策略：使得图片中每个像素覆盖的地理范围尽可能小（清晰），
    同时保证不需要下载过多瓦片。

    参数:
        geo_extent (dict): 地理范围 {min_lon, max_lon, min_lat, max_lat}
        img_width (int): 目标图片宽度（像素）
        img_height (int): 目标图片高度（像素）

    返回:
        int: 最优缩放级别
    """
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    lat_range = geo_extent["max_lat"] - geo_extent["min_lat"]

    best_zoom = 1
    for z in range(1, 19):
        matrix = TIANDITU_MATRIX[z]
        # 在此zoom下，覆盖目标范围需要的瓦片数
        tiles_x = math.ceil(lon_range / matrix["tile_span_lon"]) + 1
        tiles_y = math.ceil(lat_range / matrix["tile_span_lat"]) + 1
        # 拼接后的像素总数
        mosaic_px_w = tiles_x * 256
        mosaic_px_h = tiles_y * 256
        # 如果拼接像素不够目标分辨率，则zoom太低
        # 我们需要拼接后的分辨率至少等于目标分辨率
        if mosaic_px_w >= img_width and mosaic_px_h >= img_height:
            best_zoom = z
            # 不要超过合理的瓦片数(最多约400块)
            if tiles_x * tiles_y > 400:
                best_zoom = z - 1 if z > 1 else z
                break
            # 继续往上尝试更高分辨率
        else:
            best_zoom = z

    # 限制zoom范围
    best_zoom = max(3, min(best_zoom, 16))
    return best_zoom


def lonlat_to_tile_epsg4326(lon, lat, zoom):
    """
    将经纬度转换为天地图EPSG:4326(c矩阵集)的瓦片列号和行号。

    天地图c矩阵集坐标原点为 (-180°, 90°)（左上角）
    zoom=n 时：
        列数 = 2^n, 行数 = 2^(n-1)
        每瓦片经度跨度 = 360 / 2^n
        每瓦片纬度跨度 = 180 / 2^(n-1)
    列号 = floor( (lon - (-180)) / tile_span_lon )
    行号 = floor( (90 - lat) / tile_span_lat )

    参数:
        lon (float): 经度（度）
        lat (float): 纬度（度）
        zoom (int): 缩放级别

    返回:
        tuple: (tile_col, tile_row) 瓦片列号和行号
    """
    matrix = TIANDITU_MATRIX[zoom]
    tile_col = int(math.floor((lon + 180.0) / matrix["tile_span_lon"]))
    tile_row = int(math.floor((90.0 - lat) / matrix["tile_span_lat"]))

    # 边界裁剪
    tile_col = max(0, min(tile_col, matrix["n_cols"] - 1))
    tile_row = max(0, min(tile_row, matrix["n_rows"] - 1))

    return tile_col, tile_row


def tile_to_lonlat_epsg4326(tile_col, tile_row, zoom):
    """
    将瓦片列号和行号转换为该瓦片左上角的经纬度坐标。

    参数:
        tile_col (int): 瓦片列号
        tile_row (int): 瓦片行号
        zoom (int): 缩放级别

    返回:
        tuple: (lon, lat) 瓦片左上角的经度和纬度
    """
    matrix = TIANDITU_MATRIX[zoom]
    lon = -180.0 + tile_col * matrix["tile_span_lon"]
    lat = 90.0 - tile_row * matrix["tile_span_lat"]
    return lon, lat


def download_tile_with_retry(url_template, zoom, tile_col, tile_row, retries=TILE_RETRY):
    """
    下载单个瓦片图片，支持多服务器轮询和重试。

    参数:
        url_template (str): WMTS服务URL模板(含{s},{z},{x},{y}占位符)
        zoom (int): 缩放级别
        tile_col (int): 瓦片列号
        tile_row (int): 瓦片行号
        retries (int): 重试次数

    返回:
        PIL.Image: 瓦片图片对象，失败返回None
    """
    for attempt in range(retries):
        # 轮询t0~t7服务器
        server = (tile_col + tile_row + attempt) % 8
        url = (url_template
               .replace("{s}", str(server))
               .replace("{z}", str(zoom))
               .replace("{x}", str(tile_col))
               .replace("{y}", str(tile_row)))
        try:
            response = requests.get(url, timeout=TILE_TIMEOUT)
            if response.status_code == 200 and len(response.content) > 0:
                img = Image.open(BytesIO(response.content))
                return img
            else:
                if attempt < retries - 1:
                    time.sleep(0.3)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5)
            else:
                print(f"    瓦片下载失败: z={zoom} col={tile_col} row={tile_row} 错误: {e}")
    return None


def fetch_basemap(center_lon, center_lat, half_span_km, scale_denom, img_width, img_height):
    """
    获取天地图底图：下载瓦片、拼接、裁剪到指定地理范围并缩放到目标尺寸。

    参数:
        center_lon (float): 中心经度（度）
        center_lat (float): 中心纬度（度）
        half_span_km (float): 地图半边长（千米）
        scale_denom (int): 比例尺分母
        img_width (int): 输出图片宽度（像素）
        img_height (int): 输出图片高度（像素）

    返回:
        tuple: (PIL.Image底图, geo_extent字典)
               geo_extent包含: min_lon, max_lon, min_lat, max_lat
    """
    # 计算地理范围
    delta_lon = km_to_degree_lon(half_span_km, center_lat)
    delta_lat = km_to_degree_lat(half_span_km)

    min_lon = center_lon - delta_lon
    max_lon = center_lon + delta_lon
    min_lat = center_lat - delta_lat
    max_lat = center_lat + delta_lat

    geo_extent = {
        "min_lon": min_lon,
        "max_lon": max_lon,
        "min_lat": min_lat,
        "max_lat": max_lat,
    }

    print(f"  目标地理范围: 经度[{min_lon:.4f}, {max_lon:.4f}], 纬度[{min_lat:.4f}, {max_lat:.4f}]")

    # 选择合适的缩放级别
    zoom = select_zoom_level(geo_extent, img_width, img_height)
    print(f"  选择天地图缩放级别: zoom={zoom}")

    matrix = TIANDITU_MATRIX[zoom]
    print(f"  zoom={zoom}: 瓦片经度跨度={matrix['tile_span_lon']:.6f}°, "
          f"纬度跨度={matrix['tile_span_lat']:.6f}°, "
          f"总列数={matrix['n_cols']}, 总行数={matrix['n_rows']}")

    # 计算覆盖目标范围所需的瓦片行列号范围
    col_min, row_min = lonlat_to_tile_epsg4326(min_lon, max_lat, zoom)  # 左上角
    col_max, row_max = lonlat_to_tile_epsg4326(max_lon, min_lat, zoom)  # 右下角

    # 向外多取一圈瓦片，确保完全覆盖
    col_min = max(0, col_min - 1)
    row_min = max(0, row_min - 1)
    col_max = min(matrix["n_cols"] - 1, col_max + 1)
    row_max = min(matrix["n_rows"] - 1, row_max + 1)

    num_tiles_x = col_max - col_min + 1
    num_tiles_y = row_max - row_min + 1
    total_tiles = num_tiles_x * num_tiles_y

    print(f"  瓦片范围: col[{col_min}~{col_max}], row[{row_min}~{row_max}] => {num_tiles_x}x{num_tiles_y}={total_tiles}块")

    # 拼接瓦片为大图（mosaic）
    tile_size = 256
    mosaic_width = num_tiles_x * tile_size
    mosaic_height = num_tiles_y * tile_size
    mosaic = Image.new("RGBA", (mosaic_width, mosaic_height), (200, 200, 200, 255))

    # 拼接图左上角对应的地理坐标
    mosaic_origin_lon, mosaic_origin_lat = tile_to_lonlat_epsg4326(col_min, row_min, zoom)
    # 拼接图右下角对应的地理坐标（即最后一块瓦片右下角）
    mosaic_end_lon, mosaic_end_lat = tile_to_lonlat_epsg4326(col_max + 1, row_max + 1, zoom)

    print(f"  拼接图地理范围: 经度[{mosaic_origin_lon:.4f}, {mosaic_end_lon:.4f}], "
          f"纬度[{mosaic_end_lat:.4f}, {mosaic_origin_lat:.4f}]")

    downloaded_count = 0
    failed_count = 0

    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            px = (col - col_min) * tile_size
            py = (row - row_min) * tile_size

            # 下载影像底图瓦片
            tile_img = download_tile_with_retry(TIANDITU_IMG_URL, zoom, col, row)
            if tile_img is not None:
                tile_img = tile_img.convert("RGBA")
                mosaic.paste(tile_img, (px, py))
                downloaded_count += 1
            else:
                failed_count += 1

            # 下载并叠加地名标注瓦片
            if ENABLE_LABEL_OVERLAY:
                label_img = download_tile_with_retry(TIANDITU_CIA_URL, zoom, col, row)
                if label_img is not None:
                    label_img = label_img.convert("RGBA")
                    # 使用alpha_composite叠加标注（保留透明区域）
                    temp_layer = Image.new("RGBA", mosaic.size, (0, 0, 0, 0))
                    temp_layer.paste(label_img, (px, py))
                    mosaic = Image.alpha_composite(mosaic, temp_layer)

        # 打印下载进度
        progress = (col - col_min + 1) / num_tiles_x * 100
        print(f"    下载进度: {progress:.0f}% (列 {col - col_min + 1}/{num_tiles_x})")

    print(f"  瓦片下载完成: 成功={downloaded_count}, 失败={failed_count}, 总计={total_tiles}")

    if downloaded_count == 0:
        print("  *** 警告: 没有成功下载任何瓦片! 请检查网络连接和天地图tk密钥 ***")

    # 将目标地理范围映射到拼接图的像素坐标进行裁剪
    def geo_to_mosaic_px(lon, lat):
        """
        将经纬度转换为拼接图上的像素坐标

        参数:
            lon (float): 经度
            lat (float): 纬度

        返回:
            tuple: (px, py) 像素坐标
        """
        # 经度方向：从左到右
        frac_x = (lon - mosaic_origin_lon) / (mosaic_end_lon - mosaic_origin_lon)
        # 纬度方向：从上到下（纬度从大到小）
        frac_y = (mosaic_origin_lat - lat) / (mosaic_origin_lat - mosaic_end_lat)
        return frac_x * mosaic_width, frac_y * mosaic_height

    crop_left, crop_top = geo_to_mosaic_px(min_lon, max_lat)
    crop_right, crop_bottom = geo_to_mosaic_px(max_lon, min_lat)

    # 取整并做边界保护
    crop_left = max(0, int(round(crop_left)))
    crop_top = max(0, int(round(crop_top)))
    crop_right = min(mosaic_width, int(round(crop_right)))
    crop_bottom = min(mosaic_height, int(round(crop_bottom)))

    print(f"  裁剪像素范围: left={crop_left}, top={crop_top}, right={crop_right}, bottom={crop_bottom}")

    if crop_right <= crop_left or crop_bottom <= crop_top:
        print("  *** 警告: 裁剪范围无效，使用整个拼接图 ***")
        cropped = mosaic
    else:
        cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))

    # 缩放到目标尺寸
    basemap = cropped.resize((img_width, img_height), Image.LANCZOS)

    return basemap, geo_extent


# ============================================================
# 【CSV数据读取函数】
# ============================================================

def read_earthquake_csv(csv_path, encoding="gbk"):
    """
    读取历史地震CSV文件

    参数:
        csv_path (str): CSV文件路径
        encoding (str): 文件编码，默认gbk

    返回:
        list: 地震记录列表，每条记录为字典:
              {
                  "time_str": 发震时刻字符串,
                  "lon": 经度(float),
                  "lat": 纬度(float),
                  "depth": 深度(float),
                  "location": 参考位置(str),
                  "magnitude": 震级(float),
                  "year": 年份(int),
                  "month": 月份(int),
                  "day": 日(int)
              }
    """
    earthquakes = []
    with open(csv_path, "r", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # 跳过表头
        if header:
            print(f"  CSV表头: {[h.strip() for h in header]}")

        for row_idx, row in enumerate(reader):
            if len(row) < 6:
                continue  # 跳过不完整的行
            try:
                time_str = row[0].strip()
                lon_str = row[1].strip()
                lat_str = row[2].strip()
                depth_str = row[3].strip()
                location = row[4].strip()
                mag_str = row[5].strip()

                # 解析经纬度和震级
                lon = float(lon_str)
                lat = float(lat_str)
                mag = float(mag_str)

                # 解析深度（可能有异常值）
                try:
                    depth = float(depth_str)
                except ValueError:
                    depth = 0.0

                # 解析时间，提取年月日
                year, month, day = 0, 0, 0
                try:
                    # 格式可能为: "2026/1/26 14:56" 或 "1934/3/— —:—"
                    date_part = time_str.split(" ")[0] if " " in time_str else time_str
                    date_fields = date_part.split("/")
                    if len(date_fields) >= 1:
                        y_str = date_fields[0].strip().replace("—", "").replace("-", "")
                        year = int(y_str) if y_str.isdigit() else 0
                    if len(date_fields) >= 2:
                        m_str = date_fields[1].strip().replace("—", "").replace("-", "")
                        month = int(m_str) if m_str.isdigit() else 0
                    if len(date_fields) >= 3:
                        d_str = date_fields[2].strip().replace("—", "").replace("-", "")
                        day = int(d_str) if d_str.isdigit() else 0
                except Exception:
                    pass

                earthquakes.append({
                    "time_str": time_str,
                    "lon": lon,
                    "lat": lat,
                    "depth": depth,
                    "location": location,
                    "magnitude": mag,
                    "year": year,
                    "month": month,
                    "day": day,
                })

            except (ValueError, IndexError):
                continue  # 跳过解析失败的行

    print(f"  共读取 {len(earthquakes)} 条地震记录")
    return earthquakes


def filter_earthquakes(earthquakes, center_lon, center_lat, radius_km, min_magnitude=4.7):
    """
    筛选在指定范围内且震级不低于最小震级的地震记录

    参数:
        earthquakes (list): 所有地震记录列表
        center_lon (float): 震中经度（度）
        center_lat (float): 震中纬度（度）
        radius_km (float): 搜索半径（千米）
        min_magnitude (float): 最小震级，默认4.7

    返回:
        list: 筛选后的地震记录列表（添加了distance字段）
    """
    filtered = []
    for eq in earthquakes:
        if eq["magnitude"] < min_magnitude:
            continue
        dist = haversine_distance(center_lon, center_lat, eq["lon"], eq["lat"])
        if dist <= radius_km:
            eq_copy = eq.copy()
            eq_copy["distance"] = dist
            filtered.append(eq_copy)

    print(f"  筛选到 {len(filtered)} 条在 {radius_km}km 范围内的 M≥{min_magnitude} 地震记录")
    return filtered


# ============================================================
# 【绘图函数】
# ============================================================

def geo_to_pixel(lon, lat, geo_extent, img_width, img_height):
    """
    将经纬度坐标转换为图片像素坐标

    参数:
        lon (float): 经度（度）
        lat (float): 纬度（度）
        geo_extent (dict): 地理范围（min_lon, max_lon, min_lat, max_lat）
        img_width (int): 图片宽度（像素）
        img_height (int): 图片高度（像素）

    返回:
        tuple: (px, py) 像素坐标
    """
    px = (lon - geo_extent["min_lon"]) / (geo_extent["max_lon"] - geo_extent["min_lon"]) * img_width
    py = (geo_extent["max_lat"] - lat) / (geo_extent["max_lat"] - geo_extent["min_lat"]) * img_height
    return int(round(px)), int(round(py))


def draw_star(draw, cx, cy, radius, color, num_points=5):
    """
    绘制五角星

    参数:
        draw (ImageDraw): PIL绘图对象
        cx (int): 中心x坐标（像素）
        cy (int): 中心y坐标（像素）
        radius (int): 外接圆半径（像素）
        color (tuple): 颜色RGBA
        num_points (int): 角的数量，默认5
    """
    inner_radius = radius * 0.382  # 内接圆半径（黄金比例）
    points = []
    for i in range(num_points * 2):
        angle = math.radians(i * 360.0 / (num_points * 2) - 90)  # 从正上方开始
        if i % 2 == 0:
            r = radius
        else:
            r = inner_radius
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        points.append((x, y))
    draw.polygon(points, fill=color, outline=(0, 0, 0, 255))


def draw_north_arrow(draw, x, y, size=60):
    """
    绘制指北针（简洁箭头样式）

    参数:
        draw (ImageDraw): PIL绘图对象
        x (int): 指北针中心x坐标（像素）
        y (int): 指北针顶部y坐标（像素）
        size (int): 指北针大小（像素）
    """
    # 箭头主体
    top = (x, y)
    bottom_left = (x - size // 4, y + size)
    bottom_right = (x + size // 4, y + size)
    center_point = (x, int(y + size * 0.65))

    # 左半部分（黑色填充）
    draw.polygon([top, bottom_left, center_point], fill=(0, 0, 0, 255))
    # 右半部分（白色填充+黑色边框）
    draw.polygon([top, bottom_right, center_point], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    # 重绘左半部分边框
    draw.polygon([top, bottom_left, center_point], outline=(0, 0, 0, 255))

    # "N"字标注
    try:
        font_n = ImageFont.truetype(FONT_PATH_TITLE, size // 3)
    except (IOError, OSError):
        font_n = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "N", font=font_n)
    tw = bbox[2] - bbox[0]
    draw.text((x - tw // 2, y - size // 3 - 8), "N", fill=(0, 0, 0, 255), font=font_n)


def draw_scale_bar(draw, x, y, scale_denom, img_width, geo_extent, center_lat):
    """
    绘制线段比例尺（交替黑白色段）

    参数:
        draw (ImageDraw): PIL绘图对象
        x (int): 比例尺左端x坐标（像素）
        y (int): 比例尺y坐标（像素）
        scale_denom (int): 比例尺分母
        img_width (int): 图片宽度（像素）
        geo_extent (dict): 地理范围
        center_lat (float): 中心纬度
    """
    # 计算每像素代表的实际距离（km）
    lon_range = geo_extent["max_lon"] - geo_extent["min_lon"]
    km_per_pixel = lon_range * 111.32 * math.cos(math.radians(center_lat)) / img_width

    # 选择合适的比例尺标注距离（整数好看的值）
    nice_distances = [1, 2, 5, 10, 20, 50, 100, 200, 500]
    target_bar_width_px = img_width * 0.15  # 目标比例尺占图宽15%
    target_km = target_bar_width_px * km_per_pixel

    bar_km = nice_distances[0]
    for nd in nice_distances:
        if nd <= target_km * 1.2:
            bar_km = nd
        else:
            break

    bar_pixels = int(bar_km / km_per_pixel)

    # 白色背景
    draw.rectangle(
        [x - 8, y - 28, x + bar_pixels + 65, y + 28],
        fill=(255, 255, 255, 220),
        outline=(0, 0, 0, 200)
    )

    # 交替黑白色段（4段）
    bar_height = 8
    num_segments = 4
    seg_width = bar_pixels // num_segments
    for i in range(num_segments):
        color = (0, 0, 0, 255) if i % 2 == 0 else (255, 255, 255, 255)
        sx = x + i * seg_width
        draw.rectangle([sx, y, sx + seg_width, y + bar_height],
                       fill=color, outline=(0, 0, 0, 255))

    # 标注文字
    try:
        font_scale = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        font_scale = ImageFont.load_default()

    # 起始"0"
    draw.text((x, y + bar_height + 3), "0", fill=(0, 0, 0, 255), font=font_scale)
    # 终止距离
    end_label = f"{bar_km} km"
    bbox_end = draw.textbbox((0, 0), end_label, font=font_scale)
    tw_end = bbox_end[2] - bbox_end[0]
    draw.text((x + bar_pixels - tw_end // 2, y + bar_height + 3),
              end_label, fill=(0, 0, 0, 255), font=font_scale)

    # 比例尺数值标注（如 1:150,000）
    scale_text = f"1:{scale_denom:,}"
    bbox_s = draw.textbbox((0, 0), scale_text, font=font_scale)
    tw_s = bbox_s[2] - bbox_s[0]
    draw.text((x + bar_pixels // 2 - tw_s // 2, y - 20),
              scale_text, fill=(0, 0, 0, 255), font=font_scale)


def draw_legend(draw, x, y):
    """
    绘制图例（左下角），包含震中标记和四级地震圆点说明

    参数:
        draw (ImageDraw): PIL绘图对象
        x (int): 图例左上角x坐标（像素）
        y (int): 图例左上角y坐标（像素）
    """
    try:
        font_title = ImageFont.truetype(FONT_PATH_TITLE, 18)
        font_item = ImageFont.truetype(FONT_PATH_NORMAL, 14)
    except (IOError, OSError):
        font_title = ImageFont.load_default()
        font_item = ImageFont.load_default()

    # 图例背景
    legend_width = 170
    legend_height = 175
    draw.rectangle(
        [x, y, x + legend_width, y + legend_height],
        fill=(255, 255, 255, 230),
        outline=(0, 0, 0, 255)
    )

    # 标题 "图 例"
    draw.text((x + 50, y + 8), "图  例", fill=(0, 0, 0, 255), font=font_title)

    # 震中五角星图例项
    star_y = y + 42
    draw_star(draw, x + 22, star_y, 10, EPICENTER_STAR_COLOR)
    draw.text((x + 40, star_y - 9), "震中", fill=(0, 0, 0, 255), font=font_item)

    # 各等级圆点图例（从大到小排列）
    legend_items = [
        ("8.0级以上", COLOR_LEVEL_4, SIZE_LEVEL_4),
        ("7.0~7.9级", COLOR_LEVEL_3, SIZE_LEVEL_3),
        ("6.0~6.9级", COLOR_LEVEL_2, SIZE_LEVEL_2),
        ("4.7~5.9级", COLOR_LEVEL_1, SIZE_LEVEL_1),
    ]

    current_y = y + 68
    for label, color, dot_size in legend_items:
        cx = x + 22
        cy = current_y + 5
        half = dot_size // 2
        # 绘制圆点
        draw.ellipse(
            [cx - half, cy - half, cx + half, cy + half],
            fill=color, outline=(0, 0, 0, 180)
        )
        # 文字标注
        draw.text((x + 40, cy - 9), label, fill=(0, 0, 0, 255), font=font_item)
        current_y += 25


def draw_earthquake_points(draw, filtered_quakes, geo_extent, img_width, img_height):
    """
    在图上绘制筛选后的历史地震圆点（带白色描边增强可见性）

    参数:
        draw (ImageDraw): PIL绘图对象
        filtered_quakes (list): 筛选后的地震记录
        geo_extent (dict): 地理范围
        img_width (int): 图片宽度（像素）
        img_height (int): 图片高度（像素）

    返回:
        int: 实际绘制的地震点数量
    """
    # 按震级从小到大排序绘制，大震级覆盖在上层
    sorted_quakes = sorted(filtered_quakes, key=lambda eq: eq["magnitude"])

    drawn_count = 0
    for eq in sorted_quakes:
        level = get_earthquake_level(eq["magnitude"])
        if level == 0:
            continue

        color = get_level_color(level)
        size = get_level_size(level)
        px, py = geo_to_pixel(eq["lon"], eq["lat"], geo_extent, img_width, img_height)

        # 只绘制在图片范围内的点
        if 0 <= px <= img_width and 0 <= py <= img_height:
            half = size // 2
            # 先画白色外描边（比圆点大2像素），增强在卫星底图上的对比度
            draw.ellipse(
                [px - half - 2, py - half - 2, px + half + 2, py + half + 2],
                fill=None, outline=(255, 255, 255, 220), width=2
            )
            # 再画彩色圆点
            draw.ellipse(
                [px - half, py - half, px + half, py + half],
                fill=color, outline=(0, 0, 0, 200)
            )
            drawn_count += 1

    print(f"  实际绘制地震点: {drawn_count} 个")
    return drawn_count


def draw_epicenter(draw, center_lon, center_lat, geo_extent, img_width, img_height):
    """
    在图上绘制震中红色五角星标注

    参数:
        draw (ImageDraw): PIL绘图对象
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
        geo_extent (dict): 地理范围
        img_width (int): 图片宽度
        img_height (int): 图片高度
    """
    px, py = geo_to_pixel(center_lon, center_lat, geo_extent, img_width, img_height)
    draw_star(draw, px, py, EPICENTER_STAR_SIZE, EPICENTER_STAR_COLOR)
    print(f"  震中位置像素坐标: ({px}, {py})")


# ============================================================
# 【统计函数】
# ============================================================

def generate_statistics(filtered_quakes, radius_km):
    """
    生成地震统计信息文本

    参数:
        filtered_quakes (list): 筛选后的地震记录
        radius_km (float): 搜索半径（千米）

    返回:
        str: 格式化的统计信息文本
    """
    count_total = len(filtered_quakes)
    count_1 = sum(1 for eq in filtered_quakes if 4.7 <= eq["magnitude"] <= 5.9)
    count_2 = sum(1 for eq in filtered_quakes if 6.0 <= eq["magnitude"] <= 6.9)
    count_3 = sum(1 for eq in filtered_quakes if 7.0 <= eq["magnitude"] <= 7.9)
    count_4 = sum(1 for eq in filtered_quakes if eq["magnitude"] >= 8.0)

    # 查找最大地震
    max_eq = None
    if filtered_quakes:
        max_eq = max(filtered_quakes, key=lambda eq: eq["magnitude"])

    # 组装统计文本
    stat_text = (
        f"自1900年以来，本次地震震中{int(radius_km)}km范围内"
        f"曾发生{count_total}次4.7级以上地震，\n"
        f"其中4.7~5.9级地震{count_1}次，"
        f"6.0~6.9级地震{count_2}次，"
        f"7.0~7.9级地震{count_3}次，"
        f"8.0级以上地震{count_4}次。"
    )

    if max_eq:
        year = max_eq.get("year", 0)
        month = max_eq.get("month", 0)
        day = max_eq.get("day", 0)
        location = max_eq.get("location", "未知地点")
        mag = max_eq["magnitude"]

        year_str = str(year) if year > 0 else "未知"
        month_str = str(month) if month > 0 else "未知"
        day_str = str(day) if day > 0 else "未知"

        stat_text += (
            f"\n最大地震为{year_str}年{month_str}月{day_str}日"
            f"{location}{mag}级地震。"
        )

    return stat_text


# ============================================================
# 【主函数】
# ============================================================

def generate_earthquake_map(
    center_lon,
    center_lat,
    magnitude,
    csv_path,
    output_path,
    csv_encoding="gbk"
):
    """
    生成历史地震分布图的主函数

    参数:
        center_lon (float): 用户输入的震中经度（度）
        center_lat (float): 用户输入的震中纬度（度）
        magnitude (float): 用户输入的震级（M）
        csv_path (str): 历史地震CSV文件路径
        output_path (str): 输出图片文件路径
        csv_encoding (str): CSV文件编码，默认"gbk"

    返回:
        str: 统计信息文本
    """
    print("=" * 65)
    print("  历 史 地 震 分 布 图 生 成 工 具")
    print("=" * 65)
    print(f"  震中经度: {center_lon}°E")
    print(f"  震中纬度: {center_lat}°N")
    print(f"  震级: M{magnitude}")

    # 第一步：确定绘图范围参数
    radius_km, span_km, scale_denom = get_range_params(magnitude)
    half_span_km = span_km / 2.0
    print(f"  绘图范围: 震中 {radius_km}km ({span_km}km × {span_km}km)")
    print(f"  比例尺: 1:{scale_denom:,}")
    print()

    # 第二步：读取历史地震CSV文件
    print("[1/5] 读取历史地震数据...")
    if not os.path.exists(csv_path):
        print(f"  *** 错误: CSV文件不存在: {csv_path} ***")
        return ""
    earthquakes = read_earthquake_csv(csv_path, encoding=csv_encoding)
    if not earthquakes:
        print("  *** 警告: 未读取到任何地震记录 ***")
    print()

    # 第三步：筛选范围内的地震记录
    print("[2/5] 筛选范围内地震记录...")
    filtered_quakes = filter_earthquakes(earthquakes, center_lon, center_lat, radius_km)
    print()

    # 第四步：获取天地图底图
    print("[3/5] 获取天地图底图（正在下载瓦片，请稍候）...")
    basemap, geo_extent = fetch_basemap(
        center_lon, center_lat, half_span_km, scale_denom,
        OUTPUT_WIDTH, OUTPUT_HEIGHT
    )
    print()

    # 第五步：绘制叠加要素
    print("[4/5] 绘制地震要素...")

    # 转换为RGBA以支持透明度绘制
    result_img = basemap.convert("RGBA")

    # 创建透明叠加层用于绘制地震点
    overlay = Image.new("RGBA", result_img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # ★★★ 关键修复：调换绘制顺序 ★★★
    # 先绘制震中五角星（底层），再绘制历史地震圆点（上层）
    # 这样当地震点与震中重合时，地震圆点不会被五角星完全遮挡
    draw_epicenter(draw_overlay, center_lon, center_lat, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)
    draw_earthquake_points(draw_overlay, filtered_quakes, geo_extent, OUTPUT_WIDTH, OUTPUT_HEIGHT)

    # 合成叠加层到底图
    result_img = Image.alpha_composite(result_img, overlay)

    # 在合成后的图上绘制装饰要素（指北针、比例尺、图例、标题）
    draw_final = ImageDraw.Draw(result_img)

    # 右上角指北针
    draw_north_arrow(draw_final, OUTPUT_WIDTH - 55, 25, size=55)

    # 右下角比例尺
    draw_scale_bar(
        draw_final,
        OUTPUT_WIDTH - 280, OUTPUT_HEIGHT - 50,
        scale_denom, OUTPUT_WIDTH, geo_extent, center_lat
    )

    # 左下角图例
    draw_legend(draw_final, 12, OUTPUT_HEIGHT - 195)

    # 顶部居中标题
    try:
        font_title = ImageFont.truetype(FONT_PATH_TITLE, 22)
    except (IOError, OSError):
        font_title = ImageFont.load_default()

    title_text = f"历史地震分布图（震中{int(radius_km)}km范围）"
    bbox_t = draw_final.textbbox((0, 0), title_text, font=font_title)
    title_w = bbox_t[2] - bbox_t[0]
    title_h = bbox_t[3] - bbox_t[1]

    # 标题背景框
    title_x = OUTPUT_WIDTH // 2 - title_w // 2
    title_y = 8
    draw_final.rectangle(
        [title_x - 12, title_y - 4, title_x + title_w + 12, title_y + title_h + 6],
        fill=(255, 255, 255, 210),
        outline=(0, 0, 0, 200)
    )
    draw_final.text((title_x, title_y), title_text, fill=(0, 0, 0, 255), font=font_title)

    print()

    # 第六步：保存输出图片
    print("[5/5] 保存输出图片...")
    # 转为RGB保存（兼容Word文档插入，PNG/JPG/TIFF均支持）
    output_rgb = result_img.convert("RGB")
    output_rgb.save(output_path, dpi=(OUTPUT_DPI, OUTPUT_DPI), quality=95)
    file_size_kb = os.path.getsize(output_path) / 1024
    print(f"  图片已保存: {output_path}")
    print(f"  文件大小: {file_size_kb:.1f} KB")
    print(f"  图片尺寸: {OUTPUT_WIDTH} x {OUTPUT_HEIGHT} 像素")
    print()

    # 第七步：生成并输出统计信息
    stat_text = generate_statistics(filtered_quakes, radius_km)
    print("=" * 65)
    print("【统计信息】")
    print(stat_text)
    print("=" * 65)

    return stat_text


# ============================================================
# 【脚本入口 - 修改此处参数运行】
# ============================================================

if __name__ == "__main__":
    # ========================================
    # 用户输入参数（请根据实际情况修改）
    # ========================================

    # 震中经度（度）
    INPUT_LON = 122.06
    # 震中纬度（度）
    INPUT_LAT = 24.67
    # 震级（M）
    INPUT_MAGNITUDE = 6.6
    # 历史地震CSV文件路径（GBK编码）
    INPUT_CSV_PATH = r"../../data/geology/历史地震CSV文件.csv"
    # 输出图片路径
    OUTPUT_PATH = r"../../data/geology/output_earthquake_map.png"
    a = r"../../data/geology/省级边界数据/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"

    # 执行生成
    stat_result = generate_earthquake_map(
        center_lon=INPUT_LON,
        center_lat=INPUT_LAT,
        magnitude=INPUT_MAGNITUDE,
        csv_path=INPUT_CSV_PATH,
        output_path=OUTPUT_PATH,
        csv_encoding="gbk"
    )