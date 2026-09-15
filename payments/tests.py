from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase, override_settings

from authapp.models import UserRole
from authapp.utils import generate_access_token
from product_management.models import Product
from shopping.models import Cart, CartItem, Order, OrderStatus
from store_management.models import Store

from .models import Payment, PaymentStatus


User = get_user_model()


class PaymentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            email='buyer@example.com', password='StrongPassword123!',
            role=UserRole.BUYER, is_verified=True,
        )
        seller = User.objects.create_user(
            email='seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        store = Store.objects.create(owner=seller, name='Test Store', slug='test-store')
        product = Product.objects.create(
            store=store, name='Test Product', slug='test-product', sku='TEST-001',
            price=Decimal('100.00'), stock_quantity=10, status='published',
        )
        cart = Cart.objects.create(buyer=self.buyer)
        CartItem.objects.create(cart=cart, product=product, quantity=2)
        self.order = Order.objects.create(
            buyer=self.buyer, total_amount=Decimal('200.00'), currency='KES',
            status=OrderStatus.PENDING, shipping_address='123 Main St',
            shipping_city='Nairobi', shipping_postal_code='00100', shipping_country='Kenya',
        )
        self.order_item = self.order.items.create(
            product=product, quantity=2, price_at_purchase=product.price, seller=seller,
        )

    def authenticate(self):
        token = generate_access_token(self.buyer)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_buyer_can_initiate_payment_for_pending_order(self, initiate_payment):
        initiate_payment.return_value = {
            'MerchantRequestID': 'merchant-123',
            'CheckoutRequestID': 'checkout-123',
            'ResponseDescription': 'Accepted',
        }
        self.authenticate()

        response = self.client.post(
            reverse('payments:payment-initiate'),
            {'order_id': str(self.order.id), 'phone_number': '254712345678'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], PaymentStatus.PENDING)
        self.assertEqual(response.data['order_id'], str(self.order.id))
        self.assertTrue(Payment.objects.filter(order=self.order).exists())

    def test_buyer_cannot_initiate_payment_for_another_order(self):
        other_buyer = User.objects.create_user(
            email='other@example.com', password='StrongPassword123!',
            role=UserRole.BUYER, is_verified=True,
        )
        self.order.buyer = other_buyer
        self.order.save(update_fields=['buyer'])
        self.authenticate()

        response = self.client.post(
            reverse('payments:payment-initiate'),
            {'order_id': str(self.order.id), 'phone_number': '254712345678'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(MPESA_CALLBACK_SECRET='test-callback-secret')
    @patch('payments.services.PaymentService.process_mpesa_callback')
    def test_mpesa_callback_is_processed(self, process_callback):
        process_callback.return_value = Payment.objects.create(
            order=self.order, provider='mpesa', status=PaymentStatus.SUCCEEDED,
            amount=self.order.total_amount, currency='KES',
            checkout_request_id='checkout-123', receipt_number='receipt-123',
        )
        callback = {
            'Body': {'stkCallback': {
                'MerchantRequestID': 'merchant-123', 'CheckoutRequestID': 'checkout-123',
                'ResultCode': 0, 'ResultDesc': 'The service request is processed successfully.',
                'CallbackMetadata': {'Item': [
                    {'Name': 'Amount', 'Value': 200},
                    {'Name': 'MpesaReceiptNumber', 'Value': 'receipt-123'},
                    {'Name': 'PhoneNumber', 'Value': 254712345678},
                ]},
            }},
        }

        response = self.client.post(
            f"{reverse('payments:mpesa-callback')}?token=test-callback-secret",
            callback,
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        process_callback.assert_called_once()

    def test_payment_status_cannot_be_initiated_for_paid_order(self):
        self.order.status = OrderStatus.PROCESSING
        self.order.save(update_fields=['status'])
        self.authenticate()

        response = self.client.post(
            reverse('payments:payment-initiate'),
            {'order_id': str(self.order.id), 'phone_number': '254712345678'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_malformed_provider_response_fails_payment(self, initiate_payment):
        initiate_payment.return_value = []
        self.authenticate()

        response = self.client.post(
            reverse('payments:payment-initiate'),
            {'order_id': str(self.order.id), 'phone_number': '254712345678'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.get(order=self.order).status, PaymentStatus.FAILED)

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_buyer_can_retrieve_payment_status(self, initiate_payment):
        initiate_payment.return_value = {
            'MerchantRequestID': 'merchant-123',
            'CheckoutRequestID': 'checkout-123',
        }
        self.authenticate()
        initiate_response = self.client.post(
            reverse('payments:payment-initiate'),
            {'order_id': str(self.order.id), 'phone_number': '254712345678'},
            format='json',
        )

        response = self.client.get(
            reverse('payments:payment-detail', args=[initiate_response.data['id']]),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['order_id'], str(self.order.id))
