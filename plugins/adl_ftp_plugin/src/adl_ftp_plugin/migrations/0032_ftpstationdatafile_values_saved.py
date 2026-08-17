from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adl_ftp_plugin', '0031_standardcsvconfig_no_data_value'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ftpstationdatafile',
            name='processed_at',
            field=models.DateTimeField(blank=True, help_text="When this file's records were last handed to ADL and persisted.", null=True, verbose_name='Processed At'),
        ),
        migrations.AddField(
            model_name='ftpstationdatafile',
            name='values_saved',
            field=models.PositiveIntegerField(blank=True, help_text='Observation values ADL saved from this file the last time it was processed. 0 means the file decoded but nothing was kept — typically a variable-mapping or ingestion-window mismatch. Empty for files processed before this was recorded.', null=True, verbose_name='Values Saved'),
        ),
    ]
