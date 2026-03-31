from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('report', '0005_reporttaskrecord_ia_tif_path_dn_tif_path'),
    ]

    operations = [
        migrations.AddField(
            model_name='reporttaskrecord',
            name='dn_max_value',
            field=models.IntegerField(blank=True, null=True, verbose_name='dn最大位移 单位mm'),
        ),
    ]
