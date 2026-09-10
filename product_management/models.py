from decimal import Decimal
import uuid

from django.core.validators import MinValueValidator
from django.db import models

from core.models import TimestampedModel


class ProductStatus(models.TextChoices):
	DRAFT = 'draft', 'Draft'
	PUBLISHED = 'published', 'Published'
	ARCHIVED = 'archived', 'Archived'


class Product(TimestampedModel):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	store = models.ForeignKey('store_management.Store', on_delete=models.CASCADE, related_name='products')
	name = models.CharField(max_length=180)
	slug = models.SlugField(max_length=200, unique=True)
	description = models.TextField(blank=True, default='')
	sku = models.CharField(max_length=80)
	price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
	currency = models.CharField(max_length=3, default='KES')
	stock_quantity = models.PositiveIntegerField(default=0)
	status = models.CharField(max_length=20, choices=ProductStatus.choices, default=ProductStatus.DRAFT)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['store', 'sku'], name='unique_product_sku_per_store'),
		]
		indexes = [
			models.Index(fields=['status', 'created_at']),
			models.Index(fields=['store', 'status']),
		]

	def __str__(self):
		return self.name
