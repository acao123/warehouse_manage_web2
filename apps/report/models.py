from django.db import models


class ReportTask(models.Model):
    """
    报告任务模型
    对应数据库表 report_task，记录用户提交的地震报告生成任务
    """

    # 任务状态常量
    STATUS_CREATED = 0   # 刚创建
    STATUS_RUNNING = 1   # 正在执行
    STATUS_SUCCESS = 2   # 执行成功
    STATUS_FAILED = 3    # 执行失败

    STATUS_CHOICES = [
        (STATUS_CREATED, '刚创建'),
        (STATUS_RUNNING, '正在执行'),
        (STATUS_SUCCESS, '执行成功'),
        (STATUS_FAILED, '执行失败'),
    ]

    # 插值算法选项
    INTERP_METHOD_CHOICES = [
        ('scipy_idw', 'scipy_idw'),
        ('kriging', 'kriging'),
        ('qgis_idw', 'qgis_idw'),
        ('qgis_tin', 'qgis_tin'),
    ]

    user_id = models.BigIntegerField(verbose_name='任务创建人')
    longitude = models.DecimalField(
        max_digits=11, decimal_places=7, verbose_name='经度'
    )
    latitude = models.DecimalField(
        max_digits=11, decimal_places=7, verbose_name='纬度'
    )
    magnitude = models.FloatField(verbose_name='震级')
    address = models.CharField(max_length=100, verbose_name='参考位置')
    ori_time = models.DateTimeField(verbose_name='发震时刻')
    interp_method = models.CharField(
        max_length=36,
        default='scipy_idw',
        choices=INTERP_METHOD_CHOICES,
        verbose_name='插值算法'
    )
    sample_interval = models.IntegerField(default=1, verbose_name='等值线采样间隔')
    max_sample_points = models.IntegerField(default=10000, verbose_name='最大采样点数')
    history_record_path = models.CharField(
        max_length=200, default='', verbose_name='历史地震CSV文件位置'
    )
    intensity_kml_path = models.CharField(
        max_length=200, default='', verbose_name='烈度KML文件位置'
    )
    pga_kml_path = models.CharField(
        max_length=200, default='', verbose_name='PGA KML文件位置'
    )
    task_status = models.SmallIntegerField(
        default=STATUS_CREATED,
        choices=STATUS_CHOICES,
        verbose_name='任务状态'
    )
    success_time = models.DateTimeField(null=True, blank=True, verbose_name='任务完成时间')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'report_task'
        verbose_name = '报告任务'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user_id'], name='idx_user_id'),
        ]

    def __str__(self):
        return f'ReportTask(id={self.id}, user_id={self.user_id}, address={self.address})'
