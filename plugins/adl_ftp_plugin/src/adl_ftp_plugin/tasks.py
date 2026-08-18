import logging
from datetime import timedelta

from adl.config.celery import app
from celery.schedules import crontab
from celery_singleton import Singleton
from django.utils import timezone

from adl_ftp_plugin.models import FTPStationDataFile

logger = logging.getLogger(__name__)


def cleanup_old_ftp_files():
    cutoff = timezone.now() - timedelta(days=7)

    old_files = FTPStationDataFile.objects.filter(
        processed_at__isnull=False,
        processed_at__lt=cutoff
    )

    for data_file in old_files:
        data_file.file.delete()
        data_file.delete()


@app.task(base=Singleton, bind=True)
def run_ftp_cleanup(self):
    logger.info("[FTP CLEANUP] Starting cleanup of old FTP station data files")
    cleanup_old_ftp_files()
    logger.info("[FTP CLEANUP] Completed cleanup of old FTP station data files")


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        crontab(hour=0, minute=0),
        run_ftp_cleanup.s(),
        name="run-ftp-cleanup-daily-at-midnight"
    )
