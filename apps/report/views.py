"""
报告管理模块视图
包含：执行报告、我的报告、所有报告三个子模块
"""
import json
import logging
import os
from datetime import datetime

from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import ReportTask, ReportTaskRecord

# 获取日志记录器
logger = logging.getLogger('report')

# ============================================================
# 页面渲染视图
# ============================================================


def execute_report_page_view(request):
    """
    执行报告页面视图
    :param request: HTTP 请求对象
    :return: 渲染后的 HTML 页面
    """
    return render(request, 'report/execute_report.html')


def my_report_page_view(request):
    """
    我的报告页面视图
    :param request: HTTP 请求对象
    :return: 渲染后的 HTML 页面
    """
    return render(request, 'report/my_report.html')


def all_report_page_view(request):
    """
    所有报告页面视图
    :param request: HTTP 请求对象
    :return: 渲染后的 HTML 页面
    """
    return render(request, 'report/all_report.html')


def report_record_page_view(request):
    """
    报告信息记录页面视图
    """
    return render(request, 'report/report_record.html')


# ============================================================
# 执行报告相关接口
# ============================================================


@csrf_exempt
def create_task_view(request):
    """
    创建报告任务接口
    接收 multipart/form-data 数据（包含文件上传）
    :param request: HTTP 请求对象
    :return: JSON 格式响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})

    # 获取当前登录用户 ID
    user_id = request.session.get('user_id')
    if not user_id:
        logger.warning('创建任务失败：用户未登录')
        return JsonResponse({'code': 1, 'msg': '用户未登录，请重新登录'})

    # 检查当前用户是否有正在执行的任务
    running_task = ReportTask.objects.filter(
        user_id=user_id, task_status=ReportTask.STATUS_RUNNING
    ).first()
    if running_task:
        logger.info('用户 %s 已有正在执行的任务 id=%s，禁止重复创建', user_id, running_task.id)
        return JsonResponse({'code': 1, 'msg': '您有正在执行的任务，请点击结束后再创建'})

    # ---- 读取表单基本信息字段 ----
    longitude_str = request.POST.get('longitude', '').strip()
    latitude_str = request.POST.get('latitude', '').strip()
    magnitude_str = request.POST.get('magnitude', '').strip()
    foc_depth_str = request.POST.get('foc_depth', '').strip()
    cache_base_map_str = request.POST.get('cache_base_map', '0').strip()
    address = request.POST.get('address', '').strip()
    ori_time_str = request.POST.get('ori_time', '').strip()

    # ---- 读取插值算法相关字段 ----
    interp_method = request.POST.get('interp_method', 'scipy_idw').strip() or 'scipy_idw'
    sample_interval_str = request.POST.get('sample_interval', '').strip()
    max_sample_points_str = request.POST.get('max_sample_points', '').strip()

    # ---- 读取上传文件 ----
    history_file = request.FILES.get('history_record')
    intensity_file = request.FILES.get('intensity_kml')
    pga_file = request.FILES.get('pga_kml')

    # ---- 服务端二次校验 ----
    # 校验经度
    longitude = None
    if longitude_str:
        try:
            longitude = float(longitude_str)
            if longitude < 0:
                return JsonResponse({'code': 1, 'msg': '经度不能为负数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '经度格式错误，请输入整数或小数'})

    # 校验纬度
    latitude = None
    if latitude_str:
        try:
            latitude = float(latitude_str)
            if latitude < 0:
                return JsonResponse({'code': 1, 'msg': '纬度不能为负数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '纬度格式错误，请输入整数或小数'})

    # 校验震级
    magnitude = None
    if magnitude_str:
        try:
            magnitude = float(magnitude_str)
            if magnitude < 0:
                return JsonResponse({'code': 1, 'msg': '震级不能为负数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '震级格式错误，请输入整数或小数'})

    # 校验震源深度
    foc_depth = None
    if foc_depth_str:
        try:
            foc_depth = float(foc_depth_str)
            if foc_depth < 0:
                return JsonResponse({'code': 1, 'msg': '震源深度不能为负数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '震源深度格式错误，请输入整数或小数'})

    # 解析天地图缓存选项
    try:
        cache_base_map = int(cache_base_map_str)
        if cache_base_map not in (0, 1):
            cache_base_map = 0
    except (ValueError, TypeError):
        cache_base_map = 0

    # 校验等值线采样间隔
    sample_interval = 1
    if interp_method in ('scipy_idw', 'qgis_idw', 'kriging'):
        sample_interval = 5
    if sample_interval_str:
        try:
            sample_interval = int(sample_interval_str)
            if sample_interval <= 0:
                return JsonResponse({'code': 1, 'msg': '等值线采样间隔必须为正整数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '等值线采样间隔格式错误，请输入正整数'})

    # 校验最大采样点数
    max_sample_points = 1000
    if max_sample_points_str:
        try:
            max_sample_points = int(max_sample_points_str)
            if max_sample_points <= 0:
                return JsonResponse({'code': 1, 'msg': '最大采样点数必须为正整数'})
        except ValueError:
            return JsonResponse({'code': 1, 'msg': '最大采样点数格式错误，请输入正整数'})

    # 校验文件是否上传
    if not history_file:
        return JsonResponse({'code': 1, 'msg': '请上传历史地震CSV文件'})
    if not intensity_file:
        return JsonResponse({'code': 1, 'msg': '请上传烈度KML文件'})
    if not pga_file:
        return JsonResponse({'code': 1, 'msg': '请上传PGA KML文件'})

    # 校验文件后缀
    if not history_file.name.lower().endswith('.csv'):
        return JsonResponse({'code': 1, 'msg': '历史地震文件后缀必须为 .csv'})
    if not intensity_file.name.lower().endswith('.kml'):
        return JsonResponse({'code': 1, 'msg': '烈度KML文件后缀必须为 .kml'})
    if not pga_file.name.lower().endswith('.kml'):
        return JsonResponse({'code': 1, 'msg': 'PGA KML文件后缀必须为 .kml'})

    # 校验文件大小（不超过 1GB）
    max_size = 1 * 1024 * 1024 * 1024  # 1GB
    if history_file.size > max_size:
        return JsonResponse({'code': 1, 'msg': '历史地震CSV文件大小不能超过1G'})
    if intensity_file.size > max_size:
        return JsonResponse({'code': 1, 'msg': '烈度KML文件大小不能超过1G'})
    if pga_file.size > max_size:
        return JsonResponse({'code': 1, 'msg': 'PGA KML文件大小不能超过1G'})

    # ---- 当基本信息有任何一项为空时，从爬虫获取 ----
    if longitude is None or latitude is None or magnitude is None or not address or not ori_time_str or foc_depth is None:
        logger.info('基本信息不完整，尝试通过爬虫获取地震数据')
        try:
            from spider.earthquake_fetcher import EarthquakeFetcher
            fetcher = EarthquakeFetcher()
            earthquake_info = fetcher.fetch_first()
            if earthquake_info:
                longitude = earthquake_info.epi_lon
                latitude = earthquake_info.epi_lat
                magnitude = earthquake_info.magnitude
                address = earthquake_info.loc_name
                ori_time_str = earthquake_info.ori_time.strftime('%Y-%m-%d %H:%M:%S')
                foc_depth = earthquake_info.foc_depth
                logger.info('通过爬虫获取地震数据成功: %s', earthquake_info)
            else:
                logger.warning('爬虫未返回地震数据')
                return JsonResponse({'code': 1, 'msg': '爬虫未返回地震数据'})
        except Exception as e:
            logger.error('爬虫获取地震数据失败: %s', e, exc_info=True)
            return JsonResponse({'code': 1, 'msg': '爬取信息失败!'})

    # 填充默认值（爬虫也没拿到的字段）
    if longitude is None or latitude is None or magnitude is None or not address or not ori_time_str or foc_depth is None:
        return JsonResponse({'code': 1, 'msg': '基本信息不全!'})

    # 解析发震时刻
    ori_time = None
    if ori_time_str:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M'):
            try:
                ori_time = datetime.strptime(ori_time_str, fmt)
                break
            except ValueError:
                continue
    if not ori_time:
        return JsonResponse({'code': 1, 'msg': '发震时刻不能为空!'})

    # 校验插值算法取值
    valid_methods = [m[0] for m in ReportTask.INTERP_METHOD_CHOICES]
    if interp_method not in valid_methods:
        interp_method = 'scipy_tin'

    # ---- 保存上传文件 ----
    try:
        upload_dir = _get_upload_dir()
        os.makedirs(upload_dir, exist_ok=True)

        history_record_path = _save_file(history_file, upload_dir)
        intensity_kml_path = _save_file(intensity_file, upload_dir)
        pga_kml_path = _save_file(pga_file, upload_dir)
        logger.info(
            '文件保存成功: history=%s, intensity=%s, pga=%s',
            history_record_path, intensity_kml_path, pga_kml_path
        )
    except Exception as e:
        logger.error('文件保存失败: %s', e, exc_info=True)
        return JsonResponse({'code': 1, 'msg': f'文件保存失败: {str(e)}'})

    # ---- 创建数据库记录 ----
    try:
        task = ReportTask.objects.create(
            user_id=user_id,
            longitude=longitude,
            latitude=latitude,
            magnitude=magnitude,
            foc_depth=foc_depth,
            cache_base_map=cache_base_map,
            address=address,
            ori_time=ori_time,
            interp_method=interp_method,
            sample_interval=sample_interval,
            max_sample_points=max_sample_points,
            history_record_path=history_record_path,
            intensity_kml_path=intensity_kml_path,
            pga_kml_path=pga_kml_path,
            task_status=ReportTask.STATUS_CREATED,
        )
        logger.info('报告任务创建成功，task_id=%s, user_id=%s', task.id, user_id)
    except Exception as e:
        logger.error('报告任务入库失败: %s', e, exc_info=True)
        return JsonResponse({'code': 1, 'msg': f'任务创建失败: {str(e)}'})

    return JsonResponse({'code': 0, 'msg': '任务创建成功', 'task_id': task.id})


@require_GET
def running_tasks_view(request):
    """
    查询当前用户正在执行的任务列表（task_status 为 0/1/4）
    :param request: HTTP 请求对象
    :return: JSON 格式的任务列表
    """
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))

    status_list = [
        ReportTask.STATUS_CREATED,
        ReportTask.STATUS_RUNNING,
        ReportTask.STATUS_CANCELLING,
    ]
    queryset = ReportTask.objects.filter(
        user_id=user_id
    ).order_by('-updated_at')

    total = queryset.count()
    start = (page - 1) * limit
    end = start + limit
    task_list = queryset[start:end]

    data = [_task_to_dict(t) for t in task_list]
    logger.debug('查询用户 %s 正在执行的任务，共 %s 条', user_id, total)

    return JsonResponse({'code': 0, 'msg': '成功', 'count': total, 'data': data})


@csrf_exempt
def stop_task_view(request):
    """
    结束任务接口（将 task_status 更新为 STATUS_CANCELLING=4，后台任务检测到后自行终止）
    :param request: HTTP 请求对象
    :return: JSON 格式响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})

    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    task_id = request.POST.get('task_id', '').strip()
    if not task_id:
        return JsonResponse({'code': 1, 'msg': '缺少任务ID'})

    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '任务不存在'})

    if task.task_status not in (ReportTask.STATUS_RUNNING, ReportTask.STATUS_CANCELLING):
        return JsonResponse({'code': 1, 'msg': '只能结束正在执行或取消中的任务'})

    task.task_status = ReportTask.STATUS_CANCELLING
    task.save(update_fields=['task_status', 'updated_at'])
    logger.info('任务 %s 被用户 %s 请求取消（状态设为取消中）', task_id, user_id)

    return JsonResponse({'code': 0, 'msg': '任务取消中，请稍后查看状态'})


@csrf_exempt
def execute_task_view(request):
    """
    执行任务接口：原子重置任务进度字段，然后在后台线程池中执行任务
    :param request: HTTP 请求对象
    :return: JSON 格式响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})

    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    task_id = request.POST.get('task_id', '').strip()
    if not task_id:
        return JsonResponse({'code': 1, 'msg': '缺少任务ID'})

    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '任务不存在'})

    # 1. 检查目标任务本身是否已在执行中
    if task.task_status == ReportTask.STATUS_RUNNING:
        return JsonResponse({'code': 1, 'msg': '该任务正在执行，请稍后'})

    # 2. 检查该用户是否已有其他任务在执行中或取消中
    busy_task = ReportTask.objects.filter(
        user_id=user_id,
        task_status__in=(ReportTask.STATUS_RUNNING, ReportTask.STATUS_CANCELLING),
    ).exclude(id=task_id).first()
    if busy_task:
        logger.info('用户 %s 已有任务 id=%s 正在执行，禁止重复执行', user_id, busy_task.id)
        return JsonResponse({'code': 1, 'msg': '您有任务正在执行，请稍后执行'})

    # 3. 原子更新：状态置为执行中，进度/消息重置
    task.task_status = ReportTask.STATUS_RUNNING
    task.progress = 0
    task.message = None
    task.error_message = None
    task.save(update_fields=['task_status', 'progress', 'message', 'error_message', 'updated_at'])
    logger.info('任务 %s 状态更新为执行中，由用户 %s 触发', task_id, user_id)

    # 4. 提交到线程池异步执行
    from .tasks import start_task_async
    start_task_async(int(task_id))

    return JsonResponse({'code': 0, 'msg': '任务执行中，请从进度条查看进度'})


@require_GET
def download_report_view(request):
    """
    下载报告文档接口：根据 task_id 查询 report_task_record 中的 report_path 并返回文件
    :param request: HTTP 请求对象
    :return: 文件响应或 JSON 错误信息
    """
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    task_id = request.GET.get('task_id', '').strip()
    if not task_id:
        return JsonResponse({'code': 1, 'msg': '缺少任务ID'})

    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '任务不存在'})

    if task.task_status == ReportTask.STATUS_RUNNING:
        return JsonResponse({'code': 1, 'msg': '任务正在执行，请稍后'})

    if task.task_status != ReportTask.STATUS_SUCCESS:
        return JsonResponse({'code': 1, 'msg': '报告尚未生成，请先执行任务'})

    record = ReportTaskRecord.objects.filter(task_id=task_id).first()
    if not record or not record.report_path:
        return JsonResponse({'code': 1, 'msg': '报告文件不存在'})

    if not os.path.exists(record.report_path):
        return JsonResponse({'code': 1, 'msg': '报告文件不存在'})

    file_handle = open(record.report_path, 'rb')
    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=os.path.basename(record.report_path),
    )
    logger.info('任务 %s 的报告文档被用户 %s 下载', task_id, user_id)
    return response


@require_GET
def task_progress_view(request):
    """
    查询指定任务的进度信息接口（GET）

    参数（GET）:
        task_id: 任务ID

    返回:
        {
          "code": 0,
          "data": {
            "task_id": 1,
            "task_status": 1,
            "progress": 5,
            "message": "img1已完成，img2已完成，",
            "error_message": ""
          }
        }
    """
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    task_id = request.GET.get('task_id', '').strip()
    if not task_id:
        return JsonResponse({'code': 1, 'msg': '缺少任务ID'})

    try:
        task = ReportTask.objects.get(id=task_id)
    except ReportTask.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '任务不存在'})

    return JsonResponse({
        'code': 0,
        'data': {
            'task_id': task.id,
            'task_status': task.task_status,
            'progress': task.progress,
            'message': task.message or '',
            'error_message': task.error_message or '',
        }
    })


# ============================================================
# 我的报告相关接口
# ============================================================


@require_GET
def my_report_list_view(request):
    """
    查询当前用户的所有任务列表
    支持通过参考位置、创建时间检索
    :param request: HTTP 请求对象
    :return: JSON 格式的任务列表
    """
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'code': 1, 'msg': '用户未登录'})

    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))
    address_keyword = request.GET.get('address', '').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    queryset = ReportTask.objects.filter(user_id=user_id)
    queryset = _apply_common_filters(queryset, address_keyword, start_date, end_date)
    queryset = queryset.order_by('-created_at')

    total = queryset.count()
    start = (page - 1) * limit
    end = start + limit
    task_list = queryset[start:end]

    data = [_task_to_dict(t) for t in task_list]
    logger.debug('查询用户 %s 的任务列表，共 %s 条', user_id, total)

    return JsonResponse({'code': 0, 'msg': '成功', 'count': total, 'data': data})


# ============================================================
# 所有报告相关接口
# ============================================================


@require_GET
def all_report_list_view(request):
    """
    查询所有用户的任务列表
    支持通过参考位置、创建时间检索
    :param request: HTTP 请求对象
    :return: JSON 格式的任务列表
    """
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))
    address_keyword = request.GET.get('address', '').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    queryset = ReportTask.objects.all()
    queryset = _apply_common_filters(queryset, address_keyword, start_date, end_date)
    queryset = queryset.order_by('-created_at')

    total = queryset.count()
    start = (page - 1) * limit
    end = start + limit
    task_list = queryset[start:end]

    data = [_task_to_dict(t) for t in task_list]
    logger.debug('查询所有任务列表，共 %s 条', total)

    return JsonResponse({'code': 0, 'msg': '成功', 'count': total, 'data': data})


@csrf_exempt
def delete_tasks_view(request):
    """
    批量删除任务接口（正在执行的任务不允许删除）
    :param request: HTTP 请求对象，Body 中包含 task_ids（JSON 数组）
    :return: JSON 格式响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})

    try:
        body = json.loads(request.body)
        task_ids = body.get('task_ids', [])
    except (json.JSONDecodeError, AttributeError):
        task_ids = request.POST.getlist('task_ids')

    if not task_ids:
        return JsonResponse({'code': 1, 'msg': '请选择要删除的任务'})

    # 检查是否有正在执行的任务
    running_count = ReportTask.objects.filter(
        id__in=task_ids, task_status=ReportTask.STATUS_RUNNING
    ).count()
    if running_count > 0:
        return JsonResponse({'code': 1, 'msg': '选中的任务中包含正在执行的任务，不允许删除'})

    deleted_count, _ = ReportTask.objects.filter(id__in=task_ids).delete()
    logger.info('批量删除任务成功，共删除 %s 条，ids=%s', deleted_count, task_ids)

    return JsonResponse({'code': 0, 'msg': f'成功删除 {deleted_count} 条任务'})


# ============================================================
# 内部辅助函数
# ============================================================


def _get_upload_dir() -> str:
    """
    生成文件上传目录路径
    格式：FILE_BASE_PATH / 当前时间（%Y%m%d%H%M%S）+ UUID 后缀
    :return: 上传目录的绝对路径字符串
    """
    import uuid
    base_path = getattr(settings, 'FILE_BASE_PATH', os.path.join(settings.BASE_DIR, 'data', 'report'))
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    unique_suffix = uuid.uuid4().hex[:8]
    return os.path.join(base_path, f'{timestamp}{unique_suffix}')


def _save_file(uploaded_file, upload_dir: str) -> str:
    """
    将上传文件保存到目标目录
    :param uploaded_file: Django InMemoryUploadedFile 或 TemporaryUploadedFile 对象
    :param upload_dir: 目标目录路径
    :return: 保存后的文件路径字符串
    """
    file_path = os.path.join(upload_dir, uploaded_file.name)
    file_path = file_path.replace("\\", "/")
    with open(file_path, 'wb+') as dest:
        for chunk in uploaded_file.chunks():
            dest.write(chunk)
    return file_path


def _apply_common_filters(queryset, address_keyword: str, start_date: str, end_date: str):
    """
    应用通用检索过滤条件（参考位置关键词、创建时间范围）
    :param queryset: Django QuerySet 对象
    :param address_keyword: 参考位置关键词（模糊匹配）
    :param start_date: 创建时间起始日期，格式 YYYY-MM-DD
    :param end_date: 创建时间结束日期，格式 YYYY-MM-DD
    :return: 过滤后的 QuerySet
    """
    if address_keyword:
        queryset = queryset.filter(address__icontains=address_keyword)
    if start_date:
        try:
            queryset = queryset.filter(
                created_at__gte=datetime.strptime(start_date, '%Y-%m-%d')
            )
        except ValueError:
            pass
    if end_date:
        try:
            # 包含结束日期当天
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59
            )
            queryset = queryset.filter(created_at__lte=end_dt)
        except ValueError:
            pass
    return queryset


def _task_to_dict(task: ReportTask) -> dict:
    """
    将 ReportTask 对象转换为字典（用于 JSON 序列化）
    :param task: ReportTask 模型实例
    :return: 字典格式的任务数据
    """
    return {
        'id': task.id,
        'user_id': task.user_id,
        'longitude': str(task.longitude),
        'latitude': str(task.latitude),
        'magnitude': task.magnitude,
        'foc_depth': task.foc_depth,
        'cache_base_map': task.cache_base_map,
        'address': task.address,
        'ori_time': task.ori_time.strftime('%Y-%m-%d %H:%M:%S') if task.ori_time else '',
        'interp_method': task.interp_method,
        'sample_interval': task.sample_interval,
        'max_sample_points': task.max_sample_points,
        'history_record_path': task.history_record_path,
        'intensity_kml_path': task.intensity_kml_path,
        'pga_kml_path': task.pga_kml_path,
        'task_status': task.task_status,
        'task_status_label': task.get_task_status_display(),
        'progress': task.progress,
        'message': task.message or '',
        'error_message': task.error_message or '',
        'success_time': task.success_time.strftime('%Y-%m-%d %H:%M:%S') if task.success_time else '',
        'created_at': task.created_at.strftime('%Y-%m-%d %H:%M:%S') if task.created_at else '',
        'updated_at': task.updated_at.strftime('%Y-%m-%d %H:%M:%S') if task.updated_at else '',
    }


def _record_to_dict(record: ReportTaskRecord) -> dict:
    """
    将 ReportTaskRecord 对象转换为字典（用于 JSON 序列化）
    """
    return {
        'id': record.id,
        'user_id': record.user_id,
        'task_id': record.task_id,
        'img1_info': record.img1_info or '',
        'img1_path': record.img1_path or '',
        'img2_info': record.img2_info or '',
        'img2_path': record.img2_path or '',
        'img3_path': record.img3_path or '',
        'img4_path': record.img4_path or '',
        'img5_path': record.img5_path or '',
        'img6_path': record.img6_path or '',
        'img7_path': record.img7_path or '',
        'img8_path': record.img8_path or '',
        'img9_info': record.img9_info or '',
        'img9_path': record.img9_path or '',
        'img10_path': record.img10_path or '',
        'ia_tif_path': getattr(record, 'ia_tif_path', None) or '',
        'dn_tif_path': getattr(record, 'dn_tif_path', None) or '',
        'img11_info': record.img11_info or '',
        'img11_path': record.img11_path or '',
        'img12_info': record.img12_info or '',
        'img12_path': record.img12_path or '',
        'report_path': record.report_path or '',
        'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
        'updated_at': record.updated_at.strftime('%Y-%m-%d %H:%M:%S') if record.updated_at else '',
    }

# ============================================================
# 报告信息记录相关接口
# ============================================================


@require_GET
def report_record_list_view(request):
    """
    查询 report_task_record 表数据
    支持通过创建时间范围检索
    """
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    queryset = ReportTaskRecord.objects.all().order_by('-updated_at')

    if start_date:
        try:
            queryset = queryset.filter(
                created_at__gte=datetime.strptime(start_date, '%Y-%m-%d')
            )
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59
            )
            queryset = queryset.filter(created_at__lte=end_dt)
        except ValueError:
            pass

    total = queryset.count()
    start = (page - 1) * limit
    end = start + limit
    records = queryset[start:end]

    data = [_record_to_dict(r) for r in records]
    return JsonResponse({'code': 0, 'msg': '成功', 'count': total, 'data': data})


@require_GET
def preview_image_view(request):
    """
    图片预览接口：返回图片文件内容
    """
    img_path = request.GET.get('path', '').strip()
    if not img_path:
        return JsonResponse({'code': 1, 'msg': '缺少图片路径'})

    # 防止路径穿越攻击：解析真实路径后校验是否在允许的目录内
    real_path = os.path.realpath(img_path)
    allowed_base = os.path.realpath(
        getattr(settings, 'FILE_BASE_PATH', os.path.join(settings.BASE_DIR, 'data', 'report'))
    )
    if not real_path.startswith(allowed_base + os.sep) and real_path != allowed_base:
        return JsonResponse({'code': 1, 'msg': '无权访问该文件'})

    if not os.path.exists(real_path):
        return JsonResponse({'code': 1, 'msg': '图片文件不存在'})

    import mimetypes
    mime_type, _ = mimetypes.guess_type(real_path)
    if not mime_type or not mime_type.startswith('image/'):
        mime_type = 'image/png'

    file_handle = open(real_path, 'rb')
    response = FileResponse(file_handle, content_type=mime_type)
    return response


@require_GET
def download_tif_view(request):
    """
    栅格文件下载接口
    """
    tif_path = request.GET.get('path', '').strip()
    if not tif_path:
        return JsonResponse({'code': 1, 'msg': '缺少文件路径'})

    # 防止路径穿越攻击：解析真实路径后校验是否在允许的目录内
    real_path = os.path.realpath(tif_path)
    allowed_base = os.path.realpath(
        getattr(settings, 'FILE_BASE_PATH', os.path.join(settings.BASE_DIR, 'data', 'report'))
    )
    if not real_path.startswith(allowed_base + os.sep) and real_path != allowed_base:
        return JsonResponse({'code': 1, 'msg': '无权访问该文件'})

    if not os.path.exists(real_path):
        return JsonResponse({'code': 1, 'msg': '文件不存在'})

    file_handle = open(real_path, 'rb')
    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=os.path.basename(real_path),
    )
    return response
