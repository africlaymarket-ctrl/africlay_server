import hashlib
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from authapp.models import StaffRole, UserStaffRole
from payments.models import (
    LedgerEntry, LedgerEntryType, Payment, PaymentAttempt, PaymentAttemptStatus,
    PaymentStatus, Payout, PayoutAccount, PayoutStatus, Wallet, WalletBalanceSource,
)
from shopping.models import Order


class FinanceDashboardContractTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser('finance-root@example.com', 'StrongPassword123!')
        self.finance = user_model.objects.create_user(email='finance-worker@example.com', password='StrongPassword123!')
        self.buyer = user_model.objects.create_user(email='finance-buyer@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=self.finance, role=StaffRole.FINANCE, granted_by=self.admin)
        self.wallet = Wallet.objects.create(
            user=self.buyer, currency='KES', balance=Decimal('200.00'), held_balance=Decimal('70.00'),
            shopping_balance=Decimal('120.00'), shopping_held_balance=Decimal('20.00'),
            earnings_balance=Decimal('80.00'), earnings_held_balance=Decimal('50.00'),
        )
        self.order = Order.objects.create(
            buyer=self.buyer, total_amount=Decimal('120.00'), item_subtotal=Decimal('100.00'),
            shipping_address='Test Street', shipping_city='Nairobi', shipping_postal_code='00100',
            shipping_country='Kenya', shipping_cost=Decimal('20.00'),
        )
        for entry_type, amount in [
            (LedgerEntryType.CUSTOMER_PAYMENT, '120.00'),
            (LedgerEntryType.DELIVERY_FEE, '20.00'),
            (LedgerEntryType.COMMISSION_EARNED, '10.00'),
            (LedgerEntryType.SELLER_PROCEEDS_RELEASE, '90.00'),
            (LedgerEntryType.REFUND_COMPLETED, '5.00'),
        ]:
            LedgerEntry.objects.create(
                order=self.order, user=self.buyer, entry_type=entry_type,
                amount=Decimal(amount), reference=f'finance-contract:{entry_type}',
            )
        now = timezone.now()
        self.payment = Payment.objects.create(
            order=self.order, provider='mpesa', status=PaymentStatus.SUCCEEDED,
            amount=Decimal('120.00'), phone_number='254700000000', completed_at=now,
        )
        Payment.objects.filter(pk=self.payment.pk).update(created_at=now - timedelta(days=2))
        self.topup = PaymentAttempt.objects.create(
            user=self.buyer, wallet=self.wallet, amount=Decimal('50.00'), phone_number='254700000000',
            idempotency_key='finance-topup', callback_token_hash=hashlib.sha256(b'finance-topup').hexdigest(),
            status=PaymentAttemptStatus.SUCCEEDED,
        )
        self.account = PayoutAccount.objects.create(
            user=self.buyer, phone_number='254700000000', account_name='Buyer', confirmed_at=now,
        )
        self.paid = self.make_payout('finance-paid', PayoutStatus.SUCCEEDED, '30.00', processed_at=now)
        Payout.objects.filter(pk=self.paid.pk).update(created_at=now - timedelta(days=2))
        self.unknown = self.make_payout('finance-unknown', PayoutStatus.UNKNOWN, '40.00')
        Payout.objects.filter(pk=self.unknown.pk).update(created_at=now - timedelta(days=4))

    def make_payout(self, key, payout_status, amount, processed_at=None):
        return Payout.objects.create(
            wallet=self.wallet, payout_account=self.account, amount=Decimal(amount),
            balance_source=WalletBalanceSource.EARNINGS, status=payout_status,
            destination_phone=self.account.phone_number, destination_name=self.account.account_name,
            idempotency_key=key, callback_token_hash=hashlib.sha256(key.encode()).hexdigest(),
            originator_conversation_id=key, processed_at=processed_at,
        )

    def test_finance_summary_uses_real_separated_balances_and_no_delivery_double_count(self):
        self.client.force_authenticate(self.finance)
        response = self.client.get(reverse('payments:admin-finance-summary'))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['wallet_liability'], Decimal('200.00'))
        self.assertEqual(response.data['wallet_reserved'], Decimal('70.00'))
        self.assertEqual(response.data['wallet_breakdown'], {
            'shopping': Decimal('120.00'), 'earnings': Decimal('80.00'),
            'shopping_held': Decimal('20.00'), 'earnings_held': Decimal('50.00'),
        })
        self.assertEqual(response.data['customer_payments'], Decimal('120.00'))
        self.assertEqual(response.data['refunds_recorded'], Decimal('5.00'))
        self.assertEqual(response.data['order_mpesa_receipts'], Decimal('120.00'))
        self.assertEqual(response.data['wallet_topups_received'], Decimal('50.00'))
        self.assertEqual(response.data['payouts_completed'], Decimal('30.00'))
        self.assertEqual(response.data['payouts_pending'], Decimal('40.00'))
        self.assertEqual(response.data['payout_aging']['over_72h'], 1)
        self.assertEqual(response.data['payout_status_counts']['unknown'], 1)
        self.assertEqual(response.data['payment_status_counts']['succeeded'], 1)
        self.assertEqual(response.data['topup_status_counts']['succeeded'], 1)
        self.assertEqual(response.data['trend'][-1]['order_receipts'], Decimal('120.00'))
        self.assertEqual(response.data['trend'][-1]['wallet_topups'], Decimal('50.00'))
        self.assertEqual(response.data['trend'][-1]['b2c_payouts'], Decimal('30.00'))

    def test_completed_dates_drive_provider_totals_and_matching_drilldowns(self):
        self.client.force_authenticate(self.finance)
        today = timezone.localdate().isoformat()
        summary = self.client.get(reverse('payments:admin-finance-summary'), {'date_from': today, 'date_to': today})
        self.assertEqual(summary.status_code, status.HTTP_200_OK, summary.data)
        self.assertEqual(summary.data['order_mpesa_receipts'], Decimal('120.00'))
        self.assertEqual(summary.data['payouts_completed'], Decimal('30.00'))
        self.assertEqual(summary.data['payouts_pending'], Decimal('40.00'))
        self.assertEqual(summary.data['payment_status_counts']['succeeded'], 0)  # Created two days ago.

        receipts = self.client.get(reverse('payments:admin-payment-list'), {
            'status': 'succeeded', 'provider': 'mpesa', 'date_basis': 'completed', 'date_from': today,
        })
        payouts = self.client.get(reverse('payments:admin-payout-list'), {
            'status': 'succeeded', 'date_basis': 'processed', 'date_from': today,
        })
        active = self.client.get(reverse('payments:admin-payout-list'), {'status': 'active'})
        topups = self.client.get(reverse('payments:admin-topup-list'), {
            'status': 'succeeded', 'date_basis': 'confirmed', 'date_from': today,
        })
        self.assertEqual(receipts.data['count'], 1)
        self.assertEqual(payouts.data['count'], 1)
        self.assertEqual(active.data['count'], 1)
        self.assertEqual(active.data['results'][0]['status'], PayoutStatus.UNKNOWN)
        self.assertEqual(topups.data['count'], 1)
        self.assertEqual(topups.data['results'][0]['account_email'], self.buyer.email)
        self.assertNotEqual(topups.data['results'][0]['phone_number'], self.topup.phone_number)

    def test_finance_contract_is_not_public_or_available_to_marketplace_users(self):
        url = reverse('payments:admin-finance-summary')
        self.assertIn(self.client.get(url).status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})
        self.client.force_authenticate(self.buyer)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('payments:admin-payout-list')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('payments:admin-topup-list')).status_code, status.HTTP_403_FORBIDDEN)

    def test_active_payout_queue_puts_unknown_results_before_new_requests(self):
        self.make_payout('finance-new-request', PayoutStatus.REQUESTED, '10.00')
        self.client.force_authenticate(self.finance)
        response = self.client.get(reverse('payments:admin-payout-list'), {'status': 'active'})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual([item['status'] for item in response.data['results']], [
            PayoutStatus.UNKNOWN, PayoutStatus.REQUESTED,
        ])
