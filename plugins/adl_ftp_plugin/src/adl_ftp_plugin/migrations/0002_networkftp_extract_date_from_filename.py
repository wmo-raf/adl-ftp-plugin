# Generated by Django 5.0.6 on 2024-11-25 17:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adl_ftp_plugin', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='networkftp',
            name='extract_date_from_filename',
            field=models.BooleanField(default=False, verbose_name='Extract Date from Filename'),
        ),
    ]