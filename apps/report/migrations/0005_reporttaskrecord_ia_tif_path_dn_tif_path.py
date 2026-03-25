from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('report', '0004_reporttask_progress_message_error_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='reporttaskrecord',
            name='ia_tif_path',
            field=models.CharField(blank=True, max_length=100, null=True, verbose_name='Ia栅格文件地址'),
        ),
        migrations.AddField(
            model_name='reporttaskrecord',
            name='dn_tif_path',
            field=models.CharField(blank=True, max_length=100, null=True, verbose_name='dn栅格文件地址'),
        ),
    ]
