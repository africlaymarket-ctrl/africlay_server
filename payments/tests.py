import base64
import json
from decimal import Decimal
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import UserRole
from authapp.utils import generate_access_token
from product_management.models import Product
from shopping.models import Cart, CartItem, Order, OrderStatus
from store_management.models import Store

from payments.gateways.mpesa import MpesaGateway
from payments.models import PaymentAttempt, Wallet, WalletTransaction
from payments.services import (
    WalletOperationError,
    capture_escrow_hold,
    credit_wallet,
    debit_wallet,
    place_escrow_hold,
    release_escrow_hold,
    transfer_escrow_to_wallet,
)

from .models import Payment, PaymentStatus


class MpesaGatewayTests(TestCase):
    @override_settings(
        MPESA_BASE_URL='https://sandbox.safaricom.co.ke',
        MPESA_CONSUMER_KEY='consumer-key',
        MPESA_CONSUMER_SECRET='consumer-secret',
        MPESA_SHORTCODE='174379',
        MPESA_PASSKEY='passkey',
        MPESA_CALLBACK_BASE_URL='https://www.africlaymarket.com',
        MPESA_CALLBACK_SECRET='callback-secret',
        MPESA_CALLBACK_URL='',
        MPESA_REQUEST_TIMEOUT=10,
    )
    @patch('payments.gateways.mpesa.requests.post')
    @patch('payments.gateways.mpesa.requests.get')
    def test_initiate_payment_uses_access_token_and_secret_callback_url(self, get, post):
        get.return_value.json.return_value = {'access_token': 'generated-token'}
        post.return_value.json.return_value = {
            'MerchantRequestID': 'merchant-id',
            'CheckoutRequestID': 'checkout-id',
        }

        result = MpesaGateway().initiate_payment(
            phone_number='254712345678',
            amount=Decimal('100.00'),
            account_reference='order-id',
        )

        self.assertEqual(result['CheckoutRequestID'], 'checkout-id')
        self.assertEqual(
            post.call_args.kwargs['headers']['Authorization'],
            'Bearer generated-token',
        )
        self.assertEqual(
            post.call_args.kwargs['json']['CallBackURL'],
            'https://www.africlaymarket.com/api/payments/mpesa/callback/?token=callback-secret',
        )


class WalletApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            email='wallet-user@example.com',
            password='StrongPassword123!',
        )

    def test_wallet_list_creates_default_currency_wallet(self):
        self.client.force_authenticate(self.user)

        response = self.client.get('/api/payments/wallets/')

        self.assertEqual(response.status_code, 200)
        wallet_data = response.data[0]
        self.assertEqual(wallet_data['currency'], 'KES')
        self.assertEqual(wallet_data['balance'], '0.00')
        self.assertEqual(wallet_data['held_balance'], '0.00')
        self.assertEqual(wallet_data['available_balance'], '0.00')
        self.assertFalse(wallet_data['is_frozen'])
        self.assertTrue(Wallet.objects.filter(user=self.user, currency='KES').exists())

    def test_wallet_list_requires_authentication(self):
        response = self.client.get('/api/payments/wallets/')

        self.assertEqual(response.status_code, 401)

    def test_user_can_create_wallet_in_another_currency(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            '/api/payments/wallets/',
            {'currency': 'UGX'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['currency'], 'UGX')

    def test_user_cannot_create_duplicate_currency_wallet(self):
        self.client.force_authenticate(self.user)

        self.client.post('/api/payments/wallets/', {'currency': 'KES'}, format='json')
        response = self.client.post(
            '/api/payments/wallets/',
            {'currency': 'KES'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_wallet_transaction_history_is_limited_to_owner(self):
        self.client.force_authenticate(self.user)
        wallet = Wallet.objects.create(user=self.user, currency='UGX')
        credit_wallet(wallet, Decimal('30.00'), 'provider:history-1')

        response = self.client.get(f'/api/payments/wallets/{wallet.pk}/transactions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['transaction_type'], 'credit')
        self.assertEqual(response.data['results'][0]['balance_after'], '30.00')

    def test_user_cannot_view_another_users_wallet_history(self):
        self.client.force_authenticate(self.user)
        other_user = get_user_model().objects.create_user(
            email='other-wallet-user@example.com',
            password='StrongPassword123!',
        )
        other_wallet = Wallet.objects.create(user=other_user, currency='KES')

        response = self.client.get(f'/api/payments/wallets/{other_wallet.pk}/transactions/')

        self.assertEqual(response.status_code, 404)


class WalletLedgerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='ledger-user@example.com',
            password='StrongPassword123!',
        )
        self.wallet = Wallet.objects.create(user=self.user, currency='KES')

    def test_ledger_tracks_credit_hold_release_and_capture(self):
        credit_wallet(self.wallet, Decimal('100.00'), 'provider:credit-1')
        place_escrow_hold(self.wallet, Decimal('60.00'), 'order:hold-1')
        release_escrow_hold(self.wallet, Decimal('20.00'), 'order:release-1')
        capture_escrow_hold(self.wallet, Decimal('40.00'), 'order:capture-1')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('60.00'))
        self.assertEqual(self.wallet.held_balance, Decimal('0.00'))
        self.assertEqual(self.wallet.available_balance, Decimal('60.00'))
        self.assertEqual(WalletTransaction.objects.filter(wallet=self.wallet).count(), 4)

    def test_credit_reference_is_idempotent(self):
        first = credit_wallet(self.wallet, Decimal('25.00'), 'provider:credit-2')
        repeated = credit_wallet(self.wallet, Decimal('25.00'), 'provider:credit-2')

        self.wallet.refresh_from_db()
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(self.wallet.balance, Decimal('25.00'))
        self.assertEqual(WalletTransaction.objects.filter(wallet=self.wallet).count(), 1)

    def test_reference_cannot_be_replayed_to_another_wallet(self):
        other_user = get_user_model().objects.create_user(
            email='other-ledger-user@example.com',
            password='StrongPassword123!',
        )
        other_wallet = Wallet.objects.create(user=other_user, currency='KES')
        credit_wallet(self.wallet, Decimal('25.00'), 'provider:global-reference')

        with self.assertRaises(WalletOperationError):
            credit_wallet(other_wallet, Decimal('25.00'), 'provider:global-reference')

        other_wallet.refresh_from_db()
        self.assertEqual(other_wallet.balance, Decimal('0.00'))

    def test_escrow_transfer_moves_funds_atomically_and_idempotently(self):
        seller = get_user_model().objects.create_user(
            email='earning-seller@example.com',
            password='StrongPassword123!',
        )
        seller_wallet = Wallet.objects.create(user=seller, currency='KES')
        credit_wallet(self.wallet, Decimal('100.00'), 'provider:transfer-test')
        place_escrow_hold(self.wallet, Decimal('70.00'), 'order:transfer-hold')

        first = transfer_escrow_to_wallet(
            self.wallet,
            seller_wallet,
            Decimal('70.00'),
            'order:transfer-settlement',
        )
        repeated = transfer_escrow_to_wallet(
            self.wallet,
            seller_wallet,
            Decimal('70.00'),
            'order:transfer-settlement',
        )

        self.wallet.refresh_from_db()
        seller_wallet.refresh_from_db()
        self.assertEqual(first[0].pk, repeated[0].pk)
        self.assertEqual(self.wallet.balance, Decimal('30.00'))
        self.assertEqual(self.wallet.held_balance, Decimal('0.00'))
        self.assertEqual(seller_wallet.balance, Decimal('70.00'))

    def test_debit_cannot_spend_held_funds(self):
        credit_wallet(self.wallet, Decimal('100.00'), 'provider:credit-3')
        place_escrow_hold(self.wallet, Decimal('80.00'), 'order:hold-2')

        with self.assertRaises(WalletOperationError):
            debit_wallet(self.wallet, Decimal('21.00'), 'order:debit-1')

    def test_frozen_wallet_rejects_debits(self):
        credit_wallet(self.wallet, Decimal('50.00'), 'provider:credit-4')
        self.wallet.is_frozen = True
        self.wallet.save(update_fields=['is_frozen', 'updated_at'])

        with self.assertRaises(WalletOperationError):
            debit_wallet(self.wallet, Decimal('1.00'), 'order:debit-2')


class MpesaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            email='mpesa-user@example.com',
            password='StrongPassword123!',
        )
        self.client.force_authenticate(self.user)

    def initiate_stk(self, idempotency_key='mpesa-stk-001'):
        gateway_response = {
            'ResponseCode': '0',
            'MerchantRequestID': 'merchant-request-1',
            'CheckoutRequestID': 'checkout-request-1',
            'CustomerMessage': 'Request accepted for processing',
        }
        with patch('payments.views.initiate_stk_push', return_value=gateway_response) as gateway:
            response = self.client.post(
                '/api/payments/mpesa/stk-push/',
                {'amount': '500.00', 'phone_number': '254712345678'},
                format='json',
                HTTP_IDEMPOTENCY_KEY=idempotency_key,
            )
        return response, gateway

    def test_stk_push_is_idempotent_and_does_not_credit_before_callback(self):
        first, gateway = self.initiate_stk()
        repeated, _ = self.initiate_stk()

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.data['status'], 'pending')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['id'], first.data['id'])
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.user, currency='KES').balance, Decimal('0.00'))

    def test_successful_callback_credits_wallet_exactly_once(self):
        initiation, gateway = self.initiate_stk()
        callback_token = gateway.call_args.args[1]
        callback = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'merchant-request-1',
                    'CheckoutRequestID': 'checkout-request-1',
                    'ResultCode': 0,
                    'ResultDesc': 'The service request is processed successfully.',
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 500},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'QWE1234567'},
                            {'Name': 'PhoneNumber', 'Value': 254712345678},
                        ],
                    },
                },
            },
        }

        first = self.client.post(
            f'/api/payments/mpesa/callback/{callback_token}/',
            callback,
            format='json',
        )
        repeated = self.client.post(
            f'/api/payments/mpesa/callback/{callback_token}/',
            callback,
            format='json',
        )

        self.assertEqual(initiation.status_code, 202)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user, currency='KES').balance, Decimal('500.00'))
        self.assertEqual(WalletTransaction.objects.filter(reference='mpesa:checkout-request-1').count(), 1)

    def test_callback_rejects_invalid_signature_when_certificate_is_configured(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        payload = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'merchant-request-1',
                    'CheckoutRequestID': 'checkout-request-1',
                    'ResultCode': 0,
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 500},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'QWE1234567'},
                            {'Name': 'PhoneNumber', 'Value': 254712345678},
                        ],
                    },
                },
            },
        }

        with override_settings(MPESA_PUBLIC_CERT=public_key):
            _, gateway = self.initiate_stk()
            callback_token = gateway.call_args.args[1]
            invalid_signature = base64.b64encode(b'not-a-valid-signature')
            response = self.client.post(
                f'/api/payments/mpesa/callback/{callback_token}/',
                payload,
                format='json',
                HTTP_X_MPESA_SIGNATURE=invalid_signature.decode(),
            )

            self.assertEqual(response.status_code, 403)
            self.assertEqual(Wallet.objects.get(user=self.user, currency='KES').balance, Decimal('0.00'))

    def test_callback_accepts_valid_signature_when_certificate_is_configured(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        payload = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'merchant-request-1',
                    'CheckoutRequestID': 'checkout-request-1',
                    'ResultCode': 0,
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 500},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'QWE1234567'},
                            {'Name': 'PhoneNumber', 'Value': 254712345678},
                        ],
                    },
                },
            },
        }
        signature_bytes = private_key.sign(
            json.dumps(payload, separators=(',', ':')).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        with override_settings(MPESA_PUBLIC_CERT=public_key):
            _, gateway = self.initiate_stk()
            callback_token = gateway.call_args.args[1]
            response = self.client.post(
                f'/api/payments/mpesa/callback/{callback_token}/',
                payload,
                format='json',
                HTTP_X_MPESA_SIGNATURE=base64.b64encode(signature_bytes).decode(),
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(Wallet.objects.get(user=self.user, currency='KES').balance, Decimal('500.00'))

    def test_callback_with_wrong_amount_does_not_credit_wallet(self):
        _, gateway = self.initiate_stk()
        callback_token = gateway.call_args.args[1]
        callback = {
            'Body': {
                'stkCallback': {
                    'MerchantRequestID': 'merchant-request-1',
                    'CheckoutRequestID': 'checkout-request-1',
                    'ResultCode': 0,
                    'CallbackMetadata': {
                        'Item': [
                            {'Name': 'Amount', 'Value': 10},
                            {'Name': 'MpesaReceiptNumber', 'Value': 'QWE1234567'},
                            {'Name': 'PhoneNumber', 'Value': 254712345678},
                        ],
                    },
                },
            },
        }

        response = self.client.post(
            f'/api/payments/mpesa/callback/{callback_token}/',
            callback,
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Wallet.objects.get(user=self.user, currency='KES').balance, Decimal('0.00'))


class PaymentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = get_user_model().objects.create_user(
            email='buyer@example.com', password='StrongPassword123!',
            role=UserRole.BUYER, is_verified=True,
        )
        seller = get_user_model().objects.create_user(
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
        self.order.items.create(
            product=product, quantity=2, price_at_purchase=product.price, seller=seller,
        )

    def authenticate(self):
        token = generate_access_token(self.buyer)
        self.client.credentials(HTTP_AUTHORIZATION=f'******')

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
        other_buyer = get_user_model().objects.create_user(
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
            phone_number='254712345678',
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
