import base64
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from rest_framework.test import APIClient

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
            signed_body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
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