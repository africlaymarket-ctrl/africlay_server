import os
import subprocess
import sys
from io import BytesIO
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase
from PIL import Image

from .images import marketplace_image_variants
from .storage import SupabasePublicStorage, SupabaseStorage


class StorageSettingsTests(SimpleTestCase):
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

    def test_production_settings_use_supabase_rest_storage(self):
        environment = os.environ.copy()
        environment.update({
            'DEV_ENVIRONMENT': 'prod',
            'SUPABASE_STORAGE_BUCKET': 'test-media-bucket',
            'SUPABASE_URL': 'https://test-project.supabase.co',
            'SUPABASE_SECRET_KEY': 'test-secret',
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
        self.assertIn('core.storage.SupabaseStorage', result.stdout)


class SupabaseStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = SupabaseStorage(
            bucket_name='africlay-media',
            base_url='https://project.supabase.co',
            secret_key='test-secret',
            timeout=5,
        )

    @patch('core.storage.requests.post')
    def test_url_returns_signed_supabase_url(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            'signedURL': '/object/sign/africlay-media/products/example.jpg?token=test',
        }
        post.return_value = response

        url = self.storage.url('products/example.jpg')

        self.assertEqual(
            url,
            'https://project.supabase.co/storage/v1/object/sign/'
            'africlay-media/products/example.jpg?token=test',
        )
        response.raise_for_status.assert_called_once()

    @patch('core.storage.requests.post')
    def test_url_returns_empty_string_for_missing_object(self, post):
        response = Mock(status_code=400)
        response.json.return_value = {'statusCode': '404', 'code': 'NoSuchKey'}
        post.return_value = response

        url = self.storage.url('stores/missing-logo.png')

        self.assertEqual(url, '')
        response.raise_for_status.assert_not_called()

    @patch('core.storage.requests.post')
    def test_save_uploads_to_private_bucket(self, post):
        response = Mock()
        post.return_value = response

        name = self.storage._save('products/example.jpg', ContentFile(b'image-data'))

        self.assertEqual(name, 'products/example.jpg')
        self.assertIn('/storage/v1/object/africlay-media/products/example.jpg', post.call_args.args[0])
        self.assertNotIn('test-secret', post.call_args.args[0])
        response.raise_for_status.assert_called_once()

    @patch('core.storage.requests.get')
    def test_missing_object_does_not_block_upload(self, get):
        response = Mock(status_code=400)
        response.json.return_value = {'statusCode': '404', 'code': 'NoSuchKey'}
        get.return_value = response

        self.assertFalse(self.storage.exists('products/missing.jpg'))
        response.close.assert_called_once()


class SupabasePublicStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = SupabasePublicStorage(
            bucket_name='africlay-public-media',
            base_url='https://project.supabase.co',
            secret_key='test-secret',
            timeout=5,
        )

    def test_url_is_stable_public_cdn_url(self):
        self.assertEqual(
            self.storage.url('public/products/example image.webp'),
            'https://project.supabase.co/storage/v1/object/public/'
            'africlay-public-media/public/products/example%20image.webp',
        )

    @patch('core.storage.requests.post')
    def test_save_sets_long_lived_cache_control(self, post):
        post.return_value = Mock()

        self.storage._save('public/products/example.webp', ContentFile(b'image-data'))

        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['Cache-Control'], settings.PUBLIC_MEDIA_CACHE_CONTROL)


class MarketplaceImageTests(SimpleTestCase):
    def test_existing_image_bytes_generate_both_webp_variants(self):
        source = BytesIO()
        Image.new('RGB', (1800, 900), color='green').save(source, format='JPEG')

        detail, thumbnail = marketplace_image_variants(source.getvalue())

        self.assertTrue(detail.name.endswith('-detail.webp'))
        self.assertTrue(thumbnail.name.endswith('-thumb.webp'))
        with Image.open(detail) as detail_image:
            self.assertLessEqual(max(detail_image.size), 1600)
        with Image.open(thumbnail) as thumbnail_image:
            self.assertLessEqual(max(thumbnail_image.size), 640)
