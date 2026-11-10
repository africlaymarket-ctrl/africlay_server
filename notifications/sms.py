"""Optional marketplace SMS; never confuse provider acceptance with delivery."""

import logging

from django.db import transaction

from utility_services.utils import dispatch_sms, normalize_phone_number, valid_sms_phone

from .models import SMSDelivery, SMSDeliveryStatus

logger = logging.getLogger(__name__)

PREFERENCES = {
    'email_order_updates': 'sms_order_updates',
    'email_seller_sales': 'sms_seller_sales',
    'email_booking_updates': 'sms_booking_updates',
    'email_seller_bookings': 'sms_seller_bookings',
    'email_wallet_updates': 'sms_wallet_updates',
}

ALERT_TEXT = {
    'sms_order_updates': 'There is an update to your order.',
    'sms_seller_sales': 'Open Seller Orders for sale and handover details.',
    'sms_booking_updates': 'There is an update to your booking.',
    'sms_seller_bookings': 'There is an update to a service booking.',
    'sms_wallet_updates': 'There is an update to your wallet.',
}


def _enabled(user, preference):
    if not user.is_active:
        return False
    try:
        values = user.profile.notification_preferences
    except AttributeError:
        return False
    return isinstance(values, dict) and values.get(preference) is True


def queue_notification_sms(notification, email_preference):
    preference = PREFERENCES.get(email_preference)
    if not preference or not _enabled(notification.user, preference):
        return None
    phone = normalize_phone_number(notification.user.phone_number)
    if not valid_sms_phone(phone):
        return None
    # Notification titles can contain item or customer details. Keep SMS generic
    # until the account phone number has a separate ownership-verification flow.
    message = f'AfriClay Market: {ALERT_TEXT[preference]} Details are in your AfriClay account.'
    return SMSDelivery.objects.create(
        notification=notification, recipient=phone, message=message, preference=preference,
    )


def dispatch_notification_sms(delivery_id):
    with transaction.atomic():
        delivery = SMSDelivery.objects.select_for_update().select_related('notification__user__profile').get(pk=delivery_id)
        if delivery.status != SMSDeliveryStatus.PENDING:
            return False
        user = delivery.notification.user
        if not _enabled(user, delivery.preference) or normalize_phone_number(user.phone_number) != delivery.recipient:
            delivery.status = SMSDeliveryStatus.SUPPRESSED
            delivery.save(update_fields=['status', 'updated_at'])
            return False
        delivery.status = SMSDeliveryStatus.SUBMITTING
        delivery.save(update_fields=['status', 'updated_at'])
    try:
        result = dispatch_sms(delivery.message, delivery.recipient)[0]
    except Exception:
        logger.exception('SMS submission outcome unknown for notification %s.', delivery.notification_id)
        status = SMSDeliveryStatus.UNKNOWN
        code = 'dispatch_error'
        result = None
    else:
        status = result.status
        code = result.code
    with transaction.atomic():
        delivery = SMSDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.status == SMSDeliveryStatus.SUBMITTING:
            delivery.status = status
            delivery.provider_code = code[:32]
            if result:
                delivery.provider_message_id = result.message_id[:128]
                delivery.cost = result.cost
            delivery.save(update_fields=['status', 'provider_code', 'provider_message_id', 'cost', 'updated_at'])
    return status == SMSDeliveryStatus.ACCEPTED
