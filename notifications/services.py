"""Durable delivery of optional marketplace notification emails."""

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailDelivery, EmailDeliveryStatus

logger = logging.getLogger(__name__)
EMAIL_PREFERENCES = {'email_order_updates', 'email_seller_sales', 'email_booking_updates', 'email_seller_bookings', 'email_wallet_updates'}
DEFAULT_ENABLED = {'email_order_updates', 'email_booking_updates', 'email_wallet_updates'}
MAX_ATTEMPTS = 5


def _is_enabled(user, preference):
    if preference not in EMAIL_PREFERENCES:
        return False
    try:
        preferences = user.profile.notification_preferences
    except AttributeError:
        preferences = {}
    if not isinstance(preferences, dict):
        preferences = {}
    return preferences.get(preference, preference in DEFAULT_ENABLED) is True


def queue_notification_email(notification, preference, *, action_path=''):
    """Create the outbox row in the same transaction as its notification."""
    if not _is_enabled(notification.user, preference):
        return None
    recipient = (notification.user.email or '').strip()
    if not recipient:
        return None
    base_url = getattr(settings, 'PUBLIC_SITE_URL', 'https://www.africlaymarket.com').rstrip('/')
    action_url = base_url + action_path if action_path.startswith('/') and not action_path.startswith('//') else ''
    text_body = notification.message + (f'\n\nView in AfriClay: {action_url}' if action_url else '')
    html_body = render_to_string('notifications/email_notification.html', {
        'title': notification.title, 'message': notification.message, 'action_url': action_url,
        'support_email': settings.SUPPORT_EMAIL_ADDRESS,
    })
    return EmailDelivery.objects.create(
        notification=notification, recipient=recipient, preference=preference,
        subject=f'AfriClay Market: {notification.title}', text_body=text_body,
        html_body=html_body, next_attempt_at=timezone.now(),
    )


class TemporaryEmailError(Exception):
    pass


class PermanentEmailError(Exception):
    pass


def _send(delivery):
    if settings.EMAIL_BACKEND != 'anymail.backends.resend.EmailBackend':
        message = EmailMultiAlternatives(
            subject=delivery.subject, body=delivery.text_body,
            from_email=settings.DEFAULT_FROM_EMAIL, to=[delivery.recipient],
            reply_to=[settings.SUPPORT_EMAIL_ADDRESS],
        )
        message.attach_alternative(delivery.html_body, 'text/html')
        if message.send(fail_silently=False) != 1:
            raise RuntimeError('email_backend_rejected')
        return ''

    # The HTTP idempotency key makes retries safe after ambiguous timeouts.
    response = requests.post(
        'https://api.resend.com/emails',
        headers={
            'Authorization': f"Bearer {settings.ANYMAIL['RESEND_API_KEY']}",
            'Content-Type': 'application/json',
            'Idempotency-Key': f'africlay-notification-{delivery.pk}',
        },
        json={
            'from': settings.DEFAULT_FROM_EMAIL, 'to': [delivery.recipient],
            'subject': delivery.subject, 'text': delivery.text_body, 'html': delivery.html_body,
            'reply_to': settings.SUPPORT_EMAIL_ADDRESS,
        },
        timeout=8,
    )
    if response.status_code == 429 or response.status_code >= 500:
        raise TemporaryEmailError(f'provider_http_{response.status_code}')
    if response.status_code == 409 and 'concurrent_idempotent_requests' in response.text:
        raise TemporaryEmailError('provider_concurrent_request')
    if response.status_code >= 400:
        raise PermanentEmailError(f'provider_http_{response.status_code}')
    message_id = response.json().get('id', '')
    if not message_id:
        raise TemporaryEmailError('provider_id_missing')
    return str(message_id)


def send_notification_email(delivery_id):
    """Claim one due row, send, and retain retryable failures."""
    now = timezone.now()
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update().select_related('notification__user__profile').get(pk=delivery_id)
        stale = delivery.status == EmailDeliveryStatus.SENDING and delivery.updated_at <= now - timedelta(minutes=10)
        if not (delivery.status in {EmailDeliveryStatus.PENDING, EmailDeliveryStatus.RETRY} or stale):
            return False
        if delivery.next_attempt_at and delivery.next_attempt_at > now and not stale:
            return False
        if not _is_enabled(delivery.notification.user, delivery.preference):
            delivery.status = EmailDeliveryStatus.SUPPRESSED
            delivery.failure_reason = 'preference_disabled'
            delivery.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return False
        if delivery.attempts >= MAX_ATTEMPTS or now - delivery.created_at >= timedelta(hours=23):
            delivery.status = EmailDeliveryStatus.UNKNOWN if stale else EmailDeliveryStatus.FAILED
            delivery.failure_reason = 'retry_window_expired'
            delivery.save(update_fields=['status', 'failure_reason', 'updated_at'])
            return False
        delivery.status = EmailDeliveryStatus.SENDING
        delivery.attempts += 1
        delivery.save(update_fields=['status', 'attempts', 'updated_at'])

    try:
        provider_id = _send(delivery)
    except Exception as error:
        temporary = isinstance(error, (TemporaryEmailError, requests.RequestException))
        reason = str(error) if isinstance(error, (TemporaryEmailError, PermanentEmailError)) else type(error).__name__
        with transaction.atomic():
            delivery = EmailDelivery.objects.select_for_update().get(pk=delivery_id)
            if delivery.status == EmailDeliveryStatus.SENDING:
                delivery.status = EmailDeliveryStatus.RETRY if temporary and delivery.attempts < MAX_ATTEMPTS else EmailDeliveryStatus.FAILED
                delivery.failure_reason = reason[:100]
                delivery.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 15 * (2 ** delivery.attempts))) if delivery.status == EmailDeliveryStatus.RETRY else None
                delivery.save(update_fields=['status', 'failure_reason', 'next_attempt_at', 'updated_at'])
        logger.warning('Marketplace email delivery failed for notification %s: %s', delivery.notification_id, reason)
        return False

    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.status == EmailDeliveryStatus.SENDING:
            delivery.status = EmailDeliveryStatus.SENT
            delivery.provider_message_id = provider_id
            delivery.sent_at = timezone.now()
            delivery.next_attempt_at = None
            delivery.failure_reason = ''
            delivery.save(update_fields=['status', 'provider_message_id', 'sent_at', 'next_attempt_at', 'failure_reason', 'updated_at'])
    return True
