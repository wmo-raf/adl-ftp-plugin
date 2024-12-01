# Generated by Django 5.0.6 on 2024-11-28 19:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adl_ftp_plugin', '0003_ftpstationlink_date_granularity_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='ftpstationlink',
            name='date_pattern',
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name='Date Pattern'),
        ),
        migrations.AlterField(
            model_name='ftpstationlink',
            name='dir_structured_by_date',
            field=models.BooleanField(default=False, verbose_name='Directory Structured by Date ?'),
        ),
    ]
