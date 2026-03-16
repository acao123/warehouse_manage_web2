from django.contrib import admin
from .models import ReportTask


@admin.register(ReportTask)
class ReportTaskAdmin(admin.ModelAdmin):
    """报告任务 Admin 配置"""
    list_display = [
        'id', 'user_id', 'address', 'ori_time', 'longitude', 'latitude',
        'magnitude', 'foc_depth', 'cache_base_map', 'task_status', 'created_at',
    ]
    list_filter = ['task_status', 'interp_method']
    search_fields = ['address']
    readonly_fields = ['created_at', 'updated_at']
