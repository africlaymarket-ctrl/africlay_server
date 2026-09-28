from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase
from decimal import Decimal

from authapp.models import UserRole
from authapp.utils import generate_access_token
from product_management.models import Product
from shopping.models import Order, OrderStatus
from store_management.models import Store
from payments.models import Wallet, WalletTransaction
from payments.services import credit_wallet
from notifications.models import Notification, NotificationType


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


class CheckoutApiTests(TestCase):
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
        self.product2 = Product.objects.create(
            store=self.store,
            name='Test Product 2',
            slug='test-product-2',
            sku='TEST-002',
            price='500.00',
            stock_quantity=5,
            status='published',
        )

    def authenticate_as(self, user):
        token = generate_access_token(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_successful_checkout_creates_order(self):
        """Test that successful checkout creates an order and clears cart."""
        self.authenticate_as(self.buyer)
        
        # Add products to cart
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product2.id), 'quantity': 1},
            format='json',
        )
        
        # Checkout
        checkout_response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        # Verify order created with correct total: (1000*2) + (500*1) = 2500
        self.assertEqual(checkout_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(checkout_response.data['status'], 'pending')
        self.assertEqual(float(checkout_response.data['total_amount']), 2500.00)
        self.assertEqual(len(checkout_response.data['items']), 2)
        
        # Verify cart is now empty
        cart_response = self.client.get(reverse('shopping:cart-detail'))
        self.assertEqual(len(cart_response.data['items']), 0)

    def test_wallet_checkout_places_funds_on_hold(self):
        self.authenticate_as(self.buyer)
        wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(wallet, Decimal('1500.00'), 'provider:wallet-checkout')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )

        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'processing')
        self.assertEqual(response.data['payment_status'], 'paid')
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal('1500.00'))
        self.assertEqual(wallet.held_balance, Decimal('1000.00'))
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=wallet, transaction_type='escrow_hold').count(),
            1,
        )

    def test_checkout_creates_order_notification_for_buyer(self):
        self.authenticate_as(self.buyer)
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )

        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Notification.objects.filter(
                user=self.buyer,
                notification_type=NotificationType.ORDER,
            ).exists()
        )

    def test_wallet_checkout_with_insufficient_balance_rolls_back(self):
        self.authenticate_as(self.buyer)
        wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )

        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.buyer.orders.exists())
        self.product.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)
        self.assertEqual(wallet.balance, Decimal('0.00'))
        self.assertTrue(self.buyer.cart.items.exists())

    def test_buyer_confirms_delivery_and_settles_seller_earnings(self):
        self.authenticate_as(self.buyer)
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('1200.00'), 'provider:delivery-settlement')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        checkout = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )
        order_id = checkout.data['id']

        self.authenticate_as(self.seller)
        shipped = self.client.patch(
            reverse('shopping:seller-order-status', args=[order_id]),
            {
                'status': 'shipped',
                'courier_name': 'Sendy',
                'tracking_number': 'SNDY-DELIVERED-1',
                'shipping_cost': '150.00',
            },
            format='json',
        )
        self.assertEqual(shipped.status_code, status.HTTP_200_OK)

        self.authenticate_as(self.buyer)
        delivered = self.client.post(
            reverse('shopping:order-confirm-delivery', args=[order_id]),
            format='json',
        )
        self.assertEqual(delivered.status_code, status.HTTP_200_OK)
        self.assertEqual(delivered.data['status'], 'delivered')
        self.assertEqual(delivered.data['payment_status'], 'paid')

        repeated = self.client.post(
            reverse('shopping:order-confirm-delivery', args=[order_id]),
            format='json',
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        buyer_wallet.refresh_from_db()
        seller_wallet = Wallet.objects.get(user=self.seller, currency='KES')
        self.assertEqual(buyer_wallet.balance, Decimal('200.00'))
        self.assertEqual(buyer_wallet.held_balance, Decimal('0.00'))
        self.assertEqual(seller_wallet.balance, Decimal('1000.00'))
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=seller_wallet, transaction_type='seller_earning').count(),
            1,
        )

    def test_seller_cancellation_releases_escrow_and_restores_stock(self):
        self.authenticate_as(self.buyer)
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('1000.00'), 'provider:seller-cancellation')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        checkout = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.authenticate_as(self.seller)
        response = self.client.patch(
            reverse('shopping:seller-order-status', args=[checkout.data['id']]),
            {'status': 'cancelled'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        self.assertEqual(self.buyer.orders.get().payment_status, 'refunded')
        buyer_wallet.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(buyer_wallet.balance, Decimal('1000.00'))
        self.assertEqual(buyer_wallet.held_balance, Decimal('0.00'))
        self.assertEqual(self.product.stock_quantity, 10)

    def test_seller_must_attach_tracking_before_marking_order_shipped(self):
        self.authenticate_as(self.buyer)
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('2000.00'), 'provider:shipping-validation')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        checkout = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.authenticate_as(self.seller)
        response = self.client.patch(
            reverse('shopping:seller-order-status', args=[checkout.data['id']]),
            {'status': 'shipped'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('shipping', response.data)

    def test_seller_can_record_courier_tracking_when_shipping(self):
        self.authenticate_as(self.buyer)
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('2000.00'), 'provider:shipping-tracking')
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        checkout = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'wallet',
            },
            format='json',
        )

        self.authenticate_as(self.seller)
        response = self.client.patch(
            reverse('shopping:seller-order-status', args=[checkout.data['id']]),
            {
                'status': 'shipped',
                'courier_name': 'Sendy',
                'tracking_number': 'SNDY-123456',
                'shipping_cost': '150.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'shipped')
        self.assertEqual(response.data['courier_name'], 'Sendy')
        self.assertEqual(response.data['tracking_number'], 'SNDY-123456')
        self.assertEqual(float(response.data['shipping_cost']), 150.00)

    def test_checkout_decrements_product_stock(self):
        """Test that checkout properly decrements product stock."""
        self.authenticate_as(self.buyer)
        
        initial_stock = self.product.stock_quantity
        
        # Add product to cart and checkout
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 3},
            format='json',
        )
        
        self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        # Verify stock was decremented
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, initial_stock - 3)

    def test_checkout_with_empty_cart_fails(self):
        """Test that checkout with empty cart returns error."""
        self.authenticate_as(self.buyer)
        
        # Attempt checkout without adding items to cart
        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cart', response.data)

    def test_checkout_with_insufficient_stock_fails(self):
        """Test that checkout fails if product has insufficient stock between add and checkout."""
        self.authenticate_as(self.buyer)
        
        # Add products to cart (5 out of 10 available)
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 5},
            format='json',
        )
        
        # Manually reduce stock before checkout to simulate another buyer purchasing
        self.product.stock_quantity = 3
        self.product.save()
        
        # Attempt checkout with insufficient stock (need 5, only 3 available)
        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('product', response.data)

    def test_checkout_without_shipping_address_fails(self):
        """Test that checkout requires all shipping fields."""
        self.authenticate_as(self.buyer)
        
        # Add product to cart
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        
        # Attempt checkout without address
        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('shipping_address', response.data)

    def test_unauthenticated_user_cannot_checkout(self):
        """Test that unauthenticated users cannot checkout."""
        response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_order_details(self):
        """Test retrieving order details."""
        self.authenticate_as(self.buyer)
        
        # Create an order
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2},
            format='json',
        )
        
        checkout_response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        order_id = checkout_response.data['id']
        
        # Retrieve order details
        response = self.client.get(reverse('shopping:order-detail', args=[order_id]))
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], order_id)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(len(response.data['items']), 1)

    def test_list_buyer_orders(self):
        """Test listing orders for authenticated buyer."""
        self.authenticate_as(self.buyer)
        
        # Create multiple orders
        for i in range(2):
            self.client.post(
                reverse('shopping:cart-item-list'),
                {'product': str(self.product.id), 'quantity': 1},
                format='json',
            )
            
            self.client.post(
                reverse('shopping:checkout'),
                {
                    'shipping_address': f'{i} Test St',
                    'shipping_city': 'Nairobi',
                    'shipping_postal_code': '00100',
                    'shipping_country': 'Kenya',
                },
                format='json',
            )
        
        # List orders
        response = self.client.get(reverse('shopping:order-list'))
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_buyer_cannot_see_other_buyers_orders(self):
        """Test that buyers cannot view other buyers' orders."""
        # Create order as first buyer
        self.authenticate_as(self.buyer)
        
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        
        checkout_response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        order_id = checkout_response.data['id']
        
        # Switch to other buyer
        self.authenticate_as(self.other_buyer)
        
        # Try to retrieve first buyer's order
        response = self.client.get(reverse('shopping:order-detail', args=[order_id]))
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_checkout_order_items_have_seller_info(self):
        """Test that order items capture seller information."""
        self.authenticate_as(self.buyer)
        
        # Add product to cart and checkout
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 1},
            format='json',
        )
        
        checkout_response = self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
            },
            format='json',
        )
        
        # Verify order items include seller info
        items = checkout_response.data['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['seller_id'], str(self.seller.id))
        self.assertEqual(float(items[0]['price_at_purchase']), 1000.00)


class SellerOrderApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            email='buyer@example.com', password='StrongPassword123!',
            role=UserRole.BUYER, is_verified=True,
        )
        self.seller = User.objects.create_user(
            email='seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        self.other_seller = User.objects.create_user(
            email='other-seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        self.store = Store.objects.create(owner=self.seller, name='Seller Store', slug='seller-store')
        self.other_store = Store.objects.create(owner=self.other_seller, name='Other Store', slug='other-store')

        self.product = Product.objects.create(
            store=self.store, name='Seller Product', slug='seller-product', sku='SELLER-001',
            price='100.00', stock_quantity=10, status='published',
        )
        self.other_product = Product.objects.create(
            store=self.other_store, name='Other Product', slug='other-product', sku='OTHER-001',
            price='50.00', stock_quantity=10, status='published',
        )

    def authenticate_as(self, user):
        token = generate_access_token(user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def create_order(self):
        self.authenticate_as(self.buyer)
        self.client.post(
            reverse('shopping:cart-item-list'),
            {'product': str(self.product.id), 'quantity': 2}, format='json',
        )
        self.client.post(
            reverse('shopping:checkout'),
            {
                'shipping_address': '123 Main St', 'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100', 'shipping_country': 'Kenya',
            }, format='json',
        )
        return self.client.get(reverse('shopping:order-list')).data[0]['id']

    def test_seller_can_list_orders_containing_their_products(self):
        order_id = self.create_order()
        self.authenticate_as(self.seller)

        response = self.client.get(reverse('shopping:seller-order-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([order['id'] for order in response.data], [order_id])
        self.assertEqual(len(response.data[0]['items']), 1)

    def test_seller_cannot_view_orders_without_their_products(self):
        order_id = self.create_order()
        self.authenticate_as(self.other_seller)

        list_response = self.client.get(reverse('shopping:seller-order-list'))
        detail_response = self.client.get(reverse('shopping:seller-order-detail', args=[order_id]))

        self.assertEqual(list_response.data, [])
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_seller_can_advance_order_status(self):
        order_id = self.create_order()
        self.authenticate_as(self.seller)

        response = self.client.patch(
            reverse('shopping:seller-order-status', args=[order_id]),
            {'status': 'shipped'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        Order.objects.filter(id=order_id).update(status=OrderStatus.PROCESSING)
        response = self.client.patch(
            reverse('shopping:seller-order-status', args=[order_id]),
            {'status': 'shipped'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'shipped')
