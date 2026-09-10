from django.db import models
from django.conf import settings
import uuid

from core.models import TimestampedModel


class StoreStatus(models.TextChoices):
	ACTIVE = 'active', 'Active'
	SUSPENDED = 'suspended', 'Suspended'


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
