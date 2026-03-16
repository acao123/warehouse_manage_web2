# -*- coding: utf-8 -*-
"""
报告任务异步执行模块

包含 execute_report_task() 主执行函数，依次调用各地图生成器，
生成 10 张图片并将结果保存到 report_task_record 表。

进度更新：通过 update_progress() 辅助函数更新 report_task 表的状态，
后续可扩展为 WebSocket 推送或 Redis 缓存，以支持前端进度条。
"""

import logging
import os
import random
import threading
from datetime import datetime

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

def _gen_img1(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图一生成完成: %s', task.id, out)
        return out, str(info) if info else None
    except Exception as exc:
        logger.error('[任务 %s] 图一生成失败: %s', task.id, exc, exc_info=True)
        return None, None


def _gen_img2(task: ReportTask, output_dir: str):
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


def _gen_img3(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图三生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图三生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img4(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图四生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图四生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img5(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图五生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图五生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img6(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图六生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图六生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img7(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图七生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图七生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img8(task: ReportTask, output_dir: str):
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
        )
        logger.info('[任务 %s] 图八生成完成: %s', task.id, out)
        return out
    except Exception as exc:
        logger.error('[任务 %s] 图八生成失败: %s', task.id, exc, exc_info=True)
        return None


def _gen_img9(task: ReportTask, output_dir: str):
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
        2. 依次生成图一 ~ 图九
        3. 生成 Ia.tif → Dn.tif → 图十
        4. 将结果保存到 report_task_record 表
        5. 更新 report_task 状态为成功（失败时标记为失败）

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

    try:
        # ---- 图一 ----
        update_progress(task_id, '生成图一（历史地震分布图）', PROGRESS_STEPS['img1'])
        img1_path, img1_info = _gen_img1(task, output_dir)
        record_kwargs['img1_path'] = img1_path
        record_kwargs['img1_info'] = img1_info

        # ---- 图二 ----
        update_progress(task_id, '生成图二（烈度分布图）', PROGRESS_STEPS['img2'])
        img2_path, img2_info = _gen_img2(task, output_dir)
        record_kwargs['img2_path'] = img2_path
        record_kwargs['img2_info'] = img2_info

        # ---- 图三 ----
        update_progress(task_id, '生成图三（地质构造图）', PROGRESS_STEPS['img3'])
        record_kwargs['img3_path'] = _gen_img3(task, output_dir)

        # ---- 图四 ----
        update_progress(task_id, '生成图四（数字高程图）', PROGRESS_STEPS['img4'])
        record_kwargs['img4_path'] = _gen_img4(task, output_dir)

        # ---- 图五 ----
        update_progress(task_id, '生成图五（土地利用类型图）', PROGRESS_STEPS['img5'])
        record_kwargs['img5_path'] = _gen_img5(task, output_dir)

        # ---- 图六 ----
        update_progress(task_id, '生成图六（人口分布图）', PROGRESS_STEPS['img6'])
        record_kwargs['img6_path'] = _gen_img6(task, output_dir)

        # ---- 图七 ----
        update_progress(task_id, '生成图七（GDP网格图）', PROGRESS_STEPS['img7'])
        record_kwargs['img7_path'] = _gen_img7(task, output_dir)

        # ---- 图八 ----
        update_progress(task_id, '生成图八（道路交通图）', PROGRESS_STEPS['img8'])
        record_kwargs['img8_path'] = _gen_img8(task, output_dir)

        # ---- 图九 ----
        update_progress(task_id, '生成图九（滑坡斜坡分布图）', PROGRESS_STEPS['img9'])
        img9_path, img9_info = _gen_img9(task, output_dir)
        record_kwargs['img9_path'] = img9_path
        record_kwargs['img9_info'] = img9_info

        # ---- Ia.tif ----
        update_progress(task_id, '生成 Ia.tif', PROGRESS_STEPS['ia_tif'])
        ia_tif_path = _gen_ia_tif(task, output_dir)

        # ---- Dn.tif ----
        update_progress(task_id, '生成 Dn.tif', PROGRESS_STEPS['dn_tif'])
        dn_tif_path = None
        if ia_tif_path:
            dn_tif_path = _gen_dn_tif(task, output_dir, ia_tif_path)
        else:
            logger.warning('[任务 %s] Ia.tif 未生成，跳过 Dn.tif 及图十', task_id)

        # ---- 图十 ----
        update_progress(task_id, '生成图十（Newmark位移图）', PROGRESS_STEPS['img10'])
        record_kwargs['img10_path'] = _gen_img10(task, output_dir, dn_tif_path)

        # ---- 保存记录 ----
        update_progress(task_id, '保存记录到数据库', PROGRESS_STEPS['save'])
        ReportTaskRecord.objects.create(**record_kwargs)
        logger.info('[任务 %s] 已保存 report_task_record', task_id)

        # ---- 标记成功 ----
        task.task_status = ReportTask.STATUS_SUCCESS
        task.success_time = datetime.now()
        task.save(update_fields=['task_status', 'success_time', 'updated_at'])
        update_progress(task_id, '任务完成', PROGRESS_STEPS['done'])
        logger.info('[任务 %s] 报告任务执行成功', task_id)

    except Exception as exc:
        logger.error('[任务 %s] 报告任务执行过程中发生未捕获异常: %s', task_id, exc, exc_info=True)
        _mark_failed(task)


def _mark_failed(task: ReportTask) -> None:
    """将任务状态标记为失败。"""
    try:
        task.task_status = ReportTask.STATUS_FAILED
        task.save(update_fields=['task_status', 'updated_at'])
        logger.info('[任务 %s] 已标记为失败', task.id)
    except Exception as exc:
        logger.error('[任务 %s] 标记失败状态时出错: %s', task.id, exc, exc_info=True)


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
