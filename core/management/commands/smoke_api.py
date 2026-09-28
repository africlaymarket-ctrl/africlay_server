from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from authapp.models import User, UserRole
from authapp.utils import generate_access_token


class Command(BaseCommand):
    help = 'Exercise core API endpoints against the configured persistent database.'

    def handle(self, *args, **options):
        suffix = uuid4().hex[:8]
        seller = User.objects.create_user(
            email=f'smoke-seller-{suffix}@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        admin = User.objects.create_superuser(
            email=f'smoke-admin-{suffix}@example.com',
            password='AdminPassword123!',
        )
        client = APIClient()

        with override_settings(ALLOWED_HOSTS=['testserver', *settings.ALLOWED_HOSTS]):
            self._authenticate(client, seller)
            store = self._post(client, 'store_management:store-list-create', {
                'name': f'Smoke Store {suffix}',
                'slug': f'smoke-store-{suffix}',
                'description': 'Persistent endpoint smoke test store.',
            })
            store_slug = store['slug']

            kyc = self._post(
                client,
                'store_management:store-kyc-submit',
                {
                    'business_name': f'Smoke Store {suffix} Ltd',
                    'business_registration_number': f'BRN-{suffix}',
                    'tax_identification_number': f'TAX-{suffix}',
                    'document_type': 'business_registration',
                    'document': SimpleUploadedFile(
                        'registration.pdf', b'%PDF-1.4 smoke test', content_type='application/pdf'
                    ),
                },
                kwargs={'slug': store_slug},
                format='multipart',
            )
            self._authenticate(client, admin)
            self._post(
                client,
                'store_management:store-kyc-review',
                {'decision': 'approved'},
                kwargs={'slug': store_slug},
            )

            self._authenticate(client, seller)
            product = self._post(client, 'product_management:product-manage-list', {
                'name': f'Smoke Product {suffix}',
                'slug': f'smoke-product-{suffix}',
                'sku': f'SMOKE-{suffix}',
                'price': '25.00',
                'currency': 'KES',
                'stock_quantity': 3,
                'status': 'published',
            })
            service = self._post(client, 'service_management:service-manage-list', {
                'name': f'Smoke Service {suffix}',
                'slug': f'smoke-service-{suffix}',
                'price': '50.00',
                'currency': 'KES',
                'duration_minutes': 60,
                'status': 'published',
            })

            self._get(client, 'product_management:product-list')
            self._get(client, 'service_management:service-list')

        self.stdout.write(self.style.SUCCESS(
            f'Persistent API smoke test passed: store={store["id"]}, '
            f'kyc={kyc["id"]}, product={product["id"]}, service={service["id"]}'
        ))

    def _authenticate(self, client, user):
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(user)}')

    def _post(self, client, route_name, data, kwargs=None, format='json'):
        response = client.post(reverse(route_name, kwargs=kwargs or {}), data, format=format)
        if response.status_code not in (200, 201):
            raise CommandError(f'{route_name} failed with {response.status_code}: {response.data}')
        return response.data

    def _get(self, client, route_name):
        response = client.get(reverse(route_name))
        if response.status_code != 200:
            raise CommandError(f'{route_name} failed with {response.status_code}: {response.data}')
        return response.data
