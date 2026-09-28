from django.conf import settings
from django.test import SimpleTestCase


class StorageSettingsTests(SimpleTestCase):
    def test_google_storage_uses_canonical_private_settings(self):
        self.assertTrue(hasattr(settings, 'GS_BUCKET_NAME'))
        self.assertTrue(hasattr(settings, 'GS_PROJECT_ID'))
        self.assertTrue(hasattr(settings, 'GS_CREDENTIALS'))
        self.assertTrue(settings.GS_QUERYSTRING_AUTH)
        self.assertIsNone(settings.GS_DEFAULT_ACL)