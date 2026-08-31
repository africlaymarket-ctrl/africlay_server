from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase

from authapp.models import UserRole
from authapp.utils import generate_access_token
from store_management.models import Store


User = get_user_model()


class ProductApiTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.seller = User.objects.create_user(
			email='seller@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_verified=True,
		)
		self.other_seller = User.objects.create_user(
			email='other@example.com',
			password='StrongPassword123!',
			role=UserRole.SELLER,
			is_verified=True,
		)
		self.store = Store.objects.create(
			owner=self.seller,
			name='Nairobi Clay Studio',
			slug='nairobi-clay-studio',
		)
		self.other_store = Store.objects.create(
			owner=self.other_seller,
			name='Mombasa Ceramics',
			slug='mombasa-ceramics',
		)
		self.product_data = {
			'name': 'Hand-thrown Mug',
			'slug': 'hand-thrown-mug',
			'description': 'A locally made ceramic mug.',
			'sku': 'MUG-001',
			'price': '1800.00',
			'currency': 'KES',
			'stock_quantity': 12,
		}

	def authenticate_as(self, user):
		token = generate_access_token(user)
		self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

	def test_verified_seller_can_create_product_for_owned_store(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			self.product_data,
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['store'], str(self.store.id))
		self.assertEqual(response.data['price'], '1800.00')

	def test_seller_cannot_create_product_for_another_store(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'store': str(self.other_store.id)},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['store'], str(self.store.id))

	def test_public_catalogue_hides_drafts(self):
		self.authenticate_as(self.seller)
		create_response = self.client.post(
			reverse('product_management:product-manage-list'),
			self.product_data,
			format='json',
		)
		self.client.credentials()

		response = self.client.get(reverse('product_management:product-list'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data, [])
		self.assertEqual(create_response.data['status'], 'draft')

	def test_public_catalogue_returns_published_product(self):
		self.authenticate_as(self.seller)
		create_response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'},
			format='json',
		)
		self.client.credentials()

		response = self.client.get(reverse('product_management:product-list'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]['slug'], create_response.data['slug'])

	def test_public_catalogue_can_retrieve_product_by_slug(self):
		self.authenticate_as(self.seller)
		self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'},
			format='json',
		)
		self.client.credentials()

		response = self.client.get(
			reverse('product_management:product-detail', kwargs={'slug': self.product_data['slug']}),
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['sku'], self.product_data['sku'])

	def test_product_rejects_negative_price_and_stock(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'price': '-1.00', 'stock_quantity': -2},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
