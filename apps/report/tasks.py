# -*- coding: utf-8 -*-
"""
报告任务异步执行模块

包含 execute_report_task() 主执行函数，依次调用各地图生成器，
生成 10 张图片并将结果保存到 report_task_record 表。

进度更新：通过 update_progress() 辅助函数更新 report_task 表的状态，
后续可扩展为 WebSocket 推送或 Redis 缓存，以支持前端进度条。
"""

import concurrent.futures as _cf
import gc
import logging
import os
import random
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .models import ReportTask, ReportTaskRecord

logger = logging.getLogger('report')

# ============================================================
# 模块级线程池（用于主任务异步提交，避免每次创建/销毁开销）
# ============================================================
_TASK_EXECUTOR = _cf.ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix='ReportMainTask',
)

# ============================================================
# 进度常量（对应每个执行步骤的 progress 值）
# ============================================================
PROGRESS_STEPS = {
    'start': 0,
    'tianditu_start': 1,
    'tianditu_done': 2,
    # 图1-图9 完成后 progress 分别为 3-11（每完成一张 +1，从 3 开始）
    'ia_tif': 12,
    'dn_tif': 13,
    'img10': 14,
    'img11': 15,
    'img12': 16,  # 新增：图十二进度
    'report_done': 99,
    'done': 100,
}

# 天地图缓存文件有效性最小尺寸（字节）：小于此值视为下载不完整的无效文件
_MIN_VALID_CACHE_SIZE_BYTES = 1024


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

def _update_task_progress(
        task_id: int,
        progress: int,
        append_message: str = '',
        error_message: str = None,
        task_status: int = None,
) -> None:
    """
    更新任务进度、状态描述及错误日志到数据库。

    参数:
        task_id: 任务ID
        progress: 当前进度值（1-100）
        append_message: 追加到 message 字段的信息（如 "img1已完成，"）
        error_message: 写入 error_message 字段的错误信息（None 表示不更新该字段）
        task_status: 要更新的任务状态（None 表示不更新该字段）
    """
    try:
        task = ReportTask.objects.get(id=task_id)
        update_fields = ['progress', 'updated_at']

        task.progress = progress

        if append_message:
            # 追加方式更新 message 字段
            old_msg = task.message or ''
            task.message = old_msg + append_message
            update_fields.append('message')

        if error_message is not None:
            task.error_message = error_message
            update_fields.append('error_message')

        if task_status is not None:
            task.task_status = task_status
            update_fields.append('task_status')

        task.save(update_fields=update_fields)
        logger.info('[任务 %s] 进度更新: progress=%d, append_message=%s, task_status=%s',
                    task_id, progress, append_message or '无', task_status)
    except Exception as exc:
        logger.error('[任务 %s] 更新进度失败: %s', task_id, exc)


def _is_cancelled(task_id: int) -> bool:
    """
    检查任务是否处于取消中状态（task_status=4）。

    参数:
        task_id: 任务ID

    返回:
        True 表示任务已被请求取消，应终止执行
    """
    try:
        task = ReportTask.objects.get(id=task_id)
        return task.task_status == ReportTask.STATUS_CANCELLING
    except Exception as exc:
        logger.error('[任务 %s] 检查取消状态失败: %s', task_id, exc)
        return False


def update_progress(task_id: int, step: str, progress: int) -> None:
    """
    记录任务执行进度（兼容旧调用，内部转发到 _update_task_progress）。

    参数:
        task_id: 任务ID
        step: 当前步骤名称
        progress: 进度百分比（0-100）
    """
    logger.info('[任务 %s] 进度 %d%% - %s', task_id, progress, step)


# ============================================================
# 输出路径生成
# ============================================================

def _normalize_path(path: str) -> str:
    """将路径中所有反斜杠统一替换为正斜杠，确保跨平台路径一致性。"""
    return path.replace("\\", "/")


def _build_output_dir(task_id: int) -> str:
    """
    生成本次任务的输出目录路径。

    格式：{FILE_BASE_PATH}/task/{timestamp}{task_id}/
    示例：E:/data/report/task/20260316/1/

    参数:
        task_id: report_task 表的 id

    返回:
        输出目录绝对路径字符串
    """
    base = getattr(settings, 'FILE_BASE_PATH', os.path.join(settings.BASE_DIR, 'data', 'report'))
    timestamp = datetime.now().strftime('%Y%m%d')
    return _normalize_path(str(Path(base) / "task" / timestamp / str(task_id)))


def _img_path(output_dir: str, img_no: int) -> str:
    """返回第 img_no 张图片的输出路径（如 .../1.png）。"""
    return _normalize_path(str(Path(output_dir) / f'{img_no}.png'))


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
            earthquake_time = task.ori_time
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
        desc = (
            " 综合考虑震中附近地质构造背景、地震波衰减特性，"
            "估计了本次地震的地震动预测图。"
        )
        result = generate_earthquake_kml_map(
            kml_path=task.intensity_kml_path,
            description_text=desc,
            magnitude=task.magnitude,
            output_path=out
        )
        info = None
        if result and isinstance(result, dict):
            max_intensity = result.get('max_intensity')
            if max_intensity is not None:
                try:
                    info = int_to_roman(int(max_intensity))
                except Exception:
                    info = str(max_intensity)
        info = f'预计极震区烈度可达{info}度，极震区面积估算为{format_area(result['max_intensity_area'])}平方千米'
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
        ia_path = _normalize_path(os.path.join(output_dir, 'Ia.tif'))
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
        ac_tif_path = getattr(settings, 'AC_TIF_PATH', 'C:/地质/ac/全国ac分布/ac.tif')
        dn_path = _normalize_path(os.path.join(output_dir, 'Dn.tif'))
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
            dn_tif_path=dn_tif_path,
        )
        logger.info('[任务 %s] 图十生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图十生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img11(task: ReportTask, output_dir: str, dn_tif_path: str):
    """
    生成图十一：地震危险性图。

    返回:
        (img_path, img_info, max_dn_value) 或 (None, None, None)
    """
    try:
        from core.earthquake_hazard_map import generate_earthquake_hazard_map
        out = _img_path(output_dir, 11)
        img_path, max_dn_value, statistics_summary = generate_earthquake_hazard_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            a=0.1169,
            b=-0.1803,
            c=0.5165,
            output_path=out,
            dn_tif_path = dn_tif_path
        )
        img_info = statistics_summary
        logger.info('[任务 %s] 图十一生成完成: %s, 最大Dn=%s cm, 说明=%s',
                    task.id, out, max_dn_value, img_info)
        return out, str(img_info) if img_info else None, max_dn_value
    except Exception as exc:
        logger.error('[任务 %s] 图十一生成失败: %s', task.id, exc, exc_info=True)
        return None, None, None


def _gen_img12(task: ReportTask, output_dir: str, basemap_path=None, annotation_path=None):
    """
    生成图十二：地震滑坡评估图。

    返回:
        (img_path, img_info) 或 (None, None)
    """
    try:
        from core.earthquake_landslide_assessment_map import generate_earthquake_landslide_assessment_map
        out = _img_path(output_dir, 12)
        result = generate_earthquake_landslide_assessment_map(
            longitude=float(task.longitude),
            latitude=float(task.latitude),
            magnitude=task.magnitude,
            output_path=out,
            kml_path=task.intensity_kml_path,
            basemap_path=basemap_path,
            annotation_path=annotation_path,
        )
        if result:
            img_path = result.get('image_path')
            img_info = result.get('stats_message')
            logger.info('[任务 %s] 图十二生成完成: %s, 说明=%s', task.id, out, img_info)
            return out, str(img_info) if img_info else None
        else:
            logger.warning('[任务 %s] 图十二生成返回空结果', task.id)
            return None, None
    except Exception as exc:
        logger.error('[任务 %s] 图十二生成失败: %s', task.id, exc, exc_info=True)
        return None, None


# ============================================================
# 主执行函数
# ============================================================

def execute_report_task(task_id: int) -> None:
    """
    异步执行报告任务主函数，依次生成 10 张图片并保存到 report_task_record 表。

    执行流程：
        1. 查询任务，若不存在则 return
        2. 创建输出目录（失败写 error_message + 状态3）
        3. 初始化进度为 0，状态为 STATUS_RUNNING
        4. 若 cache_base_map==1，检查取消后下载天地图（progress=1→2）
        5. 初始化 QGIS
        6. 串行执行图1-图9（每完成一张 progress+1，从3开始）
        7. 检查是否取消；检查是否有失败图片（task_status=3）→ return
        8. 生成 Ia.tif（progress=12）
        9. 生成 Dn.tif（progress=13）
        10. 生成 图10（progress=14）
        11. 保存 ReportTaskRecord
        12. 生成 Word 报告（progress=99，更新 report_path）
        13. 检查是否取消；更新 progress=100，task_status=2（成功）
        14. finally: QGIS 资源清理

    参数:
        task_id: report_task 表的 id
    """
    logger.info('[任务 %s] 开始执行报告任务', task_id)

    # ---- 1. 查询任务 ----
    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        logger.error('[任务 %s] 任务不存在，终止执行', task_id)
        return

    # ---- 2. 创建输出目录 ----
    output_dir = _build_output_dir(task_id)
    try:
        os.makedirs(output_dir, exist_ok=True)
        logger.info('[任务 %s] 输出目录: %s', task_id, output_dir)
    except Exception as exc:
        logger.error('[任务 %s] 创建输出目录失败: %s', task_id, exc, exc_info=True)
        _mark_failed(task, error_message=f'创建输出目录失败: {exc}')
        return

    # ---- 3. 冗余初始化进度（保险） ----
    _update_task_progress(task_id, 1, task_status=ReportTask.STATUS_RUNNING)

    # 记录各图片结果
    record_kwargs = {'user_id': task.user_id, 'task_id': task_id}

    # ---- 缓存模式：预先下载天地图底图和注记 ----
    cached_basemap_path = None
    cached_annotation_path = None
    if task.cache_base_map == 1:
        # 检查是否已取消
        if _is_cancelled(task_id):
            logger.info('[任务 %s] 任务已取消，跳过天地图下载', task_id)
            _mark_failed(task)
            return

        _update_task_progress(task_id, PROGRESS_STEPS['tianditu_start'],
                              append_message='开始下载天地图，')
        try:
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
            _basemap_path = os.path.join(output_dir, f'basemap_task_{task_id}.png')
            _annotation_path = os.path.join(output_dir, f'annotation_task_{task_id}.png')
            # 验证缓存完整性：若 PNG 存在但 .pgw 世界文件缺失或文件过小，则删除强制重下
            for _cache_path in (_basemap_path, _annotation_path):
                if os.path.exists(_cache_path):
                    _pgw_path = os.path.splitext(_cache_path)[0] + '.pgw'
                    if not os.path.exists(_pgw_path) or os.path.getsize(_cache_path) < _MIN_VALID_CACHE_SIZE_BYTES:
                        try:
                            os.remove(_cache_path)
                            logger.warning('[任务 %s] 缓存文件不完整，已删除: %s', task_id, _cache_path)
                        except OSError as _del_exc:
                            logger.warning('[任务 %s] 删除不完整缓存文件失败: %s — %s',
                                           task_id, _cache_path, _del_exc)
            _bm, _ann, _err = download_basemap_with_cache(
                _extent, _width_px, _height_px,
                _basemap_path, _annotation_path, task.cache_base_map
            )
            if _err:
                logger.error('[任务 %s] 缓存底图下载失败: %s', task_id, _err)
                _mark_failed(task, error_message=f'天地图下载失败: {_err}')
                return
            cached_basemap_path = _basemap_path
            cached_annotation_path = _annotation_path
            logger.info('[任务 %s] 缓存底图下载成功: basemap=%s, annotation=%s',
                        task_id, cached_basemap_path, cached_annotation_path)
        except Exception as exc:
            logger.error('[任务 %s] 天地图下载异常: %s', task_id, exc, exc_info=True)
            _mark_failed(task, error_message=f'天地图下载异常: {exc}')
            return

        _update_task_progress(task_id, PROGRESS_STEPS['tianditu_done'],
                              append_message='天地图下载完成，')

    try:
        # ---- 5. 通过 QGISManager 确保 QGIS 已初始化 ----
        from core.qgis_manager import get_qgis_manager
        qgis_manager = get_qgis_manager()
        qgis_manager.ensure_initialized()

        # ---- 6. 串行执行图1-图9（QGIS 不支持多线程并发操作 QgsProject）----
        _img_generators = [
            ('img1', 1, _gen_img1),
            ('img2', 2, _gen_img2),
            ('img3', 3, _gen_img3),
            ('img4', 4, _gen_img4),
            ('img5', 5, _gen_img5),
            ('img6', 6, _gen_img6),
            ('img7', 7, _gen_img7),
            ('img8', 8, _gen_img8),
            ('img9', 9, _gen_img9),
        ]

        _has_failure = False

        for img_key, img_no, gen_func in _img_generators:
            # 检查是否取消
            if _is_cancelled(task_id):
                logger.info('[任务 %s] 图片生成过程中检测到取消信号，终止任务', task_id)
                _mark_failed(task)
                return

            try:
                result = gen_func(task, output_dir, cached_basemap_path, cached_annotation_path)
                current_progress = 2 + img_no  # img1=3, img2=4, ..., img9=11

                _update_task_progress(
                    task_id, current_progress,
                    append_message=f'img{img_no}已完成，',
                )

                # 根据图片编号存储结果
                if img_key in ('img1', 'img2', 'img9'):
                    # 这些函数返回 (path, info) 元组
                    img_path = result[0] if isinstance(result, tuple) else result
                    img_info = result[1] if isinstance(result, tuple) and len(result) > 1 else None
                    record_kwargs[f'{img_key}_path'] = img_path
                    record_kwargs[f'{img_key}_info'] = img_info
                else:
                    record_kwargs[f'{img_key}_path'] = result

            except Exception as exc:
                logger.error('[任务 %s] %s 执行异常: %s', task_id, img_key, exc, exc_info=True)
                current_progress = 2 + img_no
                _has_failure = True
                _update_task_progress(
                    task_id, current_progress,
                    error_message=f'{img_key}生成失败: {exc}',
                    task_status=ReportTask.STATUS_FAILED,
                )

            finally:
                gc.collect()  # 每张图后释放 QGIS 资源

        # ---- 7. 检查是否取消或失败 ----
        if _is_cancelled(task_id):
            logger.info('[任务 %s] 图1-9完成后检测到取消信号，终止任务', task_id)
            _mark_failed(task)
            return

        # 若有图片失败则终止（_has_failure 标志或重新查询 DB 状态）
        if _has_failure:
            logger.warning('[任务 %s] 图1-9中有生成失败项，终止任务', task_id)
            # 刷新 task 对象以获取 DB 中最新状态
            task.refresh_from_db()
            return

        # ---- 8. 生成 Ia.tif ----
        try:
            ia_tif_path = _gen_ia_tif(task, output_dir)
            record_kwargs['ia_tif_path'] = ia_tif_path
            _update_task_progress(task_id, PROGRESS_STEPS['ia_tif'],
                                  append_message='Ia.tif已完成，')
        except Exception as exc:
            logger.error('[任务 %s] Ia.tif 生成失败: %s', task_id, exc, exc_info=True)
            _mark_failed(task, error_message=f'Ia.tif生成失败: {exc}')
            return
        gc.collect()

        # ---- 9. 生成 Dn.tif ----
        dn_tif_path = None
        try:
            if ia_tif_path:
                dn_tif_path = _gen_dn_tif(task, output_dir, ia_tif_path)
                record_kwargs['dn_tif_path'] = dn_tif_path
                _update_task_progress(task_id, PROGRESS_STEPS['dn_tif'],
                                      append_message='Dn.tif已完成，')
            else:
                logger.warning('[任务 %s] Ia.tif 未生成，跳过 Dn.tif 及图十', task_id)
                _update_task_progress(task_id, PROGRESS_STEPS['dn_tif'])
        except Exception as exc:
            logger.error('[任务 %s] Dn.tif 生成失败: %s', task_id, exc, exc_info=True)
            _update_task_progress(task_id, PROGRESS_STEPS['dn_tif'],
                                  error_message=f'Dn.tif生成失败: {exc}')
        gc.collect()

        # ---- 10. 生成 图十 ----
        try:
            img10_path = _gen_img10(task, output_dir, dn_tif_path)
            record_kwargs['img10_path'] = img10_path
            _update_task_progress(task_id, PROGRESS_STEPS['img10'],
                                  append_message='img10已完成，')
        except Exception as exc:
            logger.error('[任务 %s] 图十生成失败: %s', task_id, exc, exc_info=True)
            _update_task_progress(task_id, PROGRESS_STEPS['img10'],
                                  error_message=f'图十生成失败: {exc}')
        gc.collect()

        # ---- 10.5 生成 图十一 ----
        try:
            img11_path, img11_info, max_dn_value = _gen_img11(task, output_dir, dn_tif_path)
            record_kwargs['img11_path'] = img11_path
            record_kwargs['img11_info'] = img11_info
            record_kwargs['dn_max_value'] = round(max_dn_value * 10) if max_dn_value is not None else None
            _update_task_progress(task_id, PROGRESS_STEPS['img11'],
                                  append_message='img11已完成，')
        except Exception as exc:
            logger.error('[任务 %s] 图十一生成失败: %s', task_id, exc, exc_info=True)
            _update_task_progress(task_id, PROGRESS_STEPS['img11'],
                                  error_message=f'图十一生成失败: {exc}')
        gc.collect()

        # ---- 10.6 生成 图十二 ----
        try:
            img12_path, img12_info = _gen_img12(task, output_dir, cached_basemap_path, cached_annotation_path)
            record_kwargs['img12_path'] = img12_path
            record_kwargs['img12_info'] = img12_info
            _update_task_progress(task_id, PROGRESS_STEPS['img12'],
                                  append_message='img12已完成，')
        except Exception as exc:
            logger.error('[任务 %s] 图十二生成失败: %s', task_id, exc, exc_info=True)
            _update_task_progress(task_id, PROGRESS_STEPS['img12'],
                                  error_message=f'图十二生成失败: {exc}')
        gc.collect()

        # ---- 11. 保存记录 ----
        try:
            ReportTaskRecord.objects.create(**record_kwargs)
            logger.info('[任务 %s] 已保存 report_task_record', task_id)
        except Exception as exc:
            logger.error('[任务 %s] 保存 report_task_record 失败: %s', task_id, exc, exc_info=True)
            _mark_failed(task, error_message=f'保存记录失败: {exc}')
            return

        # ---- 12. 生成Word文档 ----
        try:
            report_path = generate_report_word(task, output_dir, record_kwargs)
            if report_path:
                # 更新 report_path 到 ReportTaskRecord
                ReportTaskRecord.objects.filter(task_id=task_id).update(
                    report_path=report_path
                )
            _update_task_progress(task_id, PROGRESS_STEPS['report_done'],
                                  append_message='报告生成完成，')
        except Exception as exc:
            logger.error('[任务 %s] Word报告生成失败: %s', task_id, exc, exc_info=True)
            _update_task_progress(task_id, PROGRESS_STEPS['report_done'],
                                  error_message=f'Word报告生成失败: {exc}')

        # ---- 13. 检查是否取消；标记成功 ----
        if _is_cancelled(task_id):
            logger.info('[任务 %s] 报告生成后检测到取消信号，终止任务', task_id)
            _mark_failed(task)
            return

        task.refresh_from_db()
        task.task_status = ReportTask.STATUS_SUCCESS
        task.success_time = datetime.now()
        task.progress = PROGRESS_STEPS['done']
        task.save(update_fields=['task_status', 'success_time', 'progress', 'updated_at'])
        logger.info('[任务 %s] 报告任务执行成功，progress=100', task_id)

    except Exception as exc:
        logger.error('[任务 %s] 报告任务执行过程中发生未捕获异常: %s', task_id, exc, exc_info=True)
        _mark_failed(task, error_message=f'未捕获异常: {exc}')

    finally:
        # ---- 14. 最终强制释放所有 QGIS 资源，防止内存积累 ----
        try:
            from core.qgis_manager import get_qgis_manager
            get_qgis_manager().cleanup_session(task_id)
        except Exception as cleanup_exc:
            logger.warning('[任务 %s] 最终资源清理异常: %s', task_id, cleanup_exc)
        gc.collect()


def _mark_failed(task: ReportTask, error_message: str = None) -> None:
    """
    将任务状态标记为失败，并可选写入错误信息。

    参数:
        task: ReportTask 模型实例
        error_message: 可选的错误日志内容
    """
    try:
        # 刷新 task 对象，避免使用过期（stale）数据导致更新失败
        task.refresh_from_db()
        update_fields = ['task_status', 'updated_at']
        task.task_status = ReportTask.STATUS_FAILED
        if error_message is not None:
            task.error_message = error_message
            update_fields.append('error_message')
        task.save(update_fields=update_fields)
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
    if magnitude >= 7.0:
        return 150
    elif magnitude >= 6.0:
        return 50
    else:
        return 15


def _get_image_size_px(image_path: str) -> Tuple[int, int]:
    """
    获取图片的实际像素尺寸。

    参数:
        image_path: 图片文件路径

    返回:
        (width_px, height_px) 元组，失败时返回 (0, 0)
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return img.size  # (width, height)
    except Exception as exc:
        logger.warning('获取图片尺寸失败 %s: %s', image_path, exc)
        return (0, 0)


def _get_image_dpi(image_path: str) -> Tuple[float, float]:
    """
    获取图片的 DPI 信息。

    参数:
        image_path: 图片文件路径

    返回:
        (dpi_x, dpi_y) 元组，如果图片没有 DPI 信息则返回 (96, 96) 作为默认值
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            dpi = img.info.get('dpi')
            if dpi and isinstance(dpi, tuple) and len(dpi) >= 2:
                return (float(dpi[0]), float(dpi[1]))
            # 尝试从 EXIF 获取
            exif = img.getexif() if hasattr(img, 'getexif') else None
            if exif:
                # EXIF 标签 282 = XResolution, 283 = YResolution
                x_res = exif.get(282)
                y_res = exif.get(283)
                if x_res and y_res:
                    return (float(x_res), float(y_res))
    except Exception as exc:
        logger.warning('获取图片DPI失败 %s: %s', image_path, exc)
    return (96.0, 96.0)  # 默认 96 DPI


def _calculate_image_size_for_word(
        image_path: str,
        target_width_mm: Optional[float] = None,
        target_height_mm: Optional[float] = None,
        max_width_mm: float = 230.0,
        max_height_mm: float = 300.0,
        use_image_dpi: bool = True
) -> Tuple[float, float]:
    """
    计算图片在 Word 中的合适尺寸（毫米），保持宽高比。

    算法说明：
    1. 如果指定了 target_width_mm 或 target_height_mm，按指定值计算（保持宽高比）
    2. 如果启用 use_image_dpi 且图片有 DPI 信息，按图片原始物理尺寸计算
    3. 否则按最大宽高限制等比例缩放

    参数:
        image_path: 图片文件路径
        target_width_mm: 目标宽度（毫米），如果指定则优先使用
        target_height_mm: 目标高度（毫米），如果指定则优先使用
        max_width_mm: 最大宽度限制（毫米）
        max_height_mm: 最大高度限制（毫米）
        use_image_dpi: 是否使用图片自身的 DPI 信息计算物理尺寸

    返回:
        (width_mm, height_mm) 元组
    """
    width_px, height_px = _get_image_size_px(image_path)
    if width_px == 0 or height_px == 0:
        # 无法获取图片尺寸，返回默认值
        return (max_width_mm, max_width_mm * 0.75)  # 假设 4:3 比例

    aspect_ratio = width_px / height_px

    # 情况1：指定了目标宽度
    if target_width_mm is not None:
        calc_width = target_width_mm
        calc_height = target_width_mm / aspect_ratio
        # 检查高度是否超限
        if calc_height > max_height_mm:
            calc_height = max_height_mm
            calc_width = max_height_mm * aspect_ratio
        return (calc_width, calc_height)

    # 情况2：指定了目标高度
    if target_height_mm is not None:
        calc_height = target_height_mm
        calc_width = target_height_mm * aspect_ratio
        # 检查宽度是否超限
        if calc_width > max_width_mm:
            calc_width = max_width_mm
            calc_height = max_width_mm / aspect_ratio
        return (calc_width, calc_height)

    # 情况3：使用图片 DPI 计算原始物理尺寸
    if use_image_dpi:
        dpi_x, dpi_y = _get_image_dpi(image_path)
        # 像素转毫米: mm = px / dpi * 25.4
        original_width_mm = width_px / dpi_x * 25.4
        original_height_mm = height_px / dpi_y * 25.4

        # 如果原始尺寸在限制范围内，直接使用
        if original_width_mm <= max_width_mm and original_height_mm <= max_height_mm:
            return (original_width_mm, original_height_mm)

    # 情况4：按最大限制等比例缩放
    # 计算宽度和高度的缩放比例，取较小值确保都不超限
    scale_w = max_width_mm / (width_px / 96.0 * 25.4)  # 假设默认 96 DPI
    scale_h = max_height_mm / (height_px / 96.0 * 25.4)
    scale = min(scale_w, scale_h, 1.0)  # 不放大，只缩小

    final_width_mm = width_px / 96.0 * 25.4 * scale
    final_height_mm = height_px / 96.0 * 25.4 * scale

    # 确保不超过最大限制
    if final_width_mm > max_width_mm:
        scale = max_width_mm / final_width_mm
        final_width_mm = max_width_mm
        final_height_mm *= scale

    if final_height_mm > max_height_mm:
        scale = max_height_mm / final_height_mm
        final_height_mm = max_height_mm
        final_width_mm *= scale

    return (final_width_mm, final_height_mm)


def _create_inline_image(
        doc,
        image_path: str,
        target_width_mm: Optional[float] = None,
        target_height_mm: Optional[float] = None,
        max_width_mm: float = 150.0,
        max_height_mm: float = 200.0,
        use_image_dpi: bool = True
):
    """
    创建 InlineImage 对象，自动计算合适的尺寸。

    参数:
        doc: DocxTemplate 对象
        image_path: 图片文件路径
        target_width_mm: 目标宽度（毫米），可选
        target_height_mm: 目标高度（毫米），可选
        max_width_mm: 最大宽度限制（毫米）
        max_height_mm: 最大高度限制（毫米）
        use_image_dpi: 是否使用图片自身的 DPI 信息

    返回:
        InlineImage 对象，如果图片不存在则返回空字符串
    """
    from docxtpl import InlineImage
    from docx.shared import Mm

    if not image_path or not os.path.exists(image_path):
        return ''

    width_mm, height_mm = _calculate_image_size_for_word(
        image_path,
        target_width_mm=target_width_mm,
        target_height_mm=target_height_mm,
        max_width_mm=max_width_mm,
        max_height_mm=max_height_mm,
        use_image_dpi=use_image_dpi
    )

    logger.info('图片 %s 计算尺寸: %.1f x %.1f mm', image_path, width_mm, height_mm)

    # 创建 InlineImage，同时指定宽度和高度以确保尺寸精确
    return InlineImage(doc, image_path, width=Mm(width_mm), height=Mm(height_mm))


# ============================================================
# 图片尺寸配置
# ============================================================

# Word 文档中图片的默认尺寸配置（单位：毫米）
# 可根据模板实际需求调整这些值

IMAGE_SIZE_CONFIG = {
    # 默认配置：适用于大多数图片
    'default': {
        'target_width_mm': None,
        'target_height_mm': 100.0,
        'max_width_mm': 170.0,
        'max_height_mm': 220.0,
        'use_image_dpi': True,
    },
    # 可为特定图片配置不同尺寸
    'img1': {
        'target_width_mm': None,
        'target_height_mm': 110.0,
        'max_width_mm': 170.0,
        'max_height_mm': 220.0,
        'use_image_dpi': True,
    },
    'img2': {
        'target_width_mm': None,
        'target_height_mm': 100.0,
        'max_width_mm': 170.0,
        'max_height_mm': 230.0,
        'use_image_dpi': True,
    },
    'img10': {
        'target_width_mm': 150.0,
        'target_height_mm': None,
        'max_width_mm': 170.0,
        'max_height_mm': 220.0,
        'use_image_dpi': True,
    },
    'img11': {
        'target_width_mm': 150.0,
        'target_height_mm': None,
        'max_width_mm': 170.0,
        'max_height_mm': 220.0,
        'use_image_dpi': True,
    },
    'img12': {
        'target_width_mm': 150.0,
        'target_height_mm': None,
        'max_width_mm': 170.0,
        'max_height_mm': 220.0,
        'use_image_dpi': True,
    },
    # 其他图片使用默认配置
}


def _get_image_size_config(img_key: str) -> dict:
    """
    获取指定图片的尺寸配置。

    参数:
        img_key: 图片键名（如 'img1', 'img2' 等）

    返回:
        尺寸配置字典
    """
    return IMAGE_SIZE_CONFIG.get(img_key, IMAGE_SIZE_CONFIG['default'])


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

    图片尺寸处理：
        - 自动读取图片实际像素尺寸和 DPI 信息
        - 根据配置计算合适的显示尺寸（毫米）
        - 同时设置宽度和高度，确保与手动插入效果一致

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
        record = (ReportTaskRecord.objects.filter(task_id=task_id)
                  .order_by('-id')
                  .first())
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
        current_time = f'{now.year}年{now.month}月{now.day}日'

        # ---- 准备地震基本信息 ----
        # 格式：北京时间2026年3月15日14时30分，XX省XX市发生X.X级地震，震中位于北纬XX.XX度，东经XX.XX度，震源深度约XXkm
        if task.ori_time:
            t = task.ori_time
            ori_time_str = f'{t.year}年{t.month}月{t.day}日{t.hour}时{t.minute}分'
        else:
            ori_time_str = ''
        base_info = (
            f"{ori_time_str}，在{task.address}（北纬{float(task.latitude):.2f}度，东经{float(task.longitude):.2f}度）"
            f"发生{task.magnitude}级地震，"
            f"震源深度{int(task.foc_depth)}千米"
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
            'img11_info': record.img11_info or '',
            'img12_info': record.img12_info or '',
        }

        # ---- 准备图片对象（使用自动尺寸计算） ----

        # 图片一：历史地震分布图
        img1_path = record.img1_path
        if img1_path and os.path.exists(img1_path):
            config = _get_image_size_config('img1')
            context['img1'] = _create_inline_image(doc, img1_path, **config)
            logger.info('[任务 %s] 已准备图一: %s', task_id, img1_path)
        else:
            context['img1'] = ''
            logger.warning('[任务 %s] 图一不存在: %s', task_id, img1_path)

        # 图片二：烈度分布图
        img2_path = record.img2_path
        if img2_path and os.path.exists(img2_path):
            config = _get_image_size_config('img2')
            context['img2'] = _create_inline_image(doc, img2_path, **config)
            logger.info('[任务 %s] 已准备图二: %s', task_id, img2_path)
        else:
            context['img2'] = ''
            logger.warning('[任务 %s] 图二不存在: %s', task_id, img2_path)

        # 图片三：地质构造图
        img3_path = record.img3_path
        if img3_path and os.path.exists(img3_path):
            config = _get_image_size_config('img3')
            context['img3'] = _create_inline_image(doc, img3_path, **config)
            logger.info('[任务 %s] 已准备图三: %s', task_id, img3_path)
        else:
            context['img3'] = ''
            logger.warning('[任务 %s] 图三不存在: %s', task_id, img3_path)

        # 图片四：数字高程图
        img4_path = record.img4_path
        if img4_path and os.path.exists(img4_path):
            config = _get_image_size_config('img4')
            context['img4'] = _create_inline_image(doc, img4_path, **config)
            logger.info('[任务 %s] 已准备图四: %s', task_id, img4_path)
        else:
            context['img4'] = ''
            logger.warning('[任务 %s] 图四不存在: %s', task_id, img4_path)

        # 图片五：土地利用类型图
        img5_path = record.img5_path
        if img5_path and os.path.exists(img5_path):
            config = _get_image_size_config('img5')
            context['img5'] = _create_inline_image(doc, img5_path, **config)
            logger.info('[任务 %s] 已准备图五: %s', task_id, img5_path)
        else:
            context['img5'] = ''
            logger.warning('[任务 %s] 图五不存在: %s', task_id, img5_path)

        # 图片六：人口分布图
        img6_path = record.img6_path
        if img6_path and os.path.exists(img6_path):
            config = _get_image_size_config('img6')
            context['img6'] = _create_inline_image(doc, img6_path, **config)
            logger.info('[任务 %s] 已准备图六: %s', task_id, img6_path)
        else:
            context['img6'] = ''
            logger.warning('[任务 %s] 图六不存在: %s', task_id, img6_path)

        # 图片七：GDP网格图
        img7_path = record.img7_path
        if img7_path and os.path.exists(img7_path):
            config = _get_image_size_config('img7')
            context['img7'] = _create_inline_image(doc, img7_path, **config)
            logger.info('[任务 %s] 已准备图七: %s', task_id, img7_path)
        else:
            context['img7'] = ''
            logger.warning('[任务 %s] 图七不存在: %s', task_id, img7_path)

        # 图片八：道路交通图
        img8_path = record.img8_path
        if img8_path and os.path.exists(img8_path):
            config = _get_image_size_config('img8')
            context['img8'] = _create_inline_image(doc, img8_path, **config)
            logger.info('[任务 %s] 已准备图八: %s', task_id, img8_path)
        else:
            context['img8'] = ''
            logger.warning('[任务 %s] 图八不存在: %s', task_id, img8_path)

        # 图片九：滑坡分布图
        img9_path = record.img9_path
        if img9_path and os.path.exists(img9_path):
            config = _get_image_size_config('img9')
            context['img9'] = _create_inline_image(doc, img9_path, **config)
            logger.info('[任务 %s] 已准备图九: %s', task_id, img9_path)
        else:
            context['img9'] = ''
            logger.warning('[任务 %s] 图九不存在: %s', task_id, img9_path)

        # 图片十：newmark
        img10_path = record.img10_path
        if img10_path and os.path.exists(img10_path):
            config = _get_image_size_config('img10')
            context['img10'] = _create_inline_image(doc, img10_path, **config)
            logger.info('[任务 %s] 已准备图十: %s', task_id, img10_path)
        else:
            context['img10'] = ''
            logger.warning('[任务 %s] 图十不存在: %s', task_id, img10_path)

        # 图片十一：地震危险性图
        img11_path = record.img11_path
        if img11_path and os.path.exists(img11_path):
            config = _get_image_size_config('img11')
            context['img11'] = _create_inline_image(doc, img11_path, **config)
            logger.info('[任务 %s] 已准备图十一: %s', task_id, img11_path)
        else:
            context['img11'] = ''
            logger.warning('[任务 %s] 图十一不存在: %s', task_id, img11_path)

        # 图片十二：地震滑坡评估图
        img12_path = record.img12_path
        if img12_path and os.path.exists(img12_path):
            config = _get_image_size_config('img12')
            context['img12'] = _create_inline_image(doc, img12_path, **config)
            logger.info('[任务 %s] 已准备图十二: %s', task_id, img12_path)
        else:
            context['img12'] = ''
            logger.warning('[任务 %s] 图十二不存在: %s', task_id, img12_path)

        # ---- 渲染模板 ----
        doc.render(context)
        logger.info('[任务 %s] 模板渲染完成', task_id)

        # ---- 保存文档 ----
        # 文档名称 第X期 XXX地XX级地震滑坡危险性评估报告
        doc_name = f'第{task_id}期 {task.address}{task.magnitude}级地震滑坡危险性评估报告.docx'
        doc_path = os.path.join(output_dir, doc_name)
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
    备用方案：使用 python-docx 直接操作 Word 文档。

    当 docxtpl 不可用或模板不存在时调用此函数。
    """
    pass


# ============================================================
# 异步启动入口
# ============================================================

def start_task_async(task_id: int) -> None:
    """
    将报告任务提交到全局线程池异步执行。

    参数:
        task_id: 任务ID
    """
    future = _TASK_EXECUTOR.submit(execute_report_task, task_id)
    logger.info('[任务 %s] 已提交到全局线程池', task_id)
    future.add_done_callback(
        lambda f: logger.info('[任务 %s] 线程池执行完毕', task_id)
    )

def format_area(num):
    if num > 1:
        return f"{num:.0f}"
    else:
        return f"{num:.2f}"
