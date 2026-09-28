from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


class Command(BaseCommand):
    help = 'Verify that the configured Google Cloud Storage bucket is reachable.'

    def handle(self, *args, **options):
        if not settings.GS_BUCKET_NAME:
            raise CommandError('GS_BUCKET_NAME is not configured.')
        name = f'_healthcheck/{uuid4().hex}.txt'
        try:
            default_storage.save(name, ContentFile(b'africlay-storage-check'))
            with default_storage.open(name) as stored_file:
                if stored_file.read() != b'africlay-storage-check':
                    raise RuntimeError('stored content could not be read back')
            default_storage.url(name)
            default_storage.delete(name)
        except Exception as error:
            try:
                default_storage.delete(name)
            except Exception:
                pass
            raise CommandError(f'GCS storage verification failed for {settings.GS_BUCKET_NAME!r}.') from error
        self.stdout.write(self.style.SUCCESS(f'GCS bucket {settings.GS_BUCKET_NAME!r} is reachable.'))
