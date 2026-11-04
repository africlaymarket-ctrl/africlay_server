import importlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from utility_services.utils import SMSResult

from .models import SMSDelivery, SMSDeliveryStatus, create_notification
from .sms import dispatch_notification_sms


@override_settings(SMS_NOTIFICATIONS_ENABLED=True, NOTIFICATION_EMAILS_ENABLED=False)
class MarketplaceSMSTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='sms-buyer@example.com', password='StrongPassword123!', phone_number='0712345678',
        )

    def test_sms_defaults_off_and_never_uses_email_consent(self):
        with self.captureOnCommitCallbacks(execute=True):
            create_notification(self.user, title='Order paid', message='Payment confirmed.', email_preference='email_order_updates')
        self.assertFalse(SMSDelivery.objects.exists())

    def test_opted_in_sms_is_submitted_once_and_records_acceptance(self):
        self.user.profile.notification_preferences = {'sms_order_updates': True}
        self.user.profile.save(update_fields=['notification_preferences'])
        with patch('notifications.sms.dispatch_sms', return_value=[SMSResult('254712345678', 'accepted', '1000', 'provider-1')]) as send:
            with self.captureOnCommitCallbacks(execute=True):
                notification = create_notification(self.user, title='Order paid', message='Sensitive order details', email_preference='email_order_updates')
            self.assertFalse(dispatch_notification_sms(notification.sms_delivery.pk))
        self.assertEqual(send.call_count, 1)
        delivery = notification.sms_delivery
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SMSDeliveryStatus.ACCEPTED)
        self.assertEqual(delivery.provider_message_id, 'provider-1')
        self.assertNotIn('Sensitive order details', delivery.message)
        self.assertNotIn('Sign in', delivery.message)
        self.assertNotIn('http', delivery.message)

    def test_missing_phone_or_opt_out_before_commit_suppresses_send(self):
        self.user.profile.notification_preferences = {'sms_order_updates': True}
        self.user.profile.save(update_fields=['notification_preferences'])
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            notification = create_notification(self.user, title='Order paid', message='Paid.', email_preference='email_order_updates')
        self.user.profile.notification_preferences = {'sms_order_updates': False}
        self.user.profile.save(update_fields=['notification_preferences'])
        with patch('notifications.sms.dispatch_sms') as send:
            for callback in callbacks:
                callback()
        send.assert_not_called()
        notification.sms_delivery.refresh_from_db()
        self.assertEqual(notification.sms_delivery.status, SMSDeliveryStatus.SUPPRESSED)

    def test_unknown_provider_result_is_not_retried(self):
        self.user.profile.notification_preferences = {'sms_order_updates': True}
        self.user.profile.save(update_fields=['notification_preferences'])
        with patch('notifications.sms.dispatch_sms', return_value=[SMSResult('254712345678', 'unknown', 'network_error')]) as send:
            with self.captureOnCommitCallbacks(execute=True):
                notification = create_notification(self.user, title='Order paid', message='Paid.', email_preference='email_order_updates')
            self.assertFalse(dispatch_notification_sms(notification.sms_delivery.pk))
        self.assertEqual(send.call_count, 1)
        notification.sms_delivery.refresh_from_db()
        self.assertEqual(notification.sms_delivery.status, SMSDeliveryStatus.UNKNOWN)

    def test_new_sms_table_is_not_public_to_supabase_api_roles(self):
        migration = importlib.import_module('notifications.migrations.0006_secure_smsdelivery')
        self.assertIn('ENABLE ROW LEVEL SECURITY', migration.SECURE_SMS_TABLE_SQL)
        self.assertIn('REVOKE ALL PRIVILEGES', migration.SECURE_SMS_TABLE_SQL)

    def test_history_is_private_and_does_not_expose_phone_or_message(self):
        self.user.profile.notification_preferences = {'sms_order_updates': True}
        self.user.profile.save(update_fields=['notification_preferences'])
        with patch('notifications.sms.dispatch_sms', return_value=[SMSResult('254712345678', 'accepted', '1000')]):
            with self.captureOnCommitCallbacks(execute=True):
                create_notification(self.user, title='Order paid', message='Private details', email_preference='email_order_updates')
        client = APIClient()
        url = reverse('notifications:sms-history')
        self.assertEqual(client.get(url).status_code, 401)
        other = get_user_model().objects.create_user(email='other-sms@example.com', password='StrongPassword123!')
        client.force_authenticate(user=other)
        self.assertEqual(client.get(url).data, [])
        client.force_authenticate(user=self.user)
        data = client.get(url).data
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['status'], 'accepted')
        self.assertNotIn('recipient', data[0])
        self.assertNotIn('message', data[0])

    def test_sms_availability_reflects_server_flag_for_authenticated_members(self):
        client = APIClient()
        url = reverse('notifications:sms-status')
        self.assertEqual(client.get(url).status_code, 401)
        client.force_authenticate(user=self.user)
        self.assertEqual(client.get(url).data, {'enabled': True})
        with self.settings(SMS_NOTIFICATIONS_ENABLED=False):
            self.assertEqual(client.get(url).data, {'enabled': False})
