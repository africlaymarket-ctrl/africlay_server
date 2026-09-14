from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = 'Verify that the configured Google Cloud Storage bucket is reachable.'

    def handle(self, *args, **options):
        if not settings.GCS_BUCKET_NAME:
            raise CommandError('GCS_BUCKET_NAME is not configured.')
        try:
            default_storage.listdir('')
        except Exception as error:
            raise CommandError(f'Unable to reach GCS bucket {settings.GCS_BUCKET_NAME!r}: {error}') from error
        self.stdout.write(self.style.SUCCESS(f'GCS bucket {settings.GCS_BUCKET_NAME!r} is reachable.'))
