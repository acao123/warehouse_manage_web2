from django.apps import AppConfig


class AcDataConfig(AppConfig):
    """AC栅格数据应用配置"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ac_data'
    verbose_name = 'AC栅格数据管理'
