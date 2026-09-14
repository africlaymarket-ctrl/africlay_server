from django.db import models
from django.conf import settings
import uuid

from core.models import TimestampedModel


class StoreStatus(models.TextChoices):
	ACTIVE = 'active', 'Active'
	SUSPENDED = 'suspended', 'Suspended'


class StoreKYCStatus(models.TextChoices):
	PENDING = 'pending', 'Pending'
	APPROVED = 'approved', 'Approved'
	REJECTED = 'rejected', 'Rejected'


class Store(TimestampedModel):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	owner = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='store',
	)
	name = models.CharField(max_length=150)
	slug = models.SlugField(max_length=160, unique=True)
	description = models.TextField(blank=True, default='')
	legal_name = models.CharField(max_length=200, blank=True, default='')
	business_type = models.CharField(max_length=50, blank=True, default='')
	phone_number = models.CharField(max_length=20, blank=True, default='')
	address = models.CharField(max_length=255, blank=True, default='')
	city = models.CharField(max_length=100, blank=True, default='')
	country = models.CharField(max_length=100, blank=True, default='')
	country_code = models.CharField(max_length=2, blank=True, default='')
	status = models.CharField(
		max_length=20,
		choices=StoreStatus.choices,
		default=StoreStatus.ACTIVE,
	)

	class Meta:
		ordering = ['name']
		indexes = [
			models.Index(fields=['status', 'name']),
		]

	def __str__(self):
		return self.name


class StoreKYC(TimestampedModel):
	store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name='kyc')
	business_name = models.CharField(max_length=200)
	business_registration_number = models.CharField(max_length=100)
	tax_identification_number = models.CharField(max_length=100)
	status = models.CharField(
		max_length=20,
		choices=StoreKYCStatus.choices,
		default=StoreKYCStatus.PENDING,
		db_index=True,
	)
	submitted_at = models.DateTimeField()
	reviewed_at = models.DateTimeField(blank=True, null=True)
	reviewed_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		blank=True,
		null=True,
		related_name='reviewed_store_kycs',
	)
	rejection_reason = models.TextField(blank=True, default='')

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.store.name} KYC ({self.status})'
