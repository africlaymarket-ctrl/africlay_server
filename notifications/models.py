import uuid
from django.conf import settings
from django.db import models, transaction
from core.models import TimestampedModel


class NotificationType(models.TextChoices):
    ORDER = 'order', 'Order'
    BOOKING = 'booking', 'Booking'
    SYSTEM = 'system', 'System'
    PROMOTION = 'promotion', 'Promotion'


def create_notification(user, *, title, message, notification_type=None):
    if notification_type is None:
        notification_type = NotificationType.SYSTEM
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
    )
    from .services import send_notification_email

    transaction.on_commit(lambda: send_notification_email(notification.pk))
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
