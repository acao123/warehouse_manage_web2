# -*- coding: utf-8 -*-
"""
报告任务异步执行模块

包含 execute_report_task() 主执行函数，依次调用各地图生成器，
生成 10 张图片并将结果保存到 report_task_record 表。

进度更新：通过 update_progress() 辅助函数更新 report_task 表的状态，
后续可扩展为 WebSocket 推送或 Redis 缓存，以支持前端进度条。
"""

import gc
import logging
import os
import random
import threading
from datetime import datetime
from functools import wraps
from typing import Callable, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .models import ReportTask, ReportTaskRecord

logger = logging.getLogger('report')

# ============================================================
# 进度常量（用于后续扩展进度条，当前仅打日志）
# ============================================================
PROGRESS_STEPS = {
    'start': 0,
    'img1': 10,
    'img2': 20,
    'img3': 30,
    'img4': 40,
    'img5': 50,
    'img6': 60,
    'img7': 65,
    'img8': 70,
    'img9': 80,
    'ia_tif': 85,
    'dn_tif': 90,
    'img10': 95,
    'save': 98,
    'done': 100,
}


# ============================================================
# 安全装饰器：为每个图片生成函数提供异常隔离
# ============================================================

def safe_qgis_task(func: Callable) -> Callable:
    """
    装饰器：为 QGIS 任务提供安全的异常处理和资源管理。

    功能：
    1. 捕获所有异常，防止单个图片生成失败导致整个任务崩溃
    2. 每次调用后强制垃圾回收，释放 QGIS 资源
    3. 记录详细日志，便于问题排查
    """
    @wraps(func)
    def wrapper(task: ReportTask, output_dir: str, *args, **kwargs):
        task_id = task.id
        func_name = func.__name__

        try:
            logger.info('[任务 %s] 开始执行 %s', task_id, func_name)
            result = func(task, output_dir, *args, **kwargs)
            logger.info('[任务 %s] %s 执行完成', task_id, func_name)
            return result

        except Exception as exc:
            logger.error(
                '[任务 %s] %s 执行失败: %s',
                task_id, func_name, exc, exc_info=True
            )
            # 返回 None，不抛出异常，保证其他步骤可继续执行
            return None

        finally:
            # 强制垃圾回收，释放 QGIS 对象占用的内存
            gc.collect()

    return wrapper


# ============================================================
# 进度更新辅助函数
# ============================================================

def update_progress(task_id: int, step: str, progress: int) -> None:
    """
    记录任务执行进度（当前实现仅写日志，后续可扩展为缓存/推送）。

    参数:
        task_id: 任务ID
        step: 当前步骤名称
        progress: 进度百分比（0-100）
    """
    logger.info('[任务 %s] 进度 %d%% - %s', task_id, progress, step)


# ============================================================
# 输出路径生成
# ============================================================

def _build_output_dir(task_id: int) -> str:
    """
    生成本次任务的输出目录路径。

    格式：{FILE_BASE_PATH}/task/{timestamp}{random4}/{task_id}/
    示例：E:/data/report/task/20260316143025_1234/1/

    参数:
        task_id: report_task 表的 id

    返回:
        输出目录绝对路径字符串
    """
    base = getattr(settings, 'FILE_BASE_PATH', os.path.join(settings.BASE_DIR, 'data', 'report'))
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    rand4 = str(random.randint(1000, 9999))
    return os.path.join(base, 'task', f'{timestamp}{rand4}', str(task_id))


def _img_path(output_dir: str, img_no: int) -> str:
    """返回第 img_no 张图片的输出路径（如 .../1.png）。"""
    return os.path.join(output_dir, f'{img_no}.png')


# ============================================================
# 各图片生成封装函数
# ============================================================

def _gen_img1(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """
    生成图一：历史地震分布图。

    返回:
        (img_path, img_info) 或 (None, None)
    """
    try:
        from core.earthquake_map import generate_earthquake_map
        out = _img_path(output_dir, 1)
        info = generate_earthquake_map(
            center_lon=float(task.longitude),
            center_lat=float(task.latitude),
            magnitude=task.magnitude,
            csv_path=task.history_record_path,
            output_path=out,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图一生成完成: %s', task.id, out)
        return out, str(info) if info else None
    except Exception as exc:
        logger.error('[任务 %s] 图一生成失败: %s', task.id, exc, exc_info=True)
        return None, None


def _gen_img2(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """
    生成图二：地震烈度分布图。

    返回:
        (img_path, img_info) 或 (None, None)
    """
    try:
        from core.earthquake_kml_map import generate_earthquake_kml_map, int_to_roman
        out = _img_path(output_dir, 2)
        result = generate_earthquake_kml_map(
            kml_path=task.intensity_kml_path,
            description_text='',
            magnitude=task.magnitude,
            output_path=out,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        info = None
        if result and isinstance(result, dict):
            max_intensity = result.get('max_intensity')
            if max_intensity is not None:
                try:
                    info = int_to_roman(int(max_intensity))
                except Exception:
                    info = str(max_intensity)
        logger.info('[任务 %s] 图二生成完成: %s, 说明=%s', task.id, out, info)
        return out, info
    except Exception as exc:
        logger.error('[任务 %s] 图二生成失败: %s', task.id, exc, exc_info=True)
        return None, None


def _gen_img3(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图三：地质构造图。返回 img_path 或 None。"""
    try:
        from core.earthquake_geological_map2 import generate_earthquake_geology_map
        out = _img_path(output_dir, 3)
        generate_earthquake_geology_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图三生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图三生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img4(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图四：数字高程图。返回 img_path 或 None。"""
    try:
        from core.earthquake_elevation_map import generate_earthquake_elevation_map
        out = _img_path(output_dir, 4)
        generate_earthquake_elevation_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图四生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图四生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img5(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图五：土地利用类型图。返回 img_path 或 None。"""
    try:
        from core.earthquake_land_use_map import generate_earthquake_land_use_map
        out = _img_path(output_dir, 5)
        generate_earthquake_land_use_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图五生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图五生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img6(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图六：人口分布图。返回 img_path 或 None。"""
    try:
        from core.earthquake_population_map import generate_earthquake_population_map
        out = _img_path(output_dir, 6)
        generate_earthquake_population_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图六生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图六生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img7(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图七：GDP 网格图。返回 img_path 或 None。"""
    try:
        from core.gdp_grid_map import generate_gdp_grid_map
        out = _img_path(output_dir, 7)
        generate_gdp_grid_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图七生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图七生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img8(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """生成图八：道路交通图。返回 img_path 或 None。"""
    try:
        from core.earthquake_road_map import generate_earthquake_road_map
        out = _img_path(output_dir, 8)
        generate_earthquake_road_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图八生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图八生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img9(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """
    生成图九：历史滑坡、斜坡分布图。

    返回:
        (img_path, img_info) 或 (None, None)
    """
    try:
        from core.earthquake_landslide_slope_map import generate_earthquake_landslide_slope_map
        out = _img_path(output_dir, 9)
        result, stats = generate_earthquake_landslide_slope_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        logger.info('[任务 %s] 图九生成完成: %s, 说明=%s', task.id, out, stats)
        return out, str(stats) if stats else None
    except Exception as exc:
        logger.error('[任务 %s] 图九生成失败: %s', task.id, exc, exc_info=True)
        return None, None


def _gen_ia_tif(task: ReportTask, output_dir: str) -> str | None:
    """
    生成 Ia.tif（PGA KML → Ia 栅格）。

    返回:
        Ia.tif 文件路径，或 None（失败时）
    """
    try:
        from core.kml_to_Ia import KmlToIaConverter
        ia_path = os.path.join(output_dir, 'Ia.tif')
        converter = KmlToIaConverter(
            kml_path=task.pga_kml_path,
            ia_output_path=ia_path,
            interp_method=task.interp_method,
            sample_interval=task.sample_interval,
            max_sample_points=task.max_sample_points,
        )
        success = converter.run()
        if success:
            logger.info('[任务 %s] Ia.tif 生成完成: %s', task.id, ia_path)
            return ia_path
        else:
            logger.error('[任务 %s] Ia.tif 生成失败（converter.run() 返回 False）', task.id)
            return None
    except Exception as exc:
        logger.error('[任务 %s] Ia.tif 生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_dn_tif(task: ReportTask, output_dir: str, ia_tif_path: str) -> str | None:
    """
    基于 Ia.tif 生成 Dn.tif。

    返回:
        Dn.tif 文件路径，或 None（失败时）
    """
    try:
        from core.ac_ia_to_dn import calculate_dn_optimized
        ac_tif_path = getattr(settings, 'AC_TIF_PATH', 'C:/地质/ac/ac分割版/ac2.TIF')
        dn_path = os.path.join(output_dir, 'Dn.tif')
        calculate_dn_optimized(
            ac_tif_path=ac_tif_path,
            ia_tif_path=ia_tif_path,
            output_path=dn_path,
            epicenter_lon=float(task.longitude),
            epicenter_lat=float(task.latitude),
            magnitude=task.magnitude,
        )
        logger.info('[任务 %s] Dn.tif 生成完成: %s', task.id, dn_path)
        return dn_path
    except Exception as exc:
        logger.error('[任务 %s] Dn.tif 生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img10(task: ReportTask, output_dir: str, dn_tif_path: str | None):
    """
    生成图十：Newmark 位移图。

    参数:
        dn_tif_path: Dn.tif 路径（可为 None，此时使用模块默认值）

    返回:
        img_path 或 None
    """
    try:
        from core.earthquake_newmark_map import generate_earthquake_newmark_map
        out = _img_path(output_dir, 10)
        generate_earthquake_newmark_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            dn_tif_path=dn_tif_path,
        )
        logger.info('[任务 %s] 图十生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图十生成失败: %s', task.id, exc, exc_info=True)
        return None


# ============================================================
# 主执行函数
# ============================================================

def execute_report_task(task_id: int) -> None:
    """
    异步执行报告任务主函数，依次生成 10 张图片并保存到 report_task_record 表。

    执行流程：
        1. 创建输出目录
        2. 通过 QGISManager 初始化 QGIS（保证全局唯一实例）
        3. 依次生成图一 ~ 图九
        4. 生成 Ia.tif → Dn.tif → 图十
        5. 将结果保存到 report_task_record 表
        6. 更新 report_task 状态为成功（失败时标记为失败）

    异常处理：
        - 整体使用 try/except 包裹，确保任何异常都不会导致 Web 进程崩溃
        - 每个图片生成函数内部独立捕获异常，单个失败不影响其他图片
        - 最终通过 gc.collect() 强制释放 QGIS 相关资源

    参数:
        task_id: report_task 表的 id
    """
    logger.info('[任务 %s] 开始执行报告任务', task_id)

    # ---- 查询任务 ----
    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        logger.error('[任务 %s] 任务不存在，终止执行', task_id)
        return

    # ---- 创建输出目录 ----
    output_dir = _build_output_dir(task_id)
    try:
        os.makedirs(output_dir, exist_ok=True)
        logger.info('[任务 %s] 输出目录: %s', task_id, output_dir)
    except Exception as exc:
        logger.error('[任务 %s] 创建输出目录失败: %s', task_id, exc, exc_info=True)
        _mark_failed(task)
        return

    # 记录各图片结果
    record_kwargs = {'user_id': task.user_id, 'task_id': task_id}

    # ---- 缓存模式：预先下���天地图底图和注记 ----
    cached_basemap_path = None
    cached_annotation_path = None
    if task.cache_base_map == 1:
        from core.tianditu_basemap_downloader import download_basemap_with_cache
        from core.earthquake_map import (
            calculate_extent, get_magnitude_config,
            calculate_map_height_from_extent, MAP_WIDTH_MM, OUTPUT_DPI,
        )
        _config = get_magnitude_config(float(task.magnitude))
        _half_size_km = _config["map_size_km"] / 2.0
        _extent = calculate_extent(float(task.longitude), float(task.latitude), _half_size_km)
        _map_height_mm = calculate_map_height_from_extent(_extent, MAP_WIDTH_MM)
        _width_px = int(MAP_WIDTH_MM / 25.4 * OUTPUT_DPI)
        _height_px = int(_map_height_mm / 25.4 * OUTPUT_DPI)
        _basemap_path = os.path.join(output_dir, 'basemap.png')
        _annotation_path = os.path.join(output_dir, 'annotation.png')
        _bm, _ann, _err = download_basemap_with_cache(
            _extent, _width_px, _height_px,
            _basemap_path, _annotation_path, task.cache_base_map
        )
        if _err:
            logger.error('[任务 %s] 缓存底图下载失败: %s', task_id, _err)
            _mark_failed(task)
            return
        cached_basemap_path = _basemap_path
        cached_annotation_path = _annotation_path
        logger.info('[任务 %s] 缓存底图下载成功: basemap=%s, annotation=%s',
                    task_id, cached_basemap_path, cached_annotation_path)

    try:
        # 通过 QGISManager 确保 QGIS 已初始化（统一管理前缀路径和资源清理）
        from core.qgis_manager import get_qgis_manager
        qgis_manager = get_qgis_manager()
        qgis_manager.ensure_initialized()

        # ---- 图一 ----
        update_progress(task_id, '生成图一（历史地震分布图）', PROGRESS_STEPS['img1'])
        img1_path, img1_info = _gen_img1(task, output_dir,
                                          basemap_path=cached_basemap_path,
                                          annotation_path=cached_annotation_path)
        record_kwargs['img1_path'] = img1_path
        record_kwargs['img1_info'] = img1_info
        gc.collect()

        # ---- 图二 ----
        # update_progress(task_id, '生成图二（烈度分布图）', PROGRESS_STEPS['img2'])
        # img2_path, img2_info = _gen_img2(task, output_dir,
        #                                   basemap_path=cached_basemap_path,
        #                                   annotation_path=cached_annotation_path)
        # record_kwargs['img2_path'] = img2_path
        # record_kwargs['img2_info'] = img2_info
        # gc.collect()
        #
        # # ---- 图三 ----
        # update_progress(task_id, '生成图三（地质构造图）', PROGRESS_STEPS['img3'])
        # record_kwargs['img3_path'] = _gen_img3(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图四 ----
        # update_progress(task_id, '生成图四（数字高程图）', PROGRESS_STEPS['img4'])
        # record_kwargs['img4_path'] = _gen_img4(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图五 ----
        # update_progress(task_id, '生成图五（土地利用类型图）', PROGRESS_STEPS['img5'])
        # record_kwargs['img5_path'] = _gen_img5(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图六 ----
        # update_progress(task_id, '生成图六（人口分布图）', PROGRESS_STEPS['img6'])
        # record_kwargs['img6_path'] = _gen_img6(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图七 ----
        # update_progress(task_id, '生成图七（GDP网格图）', PROGRESS_STEPS['img7'])
        # record_kwargs['img7_path'] = _gen_img7(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图八 ----
        # update_progress(task_id, '生成图八（道路交通图）', PROGRESS_STEPS['img8'])
        # record_kwargs['img8_path'] = _gen_img8(task, output_dir,
        #                                         basemap_path=cached_basemap_path,
        #                                         annotation_path=cached_annotation_path)
        # gc.collect()
        #
        # # ---- 图九 ----
        # update_progress(task_id, '生成图九（滑坡斜坡分布图）', PROGRESS_STEPS['img9'])
        # img9_path, img9_info = _gen_img9(task, output_dir,
        #                                   basemap_path=cached_basemap_path,
        #                                   annotation_path=cached_annotation_path)
        # record_kwargs['img9_path'] = img9_path
        # record_kwargs['img9_info'] = img9_info
        # gc.collect()
        #
        # # ---- Ia.tif ----
        # update_progress(task_id, '生成 Ia.tif', PROGRESS_STEPS['ia_tif'])
        # ia_tif_path = _gen_ia_tif(task, output_dir)
        # gc.collect()
        #
        # # ---- Dn.tif ----
        # update_progress(task_id, '生成 Dn.tif', PROGRESS_STEPS['dn_tif'])
        # dn_tif_path = None
        # if ia_tif_path:
        #     dn_tif_path = _gen_dn_tif(task, output_dir, ia_tif_path)
        # else:
        #     logger.warning('[任务 %s] Ia.tif 未生成，跳过 Dn.tif 及图十', task_id)
        # gc.collect()
        #
        # # ---- 图十 ----
        # update_progress(task_id, '生成图十（Newmark位移图）', PROGRESS_STEPS['img10'])
        # record_kwargs['img10_path'] = _gen_img10(task, output_dir, dn_tif_path)
        # gc.collect()

        # ---- 保存记录 ----
        update_progress(task_id, '保存记录到数据库', PROGRESS_STEPS['save'])
        ReportTaskRecord.objects.create(**record_kwargs)
        logger.info('[任务 %s] 已保存 report_task_record', task_id)

        # ---- 生成Word文档 ----
        generate_report_word(task, output_dir, record_kwargs)

        # ---- 标记成功 ----
        task.task_status = ReportTask.STATUS_SUCCESS
        task.success_time = datetime.now()
        task.save(update_fields=['task_status', 'success_time', 'updated_at'])
        update_progress(task_id, '任务完成', PROGRESS_STEPS['done'])
        logger.info('[任务 %s] 报告任务执行成功', task_id)

    except Exception as exc:
        logger.error('[任务 %s] 报告任务执行过程中发生未捕获异常: %s', task_id, exc, exc_info=True)
        _mark_failed(task)

    finally:
        # 最终强制释放所有 QGIS 资源，防止内存积累
        try:
            from core.qgis_manager import get_qgis_manager
            get_qgis_manager().cleanup_session(task_id)
        except Exception as cleanup_exc:
            logger.warning('[任务 %s] 最终资源清理异常: %s', task_id, cleanup_exc)
        gc.collect()


def _mark_failed(task: ReportTask) -> None:
    """将任务状态标记为失败。"""
    try:
        task.task_status = ReportTask.STATUS_FAILED
        task.save(update_fields=['task_status', 'updated_at'])
        logger.info('[任务 %s] 已标记为失败', task.id)
    except Exception as exc:
        logger.error('[任务 %s] 标记失败状态时出错: %s', task.id, exc, exc_info=True)


# ============================================================
# Word文档生成函数
# ============================================================

def _get_map_scope_km(magnitude: float) -> int:
    """
    根据震级获取历史地震统计范围（公里）。

    参数:
        magnitude: 震级

    返回:
        统计范围（公里），默认150km
    """
    # 根据实际需求，可以根据震级返回不同的范围
    # 目前固定返回150km
    if magnitude >= 7.0:
        return 200
    elif magnitude >= 6.0:
        return 150
    elif magnitude >= 5.0:
        return 100
    else:
        return 80


def generate_report_word(task: ReportTask, output_dir: str, record_data: dict) -> Optional[str]:
    """
    根据 Word 模板生成报告文档，使用占位符替换方式。

    模板占位符说明（使用 Jinja2 语法）：
        - {{current_year}}: 当前年份
        - {{task_id}}: 任务ID（期号）
        - {{current_Time}}: 当前日期时间
        - {{address}}: 地震位置（如：XX省XX市）
        - {{base_info}}: 地震基本信息描述
        - {{img1_info}}: 历史地震说明文字
        - {{img1}}: 历史地震分布图
        - {{scope}}: 历史地震统计范围（如150）
        - {{img2_info}}: 烈度说明文字
        - {{img2}}: 烈度分布图
        - {{img3}} ~ {{img9}}: 其他专题图
        - {{img9_info}}: 滑坡分布说明

    执行流程：
        1. 检查并导入 docxtpl 库
        2. 查询 ReportTaskRecord 记录
        3. 加载 Word 模板文件
        4. 构建模板上下文（包括文字和图片）
        5. 渲染模板并保存
        6. 更新数据库记录

    参数:
        task: ReportTask 模型对象
        output_dir: 输出目录路径
        record_data: 已生成的图片路径字典

    返回:
        文档路径字符串，失败时返回 None
    """
    try:
        # 尝试导入 docxtpl 库（支持 Jinja2 模板语法）
        from docxtpl import DocxTemplate, InlineImage
        from docx.shared import Inches, Mm
    except ImportError:
        logger.error('[任务 %s] docxtpl 未安装，尝试使用 python-docx 备用方案', task.id)
        return _generate_report_word_fallback(task, output_dir, record_data)

    try:
        task_id = task.id

        # ---- 获取 ReportTaskRecord 记录 ----
        record = ReportTaskRecord.objects.filter(task_id=task_id).first()
        if not record:
            logger.warning('[任务 %s] 未找到 ReportTaskRecord，跳过Word文档生成', task_id)
            return None

        # ---- 获取模板路径 ----
        template_path = getattr(settings, 'REPORT_TEMPLATE_PATH', None)
        if not template_path or not os.path.exists(template_path):
            logger.warning('[任务 %s] Word模板文件不存在: %s，使用备用方案', task_id, template_path)
            return _generate_report_word_fallback(task, output_dir, record_data)

        # ---- 加载模板 ----
        doc = DocxTemplate(template_path)
        logger.info('[任务 %s] 已加载Word模板: %s', task_id, template_path)

        # ---- 准备日期时间字符串 ----
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_time = now.strftime('%Y年%m月%d日')

        # ---- 准备地震基本信息 ----
        # 格式：北京时间2026年3月15日14时30分，XX省XX市发生X.X级地震，震中位于北纬XX.XX度，东经XX.XX度，震源深度约XXkm
        ori_time_str = task.ori_time.strftime('%Y年%m月%d日%H时%M分') if task.ori_time else ''
        base_info = (
            f"北京时间{ori_time_str}，{task.address}发生{task.magnitude}级地震，"
            f"震中位于北纬{float(task.latitude):.2f}度，东经{float(task.longitude):.2f}度，"
            f"震源深度约{int(task.foc_depth)}km"
        )

        # ---- 获取历史地震统计范围 ----
        scope = _get_map_scope_km(task.magnitude)

        # ---- 构建模板上下文 ----
        context = {
            'current_year': current_year,
            'task_id': task_id,
            'current_Time': current_time,
            'address': f"{task.address}{task.magnitude}",
            'base_info': base_info,
            'scope': scope,
            # 文字说明
            'img1_info': record.img1_info or '',
            'img2_info': record.img2_info or '',
            'img9_info': record.img9_info or '',
        }

        # ---- 准备图片对象 ----
        # 图片宽度设置（单位：英寸）
        img_width = Inches(5.5)

        # 图片一：历史地震分布图
        img1_path = record.img1_path
        if img1_path and os.path.exists(img1_path):
            context['img1'] = InlineImage(doc, img1_path, width=img_width)
            logger.info('[任务 %s] 已准备图一: %s', task_id, img1_path)
        else:
            context['img1'] = ''
            logger.warning('[任务 %s] 图一不存在: %s', task_id, img1_path)

        # 图片二：烈度分布图
        img2_path = record.img2_path
        if img2_path and os.path.exists(img2_path):
            context['img2'] = InlineImage(doc, img2_path, width=img_width)
            logger.info('[任务 %s] 已准备图二: %s', task_id, img2_path)
        else:
            context['img2'] = ''
            logger.warning('[任务 %s] 图二不存在: %s', task_id, img2_path)

        # 图片三：地质构造图
        img3_path = record.img3_path
        if img3_path and os.path.exists(img3_path):
            context['img3'] = InlineImage(doc, img3_path, width=img_width)
            logger.info('[任务 %s] 已准备图三: %s', task_id, img3_path)
        else:
            context['img3'] = ''
            logger.warning('[任务 %s] 图三不存在: %s', task_id, img3_path)

        # 图片四：数字高程图
        img4_path = record.img4_path
        if img4_path and os.path.exists(img4_path):
            context['img4'] = InlineImage(doc, img4_path, width=img_width)
            logger.info('[任务 %s] 已准备图四: %s', task_id, img4_path)
        else:
            context['img4'] = ''
            logger.warning('[任务 %s] 图四不存在: %s', task_id, img4_path)

        # 图片五：土地利用类型图
        img5_path = record.img5_path
        if img5_path and os.path.exists(img5_path):
            context['img5'] = InlineImage(doc, img5_path, width=img_width)
            logger.info('[任务 %s] 已准备图五: %s', task_id, img5_path)
        else:
            context['img5'] = ''
            logger.warning('[任务 %s] 图五不存在: %s', task_id, img5_path)

        # 图片六：人口分布图
        img6_path = record.img6_path
        if img6_path and os.path.exists(img6_path):
            context['img6'] = InlineImage(doc, img6_path, width=img_width)
            logger.info('[任务 %s] 已准备图六: %s', task_id, img6_path)
        else:
            context['img6'] = ''
            logger.warning('[任务 %s] 图六不存在: %s', task_id, img6_path)

        # 图片七：GDP网格图
        img7_path = record.img7_path
        if img7_path and os.path.exists(img7_path):
            context['img7'] = InlineImage(doc, img7_path, width=img_width)
            logger.info('[任务 %s] 已准备图七: %s', task_id, img7_path)
        else:
            context['img7'] = ''
            logger.warning('[任务 %s] 图七不存在: %s', task_id, img7_path)

        # 图片八：道路交通图
        img8_path = record.img8_path
        if img8_path and os.path.exists(img8_path):
            context['img8'] = InlineImage(doc, img8_path, width=img_width)
            logger.info('[任务 %s] 已准备图八: %s', task_id, img8_path)
        else:
            context['img8'] = ''
            logger.warning('[任务 %s] 图八不存在: %s', task_id, img8_path)

        # 图片九：滑坡分布图
        img9_path = record.img9_path
        if img9_path and os.path.exists(img9_path):
            context['img9'] = InlineImage(doc, img9_path, width=img_width)
            logger.info('[任务 %s] 已准备图九: %s', task_id, img9_path)
        else:
            context['img9'] = ''
            logger.warning('[任务 %s] 图九不存在: %s', task_id, img9_path)

        # ---- 渲染模板 ----
        doc.render(context)
        logger.info('[任务 %s] 模板渲染完成', task_id)

        # ---- 保存文档 ----
        doc_path = os.path.join(output_dir, 'report.docx')
        doc.save(doc_path)
        logger.info('[任务 %s] Word文档已保存: %s', task_id, doc_path)

        # ---- 更新数据库记录 ----
        record.report_path = doc_path
        record.save(update_fields=['report_path', 'updated_at'])
        logger.info('[任务 %s] report_task_record.report_path 已更新', task_id)

        return doc_path

    except Exception as exc:
        logger.error('[任务 %s] Word文档生成失败: %s', task.id, exc, exc_info=True)
        # 尝试使用备用方案
        return _generate_report_word_fallback(task, output_dir, record_data)


def _generate_report_word_fallback(task: ReportTask, output_dir: str, record_data: dict) -> Optional[str]:
    """
    使用 python-docx 库的备用方案生成 Word 文档。

    当 docxtpl 不可用或模板文件不存在时，使用此函数从头创建文档。

    参数:
        task: ReportTask 模型对象
        output_dir: 输出目录路径
        record_data: 已生成的图片路径字典

    返回:
        文档路径字符串，失败时返回 None
    """
    try:
        from docx import Document
        from docx.shared import Inches, Pt, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        logger.error('[任务 %s] python-docx 未安装，跳过Word文档生成', task.id)
        return None

    try:
        task_id = task.id

        # ---- 获取 ReportTaskRecord 记录 ----
        record = ReportTaskRecord.objects.filter(task_id=task_id).first()
        if not record:
            logger.warning('[任务 %s] 未找到 ReportTaskRecord，跳过Word文档生成', task_id)
            return None

        # ---- 创建新文档 ----
        document = Document()

        # ---- 设置默认字体（需要设置中文字体）----
        document.styles['Normal'].font.name = '宋体'
        document.styles['Normal']._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

        # ---- 准备日期时间字符串 ----
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_time = now.strftime('%Y年%m月%d日')

        # ---- 添加标题行 ----
        # 第一行：年份和期号
        p1 = document.add_paragraph()
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run1 = p1.add_run(f'{current_year}年第{task_id}期')
        run1.font.size = Pt(16)
        run1.bold = True

        # 第二行：单位名称
        p2 = document.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run2 = p2.add_run('中国地震灾害防御中心')
        run2.font.size = Pt(12)

        # 第三行：部门和日期
        p3 = document.add_paragraph()
        run3a = p3.add_run('国（境）外地震灾情评估部')
        run3a.font.size = Pt(12)
        p3.add_run('\t' * 5)  # 添加制表符间隔
        run3b = p3.add_run(current_time)
        run3b.font.size = Pt(12)

        # ---- 添加地震标题 ----
        p_title = document.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_title = p_title.add_run(f'{task.address}{task.magnitude}级地震')
        run_title.font.size = Pt(18)
        run_title.bold = True

        # ---- 一、地震基本情况 ----
        document.add_heading('一、地震基本情况', level=1)
        ori_time_str = task.ori_time.strftime('%Y年%m月%d日%H时%M分') if task.ori_time else ''
        base_info = (
            f"据中国地震台网测定，北京时间{ori_time_str}，{task.address}发生{task.magnitude}级地震，"
            f"震中位于北纬{float(task.latitude):.2f}度，东经{float(task.longitude):.2f}度，"
            f"震源深度约{int(task.foc_depth)}km。"
        )
        document.add_paragraph(base_info)

        # ---- 二、历史地震��动性 ----
        document.add_heading('二、历史地震活动性', level=1)
        # 添加图一说明
        img1_info = record.img1_info
        if img1_info:
            document.add_paragraph(img1_info + '。')
        # 添加图一
        img1_path = record.img1_path
        if img1_path and os.path.exists(img1_path):
            document.add_picture(img1_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图一: %s', task_id, img1_path)
        # 添加图注
        scope = _get_map_scope_km(task.magnitude)
        p_cap1 = document.add_paragraph()
        p_cap1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap1.add_run(f'图1 1900年以来震中{scope}km范围内历史地震分布图')

        # ---- 三、地震烈度分布预估 ----
        document.add_heading('三、地震烈度分布预估', level=1)
        # 添加图二说明
        img2_info = record.img2_info
        if img2_info:
            document.add_paragraph(f'本次地震最高预估烈度为{img2_info}度。')
        # 添加图二
        img2_path = record.img2_path
        if img2_path and os.path.exists(img2_path):
            document.add_picture(img2_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图二: %s', task_id, img2_path)
        # 添加图注
        p_cap2 = document.add_paragraph()
        p_cap2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap2.add_run('图2 地震烈度预估图')

        # ---- 四、区域孕灾环境与承灾体分布 ----
        document.add_heading('四、区域孕灾环境与承灾体分布', level=1)

        # （一）地质构造
        document.add_heading('（一）地质构造', level=2)
        img3_path = record.img3_path
        if img3_path and os.path.exists(img3_path):
            document.add_picture(img3_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图三: %s', task_id, img3_path)
        p_cap3 = document.add_paragraph()
        p_cap3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap3.add_run('图3 震中附近150km地质构造图')

        # （二）地形地貌
        document.add_heading('（二）地形地貌', level=2)
        img4_path = record.img4_path
        if img4_path and os.path.exists(img4_path):
            document.add_picture(img4_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图四: %s', task_id, img4_path)
        p_cap4 = document.add_paragraph()
        p_cap4.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap4.add_run('图4 震中附近150km数字高程图（单位：m）')

        # （三）土地利用类型
        document.add_heading('（三）土地利用类型', level=2)
        img5_path = record.img5_path
        if img5_path and os.path.exists(img5_path):
            document.add_picture(img5_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图五: %s', task_id, img5_path)
        p_cap5 = document.add_paragraph()
        p_cap5.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap5.add_run('图5 震中附近150km土地利用类型分布图')

        # （四）人口公里网格
        document.add_heading('（四）人口公里网格', level=2)
        img6_path = record.img6_path
        if img6_path and os.path.exists(img6_path):
            document.add_picture(img6_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图六: %s', task_id, img6_path)
        p_cap6 = document.add_paragraph()
        p_cap6.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap6.add_run('图6 震中附近150km人口公里网格分布图（单位：人/平方千米）')

        # （五）经济公里网格
        document.add_heading('（五）经济公里网格', level=2)
        img7_path = record.img7_path
        if img7_path and os.path.exists(img7_path):
            document.add_picture(img7_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图七: %s', task_id, img7_path)
        p_cap7 = document.add_paragraph()
        p_cap7.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap7.add_run('图7 震中附近150km经济公里网格分布图（单位：万元/平方千米）')

        # （六）路网
        document.add_heading('（六）路网', level=2)
        img8_path = record.img8_path
        if img8_path and os.path.exists(img8_path):
            document.add_picture(img8_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图八: %s', task_id, img8_path)
        p_cap8 = document.add_paragraph()
        p_cap8.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap8.add_run('图8 震中附近150km路网分布图')

        # （七）历史滑坡灾害盘点
        document.add_heading('（七）历史滑坡灾害盘点', level=2)
        # 添加图九说明
        img9_info = record.img9_info
        if img9_info:
            document.add_paragraph(img9_info)
        img9_path = record.img9_path
        if img9_path and os.path.exists(img9_path):
            document.add_picture(img9_path, width=Inches(5.5))
            logger.info('[任务 %s] 已插入图九: %s', task_id, img9_path)
        p_cap9 = document.add_paragraph()
        p_cap9.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_cap9.add_run('图9 震中附近150km历史滑坡分布图')

        # ---- 保存文档 ----
        doc_path = os.path.join(output_dir, 'report.docx')
        document.save(doc_path)
        logger.info('[任务 %s] Word文档已保存（备用方案）: %s', task_id, doc_path)

        # ---- 更新数据库记录 ----
        record.report_path = doc_path
        record.save(update_fields=['report_path', 'updated_at'])
        logger.info('[任务 %s] report_task_record.report_path 已更新', task_id)

        return doc_path

    except Exception as exc:
        logger.error('[任务 %s] Word文档生成失败（备用方案）: %s', task.id, exc, exc_info=True)
        return None


# ============================================================
# 异步启动入口
# ============================================================

def start_task_async(task_id: int) -> None:
    """
    在后台线程中异步执行报告任务。

    参数:
        task_id: report_task 表的 id
    """
    thread = threading.Thread(
        target=execute_report_task,
        args=(task_id,),
        name=f'ReportTask-{task_id}',
        daemon=True,
    )
    thread.start()
    logger.info('[任务 %s] 已启动后台线程 %s', task_id, thread.name)