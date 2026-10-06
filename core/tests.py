import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase


class StorageSettingsTests(SimpleTestCase):
    def test_supabase_storage_uses_private_signed_urls(self):
        self.assertTrue(hasattr(settings, 'SUPABASE_STORAGE_BUCKET'))
        self.assertTrue(hasattr(settings, 'SUPABASE_S3_ENDPOINT_URL'))
        self.assertTrue(settings.AWS_QUERYSTRING_AUTH)
        self.assertIsNone(settings.AWS_DEFAULT_ACL)

    def test_production_settings_require_supabase_storage(self):
        environment = os.environ.copy()
        environment.update({'DEV_ENVIRONMENT': 'prod', 'SUPABASE_STORAGE_BUCKET': '   '})

        result = subprocess.run(
            [sys.executable, '-c', 'import africlay_server.settings.prod'],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('SUPABASE_STORAGE_BUCKET must be configured in production', result.stderr)

    def test_production_settings_use_supabase_storage_when_configured(self):
        environment = os.environ.copy()
        environment.update({
            'DEV_ENVIRONMENT': 'prod',
            'SUPABASE_STORAGE_BUCKET': 'test-media-bucket',
            'SUPABASE_S3_ENDPOINT_URL': 'https://test-project.storage.supabase.co/storage/v1/s3',
            'SUPABASE_S3_REGION': 'test-region',
            'SUPABASE_S3_ACCESS_KEY_ID': 'test-key-id',
            'SUPABASE_S3_SECRET_ACCESS_KEY': 'test-secret',
        })
        code = (
            'import django; django.setup(); '
            'from django.core.files.storage import default_storage; '
            'default_storage._setup(); '
            'print(type(default_storage._wrapped).__module__ + "." + '
            'type(default_storage._wrapped).__name__)'
        )

        result = subprocess.run(
            [sys.executable, '-c', code],
            cwd=settings.BASE_DIR,
            env={**environment, 'DJANGO_SETTINGS_MODULE': 'africlay_server.settings'},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('storages.backends.s3.S3Storage', result.stdout)
