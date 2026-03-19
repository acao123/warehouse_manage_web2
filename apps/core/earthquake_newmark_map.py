# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震Newmark位移分布图生成脚本
参考 earthquake_elevation_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

Newmark位移图例说明：
- Newmark位移TIF文件使用手动间隔分为十档
- 十档颜色从低到高：rgb(148,193,146), rgb(251,245,195), rgb(250,236,150),
  rgb(246,215,75), rgb(246,200,0), rgb(242,182,11), rgb(218,89,49),
  rgb(214,82,11), rgb(192,2,28), rgb(81,52,130)
- 图例显示：色块连接 + 边界位移值（从第二个值开始到最大值，共10个标签）
- 图例标题：Newmark位移(cm) - Newmark和cm使用Times New Roman字体，位移()使用SimHei字体

优化说明：
- 针对大文件TIF进行优化，只裁剪加载需要范围内的数据
- 使用GDAL的虚拟栅格(VRT)技术实现按需裁剪
- 显著减少内存占用和处理时间
- 只加载天地图矢量注记图层（放置在最上层），不加载矢量底图
"""

import os
import sys
import math
import re
import logging
import tempfile
import shutil
import requests
from xml.etree import ElementTree as ET
from PIL import Image
from io import BytesIO

# ============================================================
# Django settings 导入（可选）
# ============================================================
try:
    from django.conf import settings as _django_settings
    _DJANGO_AVAILABLE = True
except ImportError:
    _django_settings = None
    _DJANGO_AVAILABLE = False

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.earthquake_newmark_map')

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
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsLayoutMeasurement,
    QgsGeometry,
    QgsFeature,
    QgsField,
    QgsLayoutExporter,
    QgsRasterShader,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsCoordinateTransform,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# GDAL导入（用于栅格裁剪和读取最大值）
try:
    from osgeo import gdal, osr
    import numpy as np

    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False
    print("[警告] GDAL模块未找到，将使用备用方案加载栅格")

# ============================================================
# 常量定义
# ============================================================

# 天地图配置
TIANDITU_TK = (
    getattr(_django_settings, 'TIANDITU_TK', '1ef76ef90c6eb961cb49618f9b1a399d')
    if _DJANGO_AVAILABLE else '1ef76ef90c6eb961cb49618f9b1a399d'
)

# 数据文件路径（优先从 Django settings 读取）
_DEFAULT_BASE = "../../data/geology/"

DN_TIF_PATH = _DEFAULT_BASE + 'Ia/Dn.tif'
PROVINCE_SHP_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp'
)
CITY_SHP_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp'
)
COUNTY_SHP_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp'
)
# 地级市点位数据
CITY_POINTS_SHP_PATH = (
    getattr(_django_settings, 'CITY_POINTS_SHP_PATH',
            _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp')
    if _DJANGO_AVAILABLE else _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp'
)

# === 布局尺寸常量 ===
MAP_TOTAL_WIDTH_MM = 220.0
LEGEND_WIDTH_MM = 50.0
BORDER_LEFT_MM = 4.0
BORDER_TOP_MM = 4.0
BORDER_BOTTOM_MM = 2.0
BORDER_RIGHT_MM = 1.0
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM

# 输出DPI
OUTPUT_DPI = 150

# === 震级配置 ===
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

# === 边框宽度 ===
BORDER_WIDTH_MM = 0.35

# === 指北针尺寸常量 ===
NORTH_ARROW_WIDTH_MM = 12.0
NORTH_ARROW_HEIGHT_MM = 18.0

# === 经纬度字体(pt) ===
LONLAT_FONT_SIZE_PT = 8

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)
CITY_LINE_WIDTH_MM = 0.24
CITY_DASH_GAP_MM = 0.3
CITY_DASH_PATTERN = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)
COUNTY_LINE_WIDTH_MM = 0.14
COUNTY_DASH_GAP_MM = 0.2
COUNTY_DASH_PATTERN = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9
CITY_LABEL_COLOR = QColor(0, 0, 0)

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 12
LEGEND_ITEM_FONT_SIZE_PT = 10
NEWMARK_LEGEND_FONT_SIZE_PT = 10  # Newmark位移图例字体大小

# === 基本图例项配置（可单独设置） ===
BASIC_LEGEND_FONT_SIZE_PT = 10  # 基本图例项字体大小
BASIC_LEGEND_ROW_HEIGHT_MM = 8.0  # 基本图例项行高

# === Newmark位移图例项配置（可单独设置） ===
NEWMARK_LEGEND_ITEM_FONT_SIZE_PT = 10  # Newmark位移图例项字体大小
NEWMARK_LEGEND_ROW_HEIGHT_MM = 7.0  # Newmark位移图例项行高（色块高度）

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8

# === 烈度圈样式 ===
INTENSITY_LINE_COLOR = QColor(0, 0, 0)
INTENSITY_LINE_WIDTH_MM = 0.5
INTENSITY_HALO_COLOR = QColor(255, 255, 255)
INTENSITY_HALO_WIDTH_MM = 1.0
INTENSITY_LABEL_FONT_SIZE_PT = 9

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 5.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# === 烈度图例颜色 ===
INTENSITY_LEGEND_COLOR = QColor(0, 0, 0)
INTENSITY_LEGEND_LINE_WIDTH_MM = 0.5

# === Newmark位移分档配置 ===
# 十档颜色（从低到高）- 参考用户提供的图例图片
NEWMARK_COLORS = [
    QColor(148, 193, 146),  # 第1档 - 浅绿色
    QColor(251, 245, 195),  # 第2档 - 浅黄色
    QColor(250, 236, 150),  # 第3档 - 黄色
    QColor(246, 215, 75),   # 第4档 - 金黄色
    QColor(246, 200, 0),    # 第5档 - 橙黄色
    QColor(242, 182, 11),   # 第6档 - 橙色
    QColor(218, 89, 49),    # 第7档 - 深橙色
    QColor(214, 82, 11),    # 第8档 - 红橙色
    QColor(192, 2, 28),     # 第9档 - 红色
    QColor(81, 52, 130),    # 第10档 - 紫色
]

# Newmark位移图例分档值表（根据最大值选择对应列）
# 列索引对应最大值：5, 10, 20, 50, 100, 200, 300, 500, 1000
NEWMARK_LEGEND_TABLE = {
    5:    [0, 0.01, 0.1, 0.2, 0.5, 0.8, 1, 2, 3, 4, 5],
    10:   [0, 0.01, 0.1, 0.2, 0.5, 1, 2, 4, 6, 8, 10],
    20:   [0, 0.01, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 20],
    50:   [0, 0.1, 0.5, 1, 2, 5, 10, 20, 30, 40, 50],
    100:  [0, 0.1, 0.5, 1, 5, 10, 20, 40, 60, 80, 100],
    200:  [0, 0.1, 1, 2, 5, 10, 15, 25, 50, 100, 200],
    300:  [0, 0.1, 1, 2, 5, 10, 20, 50, 100, 150, 300],
    500:  [0, 0.1, 1, 2, 5, 10, 20, 50, 100, 200, 500],
    1000: [0, 1, 2, 5, 10, 20, 50, 100, 250, 500, 1000],
}

# 最大值阈值列表（用于选择对应的分档列）
NEWMARK_MAX_THRESHOLDS = [5, 10, 20, 50, 100, 200, 300, 500, 1000]

# WGS84
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# === 裁剪缓冲区（度） ===
# 在目标范围外增加缓冲区，确保边缘数据完整
CLIP_BUFFER_DEGREES = 0.1

# === 图例布局和字体设置 ===
LEGEND_ROW_COUNT = 2  # 图例行数
LEGEND_COLUMN_COUNT = 3  # 图例列数
LEGEND_FONT_TIMES_NEW_ROMAN = "Times New Roman"  # 数字标签字体和Newmark英文字体


# ============================================================
# 天地图瓦片下载函数（只下载注记图层）
# ============================================================

def download_tianditu_annotation_tiles(extent, width_px, height_px, output_path):
    """
    只下载天地图矢量注记瓦片并拼接为本地栅格图像（透明背景）

    参数:
        extent: QgsRectangle, 渲染范围 (WGS84)
        width_px: int, 输出图像宽度(像素)
        height_px: int, 输出图像高度(像素)
        output_path: str, 输出文件路径

    返回:
        QgsRasterLayer或None, 渲染后的栅格图层
    """
    try:
        tk = TIANDITU_TK

        # 计算合适的缩放级别
        lon_range = extent.xMaximum() - extent.xMinimum()
        zoom = int(math.log2(360 / lon_range * width_px / 256))
        zoom = max(1, min(zoom, 18))

        logger.info('下载天地图注记瓦片，缩放级别: %d', zoom)
        print(f"[信息] 下载天地图注记瓦片，缩放级别: {zoom}")

        def lon_to_tile_x(lon, z):
            """经度转瓦片X坐标"""
            n = 2 ** z
            x = int((lon + 180.0) / 360.0 * n)
            return max(0, min(n - 1, x))

        def lat_to_tile_y(lat, z):
            """纬度转瓦片Y坐标 (天地图c系列)"""
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

        # 获取瓦片范围
        tile_x_min = lon_to_tile_x(extent.xMinimum(), zoom)
        tile_x_max = lon_to_tile_x(extent.xMaximum(), zoom)
        tile_y_min = lat_to_tile_y(extent.yMaximum(), zoom)
        tile_y_max = lat_to_tile_y(extent.yMinimum(), zoom)

        if tile_y_min > tile_y_max:
            tile_y_min, tile_y_max = tile_y_max, tile_y_min

        logger.info('瓦片范围: x=%d-%d, y=%d-%d', tile_x_min, tile_x_max, tile_y_min, tile_y_max)
        print(f"[信息] 瓦片范围: x={tile_x_min}-{tile_x_max}, y={tile_y_min}-{tile_y_max}")

        num_tiles_x = tile_x_max - tile_x_min + 1
        num_tiles_y = tile_y_max - tile_y_min + 1
        total_tiles = num_tiles_x * num_tiles_y
        logger.info('需要下载 %d 个注记瓦片 (%d x %d)', total_tiles, num_tiles_x, num_tiles_y)
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

                # 只下载天地图注记URL (cva_c - EPSG:4326)
                cva_url = (
                    f"http://{server}.tianditu.gov.cn/cva_c/wmts?"
                    f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                    f"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
                    f"&FORMAT=tiles&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                    f"&tk={tk}"
                )

                try:
                    resp_cva = requests.get(cva_url, timeout=10)
                    if resp_cva.status_code == 200:
                        tile_cva = Image.open(BytesIO(resp_cva.content)).convert('RGBA')

                        # 粘贴到拼接图像（保留透明度）
                        paste_x = (tx - tile_x_min) * tile_size
                        paste_y = (ty - tile_y_min) * tile_size
                        mosaic.paste(tile_cva, (paste_x, paste_y), tile_cva)
                        downloaded += 1
                    else:
                        failed += 1
                        logger.warning('注记瓦片下载失败: %d,%d - 状态码: %d', tx, ty, resp_cva.status_code)
                        print(f"[警告] 注记瓦片下载失败: {tx},{ty} - 状态码: {resp_cva.status_code}")
                except Exception as e:
                    failed += 1
                    logger.warning('注记瓦片下载异常: %d,%d - %s', tx, ty, e)
                    print(f"[警告] 注记瓦片下载异常: {tx},{ty} - {e}")

        logger.info('注记瓦片下载完成: 成功 %d, 失败 %d', downloaded, failed)
        print(f"[信息] 注记瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

        if downloaded == 0:
            logger.error('没有成功下载任何注记瓦片')
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

        try:
            cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
            final_image = cropped.resize((width_px, height_px), Image.LANCZOS)

            # 保存为PNG格式（支持透明度）
            final_image.save(output_path, 'PNG')
            logger.info('注记底图已保存: %s', output_path)
            print(f"[信息] 注记底图已保存: {output_path}")
        except Exception as exc:
            logger.error('图片拼接或保存失败: %s', exc, exc_info=True)
            raise

        world_file_path = output_path.replace(".png", ".pgw")
        x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
        y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
        with open(world_file_path, 'w') as f:
            f.write(f"{x_res}\n")
            f.write("0\n")
            f.write("0\n")
            f.write(f"{-y_res}\n")
            f.write(f"{extent.xMinimum()}\n")
            f.write(f"{extent.yMaximum()}\n")

        raster_layer = QgsRasterLayer(output_path, "天地图注记", "gdal")
        if raster_layer.isValid():
            logger.info('成功加载注记栅格图层')
            print(f"[信息] 成功加载注记栅格图层")
            return raster_layer
        else:
            logger.error('无法加载注记栅格图层')
            print(f"[错误] 无法加载注记栅格图层")
            return None

    except Exception as exc:
        logger.error('下载天地图注记瓦片失败: %s', exc, exc_info=True)
        raise


# ============================================================
# 工具函数
# ============================================================

def get_magnitude_config(magnitude):
    """
    根据震级获取对应的配置参数

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


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度(km)计算地图范围(WGS84坐标)

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度
        half_size_km (float): 地图半幅宽度（公里）

    返回:
        QgsRectangle: 地图范围矩形
    """
    delta_lat = half_size_km / 111.0
    delta_lon = half_size_km / (111.0 * math.cos(math.radians(latitude)))
    xmin = longitude - delta_lon
    xmax = longitude + delta_lon
    ymin = latitude - delta_lat
    ymax = latitude + delta_lat
    return QgsRectangle(xmin, ymin, xmax, ymax)


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
    aspect_ratio = lat_range / lon_range
    return map_width_mm * aspect_ratio


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


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字

    参数:
        num (int): 阿拉伯数字

    返回:
        str: 罗马数字字符串
    """
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


def _find_name_field(layer, candidates):
    """
    在矢量图层的字段列表中查找名称字段

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


# ============================================================
# 大文件栅格裁剪优化函数
# ============================================================

def clip_raster_to_extent(input_path, output_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    使用GDAL裁剪大型栅格文件到指定范围

    该函数针对大文件进行优化，只读取和输出需要的区域数据，
    显著减少内存占用和处理时间。

    参数:
        input_path (str): 输入栅格文件路径
        output_path (str): 输出裁剪后的栅格文件路径
        extent (QgsRectangle): 目标范围（WGS84坐标）
        buffer_degrees (float): 缓冲区大小（度），默认0.1度

    返回:
        str: 成功返回输出文件路径，失败返回None
    """
    if not GDAL_AVAILABLE:
        logger.warning('GDAL不可用，无法进行栅格裁剪')
        print("[警告] GDAL不可用，无法进行栅格裁剪")
        return None

    if not os.path.exists(input_path):
        logger.error('输入栅格文件不存在: %s', input_path)
        print(f"[错误] 输入栅格文件不存在: {input_path}")
        return None

    try:
        src_ds = gdal.Open(input_path, gdal.GA_ReadOnly)
        if src_ds is None:
            logger.error('无法打开栅格文件: %s', input_path)
            print(f"[错误] 无法打开栅格文件: {input_path}")
            return None

        src_width = src_ds.RasterXSize
        src_height = src_ds.RasterYSize
        src_bands = src_ds.RasterCount

        logger.info('源栅格信息: %dx%d, %d波段', src_width, src_height, src_bands)
        print(f"[信息] 源栅格信息: {src_width}x{src_height}, {src_bands}波段")

        clip_xmin = extent.xMinimum() - buffer_degrees
        clip_xmax = extent.xMaximum() + buffer_degrees
        clip_ymin = extent.yMinimum() - buffer_degrees
        clip_ymax = extent.yMaximum() + buffer_degrees

        logger.info('裁剪范围: (%.4f, %.4f) - (%.4f, %.4f)', clip_xmin, clip_ymin, clip_xmax, clip_ymax)
        print(f"[信息] 裁剪范围: ({clip_xmin:.4f}, {clip_ymin:.4f}) - ({clip_xmax:.4f}, {clip_ymax:.4f})")

        translate_options = gdal.TranslateOptions(
            projWin=[clip_xmin, clip_ymax, clip_xmax, clip_ymin],
            projWinSRS='EPSG:4326',
            format='GTiff',
            creationOptions=[
                'COMPRESS=LZW',
                'TILED=YES',
                'BLOCKXSIZE=256',
                'BLOCKYSIZE=256',
                'BIGTIFF=IF_SAFER'
            ]
        )

        logger.info('正在裁剪栅格数据...')
        print(f"[信息] 正在裁剪栅格数据...")
        dst_ds = gdal.Translate(output_path, src_ds, options=translate_options)

        if dst_ds is None:
            logger.error('栅格裁剪失败')
            print(f"[错误] 栅格裁剪失败")
            src_ds = None
            return None

        dst_width = dst_ds.RasterXSize
        dst_height = dst_ds.RasterYSize

        dst_ds = None
        src_ds = None

        src_size = os.path.getsize(input_path) / (1024 * 1024 * 1024)
        dst_size = os.path.getsize(output_path) / (1024 * 1024)

        logger.info('裁剪完成: %dx%d, 原文件: %.2fGB -> 裁剪后: %.2fMB', dst_width, dst_height, src_size, dst_size)
        print(f"[信息] 裁剪完成: {dst_width}x{dst_height}")
        print(f"[信息] 原文件: {src_size:.2f}GB -> 裁剪后: {dst_size:.2f}MB")

        return output_path

    except Exception as exc:
        logger.error('栅格裁剪异常: %s', exc, exc_info=True)
        print(f"[错误] 栅格裁剪异常: {exc}")
        raise


def create_vrt_for_extent(input_path, output_vrt_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    创建虚拟栅格(VRT)文件，只引用指定范围的数据

    备用函数：当前主流程使用 clip_raster_to_extent 进行裁剪，此函数保留作为备用方案。

    参数:
        input_path (str): 输入栅格文件路径
        output_vrt_path (str): 输出VRT文件路径
        extent (QgsRectangle): 目标范围（WGS84坐标）
        buffer_degrees (float): 缓冲区大小（度）

    返回:
        str: 成功返回VRT文件路径，失败返回None
    """
    if not GDAL_AVAILABLE:
        print("[警告] GDAL不可用，无法创建VRT")
        return None

    if not os.path.exists(input_path):
        print(f"[错误] 输入栅格文件不存在: {input_path}")
        return None

    try:
        clip_xmin = extent.xMinimum() - buffer_degrees
        clip_xmax = extent.xMaximum() + buffer_degrees
        clip_ymin = extent.yMinimum() - buffer_degrees
        clip_ymax = extent.yMaximum() + buffer_degrees

        vrt_options = gdal.BuildVRTOptions(
            outputBounds=[clip_xmin, clip_ymin, clip_xmax, clip_ymax],
            outputSRS='EPSG:4326'
        )

        vrt_ds = gdal.BuildVRT(output_vrt_path, [input_path], options=vrt_options)

        if vrt_ds is None:
            print(f"[错误] VRT创建失败")
            return None

        vrt_ds = None

        print(f"[信息] VRT文件创建成功: {output_vrt_path}")
        return output_vrt_path

    except Exception as e:
        print(f"[错误] VRT创建异常: {e}")
        return None


class TempFileManager:
    """
    临时文件管理器

    用于管理裁剪产生的临时文件，确保在处理完成后正确清理。
    """

    def __init__(self):
        """初始化临时文件管理器"""
        self.temp_dir = None
        self.temp_files = []

    def get_temp_dir(self):
        """
        获取临时目录路径

        返回:
            str: 临时目录路径
        """
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="earthquake_newmark_")
            print(f"[信息] 创建临时目录: {self.temp_dir}")
        return self.temp_dir

    def get_temp_file(self, suffix=".tif"):
        """
        获取临时文件路径

        参数:
            suffix (str): 文件后缀

        返回:
            str: 临时文件路径
        """
        temp_dir = self.get_temp_dir()
        fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
        os.close(fd)
        self.temp_files.append(temp_path)
        return temp_path

    def cleanup(self):
        """清理所有临时文件和目录"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError as e:
                print(f"[警告] 无法删除临时文件 {temp_file}: {e}")

        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"[信息] 已清理临时目录: {self.temp_dir}")
            except OSError as e:
                print(f"[警告] 无法删除临时目录 {self.temp_dir}: {e}")

        self.temp_files = []
        self.temp_dir = None


# 全局临时文件管理器
_temp_manager = TempFileManager()


def get_temp_manager():
    """
    获取全局临时文件管理器实例

    返回:
        TempFileManager: 临时文件管理器实例
    """
    return _temp_manager


# ============================================================
# Newmark位移栅格相关函数
# ============================================================

def get_raster_max_value_in_extent(tif_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    只读取指定范围内的栅格数据并计算最大值（不裁剪保存文件）

    该函数针对大文件优化，只读取需要范围内的数据计算最大值，
    不会将整个文件加载到内存。

    参数:
        tif_path (str): TIF文件路径
        extent (QgsRectangle): 目标范围（WGS84坐标）
        buffer_degrees (float): 缓冲区大小（度）

    返回:
        float: 范围内栅格最大值，失败返回None
    """
    if not GDAL_AVAILABLE:
        logger.warning('GDAL不可用，无法读取栅格最大值')
        print("[警告] GDAL不可用，无法读取栅格最大值")
        return None

    abs_path = resolve_path(tif_path) if not os.path.isabs(tif_path) else tif_path
    if not os.path.exists(abs_path):
        logger.error('栅格文件不存在: %s', abs_path)
        print(f"[错误] 栅格文件不存在: {abs_path}")
        return None

    try:
        ds = gdal.Open(abs_path, gdal.GA_ReadOnly)
        if ds is None:
            logger.error('无法打开栅格文件: %s', abs_path)
            print(f"[错误] 无法打开栅格文件: {abs_path}")
            return None

        # 获取栅格地理变换参数
        gt = ds.GetGeoTransform()
        # gt[0]: 左上角X坐标（经度）
        # gt[1]: X方向像素分辨率
        # gt[2]: 旋转参数（通常为0）
        # gt[3]: 左上角Y坐标（纬度）
        # gt[4]: 旋转参数（通常为0）
        # gt[5]: Y方向像素分辨率（负值）

        raster_width = ds.RasterXSize
        raster_height = ds.RasterYSize

        # 计算裁剪范围（带缓冲区）
        clip_xmin = extent.xMinimum() - buffer_degrees
        clip_xmax = extent.xMaximum() + buffer_degrees
        clip_ymin = extent.yMinimum() - buffer_degrees
        clip_ymax = extent.yMaximum() + buffer_degrees

        # 将地理坐标转换为像素坐标
        px_xmin = int((clip_xmin - gt[0]) / gt[1])
        px_xmax = int((clip_xmax - gt[0]) / gt[1])
        px_ymin = int((clip_ymax - gt[3]) / gt[5])  # 注意Y方向是反的
        px_ymax = int((clip_ymin - gt[3]) / gt[5])

        # 确保像素坐标在有效范围内
        px_xmin = max(0, min(raster_width - 1, px_xmin))
        px_xmax = max(px_xmin + 1, min(raster_width, px_xmax))
        px_ymin = max(0, min(raster_height - 1, px_ymin))
        px_ymax = max(px_ymin + 1, min(raster_height, px_ymax))

        # 计算读取的宽度和高度
        read_width = px_xmax - px_xmin
        read_height = px_ymax - px_ymin

        logger.info('读取栅格范围: 像素(%d,%d) - (%d,%d), 尺寸: %dx%d',
                    px_xmin, px_ymin, px_xmax, px_ymax, read_width, read_height)
        print(f"[信息] 读取栅格范围: 像素({px_xmin},{px_ymin}) - ({px_xmax},{px_ymax})")
        print(f"[信息] 读取尺寸: {read_width} x {read_height} 像素")

        # 只读取指定范围的数据
        band = ds.GetRasterBand(1)
        nodata_value = band.GetNoDataValue()

        # 读取数据块
        data = band.ReadAsArray(px_xmin, px_ymin, read_width, read_height)

        if data is None:
            logger.error('无法读取栅格数据: %s', abs_path)
            print(f"[错误] 无法读取栅格数据")
            ds = None
            return None

        # 处理NoData值
        if nodata_value is not None:
            valid_data = data[data != nodata_value]
        else:
            valid_data = data[~np.isnan(data)]

        if valid_data.size == 0:
            logger.warning('范围内没有有效数据: %s', abs_path)
            print(f"[警告] 范围内没有有效数据")
            ds = None
            return None

        max_value = float(np.max(valid_data))

        ds = None
        logger.info('范围内栅格最大值: %.4f', max_value)
        print(f"[信息] 范围内栅格最大值: {max_value:.4f}")
        return max_value

    except Exception as exc:
        logger.error('读取栅格最大值异常: %s', exc, exc_info=True)
        print(f"[错误] 读取栅格最大值异常: {exc}")
        raise


def get_raster_max_value(tif_path):
    """
    获取栅格文件中的最大值（读取整个文件）

    参数:
        tif_path (str): TIF文件路径

    返回:
        float: 栅格最大值，失败返回None
    """
    if not GDAL_AVAILABLE:
        print("[警告] GDAL不可用，无法读取栅格最大值")
        return None

    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 栅格文件不存在: {abs_path}")
        return None

    try:
        ds = gdal.Open(abs_path, gdal.GA_ReadOnly)
        if ds is None:
            print(f"[错误] 无法打开栅格文件: {abs_path}")
            return None

        band = ds.GetRasterBand(1)
        stats = band.GetStatistics(True, True)
        # stats返回: [min, max, mean, stddev]
        max_value = stats[1]

        ds = None
        print(f"[信息] 栅格最大值: {max_value}")
        return max_value

    except Exception as e:
        print(f"[错误] 读取栅格最大值异常: {e}")
        return None


def get_clipped_raster_max_value(tif_path):
    """
    获取裁剪后栅格文件中的最大值（用于已裁剪的临时文件）

    备用函数：当前主流程使用 get_raster_max_value_in_extent 在裁剪前直接读取最大值，
    此函数保留作为备用方案（适用于已有裁剪后文件时）。

    参数:
        tif_path (str): TIF文件路径（绝对路径）

    返回:
        float: 栅格最大值，失败返回None
    """
    if not GDAL_AVAILABLE:
        print("[警告] GDAL不可用，无法读取栅格最大值")
        return None

    if not os.path.exists(tif_path):
        print(f"[错误] 栅格文件不存在: {tif_path}")
        return None

    try:
        ds = gdal.Open(tif_path, gdal.GA_ReadOnly)
        if ds is None:
            print(f"[错误] 无法打开栅格文件: {tif_path}")
            return None

        band = ds.GetRasterBand(1)
        stats = band.GetStatistics(True, True)
        # stats返回: [min, max, mean, stddev]
        max_value = stats[1]

        ds = None
        print(f"[信息] 裁剪后栅格最大值: {max_value}")
        return max_value

    except Exception as e:
        print(f"[错误] 读取栅格最大值异常: {e}")
        return None


def select_legend_column(max_value):
    """
    根据Dn最大值选择对应的图例分档列

    参数:
        max_value (float): Dn栅格的最大值

    返回:
        list: 对应的11个分档值列表（包含0到最大值的11个档位），
              如果max_value <= 0 或 > 1000则返回None（不显示图例）；
              如果max_value is None则返回默认分档列（最大值10）
    """
    if max_value is None:
        print("[警告] 最大值为None，使用默认分档（最大值10）")
        return NEWMARK_LEGEND_TABLE[10]

    if max_value <= 0:
        logger.info('Dn最大值 %.4f <= 0，不显示Newmark位移图例', max_value)
        print(f"[信息] Dn最大值 {max_value} <= 0，不显示Newmark位移图例")
        return None

    if max_value > 1000:
        logger.info('最大值 %.4f > 1000，图例文字不显示', max_value)
        print(f"[信息] 最大值 {max_value} > 1000，图例文字不显示")
        return None

    # 选择最接近但不小于max_value的阈值
    selected_threshold = None
    for threshold in NEWMARK_MAX_THRESHOLDS:
        if max_value <= threshold:
            selected_threshold = threshold
            break

    if selected_threshold is None:
        selected_threshold = 1000

    logger.info('Dn最大值: %.2f, 选择分档列: %d', max_value, selected_threshold)
    print(f"[信息] Dn最大值: {max_value:.2f}, 选择分档列: {selected_threshold}")
    return NEWMARK_LEGEND_TABLE[selected_threshold]


def format_legend_value(value):
    """
    格式化图例数值显示

    参数:
        value (float): 数值

    返回:
        str: 格式化后的字符串
    """
    if value == 0:
        return "0"
    elif value < 1:
        # 小于1的数保留2位小数
        return f"{value:.2f}"
    elif value < 10 and value != int(value):
        # 小于10且非整数，保留1位小数
        return f"{value:.1f}"
    elif value == int(value):
        # 整数
        return f"{int(value)}"
    else:
        return f"{value:.2f}"


def build_newmark_classes(legend_values):
    """
    根据图例分档值构建Newmark位移分档配置

    参数:
        legend_values (list): 11个分档值列表

    返回:
        list: Newmark位移分档配置列表，每项包含min, max, color, label
    """
    if legend_values is None or len(legend_values) != 11:
        print("[警告] 图例值无效，使用默认配置")
        legend_values = NEWMARK_LEGEND_TABLE[10]

    classes = []
    for i in range(10):
        min_val = legend_values[i]
        max_val = legend_values[i + 1]
        color = NEWMARK_COLORS[i]

        # 格式化标签
        label = format_legend_value(max_val)

        classes.append({
            "min": min_val,
            "max": max_val,
            "color": color,
            "label": label
        })

    return classes


def apply_newmark_renderer(raster_layer, legend_values):
    """
    为Newmark位移栅格图层应用手动间隔分类渲染器

    参数:
        raster_layer (QgsRasterLayer): Newmark位移栅格图层
        legend_values (list): 11个分档值列表

    返回:
        bool: 是否成功应用渲染器
    """
    if raster_layer is None or not raster_layer.isValid():
        logger.error('无效的栅格图层，无法应用渲染器')
        print("[错误] 无效的栅格图层，无法应用渲染器")
        return False

    try:
        shader = QgsRasterShader()
        color_ramp_shader = QgsColorRampShader()

        color_ramp_shader.setColorRampType(QgsColorRampShader.Discrete)

        color_ramp_items = []

        # 构建Newmark分档
        newmark_classes = build_newmark_classes(legend_values)

        for cls in newmark_classes:
            item = QgsColorRampShader.ColorRampItem(
                cls["max"],
                cls["color"],
                cls["label"]
            )
            color_ramp_items.append(item)

        color_ramp_shader.setColorRampItemList(color_ramp_items)
        shader.setRasterShaderFunction(color_ramp_shader)

        renderer = QgsSingleBandPseudoColorRenderer(
            raster_layer.dataProvider(),
            1,
            shader
        )

        raster_layer.setRenderer(renderer)
        raster_layer.triggerRepaint()

        logger.info('Newmark位移图层渲染器设置完成，使用10档分类')
        print("[信息] Newmark位移图层渲染器设置完成，使用10档分类")
        return True

    except Exception as exc:
        logger.error('应用Newmark渲染器失败: %s', exc, exc_info=True)
        print(f"[错误] 应用Newmark渲染器失败: {exc}")
        raise


def build_newmark_legend_list(legend_values):
    """
    构建Newmark位移图例列表（用于边界标签样式）

    工具函数：当前 _add_legend 中直接使用 legend_values 和 NEWMARK_COLORS 绘制图例，
    此函数保留作为工具函数，供外部调用或未来扩展使用。

    参数:
        legend_values (list): 11个分档值列表

    返回:
        tuple: (colors_list, labels_list)
               - colors_list: 10个颜色的RGBA元组列表
               - labels_list: 11个边界值标签列表（从0到最大值）
    """
    if legend_values is None:
        print("[信息] 图例值为None，不构建图例列表")
        return [], []

    # 颜色列表（10个色块）
    colors_list = []
    for i in range(10):
        color = NEWMARK_COLORS[i]
        color_rgba = (color.red(), color.green(), color.blue(), 255)
        colors_list.append(color_rgba)

    # 边界值标签列表（11个值：0, v1, v2, ..., v10）
    labels_list = []
    for val in legend_values:
        labels_list.append(format_legend_value(val))

    print(f"[信息] 构建Newmark位移图例列表完成，共 {len(colors_list)} 个色块, {len(labels_list)} 个标签")
    return colors_list, labels_list

# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据

    参数:
        kml_path (str): KML文件路径

    返回:
        list: 烈度圈数据列表，每项包含intensity和coords
    """
    if not kml_path or not os.path.exists(kml_path):
        logger.warning('KML文件不存在或未提供: %s', kml_path)
        print(f"[警告] KML文件不存在或未提供: {kml_path}")
        return []

    intensity_data = []
    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for pm in root.iter(ns + "Placemark"):
            name_elem = pm.find(ns + "name")
            if name_elem is None or name_elem.text is None:
                continue
            intensity = _extract_intensity_from_name(name_elem.text)
            if intensity is None:
                continue
            coords = _extract_kml_linestring_coords(pm, ns)
            if coords:
                intensity_data.append({
                    "intensity": intensity,
                    "coords": coords,
                })
        logger.info('从KML解析到 %d 个烈度圈', len(intensity_data))
        print(f"[信息] 从KML解析到 {len(intensity_data)} 个烈度圈")
    except Exception as exc:
        logger.error('解析KML文件失败: %s', exc, exc_info=True)
        print(f"[错误] 解析KML文件失败: {exc}")
        raise
    return intensity_data


def _extract_intensity_from_name(name):
    """
    从Placemark名称中提取烈度值
    """
    if not name:
        return None
    name = name.strip()
    m = re.match(r'(\d+)\s*度?', name)
    if m:
        return int(m.group(1))
    roman_map = {
        'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
        'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'XI': 11, 'XII': 12,
    }
    clean = re.sub(r'\s*度\s*', '', name).strip()
    if clean.upper() in roman_map:
        return roman_map[clean.upper()]
    return None


def _extract_kml_linestring_coords(placemark, ns):
    """从Placemark中提取LineString坐标"""
    coords_list = []
    for ls in placemark.iter(ns + "LineString"):
        coords_elem = ls.find(ns + "coordinates")
        if coords_elem is not None and coords_elem.text:
            parsed = _parse_kml_coords(coords_elem.text)
            coords_list.extend(parsed)
    return coords_list


def _parse_kml_coords(text):
    """解析KML坐标文本为(lon, lat)元组列表"""
    coords = []
    for part in text.strip().split():
        vals = part.split(",")
        if len(vals) >= 2:
            try:
                lon = float(vals[0])
                lat = float(vals[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def create_intensity_layer(intensity_data):
    """根据解析的烈度圈数据创建QGIS矢量图层"""
    if not intensity_data:
        return None

    try:
        layer = QgsVectorLayer("LineString?crs=EPSG:4326", "烈度圈", "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("intensity", QVariant.Int),
            QgsField("label", QVariant.String),
        ])
        layer.updateFields()

        features = []
        for item in intensity_data:
            intensity = item["intensity"]
            coords = item["coords"]
            if len(coords) < 2:
                continue
            points = [QgsPointXY(lon, lat) for lon, lat in coords]
            geom = QgsGeometry.fromPolylineXY(points)
            feat = QgsFeature(layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute("intensity", intensity)
            feat.setAttribute("label", int_to_roman(intensity))
            features.append(feat)

        provider.addFeatures(features)
        layer.updateExtents()

        halo_sl = QgsSimpleLineSymbolLayer()
        halo_sl.setColor(INTENSITY_HALO_COLOR)
        halo_sl.setWidth(INTENSITY_HALO_WIDTH_MM)
        halo_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
        halo_sl.setPenStyle(Qt.SolidLine)

        line_sl = QgsSimpleLineSymbolLayer()
        line_sl.setColor(INTENSITY_LINE_COLOR)
        line_sl.setWidth(INTENSITY_LINE_WIDTH_MM)
        line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
        line_sl.setPenStyle(Qt.SolidLine)

        symbol = QgsLineSymbol()
        symbol.changeSymbolLayer(0, halo_sl)
        symbol.appendSymbolLayer(line_sl)

        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        _setup_intensity_labels(layer)
        layer.triggerRepaint()

        logger.info('创建烈度圈图层，共 %d 条烈度线', len(features))
        print(f"[信息] 创建烈度圈图层，共 {len(features)} 条烈度线")
        return layer

    except Exception as exc:
        logger.error('创建烈度圈图层失败: %s', exc, exc_info=True)
        raise


def _setup_intensity_labels(layer):
    """配置烈度圈图层的标注"""
    settings = QgsPalLayerSettings()
    settings.fieldName = "label"
    settings.placement = Qgis.LabelPlacement.Line

    text_format = QgsTextFormat()
    font = QFont("Times New Roman", INTENSITY_LABEL_FONT_SIZE_PT)
    font.setBold(True)
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


# ============================================================
# 图层加载���数
# ============================================================

def load_newmark_raster_optimized(tif_path, extent):
    """
    优化加载Newmark位移TIF栅格图层（针对大文件优化）

    当 Dn.tif 范围内最大值 <= 0 或无法获取时，不应用渐变渲染器，legend_values 返回 None。
    """
    try:
        abs_path = resolve_path(tif_path)
        if not os.path.exists(abs_path):
            logger.error('Newmark位移底图文件不存在: %s', abs_path)
            print(f"[错误] Newmark位移底图文件不存在: {abs_path}")
            return None, None

        file_size_gb = os.path.getsize(abs_path) / (1024 * 1024 * 1024)
        logger.info('Newmark位移文件大小: %.2fGB', file_size_gb)
        print(f"[信息] Newmark位移文件大小: {file_size_gb:.2f}GB")

        legend_values = None
        # 标记是否已通过GDAL明确判断过max_value（区分"GDAL不可用/未计算"与"已计算但<=0"两种情况）
        max_value_determined = False

        if GDAL_AVAILABLE:
            logger.info('读取指定范围内的数据计算最大值...')
            print(f"[信息] 读取指定范围内的数据计算最大值...")
            try:
                max_value = get_raster_max_value_in_extent(abs_path, extent)
            except Exception as exc:
                logger.warning('读取范围内最大值失败，将尝试直接加载: %s', exc)
                print(f"[警告] 读取范围内最大值失败，将尝试直接加载: {exc}")
                max_value = None

            max_value_determined = True

            # 关键判断：max_value is None 或 max_value <= 0 时不应用渐变渲染器
            if max_value is None or max_value <= 0:
                logger.info('Dn.tif范围内最大值 %s (None或<=0)，不应用渐变渲染器', max_value)
                print(f"[信息] Dn.tif范围内最大值 {max_value} (None或<=0)，不应用渐变渲染器")
                legend_values = None
            else:
                legend_values = select_legend_column(max_value)

            if file_size_gb > 0.1:
                logger.info('文件较大，启用裁剪优化...')
                print(f"[信息] 文件较大，启用裁剪优化...")
                temp_manager = get_temp_manager()
                clipped_path = temp_manager.get_temp_file(suffix="_newmark_clipped.tif")
                try:
                    result_path = clip_raster_to_extent(abs_path, clipped_path, extent)
                except Exception as exc:
                    logger.warning('栅格裁剪失败，尝试直接加载原文件: %s', exc)
                    print(f"[警告] 栅格裁剪失败，尝试直接加载原文件: {exc}")
                    result_path = None

                if result_path and os.path.exists(result_path):
                    layer = QgsRasterLayer(result_path, "Newmark位移底图")
                    if layer.isValid():
                        logger.info('成功加载裁剪后的Newmark位移底图')
                        print(f"[信息] 成功加载裁剪后的Newmark位移底图")
                        if legend_values is not None:
                            apply_newmark_renderer(layer, legend_values)
                        return layer, legend_values
                    else:
                        logger.warning('裁剪后的栅格无效，尝试直接加载原文件')
                        print(f"[警告] 裁剪后的栅格无效，尝试直接加载原文件")
                else:
                    logger.warning('栅格裁剪失败，尝试直接加载原文件')
                    print(f"[警告] 栅格裁剪失败，尝试直接加载原文件")

        # 直接加载回退逻辑：
        # - max_value_determined=False 表示 GDAL 不可用，此时尝试整文件扫描
        # - max_value_determined=True 但裁剪失败时，legend_values 已由上方计算好，无需重新计算
        logger.info('直接加载Newmark位移底图...')
        print(f"[信息] 直接加载Newmark位移底图...")
        if not max_value_determined:
            # GDAL 不可用时，尝试通过整文件扫描获取最大值
            try:
                max_value = get_raster_max_value(tif_path)
            except Exception as exc:
                logger.warning('读取栅格最大值失败: %s', exc)
                print(f"[警告] 读取栅格最大值失败: {exc}")
                max_value = None

            if max_value is not None and max_value > 0:
                legend_values = select_legend_column(max_value)
            else:
                legend_values = None

        layer = QgsRasterLayer(abs_path, "Newmark位移底图")
        if not layer.isValid():
            logger.error('无法加载Newmark位移底图: %s', abs_path)
            print(f"[错误] 无法加载Newmark位移底图: {abs_path}")
            return None, None

        logger.info('成功加载Newmark位移底图: %s', abs_path)
        print(f"[信息] 成功加载Newmark位移底图: {abs_path}")
        if legend_values is not None:
            apply_newmark_renderer(layer, legend_values)
        return layer, legend_values

    except Exception as exc:
        logger.error('加载Newmark位移栅格失败: %s', exc, exc_info=True)
        raise


def load_vector_layer(shp_path, layer_name):
    """加载矢量图层（SHP文件）"""
    abs_path = resolve_path(shp_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 矢量文件不存在: {abs_path}")
        return None
    layer = QgsVectorLayer(abs_path, layer_name, "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载矢量图层: {abs_path}")
        return None
    print(f"[信息] 成功加载矢量图层 '{layer_name}': {abs_path}")
    return layer


# ============================================================
# 图层样式设置函数
# ============================================================

def style_province_layer(layer, epicenter_lon=None, epicenter_lat=None, extent=None):
    """设置省界图层样式。

    当传入震中坐标时，省界多边形图层仅绘制边界线，不配置标注；
    省份标注由独立的点图层（通过 create_province_label_layer 创建）负责，
    以便对靠近震中的省份名进行位置偏移，避免遮挡五角星。
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
    if epicenter_lon is None:
        # 无震中信息时，直接在省界图层上配置标注（兼容旧逻辑）
        _setup_province_labels(layer)
    # 有震中信息时，标注由 create_province_label_layer 返回的独立点图层负责
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
    print(f"[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """设置县界图层样式"""
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
    print(f"[信息] 县界图层样式设置完成")


def _setup_province_labels(layer):
    """配置省界图层标注（无偏移）。

    当无震中信息时直接在省界多边形图层上启用标注。
    有震中信息时，应使用 create_province_label_layer 创建独立点图层来处理标注偏移。
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


def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent):
    """创建省份标注点图层，支持震中附近省份标注自动偏移。

    通过将省份质心坐标写入独立的内存点图层来控制标注位置：
    - 当省份质心经纬度与震中经纬度完全相同时：向左和向下偏移3mm（转换为对应的度数）
    - 其余省份：标注点保持在质心位置不变
    点图层的标记符号设为完全透明，仅通过标注文字呈现省份名称。

    Args:
        province_layer: 省界多边形图层
        epicenter_lon: 震中经度（度）
        epicenter_lat: 震中纬度（度）
        extent: 地图范围（QgsRectangle），用于计算偏移量

    Returns:
        配置好标注的 QgsVectorLayer 内存点图层，失败时返回 None
    """
    field_name = _find_name_field(province_layer, ["省", "NAME", "name", "省名", "PROVINCE", "省份"])
    if not field_name:
        print("[警告] 未找到省份名称字段，跳过省份标注图层创建")
        return None

    # 计算3mm对应的度数偏移量
    # 根据地图范围计算：3mm / 地图宽度mm * 经度范围度 = 经度偏移量
    if extent is not None:
        map_width_deg = extent.width()   # 经度跨度（度）
        map_height_deg = extent.height() # 纬度跨度（度）
    else:
        map_width_deg = 10.0  # 默认值
        map_height_deg = 10.0
    offset_mm = 3.0  # 偏移量：3mm
    lon_offset_deg = offset_mm / MAP_WIDTH_MM * map_width_deg   # 向左偏移（经度减小）
    lat_offset_deg = offset_mm / MAP_WIDTH_MM * map_height_deg  # 向下偏移（纬度减小）

    # 判断经纬度相同的容差（用于浮点数比较）
    coord_epsilon = 1e-6

    # 创建内存点图层，字段 province_name 存储省份名称
    label_layer = QgsVectorLayer("Point?crs=EPSG:4326", "省份标注", "memory")
    if not label_layer.isValid():
        print("[错误] 无法创建省份标注内存图层（QgsVectorLayer 创建失败，请检查 QGIS 是否正确初始化）")
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

        # 判断质心是否与震中经纬度完全相同（在容差范围内）
        px, py = cx, cy
        if abs(cx - epicenter_lon) < coord_epsilon and abs(cy - epicenter_lat) < coord_epsilon:
            # 向左偏移（经度减小）和向下偏移（纬度减小）3mm
            px = cx - lon_offset_deg
            py = cy - lat_offset_deg
            offset_count += 1
            print(f"[信息] 省份标注偏移：质心({cx:.6f}, {cy:.6f}) -> 偏移后({px:.6f}, {py:.6f})")

        prov_name = feat[field_name]
        new_feat = QgsFeature(layer_fields)
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
        new_feat.setAttribute("province_name", prov_name)
        feats_to_add.append(new_feat)

    print(f"[信息] 省份标注：共 {len(feats_to_add)} 个省份，其中 {offset_count} 个省份因与震中重合进行了偏移（向左向下3mm）")
    total_input = province_layer.featureCount()
    skipped = total_input - len(feats_to_add)
    if skipped > 0:
        print(f"[信息] 省份标注：{skipped} 个省份因空几何或空质心被跳过（输入共 {total_input} 个）")
    if feats_to_add:
        success, added_feats = provider.addFeatures(feats_to_add)
        if not success:
            print(f"[警告] 省份标注要素添加失败（期望添加 {len(feats_to_add)} 个，实际添加 {len(added_feats)} 个）")
    else:
        print(f"[警告] 未找到任何有效省份要素（省界图层共 {total_input} 个要素，均因无效几何被跳过）")
    label_layer.updateExtents()

    # 设置透明点标记渲染器（点不可见，只显示标注文字）
    marker_symbol = QgsMarkerSymbol.createSimple({
        "name": "circle",
        "size": "0",
        "color": "0,0,0,0",
        "outline_color": "0,0,0,0",
    })
    label_layer.setRenderer(QgsSingleSymbolRenderer(marker_symbol))

    # 配置标注样式（与 _setup_province_labels 保持一致）
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

    print(f"[信息] 省份标注图层创建完成，共 {label_layer.featureCount()} 个标注点，"
          f"偏移量 3mm（经度: {lon_offset_deg:.6f}°，纬度: {lat_offset_deg:.6f}°）")
    return label_layer


# ============================================================
# 震中和图例图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """创建震中标记图层：红色五角星+白边"""
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


def create_city_point_layer(extent):
    """加载地级市点位数据"""
    abs_path = resolve_path(CITY_POINTS_SHP_PATH)
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    symbol_size_mm = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0

    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    inner_sl = QgsSimpleMarkerSymbolLayer()
    inner_sl.setShape(Qgis.MarkerShape.Circle)
    inner_sl.setColor(QColor(0, 0, 0))
    inner_sl.setStrokeColor(QColor(0, 0, 0))
    inner_sl.setStrokeWidth(0)
    inner_sl.setSize(symbol_size_mm * 0.45)
    inner_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    symbol = QgsMarkerSymbol()
    symbol.changeSymbolLayer(0, bg_sl)
    symbol.appendSymbolLayer(outer_sl)
    symbol.appendSymbolLayer(inner_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成")
    return layer


def create_intensity_legend_layer():
    """创建烈度图例用的线图层"""
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "烈度", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(INTENSITY_LEGEND_COLOR)
    line_sl.setWidth(INTENSITY_LEGEND_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_province_legend_layer():
    """创建省界图例用的线图层"""
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "省界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(PROVINCE_COLOR)
    line_sl.setWidth(PROVINCE_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.SolidLine)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_city_legend_layer():
    """创建市界图例用的线图层"""
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "市界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(CITY_COLOR)
    line_sl.setWidth(CITY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.CustomDashLine)
    dash_pattern = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]
    line_sl.setCustomDashVector(dash_pattern)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


def create_county_legend_layer():
    """创建县界图例用的线图层"""
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "县界", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()

    line_sl = QgsSimpleLineSymbolLayer()
    line_sl.setColor(COUNTY_COLOR)
    line_sl.setWidth(COUNTY_LINE_WIDTH_MM)
    line_sl.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    line_sl.setPenStyle(Qt.CustomDashLine)
    dash_pattern = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]
    line_sl.setCustomDashVector(dash_pattern)

    symbol = QgsLineSymbol()
    symbol.changeSymbolLayer(0, line_sl)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm,
                        legend_values=None, ordered_layers=None, show_legend_text=True):
    """创建QGIS打印布局"""
    try:
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("地震Newmark位移分布图")
        layout.setUnits(QgsUnitTypes.LayoutMillimeters)

        output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm, QgsUnitTypes.LayoutMillimeters))

        map_left = BORDER_LEFT_MM
        map_top = BORDER_TOP_MM

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

        layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
        if layers_to_set:
            map_item.setLayers(layers_to_set)
            map_item.setKeepLayerSet(True)
        map_item.invalidateCache()

        _setup_map_grid(map_item, extent)
        _add_north_arrow(layout, map_height_mm)
        _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)
        _add_legend(layout, map_item, project, map_height_mm, output_height_mm, legend_values, show_legend_text)

        return layout

    except Exception as exc:
        logger.error('创建打印布局失败: %s', exc, exc_info=True)
        raise


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


def _add_north_arrow(layout, map_height_mm):
    """添加指北针"""
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
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

    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_newmark_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    padding_x = 1.0
    padding_y = 0.5
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x + padding_x, arrow_y + padding_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM - padding_x * 2,
                                            NORTH_ARROW_HEIGHT_MM - padding_y * 2, QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """添加比例尺"""
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


def _add_legend(layout, map_item, project, map_height_mm, output_height_mm,
                legend_values=None, show_legend_text=True):
    """
    添加图例
    - 上部：震中/地级市/省界/市界/县界/烈度（3行2列）
    - 下部：Newmark位移图例（色块连接，标签在边界位置）
    """
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

    # 公共文本格式
    title_format = QgsTextFormat()
    title_format.setFont(QFont("SimHei", LEGEND_TITLE_FONT_SIZE_PT))
    title_format.setSize(LEGEND_TITLE_FONT_SIZE_PT)
    title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    title_format.setColor(QColor(0, 0, 0))

    basic_item_format = QgsTextFormat()
    basic_item_format.setFont(QFont("SimSun", BASIC_LEGEND_FONT_SIZE_PT))
    basic_item_format.setSize(BASIC_LEGEND_FONT_SIZE_PT)
    basic_item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    basic_item_format.setColor(QColor(0, 0, 0))

    newmark_format = QgsTextFormat()
    newmark_format.setFont(QFont(LEGEND_FONT_TIMES_NEW_ROMAN, NEWMARK_LEGEND_ITEM_FONT_SIZE_PT))
    newmark_format.setSize(NEWMARK_LEGEND_ITEM_FONT_SIZE_PT)
    newmark_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    newmark_format.setColor(QColor(0, 0, 0))

    # 图例背景
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

    # 标题 "图  例"
    title_label = QgsLayoutItemLabel(layout)
    title_label.setText("图  例")
    title_label.setTextFormat(title_format)
    title_label.attemptMove(QgsLayoutPoint(legend_x, legend_y + 1.0, QgsUnitTypes.LayoutMillimeters))
    title_label.attemptResize(QgsLayoutSize(legend_width, 5.0, QgsUnitTypes.LayoutMillimeters))
    title_label.setHAlign(Qt.AlignHCenter)
    title_label.setVAlign(Qt.AlignVCenter)
    title_label.setFrameEnabled(False)
    title_label.setBackgroundEnabled(False)
    layout.addLayoutItem(title_label)

    # 上部图例：3行2列
    top_legend_start_y = legend_y + 7.0
    col_count = 2
    row_count = 3
    left_pad = 2.0
    right_pad = 2.0
    col_gap = 1.0
    row_height = BASIC_LEGEND_ROW_HEIGHT_MM
    icon_width = 4.0
    icon_height = 2.5
    icon_text_gap = 1.0

    available_width = legend_width - left_pad - right_pad - (col_count - 1) * col_gap
    col_width = available_width / col_count

    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
        ("烈度", "solid_line_black"),
    ]

    for idx, (display_name, draw_type) in enumerate(legend_items):
        row = idx // col_count
        col = idx % col_count
        item_x = legend_x + left_pad + col * (col_width + col_gap)
        item_y = top_legend_start_y + row * row_height
        icon_center_y = item_y + row_height / 2.0

        if draw_type == "star":
            _draw_star_icon(layout, item_x, icon_center_y, icon_width, icon_height)
        elif draw_type == "circle":
            _draw_city_icon(layout, item_x, icon_center_y, icon_width, icon_height)
        elif draw_type == "solid_line":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM, solid=True)
        elif draw_type == "dash_line_city":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_line_county":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)
        elif draw_type == "solid_line_black":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            INTENSITY_LEGEND_COLOR, INTENSITY_LEGEND_LINE_WIDTH_MM, solid=True)

        text_x = item_x + icon_width + icon_text_gap
        text_width = col_width - icon_width - icon_text_gap

        text_label = QgsLayoutItemLabel(layout)
        text_label.setText(display_name)
        text_label.setTextFormat(basic_item_format)
        text_label.attemptMove(QgsLayoutPoint(text_x, item_y + 0.5, QgsUnitTypes.LayoutMillimeters))
        text_label.attemptResize(QgsLayoutSize(text_width, row_height - 1.0, QgsUnitTypes.LayoutMillimeters))
        text_label.setHAlign(Qt.AlignLeft)
        text_label.setVAlign(Qt.AlignVCenter)
        text_label.setFrameEnabled(False)
        text_label.setBackgroundEnabled(False)
        layout.addLayoutItem(text_label)

    top_legend_height = row_count * row_height

    # Newmark位移图例 - 边界标签样式
    if legend_values and show_legend_text:
        newmark_title_y = top_legend_start_y + top_legend_height + 2.0

        # 标题 "Newmark位移(cm)" - Newmark和cm使用Times New Roman，位移()使用SimHei
        newmark_en_format = QgsTextFormat()
        newmark_en_font = QFont(LEGEND_FONT_TIMES_NEW_ROMAN)
        newmark_en_font.setPointSizeF(10.0)
        newmark_en_format.setFont(newmark_en_font)
        newmark_en_format.setSize(10.0)
        newmark_en_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        newmark_en_format.setColor(QColor(0, 0, 0))

        newmark_title_cn_format = QgsTextFormat()
        newmark_title_cn_font = QFont("SimHei")
        newmark_title_cn_font.setPointSizeF(10.0)
        newmark_title_cn_format.setFont(newmark_title_cn_font)
        newmark_title_cn_format.setSize(10.0)
        newmark_title_cn_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        newmark_title_cn_format.setColor(QColor(0, 0, 0))

        # 标题 "Newmark位移(cm)" 分四段绘制：
        # "Newmark" -> Times New Roman, "位移(" -> SimHei, "cm" -> Times New Roman, ")" -> SimHei
        newmark_text = "Newmark"
        cn_text_left = "位移("
        cm_text = "cm"
        cn_text_right = ")"
        newmark_width_est = len(newmark_text) * 2.2  # ~15.4mm
        cn_left_width_est = 8.5   # 位移( in SimHei
        cm_width_est = 4.0        # cm in Times New Roman
        cn_right_width_est = 1.8  # ) in SimHei
        total_width_est = newmark_width_est + cn_left_width_est + cm_width_est + cn_right_width_est
        title_start_x = legend_x + (legend_width - total_width_est) / 2.0

        newmark_en_label = QgsLayoutItemLabel(layout)
        newmark_en_label.setText(newmark_text)
        newmark_en_label.setTextFormat(newmark_en_format)
        newmark_en_label.attemptMove(QgsLayoutPoint(title_start_x, newmark_title_y, QgsUnitTypes.LayoutMillimeters))
        newmark_en_label.attemptResize(QgsLayoutSize(newmark_width_est + 1, 5.0, QgsUnitTypes.LayoutMillimeters))
        newmark_en_label.setHAlign(Qt.AlignLeft)
        newmark_en_label.setVAlign(Qt.AlignVCenter)
        newmark_en_label.setFrameEnabled(False)
        newmark_en_label.setBackgroundEnabled(False)
        layout.addLayoutItem(newmark_en_label)

        cn_left_label = QgsLayoutItemLabel(layout)
        cn_left_label.setText(cn_text_left)
        cn_left_label.setTextFormat(newmark_title_cn_format)
        cn_left_label.attemptMove(QgsLayoutPoint(title_start_x + newmark_width_est, newmark_title_y,
                                                 QgsUnitTypes.LayoutMillimeters))
        cn_left_label.attemptResize(QgsLayoutSize(cn_left_width_est + 1, 5.0, QgsUnitTypes.LayoutMillimeters))
        cn_left_label.setHAlign(Qt.AlignLeft)
        cn_left_label.setVAlign(Qt.AlignVCenter)
        cn_left_label.setFrameEnabled(False)
        cn_left_label.setBackgroundEnabled(False)
        layout.addLayoutItem(cn_left_label)

        cm_label = QgsLayoutItemLabel(layout)
        cm_label.setText(cm_text)
        cm_label.setTextFormat(newmark_en_format)
        # 轻微上移，修正 Times New Roman 字母的视觉基线偏低问题
        cm_label.attemptMove(QgsLayoutPoint(title_start_x + newmark_width_est + cn_left_width_est,
                                            newmark_title_y - 0.4, QgsUnitTypes.LayoutMillimeters))
        cm_label.attemptResize(QgsLayoutSize(cm_width_est + 1, 5.0, QgsUnitTypes.LayoutMillimeters))
        cm_label.setHAlign(Qt.AlignLeft)
        cm_label.setVAlign(Qt.AlignVCenter)
        cm_label.setFrameEnabled(False)
        cm_label.setBackgroundEnabled(False)
        layout.addLayoutItem(cm_label)

        cn_right_label = QgsLayoutItemLabel(layout)
        cn_right_label.setText(cn_text_right)
        cn_right_label.setTextFormat(newmark_title_cn_format)
        cn_right_label.attemptMove(
            QgsLayoutPoint(title_start_x + newmark_width_est + cn_left_width_est + cm_width_est,
                           newmark_title_y, QgsUnitTypes.LayoutMillimeters))
        cn_right_label.attemptResize(QgsLayoutSize(cn_right_width_est + 1, 5.0, QgsUnitTypes.LayoutMillimeters))
        cn_right_label.setHAlign(Qt.AlignLeft)
        cn_right_label.setVAlign(Qt.AlignVCenter)
        cn_right_label.setFrameEnabled(False)
        cn_right_label.setBackgroundEnabled(False)
        layout.addLayoutItem(cn_right_label)

        # 色块和标签区域
        colorbar_start_y = newmark_title_y + 6.0
        colorbar_width = 8.0
        colorbar_height = NEWMARK_LEGEND_ROW_HEIGHT_MM  # 每个色块高度
        colorbar_left_pad = 3.0
        label_gap = 2.0  # 色块和标签之间的间距

        num_colors = 10
        total_colorbar_height = num_colors * colorbar_height

        # 检查是否超出图例区域
        if colorbar_start_y + total_colorbar_height > legend_y + legend_height - 2.0:
            # 调整色块高度以适应
            available_height = legend_y + legend_height - 2.0 - colorbar_start_y
            colorbar_height = available_height / num_colors

        total_colorbar_height = num_colors * colorbar_height

        # 绘制10个连接的色块
        for i in range(num_colors):
            color = NEWMARK_COLORS[i]
            color_str = f"{color.red()},{color.green()},{color.blue()},255"

            box_y = colorbar_start_y + i * colorbar_height

            color_box = QgsLayoutItemShape(layout)
            color_box.setShapeType(QgsLayoutItemShape.Rectangle)
            color_box.attemptMove(QgsLayoutPoint(legend_x + colorbar_left_pad, box_y, QgsUnitTypes.LayoutMillimeters))
            color_box.attemptResize(QgsLayoutSize(colorbar_width, colorbar_height, QgsUnitTypes.LayoutMillimeters))
            box_symbol = QgsFillSymbol.createSimple({
                'color': color_str,
                'outline_style': 'no',
            })
            color_box.setSymbol(box_symbol)
            color_box.setFrameEnabled(False)
            layout.addLayoutItem(color_box)

        # 在色块周围添加边框
        border_box = QgsLayoutItemShape(layout)
        border_box.setShapeType(QgsLayoutItemShape.Rectangle)
        border_box.attemptMove(QgsLayoutPoint(legend_x + colorbar_left_pad, colorbar_start_y,
                                              QgsUnitTypes.LayoutMillimeters))
        border_box.attemptResize(QgsLayoutSize(colorbar_width, total_colorbar_height, QgsUnitTypes.LayoutMillimeters))
        border_symbol = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',
            'outline_color': '80,80,80,255',
            'outline_width': '0.15',
            'outline_width_unit': 'MM',
        })
        border_box.setSymbol(border_symbol)
        border_box.setFrameEnabled(False)
        layout.addLayoutItem(border_box)

        # 绘制10个边界标签（从第二个值开始，标签与色块底部边界对齐）
        label_x = legend_x + colorbar_left_pad + colorbar_width + label_gap
        label_width = legend_width - colorbar_left_pad - colorbar_width - label_gap - 2.0
        label_height_mm = 4.0  # 标签高度

        for i in range(1, 11):
            # 标签的Y位置：与色块底部边界对齐
            label_y = colorbar_start_y + i * colorbar_height - label_height_mm / 2.0

            # 获取对应的数值标签
            value = legend_values[i]
            label_text = format_legend_value(value)

            value_label = QgsLayoutItemLabel(layout)
            value_label.setText(label_text)
            value_label.setTextFormat(newmark_format)
            value_label.attemptMove(QgsLayoutPoint(label_x, label_y, QgsUnitTypes.LayoutMillimeters))
            value_label.attemptResize(QgsLayoutSize(label_width, label_height_mm, QgsUnitTypes.LayoutMillimeters))
            value_label.setHAlign(Qt.AlignLeft)
            value_label.setVAlign(Qt.AlignVCenter)
            value_label.setFrameEnabled(False)
            value_label.setBackgroundEnabled(False)
            layout.addLayoutItem(value_label)

        print(f"[信息] Newmark位移图例添加完成，共10个色块，10个边界标签")
    elif legend_values and not show_legend_text:
        print("[信息] Dn最大值超过1000，不显示Newmark位移图例文字")
    else:
        print("[信息] 无Newmark位移数据，跳过Newmark位移图例")

    print("[信息] 图例添加完成")


def _draw_star_icon(layout, x, center_y, width, height):
    """在图例中绘制红色五角星图标"""
    star_label = QgsLayoutItemLabel(layout)
    star_label.setText("★")
    star_format = QgsTextFormat()
    star_format.setFont(QFont("SimSun", 10))
    star_format.setSize(10)
    star_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    star_format.setColor(EPICENTER_COLOR)
    star_label.setTextFormat(star_format)
    star_label.attemptMove(QgsLayoutPoint(x, center_y - height / 2.0 - 0.5, QgsUnitTypes.LayoutMillimeters))
    star_label.attemptResize(QgsLayoutSize(width, height + 1.0, QgsUnitTypes.LayoutMillimeters))
    star_label.setHAlign(Qt.AlignHCenter)
    star_label.setVAlign(Qt.AlignVCenter)
    star_label.setFrameEnabled(False)
    star_label.setBackgroundEnabled(False)
    layout.addLayoutItem(star_label)


def _draw_city_icon(layout, x, center_y, width, height):
    """在图例中绘制地级市圆点图标"""
    icon_size = min(width, height) * 0.6
    center_x = x + width / 2.0

    outer_circle = QgsLayoutItemShape(layout)
    outer_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    outer_circle.attemptMove(QgsLayoutPoint(center_x - icon_size / 2.0, center_y - icon_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    outer_circle.attemptResize(QgsLayoutSize(icon_size, icon_size, QgsUnitTypes.LayoutMillimeters))
    outer_symbol = QgsFillSymbol.createSimple({
        'color': '255,255,255,255',
        'outline_color': '0,0,0,255',
        'outline_width': '0.15',
        'outline_width_unit': 'MM',
    })
    outer_circle.setSymbol(outer_symbol)
    outer_circle.setFrameEnabled(False)
    layout.addLayoutItem(outer_circle)

    inner_size = icon_size * 0.4
    inner_circle = QgsLayoutItemShape(layout)
    inner_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    inner_circle.attemptMove(QgsLayoutPoint(center_x - inner_size / 2.0, center_y - inner_size / 2.0,
                                            QgsUnitTypes.LayoutMillimeters))
    inner_circle.attemptResize(QgsLayoutSize(inner_size, inner_size, QgsUnitTypes.LayoutMillimeters))
    inner_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    inner_circle.setSymbol(inner_symbol)
    inner_circle.setFrameEnabled(False)
    layout.addLayoutItem(inner_circle)


def _draw_line_icon(layout, x, center_y, width, color, line_width_mm, solid=True):
    """在图例中绘制实线图标"""
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    line_height = max(line_width_mm, 0.5)
    line_shape.attemptMove(QgsLayoutPoint(x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
    line_shape.attemptResize(QgsLayoutSize(width, line_height, QgsUnitTypes.LayoutMillimeters))
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    line_symbol = QgsFillSymbol.createSimple({
        'color': color_str,
        'outline_style': 'no',
    })
    line_shape.setSymbol(line_symbol)
    line_shape.setFrameEnabled(False)
    layout.addLayoutItem(line_shape)


def _draw_dash_line_icon(layout, x, center_y, width, color, line_width_mm, dash_gap_mm):
    """在图例中绘制虚线图标"""
    line_height = max(line_width_mm, 0.5)
    color_str = f"{color.red()},{color.green()},{color.blue()},255"
    dash_length_mm = max(dash_gap_mm * 3.5, 0.8)
    pattern_length = dash_length_mm + dash_gap_mm

    current_x = x
    while current_x < x + width:
        actual_dash_length = min(dash_length_mm, x + width - current_x)
        if actual_dash_length <= 0:
            break
        dash_shape = QgsLayoutItemShape(layout)
        dash_shape.setShapeType(QgsLayoutItemShape.Rectangle)
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(QgsLayoutSize(actual_dash_length, line_height, QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)
        current_x += pattern_length


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_newmark_map(longitude, latitude, magnitude,
                                    output_path="output_newmark_map.png",
                                    kml_path=None, dn_tif_path=None):
    """生成地震Newmark位移分布图（主入口函数）"""
    logger.info('开始生成Newmark位移分布图: lon=%.4f lat=%.4f M=%.1f output=%s',
                longitude, latitude, magnitude, output_path)
    try:
        return _generate_earthquake_newmark_map_impl(
            longitude, latitude, magnitude, output_path, kml_path, dn_tif_path
        )
    except Exception as exc:
        logger.error('生成Newmark位移分布图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_newmark_map_impl(longitude, latitude, magnitude,
                                           output_path, kml_path, dn_tif_path):
    """generate_earthquake_newmark_map 的实际实现。"""
    print("=" * 60)
    print(f"[开始] 生成地震Newmark位移分布图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print(f"  GDAL可用: {GDAL_AVAILABLE}")
    print("=" * 60)

    tif_path = dn_tif_path if dn_tif_path else DN_TIF_PATH

    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    logger.info('震级配置: 范围%dkm, 比例尺1:%d', config['map_size_km'], scale)
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    extent = calculate_extent(longitude, latitude, half_size_km)
    logger.info('地图范围: %s', extent.toString())
    print(f"[信息] 地图范围: {extent.toString()}")

    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    logger.info('地图尺寸: %.1fmm x %.1fmm', MAP_WIDTH_MM, map_height_mm)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    temp_manager = get_temp_manager()
    temp_annotation_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_temp_annotation_newmark.png")

    try:
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        # 可降级：天地图注记下载失败不影响整体流程
        annotation_raster = None
        try:
            annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px, temp_annotation_path)
        except Exception as exc:
            logger.warning('天地图注记下载失败，跳过注记图层: %s', exc)
            print(f"[警告] 天地图注记下载失败，跳过注记图层: {exc}")

        # 可降级：Newmark位移栅格加载失败不影响整体流程
        newmark_layer = None
        legend_values = None
        try:
            newmark_layer, legend_values = load_newmark_raster_optimized(tif_path, extent)
            if newmark_layer:
                project.addMapLayer(newmark_layer)
        except Exception as exc:
            logger.warning('加载Newmark位移底图失败，跳过: %s', exc)
            print(f"[警告] 加载Newmark位移底图失败，跳过: {exc}")

        # show_legend_text 由 legend_values 决定：max_value <= 0 时 legend_values 为 None
        show_legend_text = legend_values is not None

        # 可降级：矢量边界图层加载失败不影响整体流程
        county_layer = None
        try:
            county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界_地图")
            if county_layer:
                style_county_layer(county_layer)
                project.addMapLayer(county_layer)
        except Exception as exc:
            logger.warning('加载县界图层失败，跳过: %s', exc)
            print(f"[警告] 加载县界图层失败，跳过: {exc}")

        city_layer = None
        try:
            city_layer = load_vector_layer(CITY_SHP_PATH, "市界_地图")
            if city_layer:
                style_city_layer(city_layer)
                project.addMapLayer(city_layer)
        except Exception as exc:
            logger.warning('加载市界图层失败，跳过: %s', exc)
            print(f"[警告] 加载市界图层失败，跳过: {exc}")

        province_layer = None
        try:
            province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界_地图")
            if province_layer:
                style_province_layer(province_layer, longitude, latitude, extent)
                project.addMapLayer(province_layer)
        except Exception as exc:
            logger.warning('加载省界图层失败，跳过: %s', exc)
            print(f"[警告] 加载省界图层失败，跳过: {exc}")

        # 创建独立的省份标注点图层，对靠近震中的省份名进行偏移，避免遮挡五角星
        province_label_layer = None
        if province_layer:
            try:
                province_label_layer = create_province_label_layer(
                    province_layer, longitude, latitude, extent
                )
                if province_label_layer:
                    project.addMapLayer(province_label_layer, False)
                    print(f"[信息] 省份标注图层已添加到项目，要素数量: {province_label_layer.featureCount()}")
                else:
                    print("[警告] 省份标注图层创建失败，回退到在省界图层上直接配置标注")
                    _setup_province_labels(province_layer)
            except Exception as exc:
                logger.warning('创建省份标注图层失败，跳过: %s', exc)
                print(f"[警告] 创建省份标注图层失败，回退到在省界图层上直接配置标注: {exc}")
                try:
                    _setup_province_labels(province_layer)
                except Exception as fallback_exc:
                    logger.warning('回退标注配置也失败: %s', fallback_exc)

        city_point_layer = None
        try:
            city_point_layer = create_city_point_layer(extent)
            if city_point_layer:
                project.addMapLayer(city_point_layer)
        except Exception as exc:
            logger.warning('加载地级市点位图层失败，跳过: %s', exc)
            print(f"[警告] 加载地级市点位图层失败，跳过: {exc}")

        province_legend_layer = None
        try:
            province_legend_layer = create_province_legend_layer()
            if province_legend_layer:
                project.addMapLayer(province_legend_layer)
        except Exception as exc:
            logger.warning('创建省界图例图层失败，跳过: %s', exc)

        city_legend_layer = None
        try:
            city_legend_layer = create_city_legend_layer()
            if city_legend_layer:
                project.addMapLayer(city_legend_layer)
        except Exception as exc:
            logger.warning('创建市界图例图层失败，跳过: %s', exc)

        county_legend_layer = None
        try:
            county_legend_layer = create_county_legend_layer()
            if county_legend_layer:
                project.addMapLayer(county_legend_layer)
        except Exception as exc:
            logger.warning('创建县界图例图层失败，跳过: %s', exc)

        intensity_legend_layer = None
        try:
            intensity_legend_layer = create_intensity_legend_layer()
            if intensity_legend_layer:
                project.addMapLayer(intensity_legend_layer)
        except Exception as exc:
            logger.warning('创建烈度图例图层失败，跳过: %s', exc)

        intensity_data = []
        intensity_layer = None
        if kml_path:
            try:
                abs_kml = kml_path
                if not os.path.isabs(kml_path):
                    abs_kml = resolve_path(kml_path)
                intensity_data = parse_intensity_kml(abs_kml)
                if intensity_data:
                    intensity_layer = create_intensity_layer(intensity_data)
                    if intensity_layer:
                        project.addMapLayer(intensity_layer)
            except Exception as exc:
                logger.warning('加载烈度圈图层失败，跳过: %s', exc)
                print(f"[警告] 加载烈度圈图层失败，跳过: {exc}")

        epicenter_layer = None
        try:
            epicenter_layer = create_epicenter_layer(longitude, latitude)
            if epicenter_layer:
                project.addMapLayer(epicenter_layer)
        except Exception as exc:
            logger.warning('创建震中图层失败，跳过: %s', exc)
            print(f"[警告] 创建震中图层失败，跳过: {exc}")

        if annotation_raster:
            project.addMapLayer(annotation_raster)

        ordered_layers = [lyr for lyr in [
            epicenter_layer,
            annotation_raster,
            intensity_layer,
            city_point_layer,
            province_label_layer,
            province_layer,
            city_layer,
            county_layer,
            newmark_layer,
        ] if lyr is not None]

        # 关键步骤：布局创建失败则抛出异常
        try:
            layout = create_print_layout(project, longitude, latitude, magnitude,
                                         extent, scale, map_height_mm, legend_values,
                                         ordered_layers, show_legend_text)
        except Exception as exc:
            logger.error('创建打印布局失败: %s', exc, exc_info=True)
            raise

        # 关键步骤：导出失败则抛出异常
        try:
            result = export_layout_to_png(layout, output_path, OUTPUT_DPI)
        except Exception as exc:
            logger.error('导出PNG失败: %s', exc, exc_info=True)
            raise

    finally:
        temp_manager.cleanup()

        svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_newmark_temp.svg")
        if os.path.exists(svg_temp):
            try:
                os.remove(svg_temp)
            except OSError:
                pass

        if os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw_path = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw_path):
                    os.remove(pgw_path)
            except OSError:
                pass

    print("=" * 60)
    if result:
        logger.info('Newmark位移分布图已输出: %s', result)
        print(f"[完成] Newmark位移分布图已输出: {result}")
    else:
        logger.error('Newmark位移分布图输出失败')
        print("[失败] Newmark位移分布图输出失败")
    print("=" * 60)
    return result


def export_layout_to_png(layout, output_path, dpi=150):
    """将打印布局导出为PNG图片"""
    try:
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
            logger.info('PNG导出成功: %s', abs_path)
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
            logger.error('PNG导出失败: %s', msg)
            print(f"[错误] PNG导出失败: {msg}")
            return None

    except Exception as exc:
        logger.error('PNG导出异常: %s', exc, exc_info=True)
        raise


# ============================================================
# 测试方法
# ============================================================

def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("运行 earthquake_newmark_map 全部测试")
    print("=" * 60)

    # 测试震级配置
    print("\n--- 测试: get_magnitude_config ---")
    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000
    print(f"  M4.5 -> 比例尺1:{config_s['scale']} ✓")

    config_m = get_magnitude_config(6.5)
    assert config_m["scale"] == 500000
    print(f"  M6.5 -> 比��尺1:{config_m['scale']} ✓")

    config_l = get_magnitude_config(7.5)
    assert config_l["scale"] == 1500000
    print(f"  M7.5 -> 比例尺1:{config_l['scale']} ✓")

    # 测试范围计算
    print("\n--- 测试: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum()
    print(f"  15km半径范围计算正确 ✓")

    # 测试罗马数字
    print("\n--- 测试: int_to_roman ---")
    assert int_to_roman(4) == "IV"
    assert int_to_roman(9) == "IX"
    print("  罗马数字转换正确 ✓")

    # 测试图例值选择
    print("\n--- 测试: select_legend_column ---")
    legend_5 = select_legend_column(3)
    assert legend_5[10] == 5.0
    print(f"  最大值3 -> 选择5的列 ✓")

    legend_10 = select_legend_column(8)
    assert legend_10[10] == 10
    print(f"  最大值8 -> 选择10的列 ✓")

    legend_over = select_legend_column(1500)
    assert legend_over is None
    print(f"  最大值1500 -> 返回None ✓")

    legend_zero = select_legend_column(0)
    assert legend_zero is None
    print(f"  最大值0 -> 返回None ✓")

    legend_neg = select_legend_column(-1)
    assert legend_neg is None
    print(f"  最大值-1 -> 返回None ✓")

    # 测试数值格式化
    print("\n--- 测试: format_legend_value ---")
    assert format_legend_value(0) == "0"
    assert format_legend_value(0.5) == "0.50"
    assert format_legend_value(10) == "10"
    print("  数值格式化正确 ✓")

    print("\n" + "=" * 60)
    print("全部测试执行完成")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        run_all_tests()
    elif len(sys.argv) >= 4:
        try:
            lon = float(sys.argv[1])
            lat = float(sys.argv[2])
            mag = float(sys.argv[3])
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_newmark_M{mag}_{lon}_{lat}.png"
            kml = sys.argv[5] if len(sys.argv) > 5 else None
            dn_tif = sys.argv[6] if len(sys.argv) > 6 else None
            generate_earthquake_newmark_map(lon, lat, mag, out, kml, dn_tif)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_newmark_map.py <经度> <纬度> <震级> [输出文件名] [kml路径] [Dn_tif路径]")
    else:
        print("使用默认参数运行...")
        generate_earthquake_newmark_map(
            longitude=103.36, latitude=34.09,
            magnitude=7.0, output_path="earthquake_newmark_M7.0.png"
        )