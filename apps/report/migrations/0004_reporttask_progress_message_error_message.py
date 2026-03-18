# Generated migration: add progress, message, error_message fields to ReportTask

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('report', '0003_add_report_task_record'),
    ]

    operations = [
        migrations.AddField(
            model_name='reporttask',
            name='progress',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='任务处理进度（1-100）'),
        ),
        migrations.AddField(
            model_name='reporttask',
            name='message',
            field=models.CharField(blank=True, max_length=1000, null=True, verbose_name='状态描述（已完成图片追加记录）'),
        ),
        migrations.AddField(
            model_name='reporttask',
            name='error_message',
            field=models.CharField(blank=True, max_length=1000, null=True, verbose_name='错误日志'),
        ),
    ]
