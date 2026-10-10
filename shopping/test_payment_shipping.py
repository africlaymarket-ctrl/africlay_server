from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import UserRole
from payments.models import LedgerEntry, LedgerEntryType, Payment, PaymentPurpose, Wallet
from payments.services import PaymentService
from product_management.models import Product
from store_management.models import Store

from .models import Cart, CartItem, Order, PaymentStatus, SettlementStatus
from .shipping import quote_cart_items


class ShippingTariffTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            email='tariff-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        self.buyer = User.objects.create_user(
            email='tariff-buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.store = Store.objects.create(
            owner=self.seller,
            name='Nairobi Store',
            slug='nairobi-store',
            city='Nairobi',
            country='Kenya',
        )
        self.product = Product.objects.create(
            store=self.store,
            name='Six kilogram parcel',
            slug='six-kilogram-parcel',
            sku='SHIP-006',
            price=Decimal('1000.00'),
            stock_quantity=5,
            weight_kg=Decimal('6.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        self.cart = Cart.objects.create(buyer=self.buyer)
        self.item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

    def test_standard_rate_uses_base_and_extra_kilogram(self):
        quote = quote_cart_items([self.item], 'Nairobi')[0]

        self.assertEqual(quote.chargeable_weight_kg, Decimal('6.000'))
        self.assertEqual(quote.shipping_cost, Decimal('250.00'))
        self.assertEqual(quote.commission_amount, Decimal('100.00'))
        self.assertEqual(quote.seller_proceeds, Decimal('900.00'))

    def test_remote_destination_uses_remote_tariff(self):
        quote = quote_cart_items([self.item], 'Lodwar')[0]

        self.assertTrue(quote.is_remote)
        self.assertEqual(quote.shipping_cost, Decimal('1860.00'))


@override_settings(
    MPESA_ENVIRONMENT='sandbox',
    MPESA_CONSUMER_KEY='consumer-key',
    MPESA_CONSUMER_SECRET='consumer-secret',
    MPESA_SHORTCODE='174379',
    MPESA_PASSKEY='passkey',
    MPESA_CALLBACK_BASE_URL='https://api.africlaymarket.com',
)
class PayOnDeliveryFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.buyer = User.objects.create_user(
            email='pod-buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.seller = User.objects.create_user(
            email='pod-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        self.store = Store.objects.create(
            owner=self.seller,
            name='POD Store',
            slug='pod-store',
            city='Nairobi',
            country='Kenya',
        )
        self.product = Product.objects.create(
            store=self.store,
            name='POD Product',
            slug='pod-product',
            sku='POD-001',
            price=Decimal('1000.00'),
            stock_quantity=2,
            weight_kg=Decimal('1.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        cart = Cart.objects.create(buyer=self.buyer)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)

    @staticmethod
    def callback(payment, receipt):
        return {
            'Body': {'stkCallback': {
                'MerchantRequestID': payment.merchant_request_id,
                'CheckoutRequestID': payment.checkout_request_id,
                'ResultCode': 0,
                'ResultDesc': 'Processed successfully.',
                'CallbackMetadata': {'Item': [
                    {'Name': 'Amount', 'Value': int(payment.amount)},
                    {'Name': 'MpesaReceiptNumber', 'Value': receipt},
                    {'Name': 'PhoneNumber', 'Value': int(payment.phone_number)},
                ]},
            }},
        }

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_delivery_fee_balance_and_seller_release_are_audited(self, initiate_payment):
        initiate_payment.side_effect = [
            {'MerchantRequestID': 'merchant-delivery', 'CheckoutRequestID': 'checkout-delivery'},
            {'MerchantRequestID': 'merchant-balance', 'CheckoutRequestID': 'checkout-balance'},
        ]
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
            'payment_method': 'mpesa',
            'payment_option': 'pay_on_delivery',
        }, format='json')
        self.assertEqual(checkout.status_code, status.HTTP_201_CREATED, checkout.data)
        self.assertEqual(Decimal(checkout.data['shipping_cost']), Decimal('220.00'))
        self.assertEqual(Decimal(checkout.data['total_amount']), Decimal('1220.00'))
        order = Order.objects.get(pk=checkout.data['id'])

        initial = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
        }, format='json')
        self.assertEqual(initial.status_code, status.HTTP_201_CREATED, initial.data)
        delivery_payment = Payment.objects.get(pk=initial.data['id'])
        self.assertEqual(delivery_payment.purpose, PaymentPurpose.DELIVERY_FEE)
        self.assertEqual(delivery_payment.amount, Decimal('220.00'))
        PaymentService.process_mpesa_callback(self.callback(delivery_payment, 'DELIVERY220'))

        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PARTIAL)
        self.assertTrue(order.delivery_fee_paid)
        self.assertEqual(order.amount_paid, Decimal('220.00'))

        self.client.force_authenticate(self.seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped',
            'courier_name': 'Speedaf',
            'tracking_number': 'SPD-1001',
        }, format='json')
        self.assertEqual(shipped.status_code, status.HTTP_200_OK, shipped.data)

        self.client.force_authenticate(self.buyer)
        balance = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
            'purpose': 'delivery_balance',
        }, format='json')
        self.assertEqual(balance.status_code, status.HTTP_201_CREATED, balance.data)
        balance_payment = Payment.objects.get(pk=balance.data['id'])
        self.assertEqual(balance_payment.amount, Decimal('1000.00'))
        PaymentService.process_mpesa_callback(self.callback(balance_payment, 'BALANCE1000'))

        seller_wallet = Wallet.objects.get(user=self.seller, currency='KES')
        self.assertEqual(seller_wallet.balance, Decimal('900.00'))
        self.assertEqual(seller_wallet.held_balance, Decimal('900.00'))

        delivered = self.client.post(
            reverse('shopping:order-confirm-delivery', args=[order.pk]),
            format='json',
        )
        self.assertEqual(delivered.status_code, status.HTTP_200_OK, delivered.data)
        seller_wallet.refresh_from_db()
        self.assertEqual(seller_wallet.balance, Decimal('900.00'))
        self.assertEqual(seller_wallet.held_balance, Decimal('0.00'))
        self.assertEqual(order.fulfillments.get().settlement_status, SettlementStatus.RELEASED)
        self.assertTrue(LedgerEntry.objects.filter(
            order=order,
            entry_type=LedgerEntryType.COMMISSION_EARNED,
            amount=Decimal('100.00'),
        ).exists())
        self.assertTrue(LedgerEntry.objects.filter(
            order=order,
            entry_type=LedgerEntryType.SELLER_PROCEEDS_RELEASE,
            amount=Decimal('900.00'),
        ).exists())

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_cancelled_seller_segment_is_removed_from_delivery_balance(self, initiate_payment):
        initiate_payment.side_effect = [
            {'MerchantRequestID': 'merchant-delivery', 'CheckoutRequestID': 'checkout-delivery'},
            {'MerchantRequestID': 'merchant-balance', 'CheckoutRequestID': 'checkout-balance'},
        ]
        User = get_user_model()
        second_seller = User.objects.create_user(
            email='pod-second-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        second_store = Store.objects.create(
            owner=second_seller,
            name='Second POD Store',
            slug='second-pod-store',
            city='Nairobi',
            country='Kenya',
        )
        second_product = Product.objects.create(
            store=second_store,
            name='Second POD Product',
            slug='second-pod-product',
            sku='POD-002',
            price=Decimal('500.00'),
            stock_quantity=2,
            weight_kg=Decimal('1.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        CartItem.objects.create(cart=self.buyer.cart, product=second_product, quantity=1)

        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
            'payment_method': 'mpesa',
            'payment_option': 'pay_on_delivery',
        }, format='json')
        order = Order.objects.get(pk=checkout.data['id'])
        initial = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
        }, format='json')
        delivery_payment = Payment.objects.get(pk=initial.data['id'])
        PaymentService.process_mpesa_callback(self.callback(delivery_payment, 'MULTIPOD440'))

        self.client.force_authenticate(self.seller)
        cancelled = self.client.patch(
            reverse('shopping:seller-order-status', args=[order.pk]),
            {'status': 'cancelled'},
            format='json',
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK, cancelled.data)

        order.refresh_from_db()
        self.assertEqual(order.item_subtotal, Decimal('500.00'))
        self.assertEqual(order.shipping_cost, Decimal('220.00'))
        self.assertEqual(order.total_amount, Decimal('720.00'))
        self.assertEqual(order.amount_paid, Decimal('220.00'))
        self.assertEqual(order.amount_due, Decimal('500.00'))

        self.client.force_authenticate(second_seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped',
            'courier_name': 'Speedaf',
            'tracking_number': 'SPD-2002',
        }, format='json')
        self.assertEqual(shipped.status_code, status.HTTP_200_OK, shipped.data)

        self.client.force_authenticate(self.buyer)
        balance = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
            'purpose': 'delivery_balance',
        }, format='json')
        self.assertEqual(balance.status_code, status.HTTP_201_CREATED, balance.data)
        self.assertEqual(Payment.objects.get(pk=balance.data['id']).amount, Decimal('500.00'))
