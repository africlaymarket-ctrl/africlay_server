from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings

from .models import EmailDelivery, EmailDeliveryStatus, Notification, NotificationType, create_notification


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	DEFAULT_FROM_EMAIL='notifications@africlay.com',
	NOTIFICATION_EMAILS_ENABLED=True,
)
class NotificationEmailTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			email='notification-user@example.com',
			password='StrongPassword123!',
		)
		self.user.profile.notification_preferences = {'email_order_updates': True}
		self.user.profile.save(update_fields=['notification_preferences'])

	def test_create_notification_sends_email_after_commit(self):
		with self.captureOnCommitCallbacks(execute=True) as callbacks:
			notification = create_notification(
				self.user,
				title='Order placed',
				message='Your order has been placed.',
				notification_type=NotificationType.ORDER,
				email_preference='email_order_updates',
			)
			self.assertEqual(mail.outbox, [])

		self.assertEqual(len(callbacks), 1)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].subject, f'AfriClay Market: {notification.title}')
		self.assertEqual(mail.outbox[0].body, notification.message)
		self.assertIn('AfriClay Market', mail.outbox[0].alternatives[0][0])
		notification.email_delivery.refresh_from_db()
		self.assertEqual(notification.email_delivery.status, EmailDeliveryStatus.SENT)
		self.assertEqual(mail.outbox[0].from_email, 'notifications@africlay.com')
		self.assertEqual(mail.outbox[0].to, [self.user.email])

	def test_rolled_back_notification_does_not_send_email(self):
		with self.captureOnCommitCallbacks(execute=True) as callbacks:
			with self.assertRaises(RuntimeError):
				with transaction.atomic():
					create_notification(
						self.user,
						title='Order placed',
						message='Your order has been placed.',
						email_preference='email_order_updates',
					)
					raise RuntimeError('rollback notification')

		self.assertEqual(callbacks, [])
		self.assertEqual(mail.outbox, [])
		self.assertEqual(Notification.objects.count(), 0)
		self.assertEqual(EmailDelivery.objects.count(), 0)

	def test_email_failure_does_not_undo_notification(self):
		with patch('notifications.services._send', side_effect=RuntimeError('mail unavailable')):
			with self.captureOnCommitCallbacks(execute=True):
				notification = create_notification(
					self.user,
					title='Order placed',
					message='Your order has been placed.',
					email_preference='email_order_updates',
				)

		self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())
		notification.email_delivery.refresh_from_db()
		self.assertEqual(notification.email_delivery.status, EmailDeliveryStatus.FAILED)

	def test_optional_email_queue_failure_keeps_in_app_notification(self):
		with patch('notifications.services.queue_notification_email', side_effect=RuntimeError('queue unavailable')):
			with self.captureOnCommitCallbacks(execute=True):
				with transaction.atomic():
					notification = create_notification(
						self.user, title='Order paid', message='Payment confirmed.',
						email_preference='email_order_updates',
					)
		self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())
		self.assertFalse(EmailDelivery.objects.filter(notification=notification).exists())

	@override_settings(NOTIFICATION_EMAILS_ENABLED=False)
	def test_disabled_notification_email_does_not_register_commit_callback(self):
		with patch('notifications.services.send_notification_email') as send_email:
			with self.captureOnCommitCallbacks(execute=True) as callbacks:
				notification = create_notification(
					self.user,
					title='Order placed',
					message='Your order has been placed.',
					email_preference='email_order_updates',
				)

		self.assertEqual(callbacks, [])
		send_email.assert_not_called()
		self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())

	def test_opt_out_and_unmarked_notifications_remain_in_app_only(self):
		self.user.profile.notification_preferences = {'email_order_updates': False}
		self.user.profile.save(update_fields=['notification_preferences'])
		with self.captureOnCommitCallbacks(execute=True):
			first = create_notification(self.user, title='Order shipped', message='On its way.', email_preference='email_order_updates')
			second = create_notification(self.user, title='System', message='A system message.')
		self.assertEqual(mail.outbox, [])
		self.assertEqual(Notification.objects.filter(pk__in=[first.pk, second.pk]).count(), 2)

	def test_buyer_order_updates_default_on(self):
		self.user.profile.notification_preferences = {}
		self.user.profile.save(update_fields=['notification_preferences'])
		with self.captureOnCommitCallbacks(execute=True):
			create_notification(self.user, title='Order shipped', message='On its way.', email_preference='email_order_updates')
		self.assertEqual(len(mail.outbox), 1)

	def test_seller_email_requires_separate_opt_in(self):
		with self.captureOnCommitCallbacks(execute=True):
			create_notification(self.user, title='New paid order', message='Payment confirmed.', email_preference='email_seller_sales')
		self.assertEqual(mail.outbox, [])
		self.user.profile.notification_preferences['email_seller_sales'] = True
		self.user.profile.save(update_fields=['notification_preferences'])
		with self.captureOnCommitCallbacks(execute=True):
			create_notification(self.user, title='New paid order', message='Payment confirmed.', email_preference='email_seller_sales')
		self.assertEqual(len(mail.outbox), 1)
