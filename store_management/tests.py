from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from django.test import TestCase

from authapp.models import UserRole
from authapp.utils import generate_access_token


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

		response = self.client.post(reverse('store_management:store-list-create'), self.store_data, format='json')

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['name'], self.store_data['name'])
		self.assertEqual(response.data['slug'], self.store_data['slug'])
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
