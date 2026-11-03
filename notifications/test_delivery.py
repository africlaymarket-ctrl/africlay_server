import base64
import importlib
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from svix.webhooks import Webhook

from .models import EmailDeliveryStatus, create_notification
from .services import _send, send_notification_email


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='no-reply@example.com',
    NOTIFICATION_EMAILS_ENABLED=True,
)
class EmailDeliveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='buyer@example.com', password='StrongPassword123!')

    def test_transient_failure_is_recorded_and_retry_uses_same_delivery(self):
        with patch('notifications.services._send', side_effect=requests.Timeout):
            with self.captureOnCommitCallbacks(execute=True):
                notification = create_notification(self.user, title='Order paid', message='Payment confirmed.', email_preference='email_order_updates')
        delivery = notification.email_delivery
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDeliveryStatus.RETRY)
        self.assertEqual(delivery.attempts, 1)
        delivery.next_attempt_at = timezone.now() - timedelta(seconds=1)
        delivery.save(update_fields=['next_attempt_at'])
        with patch('notifications.services._send', return_value='provider-message-1') as send:
            self.assertTrue(send_notification_email(delivery.pk))
            self.assertFalse(send_notification_email(delivery.pk))
        self.assertEqual(send.call_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDeliveryStatus.SENT)
        self.assertEqual(delivery.provider_message_id, 'provider-message-1')
        self.assertEqual(delivery.attempts, 2)

    def test_html_escapes_notification_text_and_links_only_to_our_site(self):
        with self.captureOnCommitCallbacks(execute=False):
            notification = create_notification(
                self.user, title='Order <paid>', message='<script>alert(1)</script>',
                email_preference='email_order_updates', action_path='//outside.example/path',
            )
        delivery = notification.email_delivery
        self.assertNotIn('<script>', delivery.html_body)
        self.assertIn('&lt;script&gt;', delivery.html_body)
        self.assertNotIn('outside.example', delivery.html_body)

    def test_preference_changed_before_retry_suppresses_email(self):
        with patch('notifications.services._send', side_effect=requests.Timeout):
            with self.captureOnCommitCallbacks(execute=True):
                notification = create_notification(self.user, title='Order paid', message='Payment confirmed.', email_preference='email_order_updates')
        delivery = notification.email_delivery
        delivery.next_attempt_at = timezone.now() - timedelta(seconds=1)
        delivery.save(update_fields=['next_attempt_at'])
        self.user.profile.notification_preferences = {'email_order_updates': False}
        self.user.profile.save(update_fields=['notification_preferences'])
        with patch('notifications.services._send') as send:
            self.assertFalse(send_notification_email(delivery.pk))
        send.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDeliveryStatus.SUPPRESSED)

    @override_settings(EMAIL_BACKEND='anymail.backends.resend.EmailBackend', ANYMAIL={'RESEND_API_KEY': 'test-only-key'})
    def test_resend_send_uses_stable_idempotency_key(self):
        with self.captureOnCommitCallbacks(execute=False):
            notification = create_notification(self.user, title='Order paid', message='Paid.', email_preference='email_order_updates')
        response = type('Response', (), {'status_code': 200, 'json': lambda self: {'id': 'provider-message-3'}})()
        with patch('notifications.services.requests.post', return_value=response) as send:
            self.assertEqual(_send(notification.email_delivery), 'provider-message-3')
        args = send.call_args.kwargs
        self.assertEqual(args['headers']['Idempotency-Key'], f'africlay-notification-{notification.email_delivery.pk}')
        self.assertEqual(args['json']['to'], [self.user.email])
        self.assertIn('html', args['json'])
        self.assertEqual(args['timeout'], 8)

    def test_history_is_private_to_current_user(self):
        other = get_user_model().objects.create_user(email='other@example.com', password='StrongPassword123!')
        with self.captureOnCommitCallbacks(execute=True):
            create_notification(self.user, title='First order', message='One.', email_preference='email_order_updates')
            create_notification(other, title='Second order', message='Two.', email_preference='email_order_updates')
        client = APIClient()
        self.assertEqual(client.get(reverse('notifications:email-history')).status_code, 401)
        client.force_authenticate(user=self.user)
        response = client.get(reverse('notifications:email-history'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertIn('First order', response.data[0]['subject'])
        self.assertNotIn('recipient', response.data[0])

    def test_webhook_requires_signature_and_is_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            notification = create_notification(self.user, title='Order paid', message='Paid.', email_preference='email_order_updates')
        delivery = notification.email_delivery
        delivery.provider_message_id = 'provider-message-2'
        delivery.save(update_fields=['provider_message_id'])
        url = reverse('notifications:resend-email-events')
        body = json.dumps({'type': 'email.delivered', 'data': {'email_id': 'provider-message-2'}})
        secret = 'whsec_' + base64.b64encode(b'0' * 32).decode()
        with override_settings(RESEND_WEBHOOK_SECRET=secret):
            self.assertEqual(self.client.post(url, data=body, content_type='application/json').status_code, 400)
            now = datetime.now(dt_timezone.utc)
            headers = {
                'HTTP_SVIX_ID': 'msg_test_delivery',
                'HTTP_SVIX_TIMESTAMP': str(int(now.timestamp())),
                'HTTP_SVIX_SIGNATURE': Webhook(secret).sign('msg_test_delivery', now, body),
            }
            self.assertEqual(self.client.post(url, data=body, content_type='application/json', **headers).status_code, 200)
            self.assertEqual(self.client.post(url, data=body, content_type='application/json', **headers).status_code, 200)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDeliveryStatus.DELIVERED)
        self.assertIsNotNone(delivery.delivered_at)

    def test_webhook_is_disabled_without_signing_secret(self):
        with override_settings(RESEND_WEBHOOK_SECRET=''):
            response = self.client.post(reverse('notifications:resend-email-events'), data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 503)

    def test_new_private_email_tables_are_covered_by_rls_migration(self):
        migration = importlib.import_module('notifications.migrations.0004_secure_email_tables')
        self.assertIn('notifications_emaildelivery', migration.SECURE_EMAIL_TABLES_SQL)
        self.assertIn('notifications_emailconsentevent', migration.SECURE_EMAIL_TABLES_SQL)
        self.assertIn('ENABLE ROW LEVEL SECURITY', migration.SECURE_EMAIL_TABLES_SQL)
        self.assertIn('REVOKE ALL PRIVILEGES', migration.SECURE_EMAIL_TABLES_SQL)
