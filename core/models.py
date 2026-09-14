from django.db import models
from django.core.exceptions import ValidationError
from pathlib import Path
from uuid import uuid4


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def _upload_name(filename):
    return f'{uuid4().hex}{Path(filename).suffix.lower()}'


def avatar_upload_path(instance, filename):
    return f'avatars/{_upload_name(filename)}'


def store_logo_upload_path(instance, filename):
    return f'stores/{instance.pk or "pending"}/logos/{_upload_name(filename)}'


def store_banner_upload_path(instance, filename):
    return f'stores/{instance.pk or "pending"}/banners/{_upload_name(filename)}'


def kyc_document_upload_path(instance, filename):
    return f'kyc/{instance.store_id}/{_upload_name(filename)}'


def product_image_upload_path(instance, filename):
    return f'products/{instance.product.store_id}/{instance.product_id}/{_upload_name(filename)}'


def service_image_upload_path(instance, filename):
    return f'services/{instance.service.store_id}/{instance.service_id}/{_upload_name(filename)}'


MAX_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}


def validate_image_upload(upload):
    if upload.size > MAX_IMAGE_UPLOAD_SIZE:
        raise ValidationError('Images must be 5 MB or smaller.')


def validate_document_upload(upload):
    if upload.size > MAX_DOCUMENT_UPLOAD_SIZE:
        raise ValidationError('Documents must be 10 MB or smaller.')
    if Path(upload.name).suffix.lower() not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError('Documents must be PDF, JPG, JPEG, or PNG files.')
