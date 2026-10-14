import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from authapp.models import (
    AdminAuditLog,
    DashboardAccessRequest,
    DashboardAccessRequestStatus,
    StaffRole,
    UserRole,
    UserStaffRole,
)
from authapp.utils import generate_access_token
from payments.models import LedgerEntry, LedgerEntryType
from shopping.models import Order
from store_management.models import Store, StoreKYC, StoreKYCStatus, StoreStatus


User = get_user_model()


class OperationsPermissionTests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_superuser('root@example.com', 'StrongPassword123!')
        self.sales = User.objects.create_user(email='sales@example.com', password='StrongPassword123!', is_verified=True)
        self.finance = User.objects.create_user(email='finance@example.com', password='StrongPassword123!', is_verified=True)
        self.regular = User.objects.create_user(email='member@example.com', password='StrongPassword123!', is_verified=True)
        self.legacy_admin = User.objects.create_user(
            email='legacy-admin@example.com', password='StrongPassword123!', role=UserRole.ADMIN,
            is_staff=True, is_verified=True,
        )
        UserStaffRole.objects.create(user=self.sales, role=StaffRole.SALES_REVIEWER, granted_by=self.super_admin)
        UserStaffRole.objects.create(user=self.finance, role=StaffRole.FINANCE, granted_by=self.super_admin)
        self.seller = User.objects.create_user(
            email='seller@example.com', password='StrongPassword123!', role=UserRole.SELLER, is_verified=True,
        )
        self.store = Store.objects.create(owner=self.seller, name='Seller Studio', slug='seller-studio', city='Nairobi')
        self.kyc = StoreKYC.objects.create(
            store=self.store,
            business_name='Seller Studio Ltd',
            business_registration_number='REG-100',
            tax_identification_number='PIN-100',
            document_type='national_id',
            document='kyc/test.png',
            status=StoreKYCStatus.PENDING,
            submitted_at=timezone.now(),
        )
        self.order = Order.objects.create(
            buyer=self.regular,
            total_amount=Decimal('100.00'),
            item_subtotal=Decimal('100.00'),
            shipping_address='Test Street',
            shipping_city='Nairobi',
            shipping_postal_code='00100',
            shipping_country='Kenya',
        )
        self.ledger = LedgerEntry.objects.create(
            order=self.order,
            user=self.seller,
            entry_type=LedgerEntryType.COMMISSION_EARNED,
            amount=Decimal('10.00'),
            reference='commission:test-order',
        )

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def test_sales_can_review_kyc_but_cannot_access_finance_or_administration(self):
        self.authenticate(self.sales)
        queue = self.client.get(reverse('store_management:store-kyc-review-queue'))
        self.assertEqual(queue.status_code, status.HTTP_200_OK)
        self.assertEqual(queue.data['results'][0]['owner_email'], self.seller.email)
        review = self.client.post(
            reverse('store_management:store-kyc-review', kwargs={'slug': self.store.slug}),
            {'decision': 'rejected', 'rejection_reason': 'Document is unreadable.'},
            format='json',
        )
        self.assertEqual(review.status_code, status.HTTP_200_OK, review.data)
        self.assertTrue(AdminAuditLog.objects.filter(action='kyc_rejected', actor=self.sales).exists())
        self.assertEqual(self.client.get(reverse('payments:admin-finance-summary')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('authapp:admin-user-list')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('store_management:admin-store-list')).status_code, status.HTTP_403_FORBIDDEN)

    def test_finance_can_read_finance_only_and_cannot_reconcile_payouts(self):
        self.authenticate(self.finance)
        self.assertEqual(self.client.get(reverse('payments:admin-finance-summary')).status_code, status.HTTP_200_OK)
        transactions = self.client.get(reverse('payments:finance-transaction-list'))
        self.assertEqual(transactions.status_code, status.HTTP_200_OK)
        self.assertEqual(transactions.data['results'][0]['reference'], self.ledger.reference)
        self.assertEqual(self.client.get(reverse('store_management:store-kyc-review-queue')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('authapp:admin-user-list')).status_code, status.HTTP_403_FORBIDDEN)
        resolve = self.client.post(
            reverse('payments:admin-payout-resolve', kwargs={'pk': uuid.uuid4()}),
            {'resolution': 'succeeded', 'transaction_receipt': 'ABC123', 'note': 'Verified.'},
            format='json',
        )
        self.assertEqual(resolve.status_code, status.HTTP_403_FORBIDDEN)

    def test_legacy_admin_role_has_no_implicit_operations_privileges(self):
        self.authenticate(self.legacy_admin)
        self.assertEqual(self.client.get(reverse('store_management:store-kyc-review-queue')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('payments:admin-finance-summary')).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(reverse('authapp:admin-user-list')).status_code, status.HTTP_403_FORBIDDEN)

    def test_only_super_admin_can_grant_roles_and_old_tokens_are_invalidated(self):
        token = generate_access_token(self.sales)
        token_client = APIClient()
        token_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(token_client.get(reverse('authapp:user-me')).status_code, status.HTTP_200_OK)

        self.authenticate(self.super_admin)
        revoke = self.client.post(
            reverse('authapp:admin-user-staff-role', kwargs={'pk': self.sales.pk}),
            {'role': StaffRole.SALES_REVIEWER, 'action': 'revoke', 'reason': 'Assignment ended.'},
            format='json',
        )
        self.assertEqual(revoke.status_code, status.HTTP_200_OK, revoke.data)
        self.assertFalse(UserStaffRole.objects.filter(user=self.sales, role=StaffRole.SALES_REVIEWER).exists())
        self.assertTrue(AdminAuditLog.objects.filter(action='staff_role_revoked', target_id=str(self.sales.pk)).exists())
        self.assertEqual(token_client.get(reverse('authapp:user-me')).status_code, status.HTTP_401_UNAUTHORIZED)

        escalation = self.client.post(
            reverse('authapp:admin-user-staff-role', kwargs={'pk': self.regular.pk}),
            {'role': 'super_admin', 'action': 'grant', 'reason': 'Not permitted.'},
            format='json',
        )
        self.assertEqual(escalation.status_code, status.HTTP_400_BAD_REQUEST)

    def test_account_and_store_status_changes_require_reason_and_confirmation(self):
        self.authenticate(self.super_admin)
        account_url = reverse('authapp:admin-user-status', kwargs={'pk': self.regular.pk})
        self.assertEqual(self.client.post(account_url, {'action': 'suspend', 'reason': '', 'confirm': True}, format='json').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.post(account_url, {'action': 'suspend', 'reason': 'Security review.', 'confirm': False}, format='json').status_code, status.HTTP_400_BAD_REQUEST)
        suspended = self.client.post(account_url, {'action': 'suspend', 'reason': 'Security review.', 'confirm': True}, format='json')
        self.assertEqual(suspended.status_code, status.HTTP_200_OK)
        self.regular.refresh_from_db()
        self.assertFalse(self.regular.is_active)
        self.assertTrue(AdminAuditLog.objects.filter(action='account_suspended', target_id=str(self.regular.pk)).exists())

        store_url = reverse('store_management:admin-store-status', kwargs={'pk': self.store.pk})
        store_response = self.client.post(store_url, {'action': 'suspend', 'reason': 'Compliance hold.', 'confirm': True}, format='json')
        self.assertEqual(store_response.status_code, status.HTTP_200_OK)
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, StoreStatus.SUSPENDED)
        self.assertTrue(AdminAuditLog.objects.filter(action='store_suspended', target_id=str(self.store.pk)).exists())

    def test_dashboard_requests_are_super_admin_reviewed_and_audited(self):
        self.authenticate(self.regular)
        created = self.client.post(
            reverse('authapp:dashboard-access-request-list-create'),
            {'requested_role': StaffRole.FINANCE, 'request_reason': 'Assigned to payout reporting.'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        request_record = DashboardAccessRequest.objects.get(pk=created.data['id'])
        self.assertEqual(request_record.status, DashboardAccessRequestStatus.PENDING)
        self.assertEqual(self.client.get(reverse('authapp:admin-dashboard-access-request-list')).status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.super_admin)
        approved = self.client.post(
            reverse('authapp:admin-dashboard-access-request-review', kwargs={'pk': request_record.pk}),
            {'decision': 'approved', 'reason': 'Finance manager confirmed the assignment.'},
            format='json',
        )
        self.assertEqual(approved.status_code, status.HTTP_200_OK, approved.data)
        self.assertTrue(UserStaffRole.objects.filter(user=self.regular, role=StaffRole.FINANCE).exists())
        self.assertTrue(AdminAuditLog.objects.filter(action='dashboard_access_approved', target_id=str(request_record.pk)).exists())
        repeated = self.client.post(
            reverse('authapp:admin-dashboard-access-request-review', kwargs={'pk': request_record.pk}),
            {'decision': 'approved', 'reason': 'Duplicate attempt.'},
            format='json',
        )
        self.assertEqual(repeated.status_code, status.HTTP_400_BAD_REQUEST)
