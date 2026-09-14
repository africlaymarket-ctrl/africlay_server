from decimal import Decimal
import uuid

from django.core.validators import MinValueValidator
from django.db import models

from core.models import TimestampedModel, product_image_upload_path, validate_image_upload


class Category(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True, default='')
    parent = models.ForeignKey('self', on_delete=models.PROTECT, blank=True, null=True, related_name='children')
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class Tag(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ProductStatus(models.TextChoices):
	DRAFT = 'draft', 'Draft'
	PUBLISHED = 'published', 'Published'
	ARCHIVED = 'archived', 'Archived'


class Product(TimestampedModel):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	store = models.ForeignKey('store_management.Store', on_delete=models.CASCADE, related_name='products')
	category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products', blank=True, null=True)
	tags = models.ManyToManyField(Tag, related_name='products', blank=True)
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


class ProductImage(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=product_image_upload_path, validators=[validate_image_upload])
    alt_text = models.CharField(max_length=255, blank=True, default='')
    is_primary = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['product'], condition=models.Q(is_primary=True),
                name='one_primary_image_per_product',
            ),
        ]

    def __str__(self):
        return f'Image for {self.product.name}'


class ProductVariant(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    name = models.CharField(max_length=100)
    sku_suffix = models.CharField(max_length=40)
    attributes = models.JSONField(default=dict, blank=True)
    price_modifier = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_quantity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['product', 'sku_suffix'], name='unique_variant_sku_suffix_per_product'),
        ]

    def __str__(self):
        return f'{self.product.name} — {self.name}'
