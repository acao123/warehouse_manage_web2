# -*- coding: utf-8 -*-
"""
统一地图生成入口模块

封装所有地图生成方法，提供统一的错误处理和日志记录。
外部调用方只需 import 本模块，无需直接依赖各专项地图文件。
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger('report')


# ============================================================
# 统一异常
# ============================================================

class MapGenerationError(Exception):
    """地图生成失败时抛出的统一异常。"""


# ============================================================
# 地图生成封装方法
# ============================================================

def generate_earthquake_map(center_lon: float, center_lat: float, magnitude: float,
                             csv_path: str, output_path: str, csv_encoding: str = 'gbk') -> str:
    """
    生成历史地震分布图（图一）。

    参数:
        center_lon: 震中经度
        center_lat: 震中纬度
        magnitude: 震级
        csv_path: 历史地震 CSV 文件路径
        output_path: 输出 PNG 路径
        csv_encoding: CSV 文件编码，默认 gbk

    返回:
        统计信息文本字符串

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图一（历史地震分布图）: lon=%.4f lat=%.4f M=%.1f',
                center_lon, center_lat, magnitude)
    try:
        from core.earthquake_map import generate_earthquake_map as _fn
        result = _fn(center_lon, center_lat, magnitude, csv_path, output_path,
                     csv_encoding=csv_encoding)
        logger.info('图一生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图一生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图一生成失败: {exc}') from exc


def generate_earthquake_kml_map(kml_path: str, description_text: str,
                                 magnitude: float, output_path: str) -> dict:
    """
    生成地震烈度分布图（图二）。

    参数:
        kml_path: KML 烈度圈文件路径
        description_text: 说明文字
        magnitude: 震级
        output_path: 输出 PNG 路径

    返回:
        包含分析结果的字典（含 max_intensity 等字段）

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图二（烈度分布图）: kml=%s M=%.1f', kml_path, magnitude)
    try:
        from core.earthquake_kml_map import generate_earthquake_kml_map as _fn
        result = _fn(kml_path, description_text, magnitude, output_path)
        logger.info('图二生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图二生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图二生成失败: {exc}') from exc


def generate_earthquake_geology_map(longitude: float, latitude: float, magnitude: float,
                                     output_path: str) -> str:
    """
    生成地质构造图（图三）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图三（地质构造图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.earthquake_geological_map2 import generate_earthquake_geology_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path)
        logger.info('图三生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图三生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图三生成失败: {exc}') from exc


def generate_earthquake_elevation_map(longitude: float, latitude: float, magnitude: float,
                                       output_path: str, kml_path: str = None) -> str:
    """
    生成数字高程图（图四）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径
        kml_path: 可选，烈度圈 KML 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图四（数字高程图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.earthquake_elevation_map import generate_earthquake_elevation_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path, kml_path=kml_path)
        logger.info('图四生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图四生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图四生成失败: {exc}') from exc


def generate_earthquake_land_use_map(longitude: float, latitude: float, magnitude: float,
                                      output_path: str) -> str:
    """
    生成土地利用类型图（图五）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图五（土地利用类型图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.earthquake_land_use_map import generate_earthquake_land_use_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path)
        logger.info('图五生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图五生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图五生成失败: {exc}') from exc


def generate_earthquake_population_map(longitude: float, latitude: float, magnitude: float,
                                        output_path: str, kml_path: str = None) -> str:
    """
    生成人口分布图（图六）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径
        kml_path: 可选，烈度圈 KML 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图六（人口分布图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.earthquake_population_map import generate_earthquake_population_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path, kml_path=kml_path)
        logger.info('图六生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图六生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图六生成失败: {exc}') from exc


def generate_gdp_grid_map(longitude: float, latitude: float, magnitude: float,
                           output_path: str) -> str:
    """
    生成 GDP 网格图（图七）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图七（GDP网格图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.gdp_grid_map import generate_gdp_grid_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path)
        logger.info('图七生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图七生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图七生成失败: {exc}') from exc


def generate_earthquake_road_map(longitude: float, latitude: float, magnitude: float,
                                  output_path: str, kml_path: str = None) -> str:
    """
    生成道路交通图（图八）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径
        kml_path: 可选，烈度圈 KML 路径

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图八（道路交通图）: lon=%.4f lat=%.4f M=%.1f', longitude, latitude, magnitude)
    try:
        from core.earthquake_road_map import generate_earthquake_road_map as _fn
        result = _fn(longitude, latitude, magnitude, output_path=output_path, kml_path=kml_path)
        logger.info('图八生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图八生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图八生成失败: {exc}') from exc


def generate_earthquake_landslide_slope_map(longitude: float, latitude: float, magnitude: float,
                                             output_path: str,
                                             kml_path: str = None) -> tuple:
    """
    生成历史滑坡、斜坡分布图（图九）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径
        kml_path: 可选，烈度圈 KML 路径

    返回:
        (img_path, stats_message) 元组

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图九（滑坡斜坡分布图）: lon=%.4f lat=%.4f M=%.1f',
                longitude, latitude, magnitude)
    try:
        from core.earthquake_landslide_slope_map import (
            generate_earthquake_landslide_slope_map as _fn,
        )
        result, stats = _fn(longitude, latitude, magnitude,
                            output_path=output_path, kml_path=kml_path)
        logger.info('图九生成完成: %s, stats=%s', output_path, stats)
        return result, stats
    except Exception as exc:
        logger.error('图九生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图九生成失败: {exc}') from exc


def convert_kml_to_ia(kml_path: str, ia_output_path: str, interp_method: str = 'scipy_tin',
                       sample_interval: int = 5, max_sample_points: int = 50000) -> bool:
    """
    将 PGA KML 文件转换为 Ia 栅格（Ia.tif）。

    参数:
        kml_path: 输入 KML 文件路径
        ia_output_path: 输出 Ia.tif 路径
        interp_method: 插值方法，默认 scipy_tin
        sample_interval: 等值线采样间隔
        max_sample_points: 最大采样点数

    返回:
        True 表示成功，False 表示失败

    异常:
        MapGenerationError: 转换失败时抛出
    """
    logger.info('开始生成 Ia.tif: kml=%s', kml_path)
    try:
        from core.kml_to_Ia import KmlToIaConverter
        converter = KmlToIaConverter(
            kml_path=kml_path,
            ia_output_path=ia_output_path,
            interp_method=interp_method,
            sample_interval=sample_interval,
            max_sample_points=max_sample_points,
        )
        success = converter.run()
        if success:
            logger.info('Ia.tif 生成完成: %s', ia_output_path)
        else:
            logger.error('Ia.tif 生成失败（converter.run() 返回 False）')
        return success
    except Exception as exc:
        logger.error('Ia.tif 生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'Ia.tif 生成失败: {exc}') from exc


def convert_ia_to_dn(ia_tif_path: str, dn_output_path: str,
                      epicenter_lon: float, epicenter_lat: float, magnitude: float) -> str:
    """
    基于 Ia.tif 生成 Dn.tif（Newmark 位移栅格）。

    参数:
        ia_tif_path: 输入 Ia.tif 路径
        dn_output_path: 输出 Dn.tif 路径
        epicenter_lon: 震中经度
        epicenter_lat: 震中纬度
        magnitude: 震级

    返回:
        Dn.tif 文件路径

    异常:
        MapGenerationError: 计算失败时抛出
    """
    logger.info('开始生成 Dn.tif: ia=%s', ia_tif_path)
    try:
        from core.ac_ia_to_dn import calculate_dn_optimized
        ac_tif_path = getattr(settings, 'AC_TIF_PATH', 'C:/地质/ac/ac分割版/ac2.TIF')
        calculate_dn_optimized(
            ac_tif_path=ac_tif_path,
            ia_tif_path=ia_tif_path,
            output_path=dn_output_path,
            epicenter_lon=epicenter_lon,
            epicenter_lat=epicenter_lat,
            magnitude=magnitude,
        )
        logger.info('Dn.tif 生成完成: %s', dn_output_path)
        return dn_output_path
    except Exception as exc:
        logger.error('Dn.tif 生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'Dn.tif 生成失败: {exc}') from exc


def generate_earthquake_newmark_map(longitude: float, latitude: float, magnitude: float,
                                     output_path: str, kml_path: str = None,
                                     dn_tif_path: str = None) -> str:
    """
    生成 Newmark 位移分布图（图十）。

    参数:
        longitude: 震中经度
        latitude: 震中纬度
        magnitude: 震级
        output_path: 输出 PNG 路径
        kml_path: 可选，烈度圈 KML 路径
        dn_tif_path: 可选，Dn.tif 路径（不传则使用模块默认值）

    返回:
        输出文件路径

    异常:
        MapGenerationError: 生成失败时抛出
    """
    logger.info('开始生成图十（Newmark位移图）: lon=%.4f lat=%.4f M=%.1f',
                longitude, latitude, magnitude)
    try:
        from core.earthquake_newmark_map import generate_earthquake_newmark_map as _fn
        result = _fn(longitude, latitude, magnitude,
                     output_path=output_path, kml_path=kml_path, dn_tif_path=dn_tif_path)
        logger.info('图十生成完成: %s', output_path)
        return result
    except Exception as exc:
        logger.error('图十生成失败: %s', exc, exc_info=True)
        raise MapGenerationError(f'图十生成失败: {exc}') from exc
