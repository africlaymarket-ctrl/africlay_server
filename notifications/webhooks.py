"""Signed Resend delivery-status callback. It never changes commerce state."""

import json
import logging

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from svix.webhooks import Webhook, WebhookVerificationError

from .models import EmailDelivery, EmailDeliveryStatus

logger = logging.getLogger(__name__)

EVENT_STATUS = {
    'email.delivered': EmailDeliveryStatus.DELIVERED,
    'email.delivery_delayed': EmailDeliveryStatus.DEFERRED,
    'email.bounced': EmailDeliveryStatus.BOUNCED,
    'email.complained': EmailDeliveryStatus.COMPLAINED,
    'email.suppressed': EmailDeliveryStatus.SUPPRESSED,
    'email.failed': EmailDeliveryStatus.FAILED,
}
TERMINAL = {
    EmailDeliveryStatus.BOUNCED, EmailDeliveryStatus.COMPLAINED,
    EmailDeliveryStatus.SUPPRESSED, EmailDeliveryStatus.FAILED,
}


@csrf_exempt
def resend_email_events(request):
    if request.method != 'POST':
        return JsonResponse({'detail': 'Method not allowed.'}, status=405)
    secret = getattr(settings, 'RESEND_WEBHOOK_SECRET', '')
    if not secret:
        return JsonResponse({'detail': 'Webhook unavailable.'}, status=503)
    try:
        Webhook(secret).verify(request.body, request.headers)
        payload = json.loads(request.body)
    except (WebhookVerificationError, ValueError, json.JSONDecodeError):
        return JsonResponse({'detail': 'Invalid signature.'}, status=400)

    status = EVENT_STATUS.get(payload.get('type'))
    data = payload.get('data', {})
    message_id = data.get('email_id') if isinstance(data, dict) else None
    if not status or not message_id:
        return JsonResponse({'accepted': True})
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update().filter(provider_message_id=str(message_id)).first()
        if delivery is None:
            logger.info('Email status received for an untracked provider message.')
            return JsonResponse({'accepted': True})
        if delivery.status in TERMINAL or delivery.status == status:
            return JsonResponse({'accepted': True})
        if delivery.status == EmailDeliveryStatus.DELIVERED and status == EmailDeliveryStatus.DEFERRED:
            return JsonResponse({'accepted': True})
        delivery.status = status
        if status == EmailDeliveryStatus.DELIVERED:
            delivery.delivered_at = timezone.now()
        if status in TERMINAL:
            delivery.failure_reason = status
        delivery.save(update_fields=['status', 'delivered_at', 'failure_reason', 'updated_at'])
    return JsonResponse({'accepted': True})
