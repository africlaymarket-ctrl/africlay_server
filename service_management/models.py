import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import TimestampedModel, service_image_upload_path, validate_image_upload
from product_management.models import Tag


class ServiceCategory(TimestampedModel):
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


class ServiceStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    PUBLISHED = 'published', 'Published'
    ARCHIVED = 'archived', 'Archived'


class Service(TimestampedModel):
    """A bookable offering. A store may sell products, services, or both."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey('store_management.Store', on_delete=models.CASCADE, related_name='services')
    category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, related_name='services', blank=True, null=True)
    tags = models.ManyToManyField(Tag, related_name='services', blank=True)
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True, default='')
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    currency = models.CharField(max_length=3, default='KES')
    duration_minutes = models.PositiveIntegerField(help_text='Expected service duration in minutes.')
    booking_buffer_minutes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=ServiceStatus.choices, default=ServiceStatus.DRAFT)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return self.name


class ServiceImage(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=service_image_upload_path, validators=[validate_image_upload])
    alt_text = models.CharField(max_length=255, blank=True, default='')
    is_primary = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['service'], condition=models.Q(is_primary=True),
                name='one_primary_image_per_service',
            ),
        ]
