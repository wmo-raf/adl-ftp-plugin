from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from adl_ftp_plugin.models import FTPStationDataFile


class Command(BaseCommand):
    help = 'Delete processed FTP files older than specified days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Delete files processed more than this many days ago (default: 7)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Delete ALL FTP files regardless of processed_at'
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        delete_all = options['all']

        if delete_all:
            old_files = FTPStationDataFile.objects.all()
            description = 'all files'
        else:
            cutoff = timezone.now() - timedelta(days=days)
            old_files = FTPStationDataFile.objects.filter(
                processed_at__isnull=False,
                processed_at__lt=cutoff
            )
            description = f'files processed before {cutoff}'

        count = old_files.count()

        if dry_run:
            self.stdout.write(f'Would delete {count} {description}')
            for data_file in old_files[:10]:
                self.stdout.write(f'  - {data_file.file_name}')
            if count > 10:
                self.stdout.write(f'  ... and {count - 10} more')
            return

        deleted = 0
        for data_file in old_files:
            data_file.file.delete()
            data_file.delete()
            deleted += 1

        self.stdout.write(
            self.style.SUCCESS(f'Deleted {deleted} {description}')
        )
