# Generated by Django 5.0.6 on 2024-11-28 21:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adl_ftp_plugin', '0007_ftpstationdatafile'),
    ]

    operations = [
        migrations.AddField(
            model_name='ftpstationdatafile',
            name='processed',
            field=models.BooleanField(default=False, verbose_name='Processed'),
        ),
        migrations.AddField(
            model_name='ftpstationdatafile',
            name='variable_mappings',
            field=models.ManyToManyField(to='adl_ftp_plugin.ftpvariablemapping', verbose_name='Variable Mappings'),
        ),
    ]