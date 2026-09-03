import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from shopping.models import Order


class PaymentStatus(models.TextChoices):
    INITIATED = 'initiated', 'Initiated'
    PENDING = 'pending', 'Pending'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    provider = models.CharField(max_length=30, default='mpesa')
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='KES')
    phone_number = models.CharField(max_length=20)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, blank=True)
    receipt_number = models.CharField(max_length=100, blank=True)
    failure_code = models.CharField(max_length=30, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    raw_callback_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'checkout_request_id'],
                condition=Q(checkout_request_id__gt=''),
                name='unique_payment_provider_checkout',
            ),
        ]
        indexes = [models.Index(fields=['order', 'status'])]

    def __str__(self):
        return f'{self.provider} payment for order {self.order_id}'
