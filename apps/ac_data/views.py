import os
from decimal import Decimal
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from django.conf import settings
from .models import AcTif


def ac_list_page_view(request):
    """
    AC栅格数据列表页面视图
    :param request: HTTP请求对象
    :return: 渲染的HTML页面
    """
    return render(request, 'ac_data/ac_list.html')


def ac_list_view(request):
    """
    AC栅格数据列表接口
    :param request: HTTP请求对象
    :return: JSON格式的数据列表
    """
    # 获取分页参数
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 10))
    
    # 获取搜索参数
    ac_name = request.GET.get('ac_name', '').strip()
    ac_local_name = request.GET.get('ac_local_name', '').strip()
    start_longitude = request.GET.get('start_longitude', '').strip()
    end_longitude = request.GET.get('end_longitude', '').strip()
    start_latitude = request.GET.get('start_latitude', '').strip()
    end_latitude = request.GET.get('end_latitude', '').strip()
    
    # 构建查询条件
    queryset = AcTif.objects.all()
    
    if ac_name:
        queryset = queryset.filter(ac_name__icontains=ac_name)
    if ac_local_name:
        queryset = queryset.filter(ac_local_name__icontains=ac_local_name)
    if start_longitude:
        queryset = queryset.filter(start_longitude=Decimal(start_longitude))
    if end_longitude:
        queryset = queryset.filter(end_longitude=Decimal(end_longitude))
    if start_latitude:
        queryset = queryset.filter(start_latitude=Decimal(start_latitude))
    if end_latitude:
        queryset = queryset.filter(end_latitude=Decimal(end_latitude))
    
    # 获取总数
    total = queryset.count()
    
    # 分页
    start = (page - 1) * limit
    end = start + limit
    data_list = queryset[start:end]
    
    # 构建返回数据
    data = []
    for item in data_list:
        data.append({
            'id': item.id,
            'local_path': item.local_path,
            'ac_name': item.ac_name,
            'ac_local_name': item.ac_local_name,
            'start_longitude': str(item.start_longitude) if item.start_longitude else '',
            'end_longitude': str(item.end_longitude) if item.end_longitude else '',
            'start_latitude': str(item.start_latitude) if item.start_latitude else '',
            'end_latitude': str(item.end_latitude) if item.end_latitude else '',
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
        })
    
    return JsonResponse({
        'code': 0,
        'msg': '成功',
        'count': total,
        'data': data
    })


@csrf_exempt
def ac_upload_view(request):
    """
    AC栅格数据上传接口
    :param request: HTTP请求对象
    :return: JSON格式的响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})
    
    # 获取上传的文件
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'code': 1, 'msg': '请选择要上传的文件'})
    
    # 验证文件扩展名
    file_name = uploaded_file.name
    if not file_name.lower().endswith('.tif'):
        return JsonResponse({'code': 1, 'msg': '只允许上传.tif文件'})
    
    # 验证文件大小（200MB）
    if uploaded_file.size > 200 * 1024 * 1024:
        return JsonResponse({'code': 1, 'msg': '文件大小不能超过200MB'})
    
    try:
        # 临时保存文件以便GDAL读取
        temp_dir = os.path.join(settings.BASE_DIR, 'data', 'ac', 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        temp_file_path = os.path.join(temp_dir, file_name)
        
        with open(temp_file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        
        # 使用GDAL提取地理坐标范围
        try:
            from osgeo import gdal
            dataset = gdal.Open(temp_file_path)
            if dataset is None:
                os.remove(temp_file_path)
                return JsonResponse({'code': 1, 'msg': '无法读取TIF文件，请确保文件格式正确'})
            
            # 获取GeoTransform
            geo_transform = dataset.GetGeoTransform()
            width = dataset.RasterXSize
            height = dataset.RasterYSize
            
            # 计算经纬度范围
            # GeoTransform: [左上角X, 像素宽度, 旋转, 左上角Y, 旋转, 像素高度(负值)]
            start_longitude = Decimal(str(geo_transform[0]))
            start_latitude = Decimal(str(geo_transform[3] + geo_transform[5] * height))
            end_longitude = Decimal(str(geo_transform[0] + geo_transform[1] * width))
            end_latitude = Decimal(str(geo_transform[3]))
            
            dataset = None  # 关闭数据集
            
        except ImportError:
            # GDAL未安装，使用默认值
            start_longitude = None
            end_longitude = None
            start_latitude = None
            end_latitude = None
        except Exception as e:
            os.remove(temp_file_path)
            return JsonResponse({'code': 1, 'msg': f'提取地理坐标失败: {str(e)}'})
        
        # 创建数据库记录（先不设置local_path）
        ac_tif = AcTif.objects.create(
            ac_name=file_name,
            ac_local_name='',  # 暂时为空，获取ID后更新
            start_longitude=start_longitude,
            end_longitude=end_longitude,
            start_latitude=start_latitude,
            end_latitude=end_latitude,
        )
        
        # 生成本地文件名
        if start_longitude and end_longitude and start_latitude and end_latitude:
            ac_local_name = f"{ac_tif.id}_{start_longitude}_{end_longitude}_{start_latitude}_{end_latitude}.tif"
        else:
            ac_local_name = f"{ac_tif.id}_{file_name}"
        
        # 移动文件到最终位置
        final_dir = os.path.join(settings.BASE_DIR, 'data', 'ac')
        os.makedirs(final_dir, exist_ok=True)
        final_file_path = os.path.join(final_dir, ac_local_name)
        
        os.rename(temp_file_path, final_file_path)
        
        # 更新数据库记录
        ac_tif.ac_local_name = ac_local_name
        ac_tif.local_path = f'data/ac/{ac_local_name}'
        ac_tif.save()
        
        return JsonResponse({'code': 0, 'msg': '上传成功'})
        
    except Exception as e:
        return JsonResponse({'code': 1, 'msg': f'上传失败: {str(e)}'})


@csrf_exempt
def ac_edit_view(request):
    """
    AC栅格数据编辑接口（仅允许修改文件名称）
    :param request: HTTP请求对象
    :return: JSON格式的响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})
    
    # 获取参数
    ac_id = request.POST.get('id')
    ac_name = request.POST.get('ac_name', '').strip()
    
    if not ac_id:
        return JsonResponse({'code': 1, 'msg': '缺少必要参数'})
    
    if not ac_name:
        return JsonResponse({'code': 1, 'msg': '文件名称不能为空'})
    
    try:
        ac_tif = AcTif.objects.get(id=ac_id)
        ac_tif.ac_name = ac_name
        ac_tif.save()
        
        return JsonResponse({'code': 0, 'msg': '修改成功'})
        
    except AcTif.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '数据不存在'})
    except Exception as e:
        return JsonResponse({'code': 1, 'msg': f'修改失败: {str(e)}'})


@csrf_exempt
def ac_delete_view(request):
    """
    AC栅格数据删除接口
    :param request: HTTP请求对象
    :return: JSON格式的响应
    """
    if request.method != 'POST':
        return JsonResponse({'code': 1, 'msg': '请求方法错误'})
    
    # 获取参数
    ac_id = request.POST.get('id')
    
    if not ac_id:
        return JsonResponse({'code': 1, 'msg': '缺少必要参数'})
    
    try:
        ac_tif = AcTif.objects.get(id=ac_id)
        
        # 删除服务器上的文件
        file_path = os.path.join(settings.BASE_DIR, ac_tif.local_path)
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # 删除数据库记录
        ac_tif.delete()
        
        return JsonResponse({'code': 0, 'msg': '删除成功'})
        
    except AcTif.DoesNotExist:
        return JsonResponse({'code': 1, 'msg': '数据不存在'})
    except Exception as e:
        return JsonResponse({'code': 1, 'msg': f'删除失败: {str(e)}'})
