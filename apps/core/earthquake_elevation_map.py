# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的地震震中数字高程图生成脚本
参考 earthquake_geological_map2.py 的布局、指北针、经纬度、比例尺、省市加载方式。

完全适配QGIS 3.40.15 API。

高程图例说明：
- 数字高程TIF文件使用拉伸渲染（从最小值到最大值的连续渐变）
- 颜色渐变：蓝色（低）→ 青色 → 绿色 → 黄色 → 红色（高）
- 图例显示：渐变色条 + 最小值/最大值标签

优化说明：
- 针对大文件（15G）TIF进行优化，只裁剪加载需要范围内的数据
- 使用GDAL的虚拟栅格(VRT)技术实现按需裁剪
- 显著减少内存占用和处理时间
- 只加载天地图矢量注记图层（放置在最上层），不加载矢量底图
"""

import os
import sys
import math
import logging
import tempfile
import shutil
import requests
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

from core.tianditu_basemap_downloader import download_tianditu_annotation_tiles

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.earthquake_elevation_map')

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
    QgsRasterBandStats,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# GDAL导入（用于栅格裁剪）
try:
    from osgeo import gdal, osr

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

ELEVATION_TIF_PATH = (
    getattr(_django_settings, 'ELEVATION_TIF_PATH',
            'C:/地质/图4地形地貌/全国哥白尼DEM数据.tif')
    if _DJANGO_AVAILABLE else 'C:/地质/图4地形地貌/全国哥白尼DEM数据.tif'
)
PROVINCE_SHP_PATH = (
    getattr(_django_settings, 'PROVINCE_SHP_PATH',
            _DEFAULT_BASE + '行政区划/省界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/省界.shp'
)
CITY_SHP_PATH = (
    getattr(_django_settings, 'CITY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/市界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/市界.shp'
)
COUNTY_SHP_PATH = (
    getattr(_django_settings, 'COUNTY_SHP_PATH',
            _DEFAULT_BASE + '行政区划/县界.shp')
    if _DJANGO_AVAILABLE else
    _DEFAULT_BASE + '行政区划/县界.shp'
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
LONLAT_FONT_SIZE_PT = 10

# === 省界样式 ===
PROVINCE_COLOR = QColor(160, 160, 160)
PROVINCE_LINE_WIDTH_MM = 0.4
PROVINCE_LABEL_FONT_SIZE_PT = 8
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)
PROVINCE_EPICENTER_COINCIDENCE_TOL = 1e-6

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
LEGEND_ELEVATION_FONT_SIZE_PT = 10  # 高程图例字体大小

# === 基本图例项配置（可单独设置） ===
BASIC_LEGEND_FONT_SIZE_PT = 10  # 基本图例项字体大小
BASIC_LEGEND_ROW_HEIGHT_MM = 6.0  # 基本图例项行高

# === 高程图例项配置（可单独设置） ===
ELEVATION_LEGEND_FONT_SIZE_PT = 10  # 高程图例项字体大小
ELEVATION_LEGEND_ROW_HEIGHT_MM = 6  # 高程图例项行高

# === 比例尺字体 ===
SCALE_FONT_SIZE_PT = 8

# === 震中五角星 ===
EPICENTER_STAR_SIZE_MM = 5.0
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_STROKE_COLOR = QColor(255, 255, 255)
EPICENTER_STROKE_WIDTH_MM = 0.4

# WGS84
CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# === 裁剪缓冲区（度） ===
# 在目标范围外增加缓冲区，确保边缘数据完整
CLIP_BUFFER_DEGREES = 0.1

# === 图例布局和字体设置 ===
LEGEND_ROW_COUNT = 2  # 图例行数
LEGEND_COLUMN_COUNT = 3  # 图例列数
LEGEND_FONT_TIMES_NEW_ROMAN = "Times New Roman"  # (m)标签字体
LEGEND_ITEM_SPACING = 2  # 图例项间距


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
        float: 地图高度��毫米）
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
# 大文件栅格裁剪优��函数
# ============================================================

def clip_raster_to_extent(input_path, output_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    使用GDAL裁剪大型栅格文件到指定范围

    该函数针对大文件（如15GB的TIF）进行优化，只读取和输出需要的区域数据，
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
        print("[警告] GDAL不可用，无法进行栅格裁剪")
        return None

    if not os.path.exists(input_path):
        print(f"[错误] 输入栅格文件不存在: {input_path}")
        return None

    try:
        src_ds = gdal.Open(input_path, gdal.GA_ReadOnly)
        if src_ds is None:
            print(f"[错误] 无法打开栅格文件: {input_path}")
            return None

        src_width = src_ds.RasterXSize
        src_height = src_ds.RasterYSize
        src_bands = src_ds.RasterCount

        print(f"[信息] 源栅格信息: {src_width}x{src_height}, {src_bands}波段")

        clip_xmin = extent.xMinimum() - buffer_degrees
        clip_xmax = extent.xMaximum() + buffer_degrees
        clip_ymin = extent.yMinimum() - buffer_degrees
        clip_ymax = extent.yMaximum() + buffer_degrees

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

        print(f"[信息] 正在裁剪栅格数据...")
        dst_ds = gdal.Translate(output_path, src_ds, options=translate_options)

        if dst_ds is None:
            print(f"[错误] 栅格裁剪失败")
            src_ds = None
            return None

        dst_width = dst_ds.RasterXSize
        dst_height = dst_ds.RasterYSize

        dst_ds = None
        src_ds = None

        src_size = os.path.getsize(input_path) / (1024 * 1024 * 1024)
        dst_size = os.path.getsize(output_path) / (1024 * 1024)

        print(f"[信息] 裁剪完成: {dst_width}x{dst_height}")
        print(f"[信息] 原文件: {src_size:.2f}GB -> 裁剪后: {dst_size:.2f}MB")

        return output_path

    except Exception as e:
        print(f"[错误] 栅格裁剪异常: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_vrt_for_extent(input_path, output_vrt_path, extent, buffer_degrees=CLIP_BUFFER_DEGREES):
    """
    创建虚拟栅格(VRT)文件，只引用指定范围的数据

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
            self.temp_dir = tempfile.mkdtemp(prefix="earthquake_elevation_")
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
# 高程栅格图层渲染设置
# ============================================================

def apply_elevation_renderer(raster_layer):
    """
    为高程栅格图层应用拉伸渲染器（从最小值到最大值的连续渐变，蓝色→红色）

    参数:
        raster_layer (QgsRasterLayer): 高程栅格图层

    返回:
        tuple: (min_val, max_val) 实际高程最小/最大值，失败返回 (None, None)
    """
    if raster_layer is None or not raster_layer.isValid():
        print("[错误] 无效的栅格图层，无法应用渲染器")
        return None, None

    try:
        # 从栅格获取实际最小/最大值
        min_val, max_val = None, None
        if GDAL_AVAILABLE:
            try:
                ds = gdal.Open(raster_layer.source())
                if ds is not None:
                    band = ds.GetRasterBand(1)
                    stats = band.GetStatistics(True, True)
                    if stats and stats[0] < stats[1]:
                        min_val = stats[0]
                        max_val = stats[1]
                    ds = None
            except Exception as e:
                print(f"[警告] GDAL获取统计信息失败: {e}")

        if min_val is None or max_val is None:
            try:
                provider = raster_layer.dataProvider()
                stats = provider.bandStatistics(
                    1, QgsRasterBandStats.Min | QgsRasterBandStats.Max)
                min_val = stats.minimumValue
                max_val = stats.maximumValue
            except Exception as e:
                print(f"[警告] QGIS获取统计信息失败: {e}")
                min_val = 0.0
                max_val = 8850.0

        if min_val >= max_val:
            min_val = 0.0
            max_val = 8850.0

        print(f"[信息] 高程范围: {min_val:.1f} ~ {max_val:.1f} m")

        range_val = max_val - min_val

        shader = QgsRasterShader()
        color_ramp_shader = QgsColorRampShader()
        color_ramp_shader.setColorRampType(QgsColorRampShader.Interpolated)

        # 蓝色（低）→ 青色 → 绿色 → 黄色 → 红色（高）
        color_ramp_items = [
            QgsColorRampShader.ColorRampItem(
                min_val, QColor(0, 0, 255), f"{min_val:.0f}"),
            QgsColorRampShader.ColorRampItem(
                min_val + range_val * 0.25, QColor(0, 255, 255), ""),
            QgsColorRampShader.ColorRampItem(
                min_val + range_val * 0.5, QColor(0, 128, 0), ""),
            QgsColorRampShader.ColorRampItem(
                min_val + range_val * 0.75, QColor(255, 255, 0), ""),
            QgsColorRampShader.ColorRampItem(
                max_val, QColor(255, 0, 0), f"{max_val:.0f}"),
        ]
        color_ramp_shader.setColorRampItemList(color_ramp_items)
        shader.setRasterShaderFunction(color_ramp_shader)

        renderer = QgsSingleBandPseudoColorRenderer(
            raster_layer.dataProvider(), 1, shader)
        raster_layer.setRenderer(renderer)
        raster_layer.triggerRepaint()

        print(f"[信息] 高程图层拉伸渲染器设置完成，范围: {min_val:.1f} ~ {max_val:.1f} m")
        return min_val, max_val

    except Exception as e:
        print(f"[错误] 应用高程渲染器失败: {e}")
        return None, None


def build_elevation_legend_list(min_val, max_val):
    """
    构建高程图例列表（拉伸渲染，只显示最小值和最大值）

    参数:
        min_val (float): 高程最小值
        max_val (float): 高程最大值

    返回:
        list: [(color_rgba, label), ...] - 含最小值（蓝色）和最大值（红色）两项
    """
    if min_val is None:
        min_val = 0.0
    if max_val is None:
        max_val = 8850.0

    min_label = f"{min_val:.0f}"
    max_label = f"{max_val:.0f}"

    result = [
        ((0, 0, 255, 255), min_label),   # 蓝色 - 最小值
        ((255, 0, 0, 255), max_label),   # 红色 - 最大值
    ]
    print(f"[信息] 构建高程图例列表完成: 最小值={min_label}, 最大值={max_label}")
    return result


# ============================================================
# 图层加载函数
# ============================================================

def load_elevation_raster_optimized(tif_path, extent):
    """
    优化加载数字高程TIF栅格图层（针对大文件优化）

    参数:
        tif_path (str): TIF文件路径
        extent (QgsRectangle): 目标地图范围

    返回:
        tuple: (QgsRasterLayer, min_val, max_val) - 高程栅格图层及实际最小/最大高程值，
               加载失败时返回 (None, None, None)
    """
    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 数字高程底图文件不存在: {abs_path}")
        return None, None, None

    file_size_gb = os.path.getsize(abs_path) / (1024 * 1024 * 1024)
    print(f"[信息] 高程文件大小: {file_size_gb:.2f}GB")

    if GDAL_AVAILABLE and file_size_gb > 0.5:
        print(f"[信息] 文件较大，启用裁剪优化...")

        temp_manager = get_temp_manager()
        clipped_path = temp_manager.get_temp_file(suffix="_elevation_clipped.tif")

        result_path = clip_raster_to_extent(abs_path, clipped_path, extent)

        if result_path and os.path.exists(result_path):
            layer = QgsRasterLayer(result_path, "数字高程底图")
            if layer.isValid():
                print(f"[信息] 成功加载裁剪后的高程底图")
                elev_min, elev_max = apply_elevation_renderer(layer)
                return layer, elev_min, elev_max
            else:
                print(f"[警告] 裁剪后的栅格无效，尝试直接加载原文件")
        else:
            print(f"[警告] 栅格裁剪失败，尝试直接加载原文件")

    print(f"[信息] 直接加载高程底图（可能较慢）...")
    layer = QgsRasterLayer(abs_path, "数字高程底图")
    if not layer.isValid():
        print(f"[错误] 无法加载数字高程底图: {abs_path}")
        return None, None, None

    print(f"[信息] 成功加载数字高程底图: {abs_path}")
    elev_min, elev_max = apply_elevation_renderer(layer)
    return layer, elev_min, elev_max


def load_elevation_raster(tif_path):
    """
    加载数字高程TIF栅格图层并应用拉伸渲染（不进行裁剪）

    参数:
        tif_path (str): TIF文件路径

    返回:
        tuple: (QgsRasterLayer, min_val, max_val)，加载失败返回 (None, None, None)
    """
    abs_path = resolve_path(tif_path)
    if not os.path.exists(abs_path):
        print(f"[错误] 数字高程底图文件不存在: {abs_path}")
        return None, None, None

    layer = QgsRasterLayer(abs_path, "数字高程底图")
    if not layer.isValid():
        print(f"[错误] 无法加载数字高程底图: {abs_path}")
        return None, None, None

    print(f"[信息] 成功加载数字高程底图: {abs_path}")
    elev_min, elev_max = apply_elevation_renderer(layer)
    return layer, elev_min, elev_max


def load_vector_layer(shp_path, layer_name):
    """
    加载矢量图层（SHP文件）

    参数:
        shp_path (str): SHP文件路径
        layer_name (str): 图层名称

    返回:
        QgsVectorLayer: 矢量图层，加载失败返回None
    """
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


def create_province_label_layer(province_layer, epicenter_lon, epicenter_lat, extent):
    """
    创建省份标注点图层，支持震中附近省份标注自动偏移。

    当省份质心与震中坐标重合时，标注点向右下角偏移3mm，避免遮挡震中五角星标识。

    参数:
        province_layer (QgsVectorLayer): 省界多边形图层
        epicenter_lon (float): 震中经度（度）
        epicenter_lat (float): 震中纬度（度）
        extent (QgsRectangle 或 None): 地图范围，用于计算偏移量（mm转度）

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
    lon_offset_deg = offset_mm / MAP_WIDTH_MM * map_width_deg   # 向右偏移（经度增大）
    lat_offset_deg = offset_mm / MAP_WIDTH_MM * map_height_deg  # 向下偏移（纬度减小）

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
        if abs(cx - epicenter_lon) < PROVINCE_EPICENTER_COINCIDENCE_TOL and abs(cy - epicenter_lat) < PROVINCE_EPICENTER_COINCIDENCE_TOL:
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


def style_province_layer(layer, center_lon=None, center_lat=None, extent=None):
    """
    设置省界图层样式

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


def style_city_layer(layer):
    """
    设置市界图层样式

    参数:
        layer (QgsVectorLayer): 市界图层
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

    print(
        f"[信息] 市界图层样式设置完成 - 颜色: RGB({CITY_COLOR.red()},{CITY_COLOR.green()},{CITY_COLOR.blue()}), "
        f"线宽: {CITY_LINE_WIDTH_MM}mm, 虚线间隔: {CITY_DASH_GAP_MM}mm")


def style_county_layer(layer):
    """
    设置县界图层样式

    参数:
        layer (QgsVectorLayer): 县界图层
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

    print(
        f"[信息] 县界图层样式设置完成 - 颜色: RGB({COUNTY_COLOR.red()},{COUNTY_COLOR.green()},{COUNTY_COLOR.blue()}), "
        f"线宽: {COUNTY_LINE_WIDTH_MM}mm, 虚线间隔: {COUNTY_DASH_GAP_MM}mm")


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


# ============================================================
# 震中和图例图层创建
# ============================================================

def create_epicenter_layer(longitude, latitude):
    """
    创建震中标记图层：红色五角星+白边

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


def create_city_point_layer(extent):
    """
    加载地级市点位数据（不显示标注，只显示点位）

    参数:
        extent (QgsRectangle): 地图范围

    返回:
        QgsVectorLayer: 地级市点位图层
    """
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

    # 不再设置标注，只显示点位
    layer.setLabelsEnabled(False)

    layer.triggerRepaint()
    print(f"[信息] 加载地级市点位图层完成（不显示标注）")
    return layer


def create_province_legend_layer():
    """
    创建省界图例用的线图层

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
    print("[信息] 创建省界图例线图层")
    return layer


def create_city_legend_layer():
    """
    创建市界图例用的线图层

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
    print(f"[信息] 创建市界图例线图层 - 线宽: {CITY_LINE_WIDTH_MM}mm, 虚线间隔: {CITY_DASH_GAP_MM}mm")
    return layer


def create_county_legend_layer():
    """
    创建县界图例用的线图层

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
    print(f"[信息] 创建县界图例线图层 - 线宽: {COUNTY_LINE_WIDTH_MM}mm, 虚线间隔: {COUNTY_DASH_GAP_MM}mm")
    return layer


# ============================================================
# 布局创建
# ============================================================

def create_print_layout(project, longitude, latitude, magnitude, extent, scale, map_height_mm,
                        elevation_legend_list=None, ordered_layers=None):
    """
    创建QGIS打印布局

    参数:
        project (QgsProject): QGIS项目实例
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        extent (QgsRectangle): 地图范围
        scale (int): 比例尺
        map_height_mm (float): 地图高度（毫米）
        elevation_legend_list (list): 高程图例列表
        ordered_layers (list): 按渲染顺序排列的图层列表

    返回:
        QgsPrintLayout: 打印布局对象
    """
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("地震数字高程图")
    layout.setUnits(QgsUnitTypes.LayoutMillimeters)

    output_height_mm = BORDER_TOP_MM + map_height_mm + BORDER_BOTTOM_MM

    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(MAP_TOTAL_WIDTH_MM, output_height_mm,
                                   QgsUnitTypes.LayoutMillimeters))

    map_left = BORDER_LEFT_MM
    map_top = BORDER_TOP_MM

    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(map_left, map_top,
                                        QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(MAP_WIDTH_MM, map_height_mm,
                                         QgsUnitTypes.LayoutMillimeters))
    map_item.setExtent(extent)
    map_item.setCrs(CRS_WGS84)
    map_item.setFrameEnabled(True)
    map_item.setFrameStrokeWidth(QgsLayoutMeasurement(BORDER_WIDTH_MM,
                                                      QgsUnitTypes.LayoutMillimeters))
    map_item.setFrameStrokeColor(QColor(0, 0, 0))
    map_item.setBackgroundEnabled(True)
    map_item.setBackgroundColor(QColor(255, 255, 255))
    layout.addLayoutItem(map_item)

    # 显式设置地图项渲染的图层列表（setKeepLayerSet确保震中五角星始终在最上层）
    layers_to_set = ordered_layers if ordered_layers else list(project.mapLayers().values())
    if layers_to_set:
        map_item.setLayers(layers_to_set)
        map_item.setKeepLayerSet(True)
    map_item.invalidateCache()

    _setup_map_grid(map_item, extent)
    _add_north_arrow(layout, map_height_mm)
    _add_legend(layout, map_item, project, map_height_mm, output_height_mm,
                elevation_legend_list, scale=scale, extent=extent, center_lat=latitude)

    return layout


def _setup_map_grid(map_item, extent):
    """
    配置地图经纬度网格

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
    print("[信息] 经纬度网格设置完成")


def _add_north_arrow(layout, map_height_mm):
    """
    添加指北针

    参数:
        layout (QgsPrintLayout): 打印布局
        map_height_mm (float): 地图高度（毫米）
    """
    map_right = BORDER_LEFT_MM + MAP_WIDTH_MM
    map_top = BORDER_TOP_MM
    arrow_x = map_right - NORTH_ARROW_WIDTH_MM
    arrow_y = map_top

    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_elevation_temp.svg")
    create_north_arrow_svg(svg_path)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(svg_path)
    north_arrow.attemptMove(QgsLayoutPoint(arrow_x, arrow_y, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(NORTH_ARROW_WIDTH_MM, NORTH_ARROW_HEIGHT_MM,
                                            QgsUnitTypes.LayoutMillimeters))
    north_arrow.setFrameEnabled(False)
    north_arrow.setBackgroundEnabled(False)
    layout.addLayoutItem(north_arrow)
    print(f"[信息] 指北针添加完成")


def _add_scale_bar(layout, map_item, scale, extent, center_lat, map_height_mm):
    """
    添加比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图布局项
        scale (int): 比例尺
        extent (QgsRectangle): 地图范围
        center_lat (float): 中心纬度
        map_height_mm (float): 地图高度（毫米）
    """
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

    print(f"[信息] 比例尺添加完成，1:{scale:,}")


def _add_legend(layout, map_item, project, map_height_mm, output_height_mm,
                elevation_legend_list=None, scale=None, extent=None, center_lat=None):
    """
    添加图例
    - 上部：震中/地级市/省界/市界/县界（3行2列，平行排列）
    - 下部：高程图例（渐变色条 + 最小/最大值标签）
    - 底部：比例尺

    参数:
        layout (QgsPrintLayout): 打印布局
        map_item (QgsLayoutItemMap): 地图布局项
        project (QgsProject): QGIS项目
        map_height_mm (float): 地图高度（毫米）
        output_height_mm (float): 输出高度（毫米）
        elevation_legend_list (list): 高程图例列表 [(blue_rgba, min_label), (red_rgba, max_label)]
        scale (int): 比例尺分母（用于绘制比例尺）
        extent (QgsRectangle): 地图范围（用于计算比例尺）
        center_lat (float): 地图中心纬度（用于计算比例尺）
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

    # 基本图例项文本格式（可单独设置字体大小）
    basic_item_format = QgsTextFormat()
    basic_item_format.setFont(QFont("SimSun", BASIC_LEGEND_FONT_SIZE_PT))
    basic_item_format.setSize(BASIC_LEGEND_FONT_SIZE_PT)
    basic_item_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    basic_item_format.setColor(QColor(0, 0, 0))

    # 高程图例文本格式（可单独设置字体大小）- 使用Times New Roman字体
    elevation_format = QgsTextFormat()
    elevation_format.setFont(QFont(LEGEND_FONT_TIMES_NEW_ROMAN, ELEVATION_LEGEND_FONT_SIZE_PT))
    elevation_format.setSize(ELEVATION_LEGEND_FONT_SIZE_PT)
    elevation_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    elevation_format.setColor(QColor(0, 0, 0))

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

    # 上部图例：3行2列（基本图例项，共5项）
    top_legend_start_y = legend_y + 7.0

    col_count = 2  # 2列
    row_count = 3  # 3行（ceil(5/2)=3）
    left_pad = 2.0
    right_pad = 2.0
    col_gap = 1.0
    row_height = BASIC_LEGEND_ROW_HEIGHT_MM  # 使用可配置的行高
    icon_width = 4.0
    icon_height = 2.5
    icon_text_gap = 1.0

    available_width = legend_width - left_pad - right_pad - (col_count - 1) * col_gap
    col_width = available_width / col_count

    # 图例项：3行2列排列（不含烈度）
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
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

    # 高程图例（渐变色条 + 最小/最大值标签）
    if elevation_legend_list and len(elevation_legend_list) == 2:
        _min_color_rgba, min_label = elevation_legend_list[0]
        _max_color_rgba, max_label = elevation_legend_list[1]

        elevation_title_y = top_legend_start_y + top_legend_height + 2.0

        elevation_title_cn_format = QgsTextFormat()
        elevation_title_cn_font = QFont("SimHei")
        elevation_title_cn_font.setPointSizeF(10.0)
        elevation_title_cn_format.setFont(elevation_title_cn_font)
        elevation_title_cn_format.setSize(10.0)
        elevation_title_cn_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        elevation_title_cn_format.setColor(QColor(0, 0, 0))

        elevation_title_m_format = QgsTextFormat()
        elevation_title_m_font = QFont(LEGEND_FONT_TIMES_NEW_ROMAN)
        elevation_title_m_font.setPointSizeF(10.0)
        elevation_title_m_format.setFont(elevation_title_m_font)
        elevation_title_m_format.setSize(10.0)
        elevation_title_m_format.setSizeUnit(QgsUnitTypes.RenderPoints)
        elevation_title_m_format.setColor(QColor(0, 0, 0))

        # 居中显示"高程(m)"，其中 m 使用 Times New Roman
        title_group_width = 12.0
        title_group_x = legend_x + (legend_width - title_group_width) / 2.0

        elevation_title_cn_left = QgsLayoutItemLabel(layout)
        elevation_title_cn_left.setText("高程(")
        elevation_title_cn_left.setTextFormat(elevation_title_cn_format)
        elevation_title_cn_left.attemptMove(
            QgsLayoutPoint(title_group_x, elevation_title_y, QgsUnitTypes.LayoutMillimeters))
        elevation_title_cn_left.attemptResize(QgsLayoutSize(8.2, 5.0, QgsUnitTypes.LayoutMillimeters))
        elevation_title_cn_left.setHAlign(Qt.AlignRight)
        elevation_title_cn_left.setVAlign(Qt.AlignVCenter)
        elevation_title_cn_left.setFrameEnabled(False)
        elevation_title_cn_left.setBackgroundEnabled(False)
        layout.addLayoutItem(elevation_title_cn_left)

        elevation_title_m = QgsLayoutItemLabel(layout)
        elevation_title_m.setText("m")
        elevation_title_m.setTextFormat(elevation_title_m_format)
        elevation_title_m.attemptMove(
            QgsLayoutPoint(title_group_x + 8.2, elevation_title_y - 0.4,
                           QgsUnitTypes.LayoutMillimeters))
        elevation_title_m.attemptResize(QgsLayoutSize(2.0, 5.0, QgsUnitTypes.LayoutMillimeters))
        elevation_title_m.setHAlign(Qt.AlignHCenter)
        elevation_title_m.setVAlign(Qt.AlignVCenter)
        elevation_title_m.setFrameEnabled(False)
        elevation_title_m.setBackgroundEnabled(False)
        layout.addLayoutItem(elevation_title_m)

        elevation_title_cn_right = QgsLayoutItemLabel(layout)
        elevation_title_cn_right.setText(")")
        elevation_title_cn_right.setTextFormat(elevation_title_cn_format)
        elevation_title_cn_right.attemptMove(
            QgsLayoutPoint(title_group_x + 10.2, elevation_title_y,
                           QgsUnitTypes.LayoutMillimeters))
        elevation_title_cn_right.attemptResize(QgsLayoutSize(1.8, 5.0, QgsUnitTypes.LayoutMillimeters))
        elevation_title_cn_right.setHAlign(Qt.AlignLeft)
        elevation_title_cn_right.setVAlign(Qt.AlignVCenter)
        elevation_title_cn_right.setFrameEnabled(False)
        elevation_title_cn_right.setBackgroundEnabled(False)
        layout.addLayoutItem(elevation_title_cn_right)

        # 渐变色条（红色在上=最大值，蓝色在下=最小值）
        bar_start_y = elevation_title_y + 5.0
        bar_x = legend_x + 3.0
        bar_w = 6.0
        bar_h = 25.0
        n_slices = 20
        slice_h = bar_h / n_slices

        # 渐变颜色节点：蓝→青→绿→黄→红
        gradient_stops = [
            (0.0, (0, 0, 255)),
            (0.25, (0, 255, 255)),
            (0.5, (0, 128, 0)),
            (0.75, (255, 255, 0)),
            (1.0, (255, 0, 0)),
        ]

        def _interp_color(t):
            for i in range(len(gradient_stops) - 1):
                t0, c0 = gradient_stops[i]
                t1, c1 = gradient_stops[i + 1]
                if t0 <= t <= t1:
                    ratio = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                    r = int(c0[0] + ratio * (c1[0] - c0[0]))
                    g = int(c0[1] + ratio * (c1[1] - c0[1]))
                    b = int(c0[2] + ratio * (c1[2] - c0[2]))
                    return r, g, b
            return 255, 0, 0

        for i in range(n_slices):
            # 从上到下：i=0 在最顶部（最大值/红色），i=n_slices-1 在底部（最小值/蓝色）
            t = 1.0 - i / n_slices
            r, g, b = _interp_color(t)
            slice_y = bar_start_y + i * slice_h

            rect = QgsLayoutItemShape(layout)
            rect.setShapeType(QgsLayoutItemShape.Rectangle)
            rect.attemptMove(QgsLayoutPoint(bar_x, slice_y, QgsUnitTypes.LayoutMillimeters))
            rect.attemptResize(QgsLayoutSize(bar_w, slice_h + 0.02, QgsUnitTypes.LayoutMillimeters))
            sym = QgsFillSymbol.createSimple({
                'color': f"{r},{g},{b},255",
                'outline_style': 'no',
            })
            rect.setSymbol(sym)
            rect.setFrameEnabled(False)
            layout.addLayoutItem(rect)

        # 色条外边框
        bar_border = QgsLayoutItemShape(layout)
        bar_border.setShapeType(QgsLayoutItemShape.Rectangle)
        bar_border.attemptMove(QgsLayoutPoint(bar_x, bar_start_y, QgsUnitTypes.LayoutMillimeters))
        bar_border.attemptResize(QgsLayoutSize(bar_w, bar_h, QgsUnitTypes.LayoutMillimeters))
        border_sym = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',
            'outline_color': '80,80,80,255',
            'outline_width': '0.15',
            'outline_width_unit': 'MM',
        })
        bar_border.setSymbol(border_sym)
        bar_border.setFrameEnabled(False)
        layout.addLayoutItem(bar_border)

        # 标签：最大值在色条右侧顶部，最小值在色条右侧底部
        label_x = bar_x + bar_w + 1.5
        label_w = legend_width - 3.0 - bar_w - 1.5 - 1.0

        lbl_max = QgsLayoutItemLabel(layout)
        lbl_max.setText(max_label)
        lbl_max.setTextFormat(elevation_format)
        lbl_max.attemptMove(QgsLayoutPoint(label_x, bar_start_y, QgsUnitTypes.LayoutMillimeters))
        lbl_max.attemptResize(QgsLayoutSize(label_w, 4.0, QgsUnitTypes.LayoutMillimeters))
        lbl_max.setHAlign(Qt.AlignLeft)
        lbl_max.setVAlign(Qt.AlignTop)
        lbl_max.setFrameEnabled(False)
        lbl_max.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_max)

        lbl_min = QgsLayoutItemLabel(layout)
        lbl_min.setText(min_label)
        lbl_min.setTextFormat(elevation_format)
        lbl_min.attemptMove(
            QgsLayoutPoint(label_x, bar_start_y + bar_h - 4.0,
                           QgsUnitTypes.LayoutMillimeters))
        lbl_min.attemptResize(QgsLayoutSize(label_w, 4.0, QgsUnitTypes.LayoutMillimeters))
        lbl_min.setHAlign(Qt.AlignLeft)
        lbl_min.setVAlign(Qt.AlignBottom)
        lbl_min.setFrameEnabled(False)
        lbl_min.setBackgroundEnabled(False)
        layout.addLayoutItem(lbl_min)

        print(f"[信息] 高程图例添加完成，渐变色条: {min_label} ~ {max_label}")
    else:
        print("[信息] 无高程数据，跳过高程图例")

    # ── 比例尺（位于图例内容下方）──
    if scale is not None and extent is not None and center_lat is not None:
        lon_range_deg = extent.xMaximum() - extent.xMinimum()
        map_total_km = lon_range_deg * 111.0 * math.cos(math.radians(center_lat))
        km_per_mm = map_total_km / MAP_WIDTH_MM if MAP_WIDTH_MM > 0 else 1.0
        target_bar_km = MAP_WIDTH_MM * 0.18 * km_per_mm

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
        std_bar_height = 14.0

        avail_width = legend_width - 4.0
        if std_bar_width > avail_width:
            scale_factor = avail_width / std_bar_width
            std_bar_width = avail_width
            bar_length_mm *= scale_factor
            std_bar_height *= scale_factor
        else:
            scale_factor = 1.0

        # 比例尺垂直位置：距底部留 4mm 空间
        sb_height = std_bar_height
        sb_y = legend_y + legend_height - sb_height - 4.0
        sb_x = legend_x + (legend_width - std_bar_width) / 2.0

        scale_font_size = SCALE_FONT_SIZE_PT
        scale_tf = QgsTextFormat()
        scale_tf.setFont(QFont("Times New Roman", scale_font_size))
        scale_tf.setSize(scale_font_size)
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

        tick_tf = QgsTextFormat()
        tick_tf.setFont(QFont("Times New Roman", scale_font_size))
        tick_tf.setSize(scale_font_size)
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

    print("[信息] 图例添加完成")


def _draw_star_icon(layout, x, center_y, width, height):
    """
    在图例中绘制红色五角星图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        height (float): 高度
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
    在图例中绘制地级市圆点图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        height (float): 高度
    """
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
    """
    在图例中绘制实线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): X坐标
        center_y (float): 中心Y坐标
        width (float): 宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽（毫米）
        solid (bool): 是否实线
    """
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
    """
    在图例中绘制虚线图标

    参数:
        layout (QgsPrintLayout): 打印布局
        x (float): 起始X坐标
        center_y (float): 中心Y坐标
        width (float): 图标总宽度
        color (QColor): 线条颜色
        line_width_mm (float): 线宽(mm)
        dash_gap_mm (float): 虚线间隔(mm)
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
        dash_shape.attemptMove(QgsLayoutPoint(current_x, center_y - line_height / 2.0,
                                              QgsUnitTypes.LayoutMillimeters))
        dash_shape.attemptResize(QgsLayoutSize(actual_dash_length, line_height,
                                               QgsUnitTypes.LayoutMillimeters))
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

def generate_earthquake_elevation_map(longitude, latitude, magnitude,
                                      output_path="output_elevation_map.png",
                                      basemap_path=None, annotation_path=None):
    """
    生成地震震中数字高程图（主入口函数）

    针对大文件（如15GB的TIF）进行了优化，只加载所需范围内的数据。
    只加载天地图矢量注记图层（放置在最上层），不加载矢量底图。

    参数:
        longitude (float): 震中经度
        latitude (float): 震中纬度
        magnitude (float): 地震震级
        output_path (str): 输出PNG文件路径

    返回:
        str: 成功时返回输出文件路径，失败返回None
    """
    logger.info('开始生成数字高程图: lon=%.4f lat=%.4f M=%.1f output=%s',
                longitude, latitude, magnitude, output_path)
    try:
        return _generate_earthquake_elevation_map_impl(
            longitude, latitude, magnitude, output_path,
            basemap_path=basemap_path, annotation_path=annotation_path
        )
    except Exception as exc:
        logger.error('生成数字高程图失败: %s', exc, exc_info=True)
        raise


def _generate_earthquake_elevation_map_impl(longitude, latitude, magnitude,
                                             output_path,
                                             basemap_path=None, annotation_path=None):
    """generate_earthquake_elevation_map 的实际实现。"""
    print("=" * 60)
    print(f"[开始] 生成地震数字高程图（优化版）")
    print(f"  震中: ({longitude}, {latitude}), 震级: M{magnitude}")
    print(f"  输出: {output_path}")
    print(f"  GDAL可用: {GDAL_AVAILABLE}")
    print("=" * 60)

    config = get_magnitude_config(magnitude)
    half_size_km = config["map_size_km"] / 2.0
    scale = config["scale"]
    print(f"[信息] 震级配置: 范围{config['map_size_km']}km, 比例尺1:{scale}")

    extent = calculate_extent(longitude, latitude, half_size_km)
    print(f"[信息] 地图范围: {extent.toString()}")

    map_height_mm = calculate_map_height_from_extent(extent, MAP_WIDTH_MM)
    print(f"[信息] 地图尺寸: {MAP_WIDTH_MM:.1f}mm x {map_height_mm:.1f}mm")

    # 通过 QGISManager 确保 QGIS 已初始化（统一管理，支持正确的 prefix path）
    from core.qgis_manager import get_qgis_manager as _get_qgis_manager
    _get_qgis_manager().ensure_initialized()

    project = QgsProject.instance()
    project.clear()
    project.setCrs(CRS_WGS84)

    # 获取临时文件管理器
    temp_manager = get_temp_manager()

    # 临时注记底图文件路径
    temp_annotation_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_temp_annotation_elevation.png"
    )

    try:
        # ============================================================
        # 下载天地图矢量注记瓦片（只下载注记，不下载底图）
        # ============================================================
        width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        height_px = int(map_height_mm / 25.4 * OUTPUT_DPI)

        if annotation_path:
            annotation_raster = QgsRasterLayer(annotation_path, "天地图注记", "gdal")
            if not annotation_raster.isValid():
                annotation_raster = None
        else:
            annotation_raster = download_tianditu_annotation_tiles(extent, width_px, height_px, temp_annotation_path)

        # 加载数字高程底图（优化版，按需裁剪）
        elevation_layer, elev_min, elev_max = load_elevation_raster_optimized(
            ELEVATION_TIF_PATH, extent)
        if elevation_layer:
            project.addMapLayer(elevation_layer)

        # 构建高程图例列表（最小值/最大值）
        elevation_legend_list = build_elevation_legend_list(elev_min, elev_max)
        print(f"[信息] 获取到 {len(elevation_legend_list)} 个高程图例项")

        # 加载县界图层
        county_layer = load_vector_layer(COUNTY_SHP_PATH, "县界_地图")
        if county_layer:
            style_county_layer(county_layer)
            project.addMapLayer(county_layer)

        # 加载市界图层
        city_layer = load_vector_layer(CITY_SHP_PATH, "市界_地图")
        if city_layer:
            style_city_layer(city_layer)
            project.addMapLayer(city_layer)

        # 加载省界图层
        province_layer = load_vector_layer(PROVINCE_SHP_PATH, "省界_地图")
        if province_layer:
            style_province_layer(province_layer, center_lon=longitude, center_lat=latitude, extent=extent)
            project.addMapLayer(province_layer)

        # 创建省份标注图层（支持震中附近省份标注自动偏移）
        province_label_layer = None
        if province_layer:
            province_label_layer = create_province_label_layer(province_layer, longitude, latitude, extent)
            if province_label_layer:
                project.addMapLayer(province_label_layer)

        # 加载地级市点位图层（不显示标注）
        city_point_layer = create_city_point_layer(extent)
        if city_point_layer:
            project.addMapLayer(city_point_layer)

        # 创建图例辅助图层
        province_legend_layer = create_province_legend_layer()
        if province_legend_layer:
            project.addMapLayer(province_legend_layer)

        city_legend_layer = create_city_legend_layer()
        if city_legend_layer:
            project.addMapLayer(city_legend_layer)

        county_legend_layer = create_county_legend_layer()
        if county_legend_layer:
            project.addMapLayer(county_legend_layer)

        # 创建震中图层
        epicenter_layer = create_epicenter_layer(longitude, latitude)
        if epicenter_layer:
            project.addMapLayer(epicenter_layer)

        # 添加注记图层到项目（如果下载成功）
        if annotation_raster:
            project.addMapLayer(annotation_raster)

        # 按正确渲染顺序排列图层（第一项在最上层）
        # 震中图层放在最上层，确保震中五角星不被注记图层遮挡
        ordered_layers = [lyr for lyr in [
            epicenter_layer,    # 震中放在最上层，显示在注记之上
            annotation_raster,  # 天地图注记
            city_point_layer,
            province_label_layer,   # 省份标注图层（在省界图层之上）
            province_layer,
            city_layer,
            county_layer,
            elevation_layer,
        ] if lyr is not None]

        # 创建打印布局
        layout = create_print_layout(project, longitude, latitude, magnitude,
                                     extent, scale, map_height_mm, elevation_legend_list, ordered_layers)

        # 导出PNG
        result = export_layout_to_png(layout, output_path, OUTPUT_DPI)

    finally:
        # 清理临时文件
        temp_manager.cleanup()

        # 清理指北针临���SVG文件
        svg_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_north_arrow_elevation_temp.svg")
        if os.path.exists(svg_temp):
            try:
                os.remove(svg_temp)
            except OSError:
                pass

        # 清理临时注记底图文件
        if not annotation_path and os.path.exists(temp_annotation_path):
            try:
                os.remove(temp_annotation_path)
                pgw_path = temp_annotation_path.replace(".png", ".pgw")
                if os.path.exists(pgw_path):
                    os.remove(pgw_path)
            except OSError:
                pass

    print("=" * 60)
    if result:
        print(f"[完成] 数字高程图已输出: {result}")
    else:
        print("[失败] 数字高程图输出失败")
    print("=" * 60)
    return result


def export_layout_to_png(layout, output_path, dpi=150):
    """
    将打印布局导出为PNG图片

    参数:
        layout (QgsPrintLayout): 打印布局对象
        output_path (str): 输出文件路径
        dpi (int): 输出分辨率（默认150）

    返回:
        str: 成功时返回输出文件路径，失败返回None
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
# 测试方法
# ============================================================

def test_magnitude_config():
    """测试震级配置获取"""
    print("\n--- 测试: get_magnitude_config ---")
    config_s = get_magnitude_config(4.5)
    assert config_s["scale"] == 150000
    assert config_s["map_size_km"] == 30
    print(f"  M4.5 -> 范围{config_s['map_size_km']}km, 比例尺1:{config_s['scale']} ✓")

    config_m = get_magnitude_config(6.5)
    assert config_m["scale"] == 500000
    assert config_m["map_size_km"] == 100
    print(f"  M6.5 -> 范围{config_m['map_size_km']}km, 比例尺1:{config_m['scale']} ✓")

    config_l = get_magnitude_config(7.5)
    assert config_l["scale"] == 1500000
    assert config_l["map_size_km"] == 300
    print(f"  M7.5 -> 范围{config_l['map_size_km']}km, 比例尺1:{config_l['scale']} ✓")
    print("  所有震级配置测试通过 ✓")


def test_calculate_extent():
    """测试地图范围计算"""
    print("\n--- 测试: calculate_extent ---")
    extent = calculate_extent(116.4, 39.9, 15)
    assert extent.xMinimum() < 116.4 < extent.xMaximum()
    assert extent.yMinimum() < 39.9 < extent.yMaximum()
    delta_y = extent.yMaximum() - extent.yMinimum()
    assert abs(delta_y - 0.2703) < 0.02
    print(f"  15km半径范围: 纬度差{delta_y:.4f}° ✓")
    print("  所有范围计算测试通过 ✓")


def test_elevation_renderer_config():
    """测试高程拉伸渲染配置"""
    print("\n--- 测试: 高程拉伸渲染配置 ---")

    # 验证 build_elevation_legend_list 的颜色配置
    legend = build_elevation_legend_list(0.0, 8850.0)
    min_color, min_label = legend[0]
    max_color, max_label = legend[1]

    assert min_color == (0, 0, 255, 255), f"最小值颜色应为蓝色，实际: {min_color}"
    assert max_color == (255, 0, 0, 255), f"最大值颜色应为红色，实际: {max_color}"
    assert min_label == "0"
    assert max_label == "8850"
    print(f"  最小值颜色: 蓝色 RGB{min_color[:3]} ✓")
    print(f"  最大值颜色: 红色 RGB{max_color[:3]} ✓")
    print("  高程拉伸渲染配置测试通过 ✓")


def test_build_elevation_legend_list():
    """测试高程图例列表构建"""
    print("\n--- 测试: build_elevation_legend_list ---")

    legend_list = build_elevation_legend_list(100.0, 3500.0)

    assert len(legend_list) == 2
    print(f"  图例项数量: {len(legend_list)} ✓")

    color_min, label_min = legend_list[0]
    assert color_min == (0, 0, 255, 255)
    assert label_min == "100"
    print(f"  最小值项: {label_min}, RGBA{color_min} ✓")

    color_max, label_max = legend_list[1]
    assert color_max == (255, 0, 0, 255)
    assert label_max == "3500"
    print(f"  最大值项: {label_max}, RGBA{color_max} ✓")

    # 测试默认值（None输入）
    legend_default = build_elevation_legend_list(None, None)
    assert len(legend_default) == 2
    assert legend_default[0][1] == "0"
    assert legend_default[1][1] == "8850"
    print(f"  默认值测试: 最小={legend_default[0][1]}, 最大={legend_default[1][1]} ✓")

    print("  高程图例列表构建测试通过 ✓")


def test_boundary_styles():
    """测试市界和县界样式参数"""
    print("\n--- 测试: 市界和县界样式参数 ---")

    assert CITY_COLOR.red() == 160
    assert CITY_COLOR.green() == 160
    assert CITY_COLOR.blue() == 160
    assert CITY_LINE_WIDTH_MM == 0.24
    assert CITY_DASH_GAP_MM == 0.3
    print(f"  市界颜色: RGB({CITY_COLOR.red()},{CITY_COLOR.green()},{CITY_COLOR.blue()}) ✓")
    print(f"  市界线宽: {CITY_LINE_WIDTH_MM}mm ✓")
    print(f"  市界虚线间隔: {CITY_DASH_GAP_MM}mm ✓")

    assert COUNTY_COLOR.red() == 160
    assert COUNTY_COLOR.green() == 160
    assert COUNTY_COLOR.blue() == 160
    assert COUNTY_LINE_WIDTH_MM == 0.14
    assert COUNTY_DASH_GAP_MM == 0.2
    print(f"  县界颜色: RGB({COUNTY_COLOR.red()},{COUNTY_COLOR.green()},{COUNTY_COLOR.blue()}) ✓")
    print(f"  县界线宽: {COUNTY_LINE_WIDTH_MM}mm ✓")
    print(f"  县界虚线间隔: {COUNTY_DASH_GAP_MM}mm ✓")

    print("  市界和县界样式参数测试通过 ✓")


def test_legend_font_config():
    """测试图例字体配置"""
    print("\n--- 测试: 图例字体配置 ---")

    assert ELEVATION_LEGEND_FONT_SIZE_PT == 10
    print(f"  高程图例字体大小: {ELEVATION_LEGEND_FONT_SIZE_PT}pt ✓")

    assert BASIC_LEGEND_FONT_SIZE_PT == 10
    print(f"  基本图例字体大小: {BASIC_LEGEND_FONT_SIZE_PT}pt ✓")

    assert LEGEND_FONT_TIMES_NEW_ROMAN == "Times New Roman"
    print(f"  高程单位(m)字体: {LEGEND_FONT_TIMES_NEW_ROMAN} ✓")

    print("  图例字体配置测试通过 ✓")


def test_temp_file_manager():
    """测试临时文件管理器"""
    print("\n--- 测试: TempFileManager ---")

    manager = TempFileManager()

    temp_dir = manager.get_temp_dir()
    assert temp_dir is not None
    assert os.path.exists(temp_dir)
    print(f"  临时目录创建成功: {temp_dir} ✓")

    temp_file = manager.get_temp_file(suffix=".tif")
    assert temp_file is not None
    assert temp_file.endswith(".tif")
    print(f"  临时文件创建成功: {temp_file} ✓")

    manager.cleanup()
    assert not os.path.exists(temp_dir)
    print("  临时文件清理成功 ✓")

    print("  临时文件管理器测试通过 ✓")


def test_gdal_availability():
    """测试GDAL可用性"""
    print("\n--- 测试: GDAL可用性 ---")

    print(f"  GDAL模块可用: {GDAL_AVAILABLE}")

    if GDAL_AVAILABLE:
        print(f"  GDAL版本: {gdal.VersionInfo()}")
        print("  GDAL功能正常 ✓")
    else:
        print("  [警告] GDAL不可用，将使用备用方案")

    print("  GDAL可用性测试完成")


def test_clip_extent_calculation():
    """测试裁剪范围计算"""
    print("\n--- 测试: 裁剪范围计算 ---")

    extent = calculate_extent(118.18, 39.63, 150)

    clip_xmin = extent.xMinimum() - CLIP_BUFFER_DEGREES
    clip_xmax = extent.xMaximum() + CLIP_BUFFER_DEGREES
    clip_ymin = extent.yMinimum() - CLIP_BUFFER_DEGREES
    clip_ymax = extent.yMaximum() + CLIP_BUFFER_DEGREES

    assert clip_xmin < extent.xMinimum()
    assert clip_xmax > extent.xMaximum()
    assert clip_ymin < extent.yMinimum()
    assert clip_ymax > extent.yMaximum()

    buffer_applied = extent.xMinimum() - clip_xmin
    assert abs(buffer_applied - CLIP_BUFFER_DEGREES) < 0.0001

    print(
        f"  原始范围: ({extent.xMinimum():.4f}, {extent.yMinimum():.4f}) - ({extent.xMaximum():.4f}, {extent.yMaximum():.4f})")
    print(f"  裁剪范围: ({clip_xmin:.4f}, {clip_ymin:.4f}) - ({clip_xmax:.4f}, {clip_ymax:.4f})")
    print(f"  缓冲区大小: {CLIP_BUFFER_DEGREES}度 ✓")

    print("  裁剪范围计算测试通过 ✓")


def test_legend_layout_config():
    """测试图例布局配置"""
    print("\n--- 测试: 图例布局配置 ---")

    # 测试基本图例项配置
    assert BASIC_LEGEND_FONT_SIZE_PT == 10
    assert BASIC_LEGEND_ROW_HEIGHT_MM == 8.0
    print(f"  基本图例项字体大小: {BASIC_LEGEND_FONT_SIZE_PT}pt ✓")
    print(f"  基本图例项行高: {BASIC_LEGEND_ROW_HEIGHT_MM}mm ✓")

    # 测试高程图例项配置
    assert ELEVATION_LEGEND_FONT_SIZE_PT == 10
    assert ELEVATION_LEGEND_ROW_HEIGHT_MM == 6
    print(f"  高程图例项字体大小: {ELEVATION_LEGEND_FONT_SIZE_PT}pt ✓")
    print(f"  高程图例项行高: {ELEVATION_LEGEND_ROW_HEIGHT_MM}mm ✓")

    # 验证3行2列布局（5项，不含烈度）
    legend_items = [
        ("震中", "star"),
        ("地级市", "circle"),
        ("省界", "solid_line"),
        ("市界", "dash_line_city"),
        ("县界", "dash_line_county"),
    ]
    assert len(legend_items) == 5  # 3行 x 2列（最后一格空）
    print(f"  基本图例项数量: {len(legend_items)} (3行x2列，最后格空) ✓")

    print("  图例布局配置测试通过 ✓")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("运行 earthquake_elevation_map 全部测试（优化版）")
    print("=" * 60)

    test_magnitude_config()
    test_calculate_extent()
    test_elevation_renderer_config()
    test_build_elevation_legend_list()
    test_boundary_styles()
    test_legend_font_config()
    test_temp_file_manager()
    test_gdal_availability()
    test_clip_extent_calculation()
    test_legend_layout_config()

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
            out = sys.argv[4] if len(sys.argv) > 4 else f"earthquake_elevation_M{mag}_{lon}_{lat}.png"
            generate_earthquake_elevation_map(lon, lat, mag, out)
        except ValueError as e:
            print(f"[错误] 参数格式错误: {e}")
            print("用法: python earthquake_elevation_map.py <经度> <纬度> <震级> [输出文件名]")
    else:
        print("使用默认参数运行（唐山地震 M7.8）...")
        generate_earthquake_elevation_map(
            longitude=118.18, latitude=39.63,
            magnitude=2.8, output_path="earthquake_elevation_tangshan_M7.8.png"
        )

