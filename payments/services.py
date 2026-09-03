from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from shopping.models import Order, OrderStatus

from .models import Payment, PaymentStatus


class PaymentService:
    @staticmethod
    @transaction.atomic
    def process_mpesa_callback(payload):
        if not isinstance(payload, dict):
            raise ValidationError({'callback': 'Callback payload must be an object.'})
        body = payload.get('Body')
        callback = body.get('stkCallback') if isinstance(body, dict) else None
        if not isinstance(callback, dict):
            raise ValidationError({'callback': 'Malformed callback payload.'})
        checkout_request_id = callback.get('CheckoutRequestID')
        if not checkout_request_id:
            raise ValidationError({'callback': 'Missing checkout request identifier.'})

        payment = Payment.objects.select_for_update().select_related('order').filter(
            checkout_request_id=checkout_request_id,
        ).first()
        if not payment:
            raise ValidationError({'callback': 'Unknown checkout request identifier.'})
        if payment.status == PaymentStatus.SUCCEEDED:
            return payment

        result_code = callback.get('ResultCode')
        metadata_items = callback.get('CallbackMetadata', {}).get('Item', [])
        if not isinstance(metadata_items, list):
            raise ValidationError({'callback': 'Malformed callback metadata.'})
        metadata = {
            item.get('Name'): item.get('Value')
            for item in metadata_items
            if isinstance(item, dict)
        }
        if payment.status in [PaymentStatus.FAILED, PaymentStatus.CANCELLED]:
            return payment
        payment.raw_callback_payload = payload

        if result_code == 0:
            if metadata.get('Amount') is None or not metadata.get('MpesaReceiptNumber'):
                raise ValidationError({'callback': 'Successful callback is missing payment metadata.'})
            if payment.order.status != OrderStatus.PENDING:
                raise ValidationError({'callback': 'Payment is not valid for the current order status.'})
            try:
                callback_amount = Decimal(str(metadata['Amount']))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({'callback': 'Callback amount is invalid.'})
            if callback_amount != payment.amount:
                raise ValidationError({'callback': 'Callback amount does not match the order amount.'})
            payment.status = PaymentStatus.SUCCEEDED
            payment.receipt_number = str(metadata.get('MpesaReceiptNumber', ''))
            payment.completed_at = timezone.now()
            if payment.order.status == OrderStatus.PENDING:
                payment.order.status = OrderStatus.PROCESSING
                payment.order.save(update_fields=['status', 'updated_at'])
        else:
            payment.status = PaymentStatus.FAILED
            payment.failure_code = str(result_code or '')
            payment.failure_message = callback.get('ResultDesc', 'Payment failed.')[:255]
            payment.completed_at = timezone.now()

        payment.save()
        return payment
