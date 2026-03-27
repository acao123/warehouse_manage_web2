from django.db import models


class ReportTaskRecord(models.Model):
    """
    报告任务输出信息记录模型
    对应数据库表 report_task_record，记录每次报告任务生成的图片路径及说明
    """

    user_id = models.BigIntegerField(verbose_name='任务创建人')
    task_id = models.BigIntegerField(verbose_name='任务id')

    # 图片一：历史地震分布图
    img1_info = models.CharField(max_length=1024, null=True, blank=True, verbose_name='图片一说明')
    img1_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片一文件地址')

    # 图片二：烈度分布图
    img2_info = models.CharField(max_length=512, null=True, blank=True, verbose_name='图片二说明')
    img2_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片二文件地址')

    # 图片三：地质构造图
    img3_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片三文件地址')

    # 图片四：数字高程图
    img4_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片四文件地址')

    # 图片五：土地利用类型图
    img5_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片五文件地址')

    # 图片六：人口分布图
    img6_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片六文件地址')

    # 图片七：GDP网格图
    img7_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片七文件地址')

    # 图片八：道路交通图
    img8_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片八文件地址')

    # 图片九：滑坡斜坡分布图
    img9_info = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片九说明')
    img9_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片九文件地址')

    # 图片十：Newmark 位移图
    img10_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片十文件地址')

    # Ia/Dn 栅格文件
    ia_tif_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='Ia栅格文件地址')
    dn_tif_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='dn栅格文件地址')

    # 图片十一（预留）
    img11_info = models.CharField(max_length=800, null=True, blank=True, verbose_name='图片十一说明')
    img11_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片十一文件地址')

    # 图片十二（预留）
    img12_info = models.CharField(max_length=1000, null=True, blank=True, verbose_name='图片十二说明')
    img12_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='图片十二文件地址')

    # 报告文件
    report_path = models.CharField(max_length=100, null=True, blank=True, verbose_name='报告文件地址')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'report_task_record'
        verbose_name = '报告任务输出记录'
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['user_id'], name='idx_record_user_id'),
        ]

    def __str__(self):
        return f'ReportTaskRecord(id={self.id}, task_id={self.task_id}, user_id={self.user_id})'


class ReportTask(models.Model):
    """
    报告任务模型
    对应数据库表 report_task，记录用户提交的地震报告生成任务
    """

    # 任务状态常量
    STATUS_CREATED    = 0   # 刚创建
    STATUS_RUNNING    = 1   # 正在执行
    STATUS_SUCCESS    = 2   # 执行成功
    STATUS_FAILED     = 3   # 执行失败
    STATUS_CANCELLING = 4   # 任务取消中

    STATUS_CHOICES = [
        (STATUS_CREATED,    '刚创建'),
        (STATUS_RUNNING,    '正在执行'),
        (STATUS_SUCCESS,    '执行成功'),
        (STATUS_FAILED,     '执行失败'),
        (STATUS_CANCELLING, '任务取消中'),
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
    foc_depth = models.FloatField(default=10.0, verbose_name='震源深度')
    cache_base_map = models.SmallIntegerField(default=0, verbose_name='天地图缓存')
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
    progress = models.PositiveSmallIntegerField(default=0, verbose_name='任务处理进度（1-100）')
    message = models.CharField(max_length=1000, null=True, blank=True, verbose_name='状态描述（已完成图片追加记录）')
    error_message = models.CharField(max_length=1000, null=True, blank=True, verbose_name='错误日志')
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
