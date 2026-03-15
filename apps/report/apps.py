from django.apps import AppConfig


class ReportConfig(AppConfig):
    """报告管理应用配置"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'report'
    verbose_name = '报告管理'
