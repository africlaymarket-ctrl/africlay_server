from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings

from .models import Notification, create_notification


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	DEFAULT_FROM_EMAIL='notifications@africlay.com',
)
class NotificationEmailTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			email='notification-user@example.com',
			password='StrongPassword123!',
		)

	def test_create_notification_sends_email_after_commit(self):
		with self.captureOnCommitCallbacks(execute=True) as callbacks:
			notification = create_notification(
				self.user,
				title='Order placed',
				message='Your order has been placed.',
			)
			self.assertEqual(mail.outbox, [])

		self.assertEqual(len(callbacks), 1)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].subject, notification.title)
		self.assertEqual(mail.outbox[0].body, notification.message)
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
					)
					raise RuntimeError('rollback notification')

		self.assertEqual(callbacks, [])
		self.assertEqual(mail.outbox, [])
		self.assertEqual(Notification.objects.count(), 0)

	def test_email_failure_does_not_undo_notification(self):
		with patch('django.core.mail.send_mail', side_effect=RuntimeError('mail unavailable')):
			with self.captureOnCommitCallbacks(execute=True):
				notification = create_notification(
					self.user,
					title='Order placed',
					message='Your order has been placed.',
				)

		self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())
