# -*- coding: utf-8 -*-
"""
天地图矢量底图和矢量注记瓦片下载公共模块

提供天地图矢量底图(vec_c)和矢量注记(cva_c)的瓦片下载、拼接、裁剪功能，
以及支持缓存的统一下载入口函数。
"""

import math
import os
import logging
import requests
from io import BytesIO
from PIL import Image

logger = logging.getLogger('report')

try:
    import django.conf
    _django_settings = django.conf.settings
except Exception:
    _django_settings = None

TIANDITU_TK = (
    getattr(_django_settings, 'TIANDITU_TK', '1ef76ef90c6eb961cb49618f9b1a399d')
    if _django_settings is not None else '1ef76ef90c6eb961cb49618f9b1a399d'
)

from qgis.core import QgsRasterLayer


def download_tianditu_basemap_tiles(extent, width_px, height_px, output_path):
    """
    下载天地图矢量底图瓦片（vec_c）并拼接为本地栅格图像。

    参数:
        extent (QgsRectangle): 渲染范围（WGS84）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        output_path (str): 输出文件路径

    返回:
        QgsRasterLayer或None: 成功返回栅格图层，失败返回None
    """
    tk = TIANDITU_TK
    lon_range = extent.xMaximum() - extent.xMinimum()
    zoom = int(math.log2(360 / lon_range * width_px / 256))
    zoom = max(1, min(zoom, 18))
    print(f"[信息] 下载天地图矢量底图瓦片，缩放级别: {zoom}")

    def lon_to_tile_x(lon, z):
        """经度转瓦片X坐标"""
        n = 2 ** z
        x = int((lon + 180.0) / 360.0 * n)
        return max(0, min(n - 1, x))

    def lat_to_tile_y(lat, z):
        """纬度转瓦片Y坐标（天地图c系列）"""
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

    tile_x_min = lon_to_tile_x(extent.xMinimum(), zoom)
    tile_x_max = lon_to_tile_x(extent.xMaximum(), zoom)
    tile_y_min = lat_to_tile_y(extent.yMaximum(), zoom)
    tile_y_max = lat_to_tile_y(extent.yMinimum(), zoom)

    if tile_y_min > tile_y_max:
        tile_y_min, tile_y_max = tile_y_max, tile_y_min

    num_tiles_x = tile_x_max - tile_x_min + 1
    num_tiles_y = tile_y_max - tile_y_min + 1
    print(f"[信息] 需要下载 {num_tiles_x * num_tiles_y} 个底图瓦片 ({num_tiles_x} x {num_tiles_y})")

    tile_size = 256
    mosaic_width = num_tiles_x * tile_size
    mosaic_height = num_tiles_y * tile_size
    # 底色使用浅蓝色模拟海洋，陆地由矢量瓦片覆盖
    mosaic = Image.new('RGB', (mosaic_width, mosaic_height), (170, 211, 223))

    downloaded = 0
    failed = 0
    servers = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']

    for ty in range(tile_y_min, tile_y_max + 1):
        for tx in range(tile_x_min, tile_x_max + 1):
            server = servers[(tx + ty) % len(servers)]
            vec_url = (
                f"http://{server}.tianditu.gov.cn/vec_c/wmts?"
                f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                f"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
                f"&FORMAT=tiles&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                f"&tk={tk}"
            )
            try:
                resp = requests.get(vec_url, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 0:
                    tile_img = Image.open(BytesIO(resp.content)).convert('RGB')
                    paste_x = (tx - tile_x_min) * tile_size
                    paste_y = (ty - tile_y_min) * tile_size
                    mosaic.paste(tile_img, (paste_x, paste_y))
                    downloaded += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"[警告] 底图瓦片下载异常: {tx},{ty} - {e}")

    print(f"[信息] 底图瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

    if downloaded == 0:
        print("[错误] 没有成功下载任何底图瓦片")
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

    cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
    final_image = cropped.resize((width_px, height_px), Image.LANCZOS)
    final_image.save(output_path, 'PNG')
    print(f"[信息] 底图已保存: {output_path}")

    # 生成World文件，使GDAL能正确关联地理坐标
    world_file_path = output_path.replace(".png", ".pgw")
    x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
    y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
    with open(world_file_path, 'w') as f:
        f.write(f"{x_res}\n0\n0\n{-y_res}\n{extent.xMinimum()}\n{extent.yMaximum()}\n")

    raster_layer = QgsRasterLayer(output_path, "天地图底图", "gdal")
    if raster_layer.isValid():
        print("[信息] 成功加载底图栅格图层")
        return raster_layer
    else:
        print("[错误] 无法加载底图栅格图层")
        return None


def download_tianditu_annotation_tiles(extent, width_px, height_px, output_path):
    """
    下载天地图矢量注记瓦片（cva_c）并拼接为本地栅格图像（透明背景）。

    参数:
        extent (QgsRectangle): 渲染范围（WGS84）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        output_path (str): 输出文件路径

    返回:
        QgsRasterLayer或None: 成功返回栅格图层，失败返回None
    """
    tk = TIANDITU_TK
    lon_range = extent.xMaximum() - extent.xMinimum()
    zoom = int(math.log2(360 / lon_range * width_px / 256))
    zoom = max(1, min(zoom, 18))
    print(f"[信息] 下载天地图注记瓦片，缩放级别: {zoom}")

    def lon_to_tile_x(lon, z):
        """经度转瓦片X坐标"""
        n = 2 ** z
        x = int((lon + 180.0) / 360.0 * n)
        return max(0, min(n - 1, x))

    def lat_to_tile_y(lat, z):
        """纬度转瓦片Y坐标（天地图c系列）"""
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
    # 使用RGBA模式，支持透明背景
    mosaic = Image.new('RGBA', (mosaic_width, mosaic_height), (0, 0, 0, 0))

    downloaded = 0
    failed = 0
    servers = ['t0', 't1', 't2', 't3', 't4', 't5', 't6', 't7']

    for ty in range(tile_y_min, tile_y_max + 1):
        for tx in range(tile_x_min, tile_x_max + 1):
            server = servers[(tx + ty) % len(servers)]
            cva_url = (
                f"http://{server}.tianditu.gov.cn/cva_c/wmts?"
                f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                f"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
                f"&FORMAT=tiles&TILEMATRIX={zoom}&TILEROW={ty}&TILECOL={tx}"
                f"&tk={tk}"
            )
            try:
                resp = requests.get(cva_url, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 0:
                    tile_cva = Image.open(BytesIO(resp.content)).convert('RGBA')
                    paste_x = (tx - tile_x_min) * tile_size
                    paste_y = (ty - tile_y_min) * tile_size
                    mosaic.paste(tile_cva, (paste_x, paste_y), tile_cva)
                    downloaded += 1
                else:
                    failed += 1
                    print(f"[警告] 注记瓦片下载失败: {tx},{ty} - 状态码: {resp.status_code}")
            except Exception as e:
                failed += 1
                print(f"[警告] 注记瓦片下载异常: {tx},{ty} - {e}")

    print(f"[信息] 注记瓦片下载完成: 成功 {downloaded}, 失败 {failed}")

    if downloaded == 0:
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

    cropped = mosaic.crop((crop_left, crop_top, crop_right, crop_bottom))
    final_image = cropped.resize((width_px, height_px), Image.LANCZOS)
    final_image.save(output_path, 'PNG')
    print(f"[信息] 注记底图已保存: {output_path}")

    # 生成World文件
    world_file_path = output_path.replace(".png", ".pgw")
    x_res = (extent.xMaximum() - extent.xMinimum()) / width_px
    y_res = (extent.yMaximum() - extent.yMinimum()) / height_px
    with open(world_file_path, 'w') as f:
        f.write(f"{x_res}\n0\n0\n{-y_res}\n{extent.xMinimum()}\n{extent.yMaximum()}\n")

    raster_layer = QgsRasterLayer(output_path, "天地图注记", "gdal")
    if raster_layer.isValid():
        print("[信息] 成功加载注记栅格图层")
        return raster_layer
    else:
        print("[错误] 无法加载注记栅格图层")
        return None


def download_basemap_with_cache(extent, width_px, height_px, basemap_output_path,
                                 annotation_output_path, cache_base_map):
    """
    带缓存支持的天地图矢量底图和矢量注记统一下载入口函数。

    参数:
        extent (QgsRectangle): 渲染范围（WGS84）
        width_px (int): 输出图像宽度（像素）
        height_px (int): 输出图像高度（像素）
        basemap_output_path (str): 矢量底图输出文件路径
        annotation_output_path (str): 矢量注记输出文件路径
        cache_base_map (int): 缓存开关（来自report_task表的cache_base_map字段）
            - 1: 优先使用缓存，如文件已存在且非空则跳过下载
            - 0: 不使用缓存，每次重新下载

    返回:
        tuple: (basemap_raster, annotation_raster, error_msg)
            - basemap_raster (QgsRasterLayer或None): 矢量底图图层
            - annotation_raster (QgsRasterLayer或None): 矢量注记图层
            - error_msg (str或None): 错误信息，None表示成功
    """
    basemap_raster = None
    annotation_raster = None

    def _file_exists_and_nonempty(path):
        """检查文件是否存在且非空"""
        return os.path.exists(path) and os.path.getsize(path) > 0

    if cache_base_map == 1:
        # 缓存模式：如果文件已存在则跳过下载
        need_basemap = not _file_exists_and_nonempty(basemap_output_path)
        need_annotation = not _file_exists_and_nonempty(annotation_output_path)

        if not need_basemap:
            print(f"[信息] 使用缓存底图: {basemap_output_path}")
            basemap_raster = QgsRasterLayer(basemap_output_path, "天地图底图", "gdal")
            if not basemap_raster.isValid():
                basemap_raster = None
                need_basemap = True

        if not need_annotation:
            print(f"[信息] 使用缓存注记: {annotation_output_path}")
            annotation_raster = QgsRasterLayer(annotation_output_path, "天地图注记", "gdal")
            if not annotation_raster.isValid():
                annotation_raster = None
                need_annotation = True

        if need_basemap:
            try:
                basemap_raster = download_tianditu_basemap_tiles(
                    extent, width_px, height_px, basemap_output_path
                )
                if basemap_raster is None:
                    return None, None, "矢量底图下载失败：没有成功下载任何底图瓦片"
            except Exception as e:
                logger.error('下载天地图底图失败: %s', e, exc_info=True)
                return None, None, f"矢量底图下载失败：{e}"

        if need_annotation:
            try:
                annotation_raster = download_tianditu_annotation_tiles(
                    extent, width_px, height_px, annotation_output_path
                )
                if annotation_raster is None:
                    return None, None, "矢量注记下载失败：没有成功下载任何注记瓦片"
            except Exception as e:
                logger.error('下载天地图注记失败: %s', e, exc_info=True)
                return None, None, f"矢量注记下载失败：{e}"
    else:
        # 非缓存模式：每次重新下载
        try:
            basemap_raster = download_tianditu_basemap_tiles(
                extent, width_px, height_px, basemap_output_path
            )
            if basemap_raster is None:
                return None, None, "矢量底图下载失败：没有成功下载任何底图瓦片"
        except Exception as e:
            logger.error('下载天地图底图失败: %s', e, exc_info=True)
            return None, None, f"矢量底图下载失败：{e}"

        try:
            annotation_raster = download_tianditu_annotation_tiles(
                extent, width_px, height_px, annotation_output_path
            )
            if annotation_raster is None:
                return None, None, "矢量注记下载失败：没有成功下载任何注记瓦片"
        except Exception as e:
            logger.error('下载天地图注记失败: %s', e, exc_info=True)
            return None, None, f"矢量注记下载失败：{e}"

    return basemap_raster, annotation_raster, None
