from django.db import models


class AcTif(models.Model):
    """
    AC栅格数据模型
    用于存储上传的TIF文件信息及其地理坐标范围
    """
    local_path = models.CharField(max_length=200, default='', verbose_name='AC数据服务器地址')
    ac_name = models.CharField(max_length=200, verbose_name='AC文件名称')
    ac_local_name = models.CharField(max_length=200, verbose_name='AC文件本地名称')
    start_longitude = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, 
                                         verbose_name='起始经度')
    end_longitude = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, 
                                       verbose_name='结束经度')
    start_latitude = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, 
                                        verbose_name='起始纬度')
    end_latitude = models.DecimalField(max_digits=11, decimal_places=7, null=True, blank=True, 
                                      verbose_name='结束纬度')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'ac_tif'
        verbose_name = 'AC栅格数据'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ac_name'], name='idx_ac_name'),
            models.Index(fields=['start_longitude', 'end_longitude', 'start_latitude', 'end_latitude'], 
                        name='idx_longitude'),
        ]

    def __str__(self):
        return self.ac_name
