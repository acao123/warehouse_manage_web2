# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震滑坡危险性评估图生成脚本
参考 earthquake_newmark_map.py 的布局、指北针、经纬度、比例尺、烈度圈、省市加载方式。

完全适配QGIS 3.40.15 API。

危险性评估图说明：
- 读取Dn.tif文件获取Newmark位移值
- 对每个栅格像素按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算危险性概率
- Dn <= 0.1cm 的栅格直接判定为不危险（P=0）
- 使用自然断点法（Jenks）将概率值分为5类：低度危险区、较低危险区、中等危险区、较高危险区、高度危险区
- 颜色从绿色向红色过渡
- 图例色块分开显示
- 统计各危险等级面积和占比并返回

优化说明：
- 针对大文件TIF进行优化，只裁剪加载需要范围内的数据
- 先用GDAL计算危险性概率栅格，再加载到QGIS中渲染
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
logger = logging.getLogger('report.core.earthquake_hazard_map')

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

# GDAL导入（用于栅格裁剪、读取数据和危险性概率计算）
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
TIANDITU_TK = '1ef76ef90c6eb961cb49618f9b1a399d'

# 数据文件路径（优先从 Django settings 读取）
_DEFAULT_BASE = "../../data/geology/"

DN_TIF_PATH ="../../data/geology/ia/Dn.tif"
# DN_TIF_PATH ='../../data/geology/ia/Dn.tif'
PROVINCE_SHP_PATH =  _DEFAULT_BASE +'省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp'
CITY_SHP_PATH =  _DEFAULT_BASE+'省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp'
COUNTY_SHP_PATH = _DEFAULT_BASE + '省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp'
# 地级市点位数据
CITY_POINTS_SHP_PATH = _DEFAULT_BASE + '2023地级市点位数据/地级市点位数据.shp'


# === 布局尺寸常量 ===
MAP_TOTAL_WIDTH_MM = 220.0          # 布局总宽度（毫米）
LEGEND_WIDTH_MM = 50.0              # 图例区域宽度（毫米）
BORDER_LEFT_MM = 4.0                # 左边框宽度（毫米）
BORDER_TOP_MM = 4.0                 # 上边框宽度（毫米）
BORDER_BOTTOM_MM = 2.0              # 下边框宽度（毫米）
BORDER_RIGHT_MM = 1.0               # 右边框宽度（毫米）
MAP_WIDTH_MM = MAP_TOTAL_WIDTH_MM - BORDER_LEFT_MM - LEGEND_WIDTH_MM - BORDER_RIGHT_MM  # 地图区域宽度

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
BORDER_WIDTH_MM = 0.35              # 图框线宽（毫米）

# === 指北针尺寸常量 ===
NORTH_ARROW_WIDTH_MM = 12.0         # 指北针宽度（毫米）
NORTH_ARROW_HEIGHT_MM = 18.0        # 指北针高度（毫米）

# === 经纬度字体(pt) ===
LONLAT_FONT_SIZE_PT = 8             # 经纬度注记字体大小（磅）

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)      # 省界颜色
PROVINCE_LINE_WIDTH_MM = 0.4                # 省界线宽（毫米）
PROVINCE_LABEL_FONT_SIZE_PT = 8             # 省名标注字体大小（磅）
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)   # 省名标注颜色

# === 市界样式 ===
CITY_COLOR = QColor(160, 160, 160)          # 市界颜色
CITY_LINE_WIDTH_MM = 0.24                   # 市界线宽（毫米）
CITY_DASH_GAP_MM = 0.3                      # 市界虚线间距（毫米）
CITY_DASH_PATTERN = [4.0, CITY_DASH_GAP_MM / CITY_LINE_WIDTH_MM]  # 市界虚线样式

# === 县界样式 ===
COUNTY_COLOR = QColor(160, 160, 160)        # 县界颜色
COUNTY_LINE_WIDTH_MM = 0.14                 # 县界线宽（毫米）
COUNTY_DASH_GAP_MM = 0.2                    # 县界虚线间距（毫米）
COUNTY_DASH_PATTERN = [7.0, COUNTY_DASH_GAP_MM / COUNTY_LINE_WIDTH_MM]  # 县界虚线样式

# === 市名称标注 ===
CITY_LABEL_FONT_SIZE_PT = 9                 # 地级市名称标注字体大小（磅）
CITY_LABEL_COLOR = QColor(0, 0, 0)          # 地级市名称颜色

# === 图例字体 ===
LEGEND_TITLE_FONT_SIZE_PT = 12              # 图例标题字体大小（磅）
LEGEND_ITEM_FONT_SIZE_PT = 10               # 图例项字体大小（磅）

# === 基本图例项配置 ===
BASIC_LEGEND_FONT_SIZE_PT = 10              # 基本图例项字体大小（磅）
BASIC_LEGEND_ROW_HEIGHT_MM = 8.0            # 基本图例项行高（毫米）

# === 危险性图例项配置 ===
HAZARD_LEGEND_ITEM_FONT_SIZE_PT = 10        # 危险性图例项字体大小（磅）
HAZARD_LEGEND_ROW_HEIGHT_MM = 7.5           # 危险性图例项行高（毫米，色块高度）
HAZARD_LEGEND_GAP_MM = 1.5                  # 危险性图例色块之间的间距（毫米，分开显示）

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8                      # 比例尺字体大小（磅）

# === 烈度圈样式 ===
INTENSITY_LINE_COLOR = QColor(0, 0, 0)      # 烈度圈线颜色
INTENSITY_LINE_WIDTH_MM = 0.5               # 烈度圈线宽（毫米）
INTENSITY_HALO_COLOR = QColor(255, 255, 255)# 烈度圈光晕颜色
INTENSITY_HALO_WIDTH_MM = 1.0               # 烈度圈光晕宽度（毫米）
INTENSITY_LABEL_FONT_SIZE_PT = 9            # 烈度圈标注字体大小（磅）

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 5.0                # 震中五角星大小（毫米）
EPICENTER_COLOR = QColor(255, 0, 0)         # 震中五角星颜色
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)  # 震中五角星描边颜色
EPICENTER_STROKE_WIDTH_MM = 0.4             # 震中五角星描边宽度（毫米）

# === 烈度图例颜色 ===
INTENSITY_LEGEND_COLOR = QColor(0, 0, 0)    # 烈度图例线颜色
INTENSITY_LEGEND_LINE_WIDTH_MM = 0.5        # 烈度图例线宽（毫米）

# === 危险性等级配置 ===
# 5类危险性等级名称（从低到高）
HAZARD_LEVEL_NAMES = [
    "低度危险区",
    "较低危险区",
    "中等危险区",
    "较高危险区",
    "高度危险区",
]

# 5类危险性等级颜色（从绿色向红色过渡）
HAZARD_COLORS = [
    QColor(0, 168, 0),      # 第1档 - 深绿色（低度危险）
    QColor(140, 210, 0),    # 第2档 - 黄绿色（较低危险）
    QColor(255, 210, 0),    # 第3档 - 黄色（中等危险）
    QColor(255, 100, 0),    # 第4档 - 橙色（较高危险）
    QColor(200, 0, 0),      # 第5档 - 深红色（高度危险）
]

# Dn阈值：Dn <= 此值时直接判定为不危险（不参与危险性等级判定）
DN_SAFE_THRESHOLD = 0.1     # 单位：cm

# WGS84坐标系
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# === 裁剪缓冲区（度） ===
CLIP_BUFFER_DEGREES = 0.1   # 在目标范围外增加缓冲区（度），确保边缘数据完整

# === 图例字体 ===
LEGEND_FONT_TIMES_NEW_ROMAN = "Times New Roman"  # 数字标签字体


# ============================================================
# 天地图瓦片下载函数（只下载注记图层）
# ============================================================

def download_tianditu_annotation_tiles(extent, width_px, height_px, output_path):
    """
    只下载天地图矢量注记瓦片并拼接为本地栅格图像（透明背景）

    参数:
        extent (QgsRectangle): 渲染范围（WGS84坐标）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        output_path (str): 输出PNG文件路径

    返回:
        QgsRasterLayer 或 None: 渲染后的注记栅格图层，失败返回None
    """
    try:
        tk = TIANDITU_TK

        # 根据经度范围和输出宽度计算合适的缩放级别
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
            """纬度转瓦片Y坐标（天地图c系列，等经纬度投影）"""
            n = 2 ** (z - 1)
            y = int((90.0 - lat) / 180.0 * n)
            return max(0, min(n - 1, y))

        def tile_x_to_lon(x, z):
            """瓦片X坐标转对应瓦片左边界经度"""
            n = 2 ** z
            return x / n * 360.0 - 180.0

        def tile_y_to_lat(y, z):
            """瓦片Y坐标转对应瓦片上边界纬度"""
            n = 2 ** (z - 1)
            return 90.0 - y / n * 180.0

        # 计算需要下载的瓦片范围
        tile_x_min = lon_to_tile_x(extent.xMinimum(), zoom)
        tile_x_max = lon_to_tile_x(extent.xMaximum(), zoom)
        tile_y_min = lat_to_tile_y(extent.yMaximum(), zoom)
        tile_y_max = lat_to_tile_y(extent.yMinimum(), zoom)

        if tile_y_min > tile_y_max:
            tile_y_min, tile_y_max = tile_y_max, tile_y_min

        num_tiles_x = tile_x_max - tile_x_min + 1
        num_tiles_y = tile_y_max - tile_y_min + 1
        total_tiles = num_tiles_x * num_tiles_y
        print(f"[信息] 需要下载 {total_tiles} 个注记瓦片 ({num_tiles_x} x {num_tiles_y})")

        tile_size = 256
        mosaic_width = num_tiles_x * tile_size
        mosaic_height = num_tiles_y * tile_size
        # 使用RGBA模式支持透明背景
        mosaic = Image.new('RGBA', (mosaic_width, mosaic_height), (0, 0, 0, 0))

        downloaded = 0
        failed = 0
        servers = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']

        for ty in range(tile_y_min, tile_y_max + 1):
            for tx in range(tile_x_min, tile_x_max + 1):
                server = servers[(tx + ty) % len(servers)]
                # 只下载注记瓦片（cva_c：WGS84/等经纬度坐标系注记）
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
                        paste_x = (tx - tile_x_min) * tile_size
                        paste_y = (ty - tile_y_min) * tile_size
                        mosaic.paste(tile_cva, (paste_x, paste_y), tile_cva)
                        downloaded += 1
                    else:
                        failed += 1
                        print(f"[警告] 注记瓦片下载失败: {tx},{ty} - 状态码: {resp_cva.status_code}")
                except Exception as e:
                    failed += 1
                    print(f"[警告] 注记瓦片下载异常: {tx},{ty} - {e}")

        print(f"[信��] 注记瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

        if downloaded == 0:
            print("[错误] 没有成功下载任何注记瓦片")
            return None

        # 计算实际瓦片覆盖的地理范围
        actual_lon_min = tile_x_to_lon(tile_x_min, zoom)
        actual_lon_max = tile_x_to_lon(tile_x_max + 1, zoom)
        actual_lat_max = tile_y_to_lat(tile_y_min, zoom)
        actual_lat_min = tile_y_to_lat(tile_y_max + 1, zoom)

        # 计算裁剪像素坐标，提取目标范围对应的图像区域
        crop_left = int((extent.xMinimum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
        crop_right = int((extent.xMaximum() - actual_lon_min) / (actual_lon_max - actual_lon_min) * mosaic_width)
        crop_top = int((actual_lat_max - extent.yMaximum()) / (actual_lat_max - actual_lat_min) * mosaic_height)
        crop_bottom = int((actual_lat_max - extent.yMinimum()) / (actual_lat_max - actual_lat_min) * mosaic_height)

        crop_left = max(0, min(mosaic_width - 1, crop_left))
        crop_right = max(crop_left + 1, min(mosaic_width, crop_right))
        crop_top = max(0, min(mosaic_height - 1, crop_top))
        crop_bottom = max(crop_top + 1, min(mosaic_height, crop_bottom))

        cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
        final_image = cropped.resize((width_px, height_px), Image.LANCZOS)
        final_image.save(output_path, 'PNG')
        print(f"[信息] 注记底图已保存: {output_path}")

        # 写入世界文件（.pgw），用于QGIS正确识别栅格地理范围
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
            print(f"[信息] 成功加载注记栅格图层")
            return raster_layer
        else:
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
    根据震级获取对应的配置参数（地图范围、比例尺等）

    参数:
        magnitude (float): 地震震级

    返回:
        dict: 包含 radius_km、map_size_km、scale 的配置字典
    """
    if magnitude < 6:
        return MAGNITUDE_CONFIG["small"]
    elif magnitude < 7:
        return MAGNITUDE_CONFIG["medium"]
    else:
        return MAGNITUDE_CONFIG["large"]


def calculate_extent(longitude, latitude, half_size_km):
    """
    根据震中经纬度和半幅宽度（km）计算地图范围（WGS84坐标）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
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
    将相对路径转换为以当前脚本为基准的绝对路径

    参数:
        relative_path (str): 相对路径字符串

    返回:
        str: 绝对路径字符串
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, relative_path))


def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字字符串

    参数:
        num (int): 阿拉伯数字（正整数）

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
        target_min (int): 期望最小刻度数
        target_max (int): 期望最大刻度数

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
        layer (QgsVectorLayer): 矢量图层对象
        candidates (list): 候选字段名列表（按优先级排列）

    返回:
        str: 找到的字段名，未找到返回None
    """
    fields = layer.fields()
    field_names = [f.name() for f in fields]
    # 精确匹配
    for candidate in candidates:
        if candidate in field_names:
            return candidate
    # 模糊匹配（不区分大小写）
    for candidate in candidates:
        for fn in field_names:
            if candidate.lower() in fn.lower():
                return fn
    # 回退：返回第一个字符串字段
    for f in fields:
        if f.type() == QVariant.String:
            return f.name()
    return None


# ============================================================
# 临时文件管理器
# ============================================================

class TempFileManager:
    """
    临时文件管理器

    用于管理处理过程中产生的临时文件，确保在处理完成后正确清理，避免磁盘空间浪费。
    """

    def __init__(self):
        """初始化临时文件管理器，创建空的文件列表和目录变量"""
        self.temp_dir = None
        self.temp_files = []

    def get_temp_dir(self):
        """
        获取临时目录路径，如不存在则创建

        返回:
            str: 临时目录绝对路径
        """
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="earthquake_hazard_")
            print(f"[信息] 创建临时目录: {self.temp_dir}")
        return self.temp_dir

    def get_temp_file(self, suffix=".tif"):
        """
        在临时目录中创建一个新的临时文件并返回其路径

        参数:
            suffix (str): 临时文件后缀，默认为 .tif

        返回:
            str: 临时文件绝对路径
        """
        temp_dir = self.get_temp_dir()
        fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
        os.close(fd)
        self.temp_files.append(temp_path)
        return temp_path

    def cleanup(self):
        """清理所有已登记的临时文件和临时目录"""
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


# 全局临时文件管理器实例
_temp_manager = TempFileManager()


def get_temp_manager():
    """
    获取全局临时文件管理器实例

    返回:
        TempFileManager: 全局唯一的临时文件管理器实例
    """
    return _temp_manager


# ============================================================
# 自然断点法（Jenks）工具函数
# ============================================================

# jenkspy 导入（优先使用，性能最优；不可用时自动降级为 numpy 向量化实现）
try:
    import jenkspy
    JENKSPY_AVAILABLE = True
except ImportError:
    JENKSPY_AVAILABLE = False
    print("[警告] jenkspy未安装，将使用内置numpy向量化实现。"
          "建议执行 pip install jenkspy 以获得最佳性能。")


def compute_jenks_breaks(data_flat, num_classes):
    """
    使用自然断点法（Jenks Natural Breaks）计算分类边界值

    优先使用 jenkspy 库（C/Rust底层实现，速度最快）；
    若 jenkspy 不可用则降级为内置 numpy 向量化 Fisher-Jenks 实现；
    若 numpy 也不可用则退化为等间距分类。

    jenkspy 说明：
        - 安装：pip install jenkspy
        - API：jenkspy.jenks_breaks(data, nb_class=n) -> list，长度为 nb_class+1
        - 底层为 C/Rust，对百万级数据也能在秒级内完成
        - 输入支持 list、numpy.ndarray、pandas.Series

    降采样策略（jenkspy 和 numpy 实现均适用）：
        - 超过 MAX_SAMPLES 时采用分层采样（按分位数分20层，每层等比例采样）
        - 分层采样比随机采样更好地保留数据分布的极端值和形态特征

    参数:
        data_flat (numpy.ndarray 或 list): 一维数组，包含所有有效像素的概率值
        num_classes (int): 分类数目（危险性等级数，通常为5）

    返回:
        list: 长度为 num_classes+1 的边界值列表（包含最小值和最大值）
              例如 [0.0, v1, v2, v3, v4, max_val]
    """
    # ----------------------------------------------------------------
    # 步骤1：基础校验与快速退出
    # ----------------------------------------------------------------
    if not GDAL_AVAILABLE:
        # numpy 不可用时退化为等间距分类
        print("[警告] numpy不可用，使用等间距分类代替自然断点法")
        try:
            min_val = float(min(data_flat))
            max_val = float(max(data_flat))
        except (TypeError, ValueError):
            return [0.0] * (num_classes + 1)
        if max_val <= min_val:
            return [min_val] * (num_classes + 1)
        step = (max_val - min_val) / num_classes
        return [min_val + i * step for i in range(num_classes + 1)]

    # 转为 numpy 数组以便后续统一处理
    if not isinstance(data_flat, np.ndarray):
        data_flat = np.asarray(data_flat, dtype=np.float32)

    data_sorted = np.sort(data_flat.astype(np.float32) if JENKSPY_AVAILABLE
                         else data_flat.astype(np.float64))
    n = len(data_sorted)

    if n == 0:
        print("[警告] 输入数据为空，返回全零边界")
        return [0.0] * (num_classes + 1)

    min_val = float(data_sorted[0])
    max_val = float(data_sorted[-1])

    # 数据完全相同，无需分类
    if max_val <= min_val:
        print(f"[信息] 数据无变化（均为 {min_val:.6f}），返回相同边界")
        return [min_val] * (num_classes + 1)

    # 唯一值数量不足时直接用唯一值作边界
    unique_vals = np.unique(data_sorted)
    if len(unique_vals) <= num_classes:
        print(f"[信息] 唯一值数({len(unique_vals)})<=分类数({num_classes})，直接使用唯一值作边界")
        breaks = [min_val]
        idx_step = max(1, len(unique_vals) // num_classes)
        for i in range(idx_step, len(unique_vals), idx_step):
            if len(breaks) < num_classes:
                breaks.append(float(unique_vals[i]))
        while len(breaks) < num_classes:
            breaks.append(breaks[-1])
        breaks.append(max_val)
        return breaks

    # ----------------------------------------------------------------
    # 步骤2：分层降采样（超过最大样本数时执行）
    #   按分位数将数据分为 NUM_STRATA 层，各层按比例采样，
    #   确保低概率密集区和高概率稀疏区均有充足代表性样本
    # ----------------------------------------------------------------
    MAX_SAMPLES = 100000    # 最大样本数：jenkspy 处理此量级非常快，无需过度降采样
    NUM_STRATA = 20         # 分层数

    if n > MAX_SAMPLES:
        strata_edges = np.linspace(0, n, NUM_STRATA + 1, dtype=int)
        sampled_indices = []
        for s in range(NUM_STRATA):
            s_start = int(strata_edges[s])
            s_end = int(strata_edges[s + 1])
            s_size = s_end - s_start
            if s_size == 0:
                continue
            # 每层按比例分配采样数，至少1个
            n_take = max(1, int(round(MAX_SAMPLES * s_size / n)))
            n_take = min(n_take, s_size)
            # 层内均匀间隔采样（可重现，无随机性）
            layer_indices = np.linspace(s_start, s_end - 1, n_take, dtype=int)
            sampled_indices.append(layer_indices)

        all_indices = np.concatenate(sampled_indices)
        data_sorted = data_sorted[all_indices]
        data_sorted = np.sort(data_sorted)
        n = len(data_sorted)
        print(f"[信息] 分层采样完成，样本数: {n}（原始: {len(data_flat)}）")

    # ----------------------------------------------------------------
    # 步骤3：调用 jenkspy 或内置实现计算自然断点
    # ----------------------------------------------------------------
    if JENKSPY_AVAILABLE:
        breaks = _compute_jenks_with_jenkspy(data_sorted, num_classes, min_val, max_val)
    else:
        breaks = _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    print(f"[信息] 自然断点法计算完成，边界值: {[f'{v:.4f}' for v in breaks]}")
    return breaks


def _compute_jenks_with_jenkspy(data_sorted, num_classes, min_val, max_val):
    """
    使用 jenkspy 库计算自然断点（C/Rust底层，性能最优）

    兼容性说明：
        jenkspy 各版本参数名不一致，通过运行时自动探测正确参数名来规避版本差异：
        - 优先尝试位置参数调用（所有版本均支持，最稳妥）
        - 若失败则依次尝试 n_classes=、nb_class= 关键字参数

    参数:
        data_sorted (numpy.ndarray): 已排序的一维 float32 数组
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的边界值列表
    """
    breaks = None
    last_exc = None

    # ---- 策略1：位置参数（所有版本均支持，最优先）----
    try:
        breaks = jenkspy.jenks_breaks(data_sorted, num_classes)
    except TypeError as exc:
        last_exc = exc

    # ---- 策略2：关键字参数 n_classes=（jenkspy 0.4.x 部分版本）----
    if breaks is None:
        try:
            breaks = jenkspy.jenks_breaks(data_sorted, n_classes=num_classes)
        except TypeError as exc:
            last_exc = exc

    # ---- 策略3：关键字参数 nb_class=（jenkspy 旧版本）----
    if breaks is None:
        try:
            breaks = jenkspy.jenks_breaks(data_sorted, nb_class=num_classes)
        except TypeError as exc:
            last_exc = exc

    # ---- 三种策略均失败时降级为 numpy 实现 ----
    if breaks is None:
        print(f"[警告] jenkspy 所有调用方式均失败，降级为 numpy 实现。最后异常: {last_exc}")
        return _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    # ---- 后处理：统一格式化结果 ----
    breaks = [float(b) for b in breaks]

    # jenkspy 返回长度应为 num_classes+1，校验一下
    if len(breaks) != num_classes + 1:
        print(f"[警告] jenkspy 返回边界数({len(breaks)})与期望({num_classes + 1})不符，降级为 numpy 实现")
        return _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val)

    # 确保首尾与实际数据范围严格一致（规避浮点转换误差）
    breaks[0] = min_val
    breaks[-1] = max_val

    print(f"[信息] 使用 jenkspy 计算自然断点成功（样本数: {len(data_sorted)}）")
    return _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val)


def _compute_jenks_numpy(data_sorted, num_classes, min_val, max_val):
    """
    使用 numpy 向量化 Fisher-Jenks 动态规划计算自然断点（jenkspy 不可用时的降级实现）

    核心优化：通过前缀和在 O(1) 内计算任意区间的加权组内平方差（SSD），
    将算法整体复杂度从 O(n²·k) 降至 O(n·k)。

    SSD(i, j) = Σx²[i..j] - (Σx[i..j])² / count(i,j)

    参数:
        data_sorted (numpy.ndarray): 已排序的一维 float32 数组
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的边界值列表
    """
    n = len(data_sorted)
    x = data_sorted.astype(np.float64)

    # 前缀和预计算（长度 n+1，首元素为0）
    cum_x = np.zeros(n + 1, dtype=np.float64)
    cum_x2 = np.zeros(n + 1, dtype=np.float64)
    np.cumsum(x, out=cum_x[1:])
    np.cumsum(x * x, out=cum_x2[1:])

    def interval_ssd(i_arr, j_scalar):
        """
        向量化计算多个区间 [i_arr[t], j_scalar] 的加权组内平方差

        参数:
            i_arr (numpy.ndarray): 区间起始位置数组（0-indexed）
            j_scalar (int): 区间终止位置（0-indexed，含）

        返回:
            numpy.ndarray: 与 i_arr 等长的 SSD 值数组
        """
        counts = j_scalar - i_arr + 1
        sum_x = cum_x[j_scalar + 1] - cum_x[i_arr]
        sum_x2 = cum_x2[j_scalar + 1] - cum_x2[i_arr]
        ssd = sum_x2 - (sum_x * sum_x) / counts
        return np.maximum(ssd, 0.0)

    # 初始化 DP 表
    dp = np.full((num_classes + 1, n), np.inf, dtype=np.float64)
    back = np.zeros((num_classes + 1, n), dtype=np.int32)

    # k=1 时：dp[1][j] = SSD(0, j)
    for j in range(n):
        dp[1, j] = interval_ssd(np.array([0], dtype=np.int32), j)[0]
    back[1, :] = 0

    # k=2..num_classes 递推
    for k in range(2, num_classes + 1):
        for j in range(k - 1, n):
            m_arr = np.arange(k - 1, j + 1, dtype=np.int32)
            ssd_k = interval_ssd(m_arr, j)
            prev_dp = dp[k - 1, m_arr - 1]
            total_ssd = prev_dp + ssd_k
            best_idx = int(np.argmin(total_ssd))
            dp[k, j] = total_ssd[best_idx]
            back[k, j] = m_arr[best_idx]

    # 反向追踪分割点
    split_points = []
    k = num_classes
    j = n - 1
    while k >= 2:
        m = int(back[k, j])
        split_points.append(m)
        j = m - 1
        k -= 1
    split_points.reverse()

    # 构建边界值（使用相邻元素均值作为边界）
    breaks = [min_val]
    for m in split_points:
        boundary = float((x[m - 1] + x[m]) / 2.0)
        breaks.append(boundary)
    breaks.append(max_val)

    return _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val)


def _ensure_monotonic_breaks(breaks, num_classes, min_val, max_val):
    """
    后处理：确保边界值列表严格单调递增，长度为 num_classes+1

    处理规则：
    - 去除重复或逆序边界（在上一边界基础上微小递增）
    - 若清理后边界数不足，在间距最大处插入中间值
    - 确保首尾为原始数据的最小值和最大值

    参数:
        breaks (list): 原始边界值列表
        num_classes (int): 分类数目
        min_val (float): 数据最小值
        max_val (float): 数据最大值

    返回:
        list: 长度为 num_classes+1 的严格单调递增边界值列表
    """
    # 去除重复或逆序值
    cleaned = [breaks[0]]
    for b in breaks[1:]:
        if b > cleaned[-1]:
            cleaned.append(b)
        else:
            cleaned.append(cleaned[-1] + 1e-9)

    # 补充不足的边界（在间距最大处插入中间值）
    while len(cleaned) < num_classes + 1:
        gaps = [cleaned[i + 1] - cleaned[i] for i in range(len(cleaned) - 1)]
        max_gap_idx = int(np.argmax(gaps))
        mid = (cleaned[max_gap_idx] + cleaned[max_gap_idx + 1]) / 2.0
        cleaned.insert(max_gap_idx + 1, mid)

    # 确保首尾正确
    cleaned[0] = min_val
    cleaned[-1] = max_val

    return cleaned


# ============================================================
# 危险性概率计算核心函数
# ============================================================

def calculate_hazard_probability(dn_value, a, b, c):
    """
    按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算单个像素的滑坡危险性概率

    注意：Dn <= DN_SAFE_THRESHOLD（0.1cm）的像素直接返回0（不危险）。

    参数:
        dn_value (float): Newmark位移值（cm），单个像素值
        a (float): 公式参数 a
        b (float): 公式参数 b（通常为负值，使概率随Dn增大而增大）
        c (float): 公式参数 c（指数参数）

    返回:
        float: 危险性概率值，范围 [0, 1]
    """
    if dn_value <= DN_SAFE_THRESHOLD:
        return 0.0
    try:
        prob = a * (1.0 - math.exp(b * (dn_value ** c)))
        # 将概率值限制在 [0, 1] 范围内
        return max(0.0, min(1.0, prob))
    except (OverflowError, ValueError):
        return 0.0


def compute_hazard_raster(dn_array, nodata_value, a, b, c):
    """
    对整个Dn栅格数组逐像素计算危险性概率（向量化计算，效率高）

    处理规则：
    - 无效值（NoData）直接判定为不危险，概率为 0
    - Dn <= DN_SAFE_THRESHOLD 的像素概率为 0
    - 其余像素按公式 P(f) = a * (1 - EXP(b * Dn^c)) 计算

    参数:
        dn_array (numpy.ndarray): Dn值二维数组（从TIF文件读取）
        nodata_value (float 或 None): 无效值标记，None表示无NoData设置
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c

    返回:
        numpy.ndarray: 与输入同形状的危险性概率二维浮点数组
                       所有值为 [0, 1]，NoData和Dn<=0.1cm的位置值为0
    """
    # 创建输出数组，初始化为0（所有像素默认为不危险）
    prob_array = np.zeros(dn_array.shape, dtype=np.float32)

    # 构建有效像素掩膜（排除NoData值）
    if nodata_value is not None:
        # 使用容差比较，防止浮点精度问题
        valid_mask = np.abs(dn_array - nodata_value) > 1e-6
    else:
        valid_mask = ~np.isnan(dn_array)

    # NoData位置概率已经是0（初始化值），无需额外处理
    # Dn <= 阈值 的有效像素概率也是0（初始化值），无需额外处理

    # 只需处理危险区：Dn > 阈值 的有效像素进行公式计算
    hazard_mask = valid_mask & (dn_array > DN_SAFE_THRESHOLD)

    if np.any(hazard_mask):
        dn_hazard = dn_array[hazard_mask].astype(np.float64)
        # 向量化计算 P(f) = a * (1 - EXP(b * Dn^c))
        # 使用 np.clip 防止指数运算溢出
        exponent = b * np.power(dn_hazard, c)
        # 将指数限制在合理范围内防止溢出
        exponent = np.clip(exponent, -500.0, 500.0)
        prob_values = a * (1.0 - np.exp(exponent))
        # 将概率值限制在 [0, 1]
        prob_values = np.clip(prob_values, 0.0, 1.0)
        prob_array[hazard_mask] = prob_values.astype(np.float32)

    return prob_array


def generate_hazard_tif(dn_tif_path, output_tif_path, extent, a, b, c,
                        buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    读取Dn.tif，裁剪到目标范围，计算危险性概率并保存为新的GeoTIFF文件

    该函数是危险性评估图的核心处理函数：
    1. 使用GDAL裁剪Dn.tif到目标范围（带缓冲区）
    2. 逐像素按公式计算危险性概率
    3. 将结果保存为GeoTIFF（Float32格式，NoData=-1）

    参数:
        dn_tif_path (str): Dn.tif文件绝对路径
        output_tif_path (str): 输出危险性概率TIF文件路径
        extent (QgsRectangle): 目标地图范围（WGS84坐标）
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c
        buffer_degrees (float): 裁剪缓冲区大小（度），默认0.1度

    返回:
        tuple: (output_tif_path, max_dn_value, prob_array_flat)
               - output_tif_path: 成功返回输出文件路径，失败返回None
               - max_dn_value: 范围内Dn最大值（float），失败返回None
               - prob_array_flat: 所有有效像素（prob>=0）的概率一维数组，用于Jenks分类
    """
    if not GDAL_AVAILABLE:
        print("[错误] GDAL不可用，无法生成危险性栅格")
        return None, None, None

    if not os.path.exists(dn_tif_path):
        print(f"[错误] Dn.tif文件不存在: {dn_tif_path}")
        return None, None, None

    try:
        # 打开源栅格文件
        src_ds = gdal.Open(dn_tif_path, gdal.GA_ReadOnly)
        if src_ds is None:
            print(f"[错误] 无法打开Dn.tif: {dn_tif_path}")
            return None, None, None

        # 获取地理变换参数和基本信息
        gt = src_ds.GetGeoTransform()
        # gt[0]: 左上角X(经度), gt[1]: X分辨率, gt[3]: 左上角Y(纬度), gt[5]: Y分辨率(负)
        src_proj = src_ds.GetProjection()
        src_width = src_ds.RasterXSize
        src_height = src_ds.RasterYSize

        # 计算带缓冲区的裁剪范围
        clip_xmin = extent.xMinimum() - buffer_degrees
        clip_xmax = extent.xMaximum() + buffer_degrees
        clip_ymin = extent.yMinimum() - buffer_degrees
        clip_ymax = extent.yMaximum() + buffer_degrees

        # 将地理坐标转换为像素坐标
        px_xmin = int((clip_xmin - gt[0]) / gt[1])
        px_xmax = int((clip_xmax - gt[0]) / gt[1]) + 1
        px_ymin = int((clip_ymax - gt[3]) / gt[5])
        px_ymax = int((clip_ymin - gt[3]) / gt[5]) + 1

        # 确保像素坐标在有效范围内
        px_xmin = max(0, min(src_width - 1, px_xmin))
        px_xmax = max(px_xmin + 1, min(src_width, px_xmax))
        px_ymin = max(0, min(src_height - 1, px_ymin))
        px_ymax = max(px_ymin + 1, min(src_height, px_ymax))

        read_width = px_xmax - px_xmin
        read_height = px_ymax - px_ymin

        print(f"[信息] 读取Dn栅格范围: ({px_xmin},{px_ymin}) - ({px_xmax},{px_ymax}), 尺寸: {read_width}x{read_height}")

        # 读取指定范围的Dn数据
        band = src_ds.GetRasterBand(1)
        nodata_value = band.GetNoDataValue()
        dn_array = band.ReadAsArray(px_xmin, px_ymin, read_width, read_height)

        if dn_array is None:
            print("[错误] 无法读取Dn栅格数据")
            src_ds = None
            return None, None, None

        dn_array = dn_array.astype(np.float64)

        # 计算范围内Dn最大值（用于统计报告）
        if nodata_value is not None:
            valid_dn = dn_array[np.abs(dn_array - nodata_value) > 1e-6]
        else:
            valid_dn = dn_array[~np.isnan(dn_array)]

        if valid_dn.size == 0:
            print("[警告] 范围内没有有效Dn数据")
            src_ds = None
            return None, None, None

        max_dn_value = float(np.max(valid_dn))
        print(f"[信息] 范围内Dn最大值: {max_dn_value:.4f} cm")

        # 计算危险性概率栅格
        print(f"[信息] 计算危险性概率，参数: a={a}, b={b}, c={c}")
        prob_array = compute_hazard_raster(dn_array, nodata_value, a, b, c)

        # 所有像素都参与统计（NoData已被设为0，不再是-1）
        prob_flat = prob_array.flatten()
        print(f"[信息] 总像素数: {len(prob_flat)}，概率范围: [{float(np.min(prob_flat)):.4f}, {float(np.max(prob_flat)):.4f}]")

        # 计算输出栅格的地理变换参数（基于裁剪后的左上角坐标）
        out_x_origin = gt[0] + px_xmin * gt[1]
        out_y_origin = gt[3] + px_ymin * gt[5]
        out_gt = (out_x_origin, gt[1], gt[2], out_y_origin, gt[4], gt[5])

        # 创建输出GeoTIFF（Float32格式）
        driver = gdal.GetDriverByName('GTiff')
        out_ds = driver.Create(
            output_tif_path,
            read_width, read_height, 1,
            gdal.GDT_Float32,
            options=['COMPRESS=LZW', 'TILED=YES', 'BLOCKXSIZE=256', 'BLOCKYSIZE=256']
        )

        if out_ds is None:
            print(f"[错误] 无法创建输出危险性栅格文件: {output_tif_path}")
            src_ds = None
            return None, None, None

        out_ds.SetGeoTransform(out_gt)
        # 使用源文件的投影坐标系
        if src_proj:
            out_ds.SetProjection(src_proj)
        else:
            # 默认使用WGS84
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            out_ds.SetProjection(srs.ExportToWkt())

        out_band = out_ds.GetRasterBand(1)
        out_band.WriteArray(prob_array)
        out_band.FlushCache()

        out_ds = None
        src_ds = None

        print(f"[信息] 危险性概率栅格已保存: {output_tif_path}")
        return output_tif_path, max_dn_value, prob_flat

    except Exception as exc:
        logger.error('生成危险性栅格失败: %s', exc, exc_info=True)
        print(f"[错误] 生成危险性栅格失败: {exc}")
        raise


# ============================================================
# 危险性等级分类与渲染
# ============================================================

def classify_hazard_levels(prob_flat, num_classes=5):
    """
    使用自然断点法对危险性概率值进行5类分级，返回各类边界值

    分类规则：
    - 概率值 = 0 的像素（Dn <= 0.1cm）归入第1类（低度危险）
    - 概率值 > 0 的像素按自然断点法分为5类

    参数:
        prob_flat (numpy.ndarray): 所有有效像素的概率值一维数组（已排除NoData）
        num_classes (int): 分类数目，默认5

    返回:
        list: 长度为 num_classes+1 的边界值列表（包含0和最大概率值）
              例如 [0.0, v1, v2, v3, v4, max_prob]
    """
    if prob_flat is None or len(prob_flat) == 0:
        print("[警告] 无有效概率数据，使用等间距分类")
        return [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

    # 提取大于0的概率值（Dn > 0.1cm 的危险像素）
    nonzero_probs = prob_flat[prob_flat > 0]

    if len(nonzero_probs) == 0:
        print("[信息] 所有有效像素概率均为0（Dn <= 0.1cm），不存在危险区")
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    print(f"[信息] 参与Jenks分类的非零概率像素数: {len(nonzero_probs)}")

    # 对非零概率值执行自然断点法分类
    breaks = compute_jenks_breaks(nonzero_probs, num_classes)

    # 确保第一个边界为0（包含零概率像素在第1类中）
    breaks[0] = 0.0

    return breaks


def apply_hazard_renderer(raster_layer, breaks):
    """
    为危险性概率栅格图层应用离散色彩渲染器（5类危险等级颜色）

    使用 QgsColorRampShader.Discrete 离散渲染，每类使用对应颜色。
    NoData值（-1.0）不参与渲染。

    参数:
        raster_layer (QgsRasterLayer): 危险性概率栅格图层
        breaks (list): 长度为6的边界值列表（由 classify_hazard_levels 返回）

    返回:
        bool: 渲染器应用是否成功
    """
    if raster_layer is None or not raster_layer.isValid():
        print("[错误] 无效的栅格图层，无法应用危险性渲染器")
        return False

    if breaks is None or len(breaks) != 6:
        print("[错误] 无效的边界值列表（长度应为6）")
        return False

    try:
        shader = QgsRasterShader()
        color_ramp_shader = QgsColorRampShader()
        # 使用离散分类（每个色阶对应一个等级）
        color_ramp_shader.setColorRampType(QgsColorRampShader.Discrete)

        color_ramp_items = []
        # 5个等级，每个等级对应 breaks[i] ~ breaks[i+1] 的范围
        for i in range(5):
            upper_bound = breaks[i + 1]
            color = HAZARD_COLORS[i]
            label = HAZARD_LEVEL_NAMES[i]
            item = QgsColorRampShader.ColorRampItem(upper_bound, color, label)
            color_ramp_items.append(item)

        color_ramp_shader.setColorRampItemList(color_ramp_items)
        shader.setRasterShaderFunction(color_ramp_shader)

        renderer = QgsSingleBandPseudoColorRenderer(
            raster_layer.dataProvider(),
            1,  # 波段1
            shader
        )
        raster_layer.setRenderer(renderer)
        raster_layer.triggerRepaint()

        print("[信息] 危险性栅格渲染器设置完成，使用5档分类")
        return True

    except Exception as exc:
        logger.error('应用危险性渲染器失败: %s', exc, exc_info=True)
        print(f"[错误] 应用危险性渲染器失败: {exc}")
        raise


# ============================================================
# 面积统计函数
# ============================================================

def calculate_area_statistics(prob_array_flat, breaks, dn_tif_path, extent,
                               buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    统计各危险等级的面积（平方公里）和占比（百分比）

    面积计算方法：
    - 根据TIF文件的像素分辨率（度/像素）估算每个像素的面积（平方公里）
    - 纬度方向：1度 ≈ 111 km
    - 经度方向：1度 ≈ 111 * cos(中心纬度) km
    - 每个像素面积 = 像素高度km × 像素宽度km

    参数:
        prob_array_flat (numpy.ndarray): 所有有效像素的概率值一维数组
        breaks (list): 长度为6的危险等级边界值列表
        dn_tif_path (str): Dn.tif文件路径（用于获取像素分辨率）
        extent (QgsRectangle): 地图范围（用于计算纬度中心点）
        buffer_degrees (float): 裁剪缓冲区大小（度）

    返回:
        dict: 包含各危险等级面积和占比的字典，键为等级名称，值为字典 {area_km2, percent}
              额外包含 'total_valid_km2' 表示总有效面积
    """
    if not GDAL_AVAILABLE or prob_array_flat is None or len(prob_array_flat) == 0:
        print("[警告] 无法计算面积统计，返回空结果")
        # 返回空结果（各等级面积为0）
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result

    try:
        # 获取像素分辨率（度/像素）
        pixel_area_km2 = 1.0  # 默认值，将在下方被真实值覆盖
        if os.path.exists(dn_tif_path):
            ds = gdal.Open(dn_tif_path, gdal.GA_ReadOnly)
            if ds is not None:
                gt = ds.GetGeoTransform()
                # gt[1]: X方向分辨率（度/像素）, gt[5]: Y方向分辨率（负值，度/像素）
                pixel_width_deg = abs(gt[1])
                pixel_height_deg = abs(gt[5])
                # 使用地图范围中心纬度计算像素面积
                center_lat = (extent.yMinimum() + extent.yMaximum()) / 2.0
                pixel_width_km = pixel_width_deg * 111.0 * math.cos(math.radians(center_lat))
                pixel_height_km = pixel_height_deg * 111.0
                pixel_area_km2 = pixel_width_km * pixel_height_km
                ds = None
                print(f"[信息] 像素分辨率: {pixel_width_deg:.6f}° x {pixel_height_deg:.6f}°, "
                      f"像素面积: {pixel_area_km2:.6f} km²")

        total_pixels = len(prob_array_flat)
        total_area_km2 = total_pixels * pixel_area_km2

        # 统计各等级像素数
        result = {}
        for i in range(5):
            lower = breaks[i]
            upper = breaks[i + 1]
            if i == 0:
                # 第1类包含等于0的像素（Dn <= 0.1cm）
                mask = prob_array_flat <= upper
            elif i == 4:
                # 最后一类包含大于上一级下界的所有像素
                mask = prob_array_flat > breaks[i]
            else:
                mask = (prob_array_flat > lower) & (prob_array_flat <= upper)

            count = int(np.sum(mask))
            area_km2 = count * pixel_area_km2
            percent = (count / total_pixels * 100.0) if total_pixels > 0 else 0.0
            result[HAZARD_LEVEL_NAMES[i]] = {
                'area_km2': round(area_km2, 2),
                'percent': round(percent, 2),
            }

        result['total_valid_km2'] = round(total_area_km2, 2)

        # 打印统计结果
        print("[信息] 危险性等级面积统计:")
        for name in HAZARD_LEVEL_NAMES:
            info = result[name]
            print(f"  {name}: {info['area_km2']:.2f} km² ({info['percent']:.2f}%)")
        print(f"  总有效面积: {result['total_valid_km2']:.2f} km²")

        return result

    except Exception as exc:
        logger.error('计算面积统计失败: %s', exc, exc_info=True)
        print(f"[错误] 计算面积统计失败: {exc}")
        # 返回空结果
        result = {}
        for name in HAZARD_LEVEL_NAMES:
            result[name] = {'area_km2': 0.0, 'percent': 0.0}
        result['total_valid_km2'] = 0.0
        return result


def build_statistics_summary(max_dn_value, area_stats):
    """
    生成统计摘要文字描述

    格式：
    本次地震极震区最大Newmark滑坡位移达XXcm。总得来看，
    极低危险等级面积为XX平方千米，占比XX%；低危险等级面积为XX平��千米，占比XX%；
    中危险等级面积为XX平方千米，占比XX%；高危险等级面积为XX平方千米，占比XX%；
    极高危险等级面积为XX平方千米，占比XX%

    参数:
        max_dn_value (float): 极震区Dn最大值（cm）
        area_stats (dict): calculate_area_statistics 返回的统计字典

    返回:
        str: 格式化的统计摘要文字
    """
    if max_dn_value is None:
        max_dn_str = "N/A"
    elif max_dn_value == int(max_dn_value):
        max_dn_str = str(int(max_dn_value))
    else:
        max_dn_str = f"{max_dn_value:.2f}"

    # 与统计结果对应的摘要用名称（与HAZARD_LEVEL_NAMES顺序一致）
    display_names = ["极低危险等级", "低危险等级", "中危险等级", "高危险等级", "极高危险等级"]

    summary = f"本次地震极震区最大Newmark滑坡位移达{max_dn_str}cm。总得来看，"

    parts = []
    for i, level_name in enumerate(HAZARD_LEVEL_NAMES):
        info = area_stats.get(level_name, {'area_km2': 0.0, 'percent': 0.0})
        area_km2 = info['area_km2']
        percent = info['percent']
        # 格式化面积值
        if area_km2 == int(area_km2):
            area_str = str(int(area_km2))
        else:
            area_str = f"{area_km2:.2f}"
        parts.append(f"{display_names[i]}面积为{area_str}平方千米，占比{percent:.2f}%")

    summary += "；".join(parts)
    return summary


# ============================================================
# KML烈度圈解析
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件，提取烈度圈坐标数据

    参数:
        kml_path (str): KML文件路径

    返回:
        list: 烈度圈数据列表，每项为包含 intensity 和 coords 键的字典
    """
    if not kml_path or not os.path.exists(kml_path):
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
                intensity_data.append({"intensity": intensity, "coords": coords})

        print(f"[信息] 从KML解析到 {len(intensity_data)} 个烈度圈")
    except Exception as exc:
        print(f"[错误] 解析KML文件失败: {exc}")
        raise
    return intensity_data


def _extract_intensity_from_name(name):
    """
    从Placemark名称中提取烈度数值

    参数:
        name (str): KML Placemark 名称字符串

    返回:
        int 或 None: 烈度整数值，无法解析返回None
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
    """
    从KML Placemark 中提取 LineString 坐标列表

    参数:
        placemark: KML Placemark 元素
        ns (str): KML命名空间前缀

    返回:
        list: (lon, lat) 坐标元组列表
    """
    coords_list = []
    for ls in placemark.iter(ns + "LineString"):
        coords_elem = ls.find(ns + "coordinates")
        if coords_elem is not None and coords_elem.text:
            parsed = _parse_kml_coords(coords_elem.text)
            coords_list.extend(parsed)
    return coords_list


def _parse_kml_coords(text):
    """
    解析KML坐标文本为 (lon, lat) 元组列表

    参数:
        text (str): KML坐标文本，格式为 "lon,lat,alt lon,lat,alt ..."

    返回:
        list: (lon, lat) 坐标元组列表
    """
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
    """
    根据解析的烈度圈数据创建QGIS矢量线图层（含烈度标注）

    参数:
        intensity_data (list): parse_intensity_kml 返回的烈度圈数据列表

    返回:
        QgsVectorLayer 或 None: 烈度圈矢量图层，数据为空返回None
    """
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

        # 设置光晕线（白色底）+ 实线（黑色）的双层符号
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

        print(f"[信息] 创建烈度圈图层，共 {len(features)} 条烈度线")
        return layer

    except Exception as exc:
        logger.error('创建烈度圈图层失败: %s', exc, exc_info=True)
        raise


def _setup_intensity_labels(layer):
    """
    配置烈度圈图层的罗马数字标注样式

    参数:
        layer (QgsVectorLayer): 烈度圈图层
    """
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
# 图层加载函数
# ============================================================

def load_vector_layer(shp_path, layer_name):
    """
    加载SHP矢量图层

    参数:
        shp_path (str): SHP文件路径（相对或绝对路径）
        layer_name (str): 图层显示名称

    返回:
        QgsVectorLayer 或 None: 加载成功的矢量图层，失败返回None
    """
    abs_path = resolve_path(shp_path) if not os.path.isabs(shp_path) else shp_path
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
    """
    设置省界图层样式（仅边界线，不配置标注）

    当传入震中坐标时，省界多边形图层仅绘制边界线；
    省份标注由独立的点图层（通过 create_province_label_layer 创建）负责。

    参数:
        layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float 或 None): 震中经度，用于标注偏移判断
        epicenter_lat (float 或 None): 震中纬度，用于标注偏移判断
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量
    """
    fill_sl = QgsSimpleFillSymbolLayer()
    fill_sl.setColor(QColor(0, 0, 0, 0))  # 填充透明
    fill_sl.setStrokeColor(PROVINCE_COLOR)
    fill_sl.setStrokeWidth(PROVINCE_LINE_WIDTH_MM)
    fill_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    fill_sl.setStrokeStyle(Qt.SolidLine)

    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill_sl)
    layer.renderer().setSymbol(symbol)
    if epicenter_lon is None:
        # 无震中信息时，直接在省界图层上配置标注
        _setup_province_labels(layer)
    layer.triggerRepaint()
    print("[信息] 省界图层样式设置完成")


def style_city_layer(layer):
    """
    设置市界图层样式（虚线边界，透明填充）

    参数:
        layer (QgsVectorLayer): 市界多边形图层
    """
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
    print("[信息] 市界图层样式设置完成")


def style_county_layer(layer):
    """
    设置县界图层样式（虚线边界，透明填充）

    参数:
        layer (QgsVectorLayer): 县界多边形图层
    """
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
    print("[信息] 县界图层样式设置完成")


def _setup_province_labels(layer):
    """
    配置省界图层标注（无偏移，直接在省界图层上启用）

    参数:
        layer (QgsVectorLayer): 省界多边形图层
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
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移

    对质心与震中重合的省份进行3mm偏移，避免标注遮挡震中五角星。

    参数:
        province_layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float): 震中经度（度）
        epicenter_lat (float): 震中纬度（度）
        extent (QgsRectangle): 地图范围，用于计算偏移量（mm转度）

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
    lon_offset_deg = offset_mm / MAP_WIDTH_MM * map_width_deg
    lat_offset_deg = offset_mm / MAP_WIDTH_MM * map_height_deg
    coord_epsilon = 1e-6

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
        if abs(cx - epicenter_lon) < coord_epsilon and abs(cy - epicenter_lat) < coord_epsilon:
            # 质心与震中重合，向左向下偏移3mm
            px = cx - lon_offset_deg
            py = cy - lat_offset_deg
            offset_count += 1

        prov_name = feat[field_name]
        new_feat = QgsFeature(layer_fields)
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(px, py)))
        new_feat.setAttribute("province_name", prov_name)
        feats_to_add.append(new_feat)

    if feats_to_add:
        provider.addFeatures(feats_to_add)
    label_layer.updateExtents()

    print(f"[信息] 省份标注：共 {len(feats_to_add)} 个省份，其中 {offset_count} 个进行了偏移")

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


# ============================================================
# 震中与辅助图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层（红色五角星+白色描边）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）

    返回:
        QgsVectorLayer: 震中点图层
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


def create_city_point_layer(extent):
    """
    加载地级市点位图层并设置符号样式（同心圆标记）

    参数:
        extent (QgsRectangle): 地图范围（暂未使用，保留接口一致性）

    返回:
        QgsVectorLayer 或 None: 地级市点图层，文件不存在返回None
    """
    abs_path = resolve_path(CITY_POINTS_SHP_PATH) if not os.path.isabs(CITY_POINTS_SHP_PATH) else CITY_POINTS_SHP_PATH
    if not os.path.exists(abs_path):
        print(f"[警告] 地级市点位数据不存在: {abs_path}")
        return None

    layer = QgsVectorLayer(abs_path, "地级市", "ogr")
    if not layer.isValid():
        print(f"[错误] 无法加载地级市点位图层: {abs_path}")
        return None

    symbol_size_mm = CITY_LABEL_FONT_SIZE_PT * 0.353 / 3.0

    # 白色背景圆（最大）
    bg_sl = QgsSimpleMarkerSymbolLayer()
    bg_sl.setShape(Qgis.MarkerShape.Circle)
    bg_sl.setColor(QColor(255, 255, 255))
    bg_sl.setStrokeColor(QColor(0, 0, 0))
    bg_sl.setStrokeWidth(0.15)
    bg_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    bg_sl.setSize(symbol_size_mm * 1.4)
    bg_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 外圆（透明填充）
    outer_sl = QgsSimpleMarkerSymbolLayer()
    outer_sl.setShape(Qgis.MarkerShape.Circle)
    outer_sl.setColor(QColor(0, 0, 0, 0))
    outer_sl.setStrokeColor(QColor(0, 0, 0))
    outer_sl.setStrokeWidth(0.15)
    outer_sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    outer_sl.setSize(symbol_size_mm)
    outer_sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)

    # 内圆（实心黑色）
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
    print("[信息] 加载地级市点位图层完成")
    return layer


def create_intensity_legend_layer():
    """
    创建烈度图例用的内存线图层（用于在图例中绘制烈度线样式）

    返回:
        QgsVectorLayer: 烈度图例线图层
    """
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
    """
    创建省界图例用的内存线图层

    返回:
        QgsVectorLayer: 省界图例线图层
    """
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
    """
    创建市界图例用的内存线图层（虚线样式）

    返回:
        QgsVectorLayer: 市界图例线图层
    """
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
    """
    创建县界图例用的内存线图层（虚线样式）

    返回:
        QgsVectorLayer: 县界图例线图层
    """
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
# 布局创建函数
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale,
                        map_height_mm, breaks=None, ordered_layers=None):
    """
    创建QGIS打印布局，包含地图、指北针、比例尺、经纬度网格和图例

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图范围
        scale (int): 地图比例尺分母
        map_height_mm (float): ��图区域高度（毫米）
        breaks (list 或 None): 危险性等级边界值列表（6个值）
        ordered_layers (list 或 None): 按显示顺序排列的图层列表（顶层在前）

    返回:
        QgsPrintLayout: 创建好的打印布局对象
    """
    try:
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName("地震滑坡危险性评估图")
        layout.setUnits(QgsUnitTypes.LayoutMillimeters)

        output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm, QgsUnitTypes.LayoutMillimeters))

        map_left = BORDER_LEFT_MM
        map_top = BORDER_TOP_MM

        # 创建地图项
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

        # 设置图层顺序
        layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
        if layers_to_set:
            map_item.setLayers(layers_to_set)
            map_item.setKeepLayerSet(True)
        map_item.invalidateCache()

        # 添加各布局组件
        _setup_map_grid(map_item, extent)
        _add_north_arrow(layout, map_height_mm)
        _add_scale_bar(layout, map_item, scale, extent, latitude, map_height_mm)
        _add_hazard_legend(layout, map_height_mm, output_height_mm, breaks)

        return layout

    except Exception as exc:
        logger.error('创建打印布局失败: %s', exc, exc_info=True)
        raise


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格（仅显示内侧刻度线和外侧注记）

    参数:
        map_item (QgsLayoutItemMap): 地图布局项
        extent (QgsRectangle): 地图范围
    """
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

    # 只在上边和左边显示经纬度注记
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
    """
    在地图右上角添加指北针（白底方框内SVG指北针图案）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        map_height_mm (float): 地图区域高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    # 白色背景方框
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

    # 创建并加载指北针SVG
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_hazard_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    padding_x = 1.0
    padding_y = 0.5
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x + padding_x, arrow_y + padding_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(
        NORTH_ARROW_WIDTH_MM - padding_x * 2,
        NORTH_ARROW_HEIGHT_MM - padding_y * 2,
        QgsUnitTypes.LayoutMillimeters
    ))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """
    在地图右下角添加比例尺（黑白交替矩形段，含比例尺文字）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        map_item (QgsLayoutItemMap): 地图布局项（未直接使用，保留接口）
        scale (int): 比例尺分母
        extent (QgsRectangle): 地图范围
        center_lat (float): 地图中心纬度（用于计算实际距离）
        map_height_mm (float): 地图区域高度（毫米）
    """
    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM
    map_right = map_left + MAP_WIDTH_MM
    map_bottom = map_top + map_height_mm

    # 计算比例尺对应的公里数和毫米长度
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

    # 白色背景方框
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

    # 比例尺文字标签（例如 1:500,000）
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

    # 黑白交替矩形段
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

    # 起始刻度标签（0）
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

    # 中间刻度标签
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

    # 末端刻度标签（含单位km）
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


def _add_hazard_legend(layout, map_height_mm, output_height_mm, breaks=None):
    """
    添加危险性评估图图例区域

    图例结构：
    - 顶部标题"图  例"
    - 基础图例（3行2列）：震中、地级市、省界、市界、县界、烈度
    - 危险性等级图例（5个分开的色块，从低到高）

    图例色块使用分开样式（每个色块之间有间距），各色块独立显示。

    参数:
        layout (QgsPrintLayout): 打印布局对象
        map_height_mm (float): 地图区域高度（毫米）
        output_height_mm (float): 布局总高度（毫米）
        breaks (list 或 None): 危险性等级边界值列表（6个值）
    """
    legend_x = BORDER_LEFT_MM + MAP_WIDTH_MM
    legend_y = BORDER_TOP_MM
    legend_width = LEGEND_WIDTH_MM
    legend_height = map_height_mm

    # 公共文本格式定义
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

    hazard_label_format = QgsTextFormat()
    hazard_label_format.setFont(QFont("SimSun", HAZARD_LEGEND_ITEM_FONT_SIZE_PT))
    hazard_label_format.setSize(HAZARD_LEGEND_ITEM_FONT_SIZE_PT)
    hazard_label_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    hazard_label_format.setColor(QColor(0, 0, 0))

    # 图例背景矩形
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

    # 标题标签
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

    # 基础图例项（3行2列布局）
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
                            PROVINCE_COLOR, PROVINCE_LINE_WIDTH_MM)
        elif draw_type == "dash_line_city":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 CITY_COLOR, CITY_LINE_WIDTH_MM, CITY_DASH_GAP_MM)
        elif draw_type == "dash_line_county":
            _draw_dash_line_icon(layout, item_x, icon_center_y, icon_width,
                                 COUNTY_COLOR, COUNTY_LINE_WIDTH_MM, COUNTY_DASH_GAP_MM)
        elif draw_type == "solid_line_black":
            _draw_line_icon(layout, item_x, icon_center_y, icon_width,
                            INTENSITY_LEGEND_COLOR, INTENSITY_LEGEND_LINE_WIDTH_MM)

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

    # ---- 危险性等级图例（5个分开的色块）----
    if breaks is not None and len(breaks) == 6:
        hazard_section_start_y = top_legend_start_y + top_legend_height + 2.0

        # 危险性图例标题：使用SimHei字体
        hazard_title_format = QgsTextFormat()
        hazard_title_format.setFont(QFont("SimHei", 10))
        hazard_title_format.setSize(10)
        hazard_title_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        hazard_title_format.setColor(QColor(0, 0, 0))

        hazard_title_label = QgsLayoutItemLabel(layout)
        hazard_title_label.setText("危险性等级")
        hazard_title_label.setTextFormat(hazard_title_format)
        hazard_title_label.attemptMove(
            QgsLayoutPoint(legend_x, hazard_section_start_y, QgsUnitTypes.LayoutMillimeters))
        hazard_title_label.attemptResize(QgsLayoutSize(legend_width, 5.0, QgsUnitTypes.LayoutMillimeters))
        hazard_title_label.setHAlign(Qt.AlignHCenter)
        hazard_title_label.setVAlign(Qt.AlignVCenter)
        hazard_title_label.setFrameEnabled(False)
        hazard_title_label.setBackgroundEnabled(False)
        layout.addLayoutItem(hazard_title_label)

        # 色块绘制参数
        colorbar_start_y = hazard_section_start_y + 6.0
        colorbar_width = 8.0  # 色块宽度（毫米）
        colorbar_height = HAZARD_LEGEND_ROW_HEIGHT_MM  # 单个色块高度（毫米）
        colorbar_gap = HAZARD_LEGEND_GAP_MM  # 色块之间间距（毫米，分开显示）
        colorbar_left_pad = 3.0  # 色块左边距（毫米）
        label_gap = 2.0  # 色块与标签之间间距（毫米）
        label_width = legend_width - colorbar_left_pad - colorbar_width - label_gap - 2.0

        # 检查总高度是否超出图例区域，若超出则压缩色块高度
        total_needed = colorbar_height * 5 + colorbar_gap * 4
        available_height = legend_y + legend_height - colorbar_start_y - 2.0
        if total_needed > available_height and available_height > 0:
            # 按比例压缩色块高度和间距
            compress_ratio = available_height / total_needed
            colorbar_height = colorbar_height * compress_ratio
            colorbar_gap = colorbar_gap * compress_ratio
            print(f"[信息] 图例高度不足，压缩色块高度至 {colorbar_height:.2f}mm，间距至 {colorbar_gap:.2f}mm")

        # 逐个绘制5个分开的色块及对应的危险等级名称标签
        for i in range(5):
            color = HAZARD_COLORS[i]
            color_str = f"{color.red()},{color.green()},{color.blue()},255"

            # 每个色块的Y起始坐标（色块分开，之间有间距）
            box_y = colorbar_start_y + i * (colorbar_height + colorbar_gap)

            # 绘制色块矩形（带黑色细边框）
            color_box = QgsLayoutItemShape(layout)
            color_box.setShapeType(QgsLayoutItemShape.Rectangle)
            color_box.attemptMove(
                QgsLayoutPoint(legend_x + colorbar_left_pad, box_y, QgsUnitTypes.LayoutMillimeters))
            color_box.attemptResize(
                QgsLayoutSize(colorbar_width, colorbar_height, QgsUnitTypes.LayoutMillimeters))
            box_symbol = QgsFillSymbol.createSimple({
                'color': color_str,
                'outline_color': '80,80,80,255',
                'outline_width': '0.15',
                'outline_width_unit': 'MM',
            })
            color_box.setSymbol(box_symbol)
            color_box.setFrameEnabled(False)
            layout.addLayoutItem(color_box)

            # 绘制危险等级名称标签（垂直居中于色块）
            label_x = legend_x + colorbar_left_pad + colorbar_width + label_gap
            # 标签高度与色块高度一致，确保垂直居中
            name_label = QgsLayoutItemLabel(layout)
            name_label.setText(HAZARD_LEVEL_NAMES[i])
            name_label.setTextFormat(hazard_label_format)
            name_label.attemptMove(QgsLayoutPoint(label_x, box_y, QgsUnitTypes.LayoutMillimeters))
            name_label.attemptResize(QgsLayoutSize(label_width, colorbar_height, QgsUnitTypes.LayoutMillimeters))
            name_label.setHAlign(Qt.AlignLeft)
            name_label.setVAlign(Qt.AlignVCenter)
            name_label.setFrameEnabled(False)
            name_label.setBackgroundEnabled(False)
            layout.addLayoutItem(name_label)

        print(f"[信息] 危险性等级图例添加完成，共5个分开色块")
    else:
        print("[信息] 无有效危险性分级数据，跳过危险性图例")

    print("[信息] 图例添加完成")


def _draw_star_icon(layout, x, center_y, width, height):
    """
    在图例指定位置绘制红色五角星图标

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 图标左边界X坐标（毫米）
        center_y (float): 图标垂直中心Y坐标（毫米）
        width (float): 图标区域宽度（毫米）
        height (float): 图标区域高度（毫米）
    """
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
    """
    在图例指定位置绘制地级市同心圆图标（白底外圆+黑色内实心圆）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 图标左边界X坐标（毫米）
        center_y (float): 图标垂直中心Y坐标（毫米）
        width (float): 图标区域宽度（毫米）
        height (float): 图标区域高度（毫米）
    """
    icon_size = min(width, height) * 0.6
    center_x = x + width / 2.0

    # 白底外圆
    outer_circle = QgsLayoutItemShape(layout)
    outer_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    outer_circle.attemptMove(
        QgsLayoutPoint(center_x - icon_size / 2.0, center_y - icon_size / 2.0, QgsUnitTypes.LayoutMillimeters))
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

    # 黑色内实心圆
    inner_size = icon_size * 0.4
    inner_circle = QgsLayoutItemShape(layout)
    inner_circle.setShapeType(QgsLayoutItemShape.Ellipse)
    inner_circle.attemptMove(
        QgsLayoutPoint(center_x - inner_size / 2.0, center_y - inner_size / 2.0, QgsUnitTypes.LayoutMillimeters))
    inner_circle.attemptResize(QgsLayoutSize(inner_size, inner_size, QgsUnitTypes.LayoutMillimeters))
    inner_symbol = QgsFillSymbol.createSimple({
        'color': '0,0,0,255',
        'outline_style': 'no',
    })
    inner_circle.setSymbol(inner_symbol)
    inner_circle.setFrameEnabled(False)
    layout.addLayoutItem(inner_circle)


def _draw_line_icon(layout, x, center_y, width, color, line_width_mm):
    """
    在图例指定位置绘制实线图标

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 线段左起点X坐标（毫米）
        center_y (float): 线段垂直中心Y坐标（毫米）
        width (float): 线段长度（毫米）
        color (QColor): 线段颜色
        line_width_mm (float): 线段宽度（毫米）
    """
    line_shape = QgsLayoutItemShape(layout)
    line_shape.setShapeType(QgsLayoutItemShape.Rectangle)
    line_height = max(line_width_mm, 0.5)
    line_shape.attemptMove(
        QgsLayoutPoint(x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
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
    """
    在图例指定位置绘制虚线图标（通过多个短矩形模拟虚线效果）

    参数:
        layout (QgsPrintLayout): 打印布局对象
        x (float): 虚线左起点X坐标（毫米）
        center_y (float): 虚线垂直中心Y坐标（毫米）
        width (float): 虚线总长度（毫米）
        color (QColor): 虚线颜色
        line_width_mm (float): 线段宽度（毫米）
        dash_gap_mm (float): 虚线间隔长度（毫米）
    """
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
        dash_shape.attemptMove(
            QgsLayoutPoint(current_x, center_y - line_height / 2.0, QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(
            QgsLayoutSize(actual_dash_length, line_height, QgsUnitTypes.LayoutMillimeters))
        dash_symbol = QgsFillSymbol.createSimple({
            'color': color_str,
            'outline_style': 'no',
        })
        dash_shape.setSymbol(dash_symbol)
        dash_shape.setFrameEnabled(False)
        layout.addLayoutItem(dash_shape)
        current_x += pattern_length


# ============================================================
# PNG导出函数
# ============================================================

def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片文件

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出PNG文件路径（相对或绝对路径）
        dpi (int): 输出分辨率，默认150 DPI

    返回:
        str 或 None: 成功返回输出文件绝对路径，失败返回None
    """
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

    except Exception as exc:
        logger.error('PNG导出异常: %s', exc, exc_info=True)
        raise


# ============================================================
# 主生成函数
# ============================================================

def generate_earthquake_hazard_map(longitude, latitude, magnitude,
                                   a, b, c,
                                   output_path="output_hazard_map.png",
                                   kml_path=None,
                                   dn_tif_path=None):
    """
    生成地震滑坡危险性评估图（主入口函数）

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        a (float): 危险性公式参数 a（P(f) = a * (1 - EXP(b * Dn^c))）
        b (float): 危险性公式参数 b
        c (float): 危险性公式参数 c
        output_path (str): 输出PNG文件路径，默认为 output_hazard_map.png
        kml_path (str 或 None): 烈度圈KML文件路径，None表示不加载烈度圈
        dn_tif_path (str 或 None): Dn.tif文件路径，None时使用默认路径

    返回:
        tuple: (output_image_path, statistics_summary)
               - output_image_path: 输出PNG文件路径（失败为None）
               - statistics_summary: 统计摘要文字字符串
    """
    logger.info('开始生成危险性评估图: lon=%.4f lat=%.4f M=%.1f a=%s b=%s c=%s output=%s',
                longitude, latitude, magnitude, a, b, c, output_path)
    try:
        return _generate_earthquake_hazard_map_impl(
            longitude, latitude, magnitude, a, b, c, output_path, kml_path, dn_tif_path
        )
    except Exception as exc:
        logger.error('生成危险性评估图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_hazard_map_impl(longitude, latitude, magnitude,
                                         a, b, c,
                                         output_path, kml_path, dn_tif_path):
    """
    generate_earthquake_hazard_map 的内部实现函数

    参数:
        longitude (float): 震中经度（度）
        latitude (float): 震中纬度（度）
        magnitude (float): 地震震级
        a (float): 公式参数 a
        b (float): 公式参数 b
        c (float): 公式参数 c
        output_path (str): 输出PNG文件路径
        kml_path (str 或 None): 烈度圈KML文件路径
        dn_tif_path (str 或 None): Dn.tif文件路径

    返回:
        tuple: (output_image_path, statistics_summary)
    """
    print("=" * 60)
    print(f"[开��] 生成地震滑坡危险性评估图")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  公式参数: a={a}, b={b}, c={c}")
    print(f"  输出: {output_path}")
    if kml_path:
        print(f"  烈度圈KML: {kml_path}")
    print(f"  GDAL可用: {GDAL_AVAILABLE}")
    print("=" * 60)

    # 确定Dn.tif文件路径
    tif_path = dn_tif_path if dn_tif_path else DN_TIF_PATH
    abs_tif_path = resolve_path(tif_path) if not os.path.isabs(tif_path) else tif_path

    # 获取震级配置（地图范围和比例尺）
    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    # 计算地图范围
    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    # 计算地图像素高度
    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 初始化QGIS（统一通过 QGISManager 管理）
    from qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    temp_manager = get_temp_manager()
    # 天地图注记临时文件路径
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_temp_annotation_hazard.png")
    # 指北针SVG临时文件路径
    svg_temp_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_north_arrow_hazard_temp.svg")

    # 初始化统计结果（用于异常时返回默认值）
    statistics_summary = "统计信息生成失败"
    output_image_path = None

    try:
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        # ---- 步骤1：下载天地图注记（可降级）----
        annotation_raster = None
        try:
            annotation_raster = download_tianditu_annotation_tiles(
                extent, width_px, height_px, temp_annotation_path)
        except Exception as exc:
            logger.warning('天地图注记下载失败，跳过注记图层: %s', exc)
            print(f"[警告] 天地图注记下载失败，跳过注记图层: {exc}")

        # ---- 步骤2：生成危险性概率栅格 ----
        hazard_layer = None
        breaks = None
        max_dn_value = None
        area_stats = {}
        prob_flat = None

        if not GDAL_AVAILABLE:
            print("[警告] GDAL不可用，无法生成危险性栅格，跳过危险性图层")
        elif not os.path.exists(abs_tif_path):
            print(f"[警告] Dn.tif文件不存在: {abs_tif_path}，跳过危险性图层")
        else:
            try:
                # 生成危险性概率TIF文件（裁剪范围 + 公式计算）
                hazard_tif_path = temp_manager.get_temp_file(suffix="_hazard_prob.tif")
                result_path, max_dn_value, prob_flat = generate_hazard_tif(
                    abs_tif_path, hazard_tif_path, extent, a, b, c)

                if result_path and prob_flat is not None and len(prob_flat) > 0:
                    # 使用自然断点法对概率值分级
                    breaks = classify_hazard_levels(prob_flat, num_classes=5)

                    # 加载危险性概率栅格图层并应用颜色渲染
                    hazard_layer = QgsRasterLayer(result_path, "危险性评估")
                    if hazard_layer.isValid():
                        apply_hazard_renderer(hazard_layer, breaks)
                        project.addMapLayer(hazard_layer)
                        print("[信息] 危险性评估栅格图层加载成功")
                    else:
                        print("[警告] 危险性评估栅格图层无效，跳过")
                        hazard_layer = None

                    # 计算各危险等级面积统计
                    area_stats = calculate_area_statistics(
                        prob_flat, breaks, abs_tif_path, extent)
                else:
                    print("[警告] 危险性概率栅格生成失败或无有效数据")
                    area_stats = {name: {'area_km2': 0.0, 'percent': 0.0}
                                  for name in HAZARD_LEVEL_NAMES}
                    area_stats['total_valid_km2'] = 0.0

            except Exception as exc:
                logger.warning('生成危险性栅格失败，跳过: %s', exc)
                print(f"[警告] 生成危险性栅格失败，跳过: {exc}")
                area_stats = {name: {'area_km2': 0.0, 'percent': 0.0}
                              for name in HAZARD_LEVEL_NAMES}
                area_stats['total_valid_km2'] = 0.0

        # ---- 步骤3：加载矢量边界图层（可降级）----
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

        # 创建省份标注点图层（支持震中附近偏移）
        province_label_layer = None
        if province_layer:
            try:
                province_label_layer = create_province_label_layer(
                    province_layer, longitude, latitude, extent)
                if province_label_layer:
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

        # ---- 步骤4：加载辅助点位和图例图层（可降级）----
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

        # ---- 步骤5：加载烈度圈（可降级）----
        intensity_layer = None
        if kml_path:
            try:
                abs_kml = kml_path if os.path.isabs(kml_path) else resolve_path(kml_path)
                intensity_data = parse_intensity_kml(abs_kml)
                if intensity_data:
                    intensity_layer = create_intensity_layer(intensity_data)
                    if intensity_layer:
                        project.addMapLayer(intensity_layer)
            except Exception as exc:
                logger.warning('加载烈度圈图层失败，跳过: %s', exc)
                print(f"[警告] 加载烈度圈图层失败，跳过: {exc}")

        # ---- 步骤6：创建震中图层 ----
        epicenter_layer = None
        try:
            epicenter_layer = create_epicenter_layer(longitude, latitude)
            if epicenter_layer:
                project.addMapLayer(epicenter_layer)
        except Exception as exc:
            logger.warning('创建震中图层失败，跳过: %s', exc)
            print(f"[警告] 创建震中图层失败，跳过: {exc}")

        # 注记图层最后加载（显示在最上层）
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # ---- 步骤7：设置图层显示顺序（顶层在列表前）----
        ordered_layers = [lyr for lyr in [
            epicenter_layer,  # 震中（最顶层）
            annotation_raster,  # 天地图注记
            intensity_layer,  # 烈度圈
            city_point_layer,  # 地级市点位
            province_label_layer,  # 省份标注（独立点图层）
            province_layer,  # 省界
            city_layer,  # 市界
            county_layer,  # 县界
            hazard_layer,  # 危险性评估热力图（最底层）
        ] if lyr is not None]

        # ---- 步骤8：创建打印布局（关键步骤，失败则抛出异常）----
        try:
            layout = create_print_layout(
                project, longitude, latitude, magnitude,
                extent, scale, map_height_mm,
                breaks=breaks,
                ordered_layers=ordered_layers
            )
        except Exception as exc:
            logger.error('创建打印布局失败: %s', exc, exc_info=True)
            raise

        # ---- 步骤9：导出PNG（关键步骤，失败则抛出异常）----
        try:
            output_image_path = export_layout_to_png(layout, output_path, OUTPUT_DPI)
        except Exception as exc:
            logger.error('导出PNG失败: %s', exc, exc_info=True)
            raise

        # ---- 步骤10：生成统计摘要文字 ----
        try:
            statistics_summary = build_statistics_summary(max_dn_value, area_stats)
            print(f"[信息] 统计摘要: {statistics_summary}")
        except Exception as exc:
            logger.warning('生成统计摘要失败: %s', exc)
            statistics_summary = "统计信息生成失败"

    finally:
        # 清理所有临时文件和目录
        temp_manager.cleanup()

        # 清理指北针SVG临时文件
        if os.path.exists(svg_temp_path):
            try:
                os.remove(svg_temp_path)
            except OSError:
                pass

        # 清理天地图注记临时文件（PNG + 世界文件）
        if os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw_path = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw_path):
                    os.remove(pgw_path)
            except OSError:
                pass

    print("=" * 60)
    if output_image_path:
        print(f"[完成] 危险性评估图已输出: {output_image_path}")
        print(f"[完成] 统计摘要: {statistics_summary}")
    else:
        print("[失败] 危险性评估图输出失败")
    print("=" * 60)

    return output_image_path, statistics_summary


# ============================================================
# 测试方法
# ============================================================

def run_all_tests():
    """
    运行所有单元测试（不依赖QGIS环境的纯函数测试）

    测试内容包括：
    - 震级配置获取
    - 地图范围计算
    - 罗马数字转换
    - 危险性概率公式计算
    - 自然断点法分级
    - 面积统计摘要生成
    - 危险性概率栅格向量化计算
    - 刻度间隔选取
    """
    print("\n" + "=" * 60)
    print("运行 earthquake_hazard_map 全部测试")
    print("=" * 60)

    # ---- 测试1：震级配置获取 ----
    print("\n--- 测试1: get_magnitude_config ---")
    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000, f"期望150000，实际{config_s['scale']}"
    print(f"  M4.5 -> 比例尺1:{config_s['scale']} ✓")

    config_m = get_magnitude_config(6.5)
    assert config_m["scale"] == 500000, f"期望500000，实际{config_m['scale']}"
    print(f"  M6.5 -> 比例尺1:{config_m['scale']} ✓")

    config_l = get_magnitude_config(7.5)
    assert config_l["scale"] == 1500000, f"期望1500000，实际{config_l['scale']}"
    print(f"  M7.5 -> 比例尺1:{config_l['scale']} ✓")

    # ---- 测试2：地图范围计算 ----
    print("\n--- 测试2: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum(), "震中经度应在范围内"
    assert extent.yMinimum() < 39.9 < extent.yMaximum(), "震中纬度应在范围内"
    print(f"  15km半径范围计算正确 ✓")
    print(f"  范围: ({extent.xMinimum():.4f},{extent.yMinimum():.4f}) - "
          f"({extent.xMaximum():.4f},{extent.yMaximum():.4f})")

    # ---- 测试3：罗马数字转换 ----
    print("\n--- 测试3: int_to_roman ---")
    assert int_to_roman(4) == "IV", f"期望IV，实际{int_to_roman(4)}"
    assert int_to_roman(9) == "IX", f"期望IX，实际{int_to_roman(9)}"
    assert int_to_roman(8) == "VIII", f"期望VIII，实际{int_to_roman(8)}"
    print("  罗马数字转换正确: 4->IV, 9->IX, 8->VIII ✓")

    # ---- 测试4：危险性概率公式 ----
    print("\n--- 测试4: calculate_hazard_probability ---")
    # Dn <= 0.1cm 时概率为0
    p_safe = calculate_hazard_probability(0.05, a=0.335, b=-0.048, c=0.565)
    assert p_safe == 0.0, f"Dn=0.05cm时应为0，实际{p_safe}"
    print(f"  Dn=0.05cm (<=0.1): P={p_safe} ✓")

    p_safe2 = calculate_hazard_probability(0.1, a=0.335, b=-0.048, c=0.565)
    assert p_safe2 == 0.0, f"Dn=0.1cm时应为0，实际{p_safe2}"
    print(f"  Dn=0.10cm (=阈值): P={p_safe2} ✓")

    # Dn > 0.1cm 时概率 > 0
    p_hazard = calculate_hazard_probability(10.0, a=0.335, b=-0.048, c=0.565)
    assert p_hazard > 0.0, f"Dn=10cm时概率应>0，实际{p_hazard}"
    assert 0.0 <= p_hazard <= 1.0, f"概率应在[0,1]范围内，实际{p_hazard}"
    print(f"  Dn=10.0cm: P={p_hazard:.4f} ✓")

    # 验证概率值被限制在[0,1]
    p_clamped = calculate_hazard_probability(1000.0, a=0.335, b=-0.048, c=0.565)
    assert 0.0 <= p_clamped <= 1.0, f"极大Dn时概率应在[0,1]，实际{p_clamped}"
    print(f"  Dn=1000cm (极大值限制): P={p_clamped:.4f} ✓")

    # ---- 测试5：向量化危险性概率计算 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试5: compute_hazard_raster ---")
        test_dn = np.array([[0.0, 0.05, 0.1, 1.0, 10.0],
                            [50.0, -9999.0, 100.0, 0.08, 5.0]], dtype=np.float64)
        prob_result = compute_hazard_raster(test_dn, nodata_value=-9999.0,
                                            a=0.335, b=-0.048, c=0.565)

        # NoData位置应为0（判定为不危险）
        assert prob_result[1, 1] == 0.0, f"NoData位置应为0（不危险），实际{prob_result[1, 1]}"
        print(f"  NoData(-9999)位置: prob={prob_result[1, 1]}（判定为不危险）✓")

        # Dn <= 0.1cm 的位置应为0
        assert prob_result[0, 0] == 0.0, f"Dn=0位置应为0，实际{prob_result[0, 0]}"
        assert prob_result[0, 1] == 0.0, f"Dn=0.05位置应为0，实际{prob_result[0, 1]}"
        assert prob_result[0, 2] == 0.0, f"Dn=0.1位置应为0，实际{prob_result[0, 2]}"
        print(f"  Dn<=0.1cm位置概率均为0 ✓")

        # Dn > 0.1cm 的位置应有正概率
        assert prob_result[0, 3] > 0.0, f"Dn=1.0位置应>0，实际{prob_result[0, 3]}"
        assert prob_result[0, 4] > 0.0, f"Dn=10.0位置应>0，实际{prob_result[0, 4]}"
        # 概率值应随Dn增大而增大（b为负时）
        assert prob_result[0, 4] >= prob_result[0, 3], \
            f"Dn=10时概率应>=Dn=1时，实际{prob_result[0, 4]} vs {prob_result[0, 3]}"
        print(f"  Dn>0.1cm时概率正确计算，且随Dn增大而增大 ✓")

        # 所有有效概率值应在[0,1]范围内
        # 所有概率值应在[0,1]范围内（NoData也被设为0）
        assert float(np.min(prob_result)) >= 0.0, "所有概率应>=0"
        assert float(np.max(prob_result)) <= 1.0, "所有概率应<=1"
        print(f"  所有概率值均在[0,1]范围内 ✓")
    else:
        print("\n--- 测试5: compute_hazard_raster (GDAL不可用，跳过) ---")

    # ---- 测试6：自然断点法分级 ----
    if GDAL_AVAILABLE:
        print("\n--- 测试6: classify_hazard_levels ---")
        # 创建测试概率数据
        np.random.seed(42)
        test_probs = np.concatenate([
            np.zeros(200),  # 大量零概率（安全区）
            np.random.uniform(0.001, 0.1, 100),  # 低危险
            np.random.uniform(0.1, 0.3, 80),  # 较低危险
            np.random.uniform(0.3, 0.6, 60),  # 中等危险
            np.random.uniform(0.6, 0.8, 40),  # 较高危险
            np.random.uniform(0.8, 1.0, 20),  # 高度危险
        ])

        breaks = classify_hazard_levels(test_probs, num_classes=5)
        assert len(breaks) == 6, f"边界值列表长度应为6，实际{len(breaks)}"
        assert breaks[0] == 0.0, f"第一个边界应为0，实际{breaks[0]}"
        # 边界值应单调递增
        for i in range(len(breaks) - 1):
            assert breaks[i] <= breaks[i + 1], \
                f"边界值应单调递增，breaks[{i}]={breaks[i]} > breaks[{i + 1}]={breaks[i + 1]}"
        print(f"  边界值列表: {[f'{v:.4f}' for v in breaks]} ✓")
        print(f"  边界值长度为6，第一个为0，单调递增 ✓")

        # 测试空数据情况
        breaks_empty = classify_hazard_levels(np.array([]), num_classes=5)
        assert len(breaks_empty) == 6, "空数据时应返回长度为6的等间距列表"
        print(f"  空数据时返回默认等间距分类 ✓")

        # 测试全零数据情况
        breaks_zero = classify_hazard_levels(np.zeros(100), num_classes=5)
        assert breaks_zero[0] == 0.0, "全零数据时第一个边界应为0"
        print(f"  全零概率数据时正确处理 ✓")
    else:
        print("\n--- 测试6: classify_hazard_levels (GDAL不可用，跳过) ---")

    # ---- 测试7：统计摘要生成 ----
    print("\n--- 测试7: build_statistics_summary ---")
    test_area_stats = {
        "低度危险区": {'area_km2': 1500.0, 'percent': 60.0},
        "较低危险区": {'area_km2': 500.0, 'percent': 20.0},
        "中等危险区": {'area_km2': 300.0, 'percent': 12.0},
        "较高危险区": {'area_km2': 150.0, 'percent': 6.0},
        "高度危险区": {'area_km2': 50.0, 'percent': 2.0},
        'total_valid_km2': 2500.0,
    }
    summary = build_statistics_summary(25.5, test_area_stats)
    assert "25.50cm" in summary, f"摘要中应包含最大Dn值，实际: {summary}"
    assert "低度危险区" not in summary or "极低危险等级" in summary, "摘要应包含危险等级面积信息"
    assert "平方千米" in summary, "摘要应包含面积单位"
    assert "占比" in summary, "摘要应包含占比信息"
    print(f"  统计摘要生成正确 ✓")
    print(f"  摘要示例: {summary[:80]}...")

    # 测试None最大值
    summary_none = build_statistics_summary(None, test_area_stats)
    assert "N/A" in summary_none, "最大值为None时应显示N/A"
    print(f"  最大值为None时正确显示N/A ✓")

    # 测试整数最大值格式化
    summary_int = build_statistics_summary(100.0, test_area_stats)
    assert "100cm" in summary_int, f"整数最大值应不含小数点，实际: {summary_int[:50]}"
    print(f"  整数最大值格式化正确（不含多余小数位）✓")

    # ---- 测试8：刻度间隔选取 ----
    print("\n--- 测试8: _choose_tick_step ---")
    step_small = _choose_tick_step(0.5)
    assert step_small in [0.01, 0.02, 0.05, 0.1, 0.2, 0.25], f"小范围刻度应为小值，实际{step_small}"
    print(f"  0.5度范围 -> 刻度间隔: {step_small} ✓")

    step_large = _choose_tick_step(10.0)
    assert step_large in [1.0, 2.0, 5.0], f"大范围刻度应为大值，实际{step_large}"
    print(f"  10度范围 -> 刻度间隔: {step_large} ✓")

    # ---- 测试9：KML烈度解析工具函数 ----
    print("\n--- 测试9: _extract_intensity_from_name ---")
    assert _extract_intensity_from_name("8度") == 8, "应解析出8"
    assert _extract_intensity_from_name("VII度") == 7, "应解析出7"
    assert _extract_intensity_from_name("IX") == 9, "应解析出9"
    assert _extract_intensity_from_name("") is None, "空字符串应返回None"
    assert _extract_intensity_from_name("无效名称") is None, "无效名称应返回None"
    print("  烈度名称解析: '8度'->8, 'VII度'->7, 'IX'->9, ''->None ✓")

    print("\n" + "=" * 60)
    print("全部测试执行完成 ✓")
    print("=" * 60)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        # 运行测试模式（不需要QGIS环境的纯函数测试）
        run_all_tests()
    elif len(sys.argv) >= 7:
        # 命令行运行模式：传入震中、震级和公式参数
        # 用法: python earthquake_hazard_map.py <经度> <纬度> <震级> <a> <b> <c>
        #       [输出文件名] [kml路径] [Dn_tif路径]
        try:
            _lon = float(sys.argv[1])
            _lat = float(sys.argv[2])
            _mag = float(sys.argv[3])
            _a = float(sys.argv[4])
            _b = float(sys.argv[5])
            _c = float(sys.argv[6])
            _out = sys.argv[7] if len(sys.argv) > 7 else \
                f"earthquake_hazard_M{_mag}_{_lon}_{_lat}.png"
            _kml = sys.argv[8] if len(sys.argv) > 8 else None
            _dn_tif = sys.argv[9] if len(sys.argv) > 9 else None
            _img_path, _summary = generate_earthquake_hazard_map(
                _lon, _lat, _mag, _a, _b, _c, _out, _kml, _dn_tif)
            print(f"\n统计摘要:\n{_summary}")
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_hazard_map.py <经度> <纬度> <震级> <a> <b> <c> "
                  "[输出文件名] [kml路径] [Dn_tif路径]")
    else:
        # 使用默认参数演示运行
        print("使用默认参数运行示例...")
        _img_path, _summary = generate_earthquake_hazard_map(
            longitude=103.36,
            latitude=34.09,
            magnitude=5.0,
            a=0.1169,
            b=-0.1803,
            c=0.5165,
            output_path="earthquake_hazard_M7.0.png"
        )
        if _img_path:
            print(f"\n统计摘要:\n{_summary}")