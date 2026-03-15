# Generated migration for report app
from django.db import migrations, models


class Migration(migrations.Migration):
    """报告任务表初始迁移"""

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ReportTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.BigIntegerField(verbose_name='任务创建人')),
                ('longitude', models.DecimalField(decimal_places=7, max_digits=11, verbose_name='经度')),
                ('latitude', models.DecimalField(decimal_places=7, max_digits=11, verbose_name='纬度')),
                ('magnitude', models.FloatField(verbose_name='震级')),
                ('address', models.CharField(max_length=100, verbose_name='参考位置')),
                ('ori_time', models.DateTimeField(verbose_name='发震时刻')),
                ('interp_method', models.CharField(
                    choices=[
                        ('scipy_idw', 'scipy_idw'),
                        ('kriging', 'kriging'),
                        ('qgis_idw', 'qgis_idw'),
                        ('qgis_tin', 'qgis_tin'),
                    ],
                    default='scipy_idw',
                    max_length=36,
                    verbose_name='插值算法',
                )),
                ('sample_interval', models.IntegerField(default=1, verbose_name='等值线采样间隔')),
                ('max_sample_points', models.IntegerField(default=10000, verbose_name='最大采样点数')),
                ('history_record_path', models.CharField(default='', max_length=200, verbose_name='历史地震CSV文件位置')),
                ('intensity_kml_path', models.CharField(default='', max_length=200, verbose_name='烈度KML文件位置')),
                ('pga_kml_path', models.CharField(default='', max_length=200, verbose_name='PGA KML文件位置')),
                ('task_status', models.SmallIntegerField(
                    choices=[(0, '刚创建'), (1, '正在执行'), (2, '执行成功'), (3, '执行失败')],
                    default=0,
                    verbose_name='任务状态',
                )),
                ('success_time', models.DateTimeField(blank=True, null=True, verbose_name='任务完成时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '报告任务',
                'verbose_name_plural': '报告任务',
                'db_table': 'report_task',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='reporttask',
            index=models.Index(fields=['user_id'], name='idx_user_id'),
        ),
    ]
