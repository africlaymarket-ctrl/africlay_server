import uuid
import logging
from django.conf import settings
from django.db import models, transaction
from core.models import TimestampedModel

logger = logging.getLogger(__name__)


class NotificationType(models.TextChoices):
    ORDER = 'order', 'Order'
    BOOKING = 'booking', 'Booking'
    SYSTEM = 'system', 'System'
    PROMOTION = 'promotion', 'Promotion'


def create_notification(user, *, title, message, notification_type=None, email_preference=None, action_path=''):
    if notification_type is None:
        notification_type = NotificationType.SYSTEM
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
    )
    if settings.NOTIFICATION_EMAILS_ENABLED and email_preference:
        from .services import queue_notification_email, send_notification_email
        try:
            # Optional email failures must never roll back an order or payment.
            with transaction.atomic():
                delivery = queue_notification_email(notification, email_preference, action_path=action_path)
        except Exception:
            logger.exception('Unable to queue optional email for notification %s.', notification.pk)
            delivery = None
        if delivery is not None:
            def dispatch():
                try:
                    send_notification_email(delivery.pk)
                except Exception:
                    logger.exception('Unable to dispatch optional email for notification %s.', notification.pk)

            transaction.on_commit(dispatch)
    if settings.SMS_NOTIFICATIONS_ENABLED and email_preference:
        from .sms import queue_notification_sms, dispatch_notification_sms
        try:
            with transaction.atomic():
                sms_delivery = queue_notification_sms(notification, email_preference)
        except Exception:
            logger.exception('Unable to queue optional SMS for notification %s.', notification.pk)
            sms_delivery = None
        if sms_delivery is not None:
            def dispatch_sms_after_commit():
                try:
                    dispatch_notification_sms(sms_delivery.pk)
                except Exception:
                    logger.exception('Unable to dispatch optional SMS for notification %s.', notification.pk)

            transaction.on_commit(dispatch_sms_after_commit)
    return notification


class Notification(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NotificationType.choices, default=NotificationType.SYSTEM)
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f'{self.notification_type} notification for {self.user.email}'


class EmailDeliveryStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    SENDING = 'sending', 'Sending'
    SENT = 'sent', 'Accepted by provider'
    DELIVERED = 'delivered', 'Delivered'
    RETRY = 'retry', 'Retry scheduled'
    DEFERRED = 'deferred', 'Delayed by provider'
    FAILED = 'failed', 'Failed'
    BOUNCED = 'bounced', 'Bounced'
    COMPLAINED = 'complained', 'Spam complaint'
    SUPPRESSED = 'suppressed', 'Suppressed'
    UNKNOWN = 'unknown', 'Needs review'


class EmailDelivery(TimestampedModel):
    """Durable, recipient-specific delivery record for a marketplace notification."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.OneToOneField(Notification, on_delete=models.CASCADE, related_name='email_delivery')
    recipient = models.EmailField()
    preference = models.CharField(max_length=48)
    subject = models.CharField(max_length=255)
    text_body = models.TextField()
    html_body = models.TextField()
    status = models.CharField(max_length=16, choices=EmailDeliveryStatus.choices, default=EmailDeliveryStatus.PENDING)
    provider_message_id = models.CharField(max_length=128, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]


class EmailConsentEvent(models.Model):
    """Append-only record of explicit promotional email preference changes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_consent_events')
    consented = models.BooleanField()
    source = models.CharField(max_length=32, default='profile_settings')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SMSDeliveryStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    SUBMITTING = 'submitting', 'Submitting'
    ACCEPTED = 'accepted', 'Accepted by provider'
    REJECTED = 'rejected', 'Rejected'
    UNKNOWN = 'unknown', 'Outcome unknown'
    SUPPRESSED = 'suppressed', 'Preference or phone changed'


class SMSDelivery(TimestampedModel):
    """One provider submission per opted-in notification; no automatic retry."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.OneToOneField(Notification, on_delete=models.CASCADE, related_name='sms_delivery')
    recipient = models.CharField(max_length=12)
    message = models.CharField(max_length=160)
    preference = models.CharField(max_length=48)
    status = models.CharField(max_length=12, choices=SMSDeliveryStatus.choices, default=SMSDeliveryStatus.PENDING)
    provider_code = models.CharField(max_length=32, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
