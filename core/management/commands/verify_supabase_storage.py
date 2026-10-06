from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Verify that the configured Supabase Storage bucket is reachable.'

    def handle(self, *args, **options):
        if not settings.SUPABASE_STORAGE_BUCKET:
            raise CommandError('SUPABASE_STORAGE_BUCKET is not configured.')

        name = f'_healthcheck/{uuid4().hex}.txt'
        try:
            saved_name = default_storage.save(name, ContentFile(b'africlay-storage-check'))
            with default_storage.open(saved_name, 'rb') as stored_file:
                if stored_file.read() != b'africlay-storage-check':
                    raise RuntimeError('stored content could not be read back')
            url = default_storage.url(saved_name)
            if not url.startswith('https://'):
                raise RuntimeError('storage generated a non-HTTPS URL')
        except Exception as error:
            raise CommandError(
                f'Supabase Storage verification failed for bucket {settings.SUPABASE_STORAGE_BUCKET!r}.'
            ) from error
        finally:
            try:
                default_storage.delete(name)
            except Exception:
                pass

        self.stdout.write(self.style.SUCCESS(
            f'Supabase Storage bucket {settings.SUPABASE_STORAGE_BUCKET!r} is reachable and generated signed URLs.'
        ))
