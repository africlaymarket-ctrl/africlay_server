from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.test import TestCase

from authapp.models import UserRole
from authapp.utils import generate_access_token
from .models import StoreKYC, StoreKYCStatus


User = get_user_model()


class StoreApiTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.store_data = {
			'name': 'Nairobi Clay Studio',
			'slug': 'nairobi-clay-studio',
			'description': 'Handmade ceramic pieces from Nairobi.',
		}
		self.seller = User.objects.create_user(
			email='seller@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_verified=True,
		)

	def authenticate_as(self, user):
		token = generate_access_token(user)
		self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

	def test_verified_seller_can_create_store(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('store_management:store-list-create'),
			{**self.store_data, 'legal_name': 'Nairobi Clay Studio Ltd', 'city': 'Nairobi'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['name'], self.store_data['name'])
		self.assertEqual(response.data['slug'], self.store_data['slug'])
		self.assertEqual(response.data['legal_name'], 'Nairobi Clay Studio Ltd')
		self.assertEqual(response.data['city'], 'Nairobi')
		self.assertEqual(response.data['owner'], str(self.seller.id))

	def test_buyer_cannot_create_store(self):
		buyer = User.objects.create_user(
			email='buyer@example.com',
			password='StrongPassword123!',
			role=UserRole.BUYER,
			is_verified=True,
		)
		self.authenticate_as(buyer)

		response = self.client.post(reverse('store_management:store-list-create'), self.store_data, format='json')

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_unverified_seller_cannot_create_store(self):
		self.seller.is_verified = False
		self.seller.save(update_fields=['is_verified'])
		self.authenticate_as(self.seller)

		response = self.client.post(reverse('store_management:store-list-create'), self.store_data, format='json')

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_seller_cannot_create_duplicate_store(self):
		self.authenticate_as(self.seller)
		create_url = reverse('store_management:store-list-create')
		self.assertEqual(self.client.post(create_url, self.store_data, format='json').status_code, status.HTTP_201_CREATED)

		response = self.client.post(
			create_url,
			{**self.store_data, 'slug': 'another-store'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_staff_seller_cannot_update_another_sellers_store(self):
		self.create_store()
		other_seller = User.objects.create_user(
			email='other-seller@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_verified=True,
		)
		other_store = self.create_store(other_seller, slug='other-clay-studio')
		staff_seller = User.objects.create_user(
			email='staff-seller@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_staff=True,
			is_verified=True,
		)
		self.authenticate_as(staff_seller)

		response = self.client.patch(
			reverse('store_management:store-detail', kwargs={'slug': other_store['slug']}),
			{'description': 'Unauthorized update'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_anonymous_user_can_retrieve_active_store(self):
		self.authenticate_as(self.seller)
		create_response = self.client.post(
			reverse('store_management:store-list-create'),
			self.store_data,
			format='json',
		)
		self.client.credentials()

		response = self.client.get(
			reverse(
				'store_management:store-detail',
				kwargs={'slug': create_response.data['slug']},
			),
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['name'], self.store_data['name'])

	def create_store(self, user=None, slug=None):
		self.authenticate_as(user or self.seller)
		response = self.client.post(
			reverse('store_management:store-list-create'),
			{**self.store_data, 'slug': slug or self.store_data['slug']},
			format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		return response.data

	def kyc_payload(self, business_name='Nairobi Clay Studio Ltd'):
		return {
			'business_name': business_name,
			'business_registration_number': 'BRN-001',
			'tax_identification_number': 'TAX-001',
			'document_type': 'business_registration',
			'document': SimpleUploadedFile('registration.pdf', b'%PDF-1.4 test document', content_type='application/pdf'),
		}

	def test_seller_can_submit_store_kyc(self):
		store = self.create_store()
		response = self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(),
			format='multipart',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['status'], StoreKYCStatus.PENDING)
		self.assertEqual(response.data['store'], store['id'])
		self.assertIsNotNone(response.data['submitted_at'])

	def test_seller_can_attach_kyc_document_through_submit_endpoint(self):
		store = self.create_store()
		response = self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(),
			format='multipart',
		)
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['document_type'], 'business_registration')
		self.assertIn('kyc/', response.data['document'])

	def test_seller_cannot_submit_duplicate_pending_kyc(self):
		store = self.create_store()
		url = reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']})
		data = self.kyc_payload()

		self.assertEqual(self.client.post(url, data, format='multipart').status_code, status.HTTP_201_CREATED)
		response = self.client.post(url, data, format='json')

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(StoreKYC.objects.count(), 1)

	def test_admin_can_approve_submitted_kyc(self):
		store = self.create_store()
		self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(), format='multipart',
		)
		admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
		self.authenticate_as(admin)

		response = self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'approved'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['status'], StoreKYCStatus.APPROVED)
		self.assertEqual(response.data['reviewed_by'], str(admin.id))

	def test_admin_rejection_requires_reason(self):
		store = self.create_store()
		self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(), format='multipart',
		)
		admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
		self.authenticate_as(admin)

		response = self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'rejected'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_seller_can_resubmit_rejected_kyc(self):
		store = self.create_store()
		url = reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']})
		data = self.kyc_payload()
		self.client.post(url, data, format='multipart')
		admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
		self.authenticate_as(admin)
		self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'rejected', 'rejection_reason': 'Document needs correction.'},
			format='json',
		)

		self.authenticate_as(self.seller)
		response = self.client.post(url, {**data, 'business_name': 'Updated Studio Ltd'}, format='json')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['status'], StoreKYCStatus.PENDING)
		self.assertEqual(response.data['business_name'], 'Updated Studio Ltd')

	def test_seller_can_replace_document_when_resubmitting_rejected_kyc(self):
		store = self.create_store()
		url = reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']})
		initial_response = self.client.post(url, self.kyc_payload(), format='multipart')
		admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
		self.authenticate_as(admin)
		self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'rejected', 'rejection_reason': 'Document needs correction.'}, format='json',
		)

		self.authenticate_as(self.seller)
		old_document_name = StoreKYC.objects.get(store_id=store['id']).document.name
		replacement = self.kyc_payload('Updated Studio Ltd')
		replacement['document'] = SimpleUploadedFile('replacement.pdf', b'%PDF-1.4 replacement', content_type='application/pdf')
		response = self.client.post(url, replacement, format='multipart')

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertNotEqual(response.data['document'], initial_response.data['document'])
		self.assertFalse(default_storage.exists(old_document_name))

	def test_non_admin_cannot_review_kyc(self):
		store = self.create_store()
		self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(), format='multipart',
		)

		response = self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'approved'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_staff_user_cannot_review_kyc(self):
		store = self.create_store()
		self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(), format='multipart',
		)
		staff = User.objects.create_user(
			email='staff@example.com',
			password='StaffPassword123!',
			role=UserRole.BUYER,
			is_staff=True,
			is_verified=True,
		)
		self.authenticate_as(staff)

		response = self.client.post(
			reverse('store_management:store-kyc-review', kwargs={'slug': store['slug']}),
			{'decision': 'approved'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_seller_cannot_retrieve_another_sellers_kyc(self):
		store = self.create_store()
		self.client.post(
			reverse('store_management:store-kyc-submit', kwargs={'slug': store['slug']}),
			self.kyc_payload(), format='multipart',
		)
		other_seller = User.objects.create_user(
			email='other-seller@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_verified=True,
		)
		self.authenticate_as(other_seller)

		response = self.client.get(
			reverse('store_management:store-kyc-detail', kwargs={'slug': store['slug']}),
		)

		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
