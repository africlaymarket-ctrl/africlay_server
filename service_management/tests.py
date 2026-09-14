from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import UserRole
from authapp.utils import generate_access_token
from product_management.models import Tag
from store_management.models import Store, StoreKYC, StoreKYCStatus


User = get_user_model()


class ServiceApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email='seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        self.store = Store.objects.create(owner=self.seller, name='Nairobi Clay Studio', slug='nairobi-clay-studio')
        self.service_data = {
            'name': 'Pottery workshop', 'slug': 'pottery-workshop', 'price': '2500.00',
            'currency': 'KES', 'duration_minutes': 120,
        }

    def authenticate_as(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(user)}')

    def test_admin_creates_service_category_and_seller_creates_tagged_service(self):
        admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
        self.authenticate_as(admin)
        category_response = self.client.post(
            reverse('service_management:category-list'),
            {'name': 'Workshops', 'slug': 'workshops'}, format='json',
        )
        tag = Tag.objects.create(name='Beginner', slug='beginner')
        self.assertEqual(category_response.status_code, status.HTTP_201_CREATED)

        self.authenticate_as(self.seller)
        response = self.client.post(
            reverse('service_management:service-manage-list'),
            {**self.service_data, 'category': category_response.data['id'], 'tags': [str(tag.id)]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['store'], str(self.store.id))
        self.assertEqual([str(tag_id) for tag_id in response.data['tags']], [str(tag.id)])

    def test_public_catalogue_only_shows_published_services(self):
        self.authenticate_as(self.seller)
        self.client.post(reverse('service_management:service-manage-list'), self.service_data, format='json')
        self.client.credentials()
        self.assertEqual(self.client.get(reverse('service_management:service-list')).data, [])

    def test_seller_cannot_publish_service_before_kyc_approval(self):
        self.authenticate_as(self.seller)
        response = self.client.post(
            reverse('service_management:service-manage-list'),
            {**self.service_data, 'status': 'published'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_approved_store_can_publish_service(self):
        StoreKYC.objects.create(
            store=self.store, business_name='Nairobi Clay Studio Ltd',
            business_registration_number='BRN-001', tax_identification_number='TAX-001',
            document_type='business_registration', document='kyc/test.pdf',
            status=StoreKYCStatus.APPROVED, submitted_at=timezone.now(), reviewed_at=timezone.now(),
        )
        self.authenticate_as(self.seller)
        response = self.client.post(
            reverse('service_management:service-manage-list'),
            {**self.service_data, 'status': 'published'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_seller_can_upload_service_image_through_endpoint(self):
        self.authenticate_as(self.seller)
        service_response = self.client.post(
            reverse('service_management:service-manage-list'), self.service_data, format='json',
        )
        image = SimpleUploadedFile('workshop.gif', b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;', content_type='image/gif')
        response = self.client.post(
            reverse('service_management:service-image-list', kwargs={'pk': service_response.data['id']}),
            {'image': image, 'alt_text': 'Workshop studio'}, format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('services/', response.data['image'])
