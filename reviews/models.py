import uuid
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from core.models import TimestampedModel


class Review(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, default='')

    # Generic targets — at least one must be set
    product = models.ForeignKey('product_management.Product', on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    service = models.ForeignKey('service_management.Service', on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    store = models.ForeignKey('store_management.Store', on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['reviewer', 'product'], condition=models.Q(product__isnull=False), name='one_review_per_product'),
            models.UniqueConstraint(fields=['reviewer', 'service'], condition=models.Q(service__isnull=False), name='one_review_per_service'),
            models.UniqueConstraint(fields=['reviewer', 'store'], condition=models.Q(store__isnull=False), name='one_review_per_store'),
        ]
        indexes = [
            models.Index(fields=['product', '-created_at']),
            models.Index(fields=['service', '-created_at']),
            models.Index(fields=['store', '-created_at']),
        ]

    def __str__(self):
        target = self.product or self.service or self.store
        return f'{self.rating}★ by {self.reviewer.email} on {target}'
