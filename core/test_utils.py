from uuid import uuid4

from authapp.models import User, UserRole
from authapp.utils import generate_access_token
from store_management.models import Store, StoreKYC, StoreKYCStatus
from django.utils import timezone


def unique_slug(prefix):
    return f'{prefix}-{uuid4().hex[:8]}'


def create_verified_user(role=UserRole.SELLER, prefix='test-user'):
    identifier = uuid4().hex[:8]
    return User.objects.create_user(
        email=f'{prefix}-{identifier}@example.com',
        password='StrongPassword123!',
        role=role,
        is_verified=True,
    )


def authenticate_client(client, user):
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(user)}')


def create_store(owner, name='Nairobi Clay Studio'):
    return Store.objects.create(
        owner=owner,
        name=name,
        slug=unique_slug(name.lower().replace(' ', '-')),
    )


def approve_store_kyc(store):
    now = timezone.now()
    return StoreKYC.objects.create(
        store=store,
        business_name=f'{store.name} Ltd',
        business_registration_number='BRN-001',
        tax_identification_number='TAX-001',
        document_type='business_registration',
        document='kyc/test.pdf',
        status=StoreKYCStatus.APPROVED,
        submitted_at=now,
        reviewed_at=now,
    )
