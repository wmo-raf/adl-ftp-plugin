from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adl_ftp_plugin', '0033_alter_ftpstationlink_start_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='ftpstationdatafile',
            name='latest_observation_time',
            field=models.DateTimeField(blank=True, help_text="Newest observation decoded from this file the last time it was processed. When the file is processed again, only rows newer than this (or than the run's start date, whichever is earlier) are handed to ADL.", null=True, verbose_name='Latest Observation Time'),
        ),
    ]
