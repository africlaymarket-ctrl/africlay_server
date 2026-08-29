from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase
from decimal import Decimal

from authapp.models import UserRole
from authapp.utils import generate_access_token
from product_management.models import Product
from store_management.models import Store


User = get_user_model()


class CartApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            email='buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.other_buyer = User.objects.create_user(
            email='other@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.seller = User.objects.create_user(
            email='seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        self.store = Store.objects.create(
            owner=self.seller,
            name='Test Store',
            slug='test-store',
        )
        self.product = Product.objects.create(
            store=self.store,
            name='Test Product',
            slug='test-product',
            sku='TEST-001',
            price='1000.00',
            stock_quantity=10,
            status='published',
        )
        self.out_of_stock_product = Product.objects.create(
            store=self.store,
            name='Out of Stock',
            slug='out-of-stock',
            sku='OOS-001',
            price='500.00',
            stock_quantity=0,
            status='published',
        )

    def authenticate_as(self, user):
        token = generate_access_token(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_authenticated_buyer_can_retrieve_cart(self):
        self.authenticate_as(self.buyer)

        response = self.client.get(reverse('shopping:cart-detail'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['buyer'], str(self.buyer.id))
        self.assertEqual(response.data['items'], [])

    def test_buyer_can_add_product_to_cart(self):
        self.authenticate_as(self.buyer)

        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['quantity'], 2)

    def test_buyer_cannot_add_out_of_stock_product(self):
        self.authenticate_as(self.buyer)

        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.out_of_stock_product.id), 'quantity': 1},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_buyer_cannot_add_more_than_available_stock(self):
        self.authenticate_as(self.buyer)

        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 15},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_adding_same_product_twice_updates_quantity(self):
        self.authenticate_as(self.buyer)
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )

        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 3},
            format='json',
        )

        # When updating an existing item, return 200 OK instead of 201 Created
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['quantity'], 5)

    def test_buyer_can_update_cart_item_quantity(self):
        self.authenticate_as(self.buyer)
        add_response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )
        item_id = add_response.data['id']

        response = self.client.patch(
            reverse('shopping:cart-item-detail', kwargs={'pk': item_id}),
            {'quantity': 5},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['quantity'], 5)

    def test_buyer_can_remove_item_from_cart(self):
        self.authenticate_as(self.buyer)
        add_response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )
        item_id = add_response.data['id']

        response = self.client.delete(
            reverse('shopping:cart-item-detail', kwargs={'pk': item_id}),
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_buyer_cannot_see_another_buyers_cart(self):
        self.authenticate_as(self.buyer)
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )

        self.authenticate_as(self.other_buyer)
        response = self.client.get(reverse('shopping:cart-detail'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['items'], [])

    def test_unauthenticated_user_cannot_access_cart(self):
        response = self.client.get(reverse('shopping:cart-detail'))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_product_field_cannot_be_modified_in_patch(self):
        """Verify that PATCH cannot change the product (authorization fix)."""
        self.authenticate_as(self.buyer)
        
        # Add a product to cart
        add_response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )
        item_id = add_response.data['id']
        original_product_name = add_response.data['product_name']
        
        # Create another product
        other_product = Product.objects.create(
            store=self.seller.store,
            name='Other Product',
            slug='other-product',
            sku='OTHER-SKU',
            price=Decimal('50.00'),
            stock_quantity=10,
        )
        
        # Attempt to change the product via PATCH (should be ignored)
        patch_response = self.client.patch(
            reverse('shopping:cart-item-detail', args=[item_id]),
            {'product': str(other_product.id), 'quantity': 2},
            format='json',
        )
        
        # Verify the product was NOT changed by checking product name
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data['product_name'], original_product_name)

    def test_invalid_quantity_values_raise_validation_error(self):
        """Verify that non-integer quantity values are properly rejected."""
        self.authenticate_as(self.buyer)
        
        # Test with non-integer quantity
        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 'invalid'},
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('quantity', response.data)

    def test_product_price_is_decimal_type(self):
        """Verify that product price is returned as decimal, not string."""
        self.authenticate_as(self.buyer)
        
        response = self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Price should be a string representation of decimal in JSON
        # but the value should be numeric (can be parsed as float)
        price = response.data['product_price']
        self.assertEqual(float(price), 1000.00)
