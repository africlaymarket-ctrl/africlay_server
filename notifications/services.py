import logging

from django.conf import settings
from django.core import mail

from .models import Notification

logger = logging.getLogger(__name__)


def send_notification_email(notification_id):
    try:
        notification = Notification.objects.select_related('user').get(pk=notification_id)
        recipient = notification.user.email.strip()
        if not recipient:
            return False

        sent_count = mail.send_mail(
            subject=notification.title,
            message=notification.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Notification.DoesNotExist:
        return False
    except Exception:
        logger.exception('Unable to email notification %s.', notification_id)
        return False

    if sent_count != 1:
        logger.warning('Email backend did not send notification %s.', notification_id)
        return False
    return True