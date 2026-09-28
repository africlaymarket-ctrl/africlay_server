import uuid

from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class Conversation(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='buyer_conversations')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_conversations')
    store = models.ForeignKey('store_management.Store', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    product = models.ForeignKey('product_management.Product', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    service = models.ForeignKey('service_management.Service', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations')
    last_message_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['buyer', 'seller', 'store', 'product', 'service'],
                name='unique_message_conversation',
            )
        ]

    def __str__(self):
        return f'{self.buyer.email} -> {self.seller.email}'


class Message(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    body = models.TextField()
    attachment_url = models.URLField(blank=True, null=True)
    attachment_name = models.CharField(max_length=255, blank=True, default='')
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['sender', 'created_at']),
        ]

    def __str__(self):
        return f'{self.sender.email}: {self.body[:40]}'


class MessageReadReceipt(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='read_receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='message_read_receipts')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user')
        ordering = ['-read_at']

    def __str__(self):
        return f'{self.user.email} read {self.message_id}'
