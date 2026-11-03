from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    CURRENT_POLICY_VERSIONS, BlacklistedToken, PolicyAcceptance, PolicyCode,
    UserProfile, UserAddress, OTPVerification, OTPType, UserRole,
)
from .utils import generate_access_token, generate_refresh_token

User = get_user_model()


class AuthTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user_email = 'buyer@example.com'
        self.user_password = 'StrongPassword123!'
        self.user = User.objects.create_user(
            email=self.user_email,
            password=self.user_password,
            role=UserRole.BUYER,
            phone_number='+254700000001',
            is_verified=True,
            is_active=True,
        )
        self.user.profile.first_name = 'John'
        self.user.profile.last_name = 'Doe'
        self.user.profile.save()

    def test_user_creation_and_profile(self):
        """Test User model and automatically created UserProfile."""
        self.assertEqual(self.user.email, self.user_email)
        self.assertTrue(self.user.check_password(self.user_password))
        self.assertTrue(hasattr(self.user, 'profile'))
        self.assertEqual(self.user.profile.first_name, 'John')
        self.assertEqual(self.user.full_name, 'John Doe')
        self.assertTrue(self.user.is_buyer)

    def test_seller_accounts_can_also_buy(self):
        seller = User.objects.create_user(
            email='seller-buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )

        self.assertTrue(seller.is_seller)
        self.assertTrue(seller.is_buyer)

    def test_user_uses_shared_timestamp_fields(self):
        """All timestamped models expose the shared creation/update contract."""
        created_at = self.user.created_at
        updated_at = self.user.updated_at
        self.assertIsNotNone(self.user.created_at)
        self.assertIsNotNone(self.user.updated_at)
        self.assertLessEqual(self.user.created_at, self.user.updated_at)

        self.user.profile.bio = 'Updated profile'
        self.user.profile.save()
        self.user.refresh_from_db()

        self.assertEqual(self.user.created_at, created_at)
        self.assertGreaterEqual(self.user.updated_at, updated_at)

    def test_create_superuser(self):
        """Test superuser creation."""
        admin = User.objects.create_superuser(
            email='admin@example.com',
            password='AdminPassword123!',
        )
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_verified)
        self.assertEqual(admin.role, UserRole.SUPER_ADMIN)

    @patch('authapp.utils.send_otp_email')
    def test_user_registration(self, mock_send_otp_email):
        """Test POST /api/auth/register/ creates user, profile, and OTP."""
        url = reverse('authapp:register')
        data = {
            'email': 'newuser@example.com',
            'password': 'NewPassword123!',
            'password_confirm': 'NewPassword123!',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'role': 'buyer',
            'accepted_policies': [PolicyCode.BUYER_SHIPPING_DELIVERY],
            'phone_number': '+254700000002',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message'], 'Registration successful. Verify your email before signing in.')

        user = User.objects.filter(email='newuser@example.com').first()
        self.assertIsNotNone(user)
        self.assertFalse(user.is_verified)
        self.assertEqual(user.profile.first_name, 'Jane')
        acceptance = user.policy_acceptances.get()
        self.assertEqual(acceptance.policy_code, PolicyCode.BUYER_SHIPPING_DELIVERY)
        self.assertEqual(acceptance.policy_version, CURRENT_POLICY_VERSIONS[PolicyCode.BUYER_SHIPPING_DELIVERY])
        self.assertEqual(acceptance.accepted_role, UserRole.BUYER)

        otp = OTPVerification.objects.get(user=user, otp_type=OTPType.EMAIL_VERIFICATION)
        raw_code = mock_send_otp_email.call_args.args[1]
        self.assertNotEqual(otp.otp_code, raw_code)
        self.assertTrue(otp.matches_code(raw_code))

    def test_public_registration_rejects_privileged_roles(self):
        url = reverse('authapp:register')
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            response = self.client.post(url, {
                'email': f'{role}@example.com',
                'password': 'NewPassword123!',
                'password_confirm': 'NewPassword123!',
                'role': role,
                'accepted_policies': [PolicyCode.BUYER_SHIPPING_DELIVERY],
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertFalse(User.objects.filter(email=f'{role}@example.com').exists())

    def test_registration_requires_exact_policies_for_role(self):
        url = reverse('authapp:register')
        base = {
            'password': 'NewPassword123!',
            'password_confirm': 'NewPassword123!',
            'first_name': 'Policy',
        }
        cases = [
            ('missing@example.com', UserRole.BUYER, [], status.HTTP_400_BAD_REQUEST),
            ('wrong@example.com', UserRole.BUYER, [PolicyCode.SELLER_COMMISSION], status.HTTP_400_BAD_REQUEST),
            ('seller@example.com', UserRole.SELLER, [PolicyCode.SELLER_PARTNER_TERMS, PolicyCode.SELLER_COMMISSION], status.HTTP_201_CREATED),
            ('both@example.com', UserRole.BOTH, list(PolicyCode.values), status.HTTP_201_CREATED),
        ]
        for email, role, policies, expected_status in cases:
            response = self.client.post(url, {**base, 'email': email, 'role': role, 'accepted_policies': policies}, format='json')
            self.assertEqual(response.status_code, expected_status, response.data)

        self.assertEqual(User.objects.get(email='seller@example.com').policy_acceptances.count(), 2)
        self.assertEqual(User.objects.get(email='both@example.com').policy_acceptances.count(), 3)

    def test_policy_acceptance_is_append_only(self):
        acceptance = PolicyAcceptance.objects.create(
            user=self.user,
            policy_code=PolicyCode.BUYER_SHIPPING_DELIVERY,
            policy_version=CURRENT_POLICY_VERSIONS[PolicyCode.BUYER_SHIPPING_DELIVERY],
            accepted_role=UserRole.BUYER,
        )
        acceptance.policy_version = 'changed'
        with self.assertRaisesMessage(ValueError, 'append-only'):
            acceptance.save()
        with self.assertRaisesMessage(ValueError, 'append-only'):
            acceptance.delete()

    def test_verify_email_otp(self):
        """Test POST /api/auth/verify-email/ validates OTP and activates user."""
        unverified_user = User.objects.create_user(
            email='unverified@example.com',
            password='Password123!',
            is_verified=False,
        )
        otp = OTPVerification.objects.create(
            user=unverified_user,
            otp_code='123456',
            otp_type=OTPType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timezone.timedelta(minutes=15),
        )

        url = reverse('authapp:verify-email')
        response = self.client.post(url, {'email': 'unverified@example.com', 'otp_code': '123456'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', response.data)

        unverified_user.refresh_from_db()
        self.assertTrue(unverified_user.is_verified)

        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    def test_verification_code_locks_after_five_failed_attempts(self):
        user = User.objects.create_user(
            email='locked-otp@example.com',
            password='Password123!',
            is_verified=False,
        )
        otp = OTPVerification.objects.create(
            user=user,
            otp_code='123456',
            otp_type=OTPType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timezone.timedelta(minutes=15),
        )
        url = reverse('authapp:verify-email')
        for _ in range(5):
            response = self.client.post(url, {'email': user.email, 'otp_code': '000000'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        otp.refresh_from_db()
        self.assertEqual(otp.failed_attempts, OTPVerification.MAX_FAILED_ATTEMPTS)
        self.assertTrue(otp.is_used)
        valid_response = self.client.post(url, {'email': user.email, 'otp_code': '123456'}, format='json')
        self.assertEqual(valid_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resend_otp(self):
        """Test POST /api/auth/resend-otp/ dispatches new code."""
        unverified_user = User.objects.create_user(
            email='resend@example.com',
            password='Password123!',
            is_verified=False,
        )
        url = reverse('authapp:resend-otp')
        response = self.client.post(url, {'email': 'resend@example.com', 'otp_type': 'email_verification'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertTrue(OTPVerification.objects.filter(user=unverified_user, otp_type=OTPType.EMAIL_VERIFICATION).exists())

    def test_user_login(self):
        """Test POST /api/auth/login/ returns tokens and profile."""
        url = reverse('authapp:login')
        response = self.client.post(url, {'email': self.user_email, 'password': self.user_password}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', response.data)
        self.assertIn('access', response.data['tokens'])
        self.assertIn('refresh', response.data['tokens'])

    def test_unverified_user_login_returns_no_tokens(self):
        user = User.objects.create_user(
            email='login-unverified@example.com',
            password='Password123!',
            is_verified=False,
        )
        response = self.client.post(
            reverse('authapp:login'),
            {'email': user.email, 'password': 'Password123!'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['verification_required'])
        self.assertNotIn('tokens', response.data)

    def test_token_refresh(self):
        """Test POST /api/auth/token/refresh/ issues new token pair."""
        refresh_token = generate_refresh_token(self.user)
        url = reverse('authapp:token-refresh')
        response = self.client.post(url, {'refresh': refresh_token}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_user_logout(self):
        """Test POST /api/auth/logout/ blacklists refresh token."""
        access_token = generate_access_token(self.user)
        refresh_token = generate_refresh_token(self.user)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        url = reverse('authapp:logout')
        response = self.client.post(url, {'refresh': refresh_token}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertEqual(self.user.auth_version, 1)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        self.assertEqual(self.client.get(reverse('authapp:user-me')).status_code, status.HTTP_401_UNAUTHORIZED)

        # Attempting to refresh with the blacklisted token should now fail
        refresh_url = reverse('authapp:token-refresh')
        refresh_resp = self.client.post(refresh_url, {'refresh': refresh_token}, format='json')
        self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('authapp.utils.send_otp_email')
    def test_password_reset_flow(self, mock_send_otp_email):
        """Test password reset request and confirmation with OTP."""
        # 1. Request reset
        req_url = reverse('authapp:password-reset-request')
        response = self.client.post(req_url, {'email': self.user_email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        otp_record = OTPVerification.objects.filter(user=self.user, otp_type=OTPType.PASSWORD_RESET).first()
        self.assertIsNotNone(otp_record)

        # 2. Confirm reset
        confirm_url = reverse('authapp:password-reset-confirm')
        raw_code = mock_send_otp_email.call_args.args[1]
        confirm_data = {
            'email': self.user_email,
            'otp_code': raw_code,
            'new_password': 'BrandNewPassword123!',
            'new_password_confirm': 'BrandNewPassword123!',
        }
        confirm_resp = self.client.post(confirm_url, confirm_data, format='json')
        self.assertEqual(confirm_resp.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPassword123!'))
        self.assertEqual(self.user.auth_version, 1)

    def test_password_reset_request_does_not_reveal_account_existence(self):
        url = reverse('authapp:password-reset-request')
        known = self.client.post(url, {'email': self.user_email}, format='json')
        unknown = self.client.post(url, {'email': 'missing@example.com'}, format='json')
        self.assertEqual(known.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(known.data, unknown.data)

    def test_change_password(self):
        """Test POST /api/auth/change-password/ for authenticated user."""
        access_token = generate_access_token(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        url = reverse('authapp:change-password')
        data = {
            'old_password': self.user_password,
            'new_password': 'UpdatedPassword123!',
            'new_password_confirm': 'UpdatedPassword123!',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('UpdatedPassword123!'))
        self.assertEqual(self.user.auth_version, 1)
        self.assertEqual(self.client.get(reverse('authapp:user-me')).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_me_profile_retrieval_and_update(self):
        """Test GET and PATCH /api/auth/me/."""
        access_token = generate_access_token(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        url = reverse('authapp:user-me')

        # Retrieve
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(get_resp.data['email'], self.user_email)
        self.assertEqual(get_resp.data['profile']['first_name'], 'John')

        # Update
        patch_resp = self.client.patch(url, {'first_name': 'Johnny', 'bio': 'Artisan potter'}, format='json')
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.first_name, 'Johnny')
        self.assertEqual(self.user.profile.bio, 'Artisan potter')

    def test_email_preferences_are_validated_and_preserved(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(self.user)}')
        url = reverse('authapp:user-me')
        enabled = self.client.patch(url, {'notification_preferences': {'email_order_updates': True}}, format='json')
        self.assertEqual(enabled.status_code, status.HTTP_200_OK)
        self.assertTrue(enabled.data['user']['profile']['notification_preferences']['email_order_updates'])
        second = self.client.patch(url, {'notification_preferences': {'email_seller_sales': True}}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data['user']['profile']['notification_preferences']['email_order_updates'])
        self.assertEqual(self.client.patch(url, {'notification_preferences': {'email_order_updates': 'yes'}}, format='json').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.patch(url, {'notification_preferences': {'email_promotions': True}}, format='json').status_code, status.HTTP_400_BAD_REQUEST)

    @patch('authapp.views.send_account_notice_email')
    def test_phone_change_sends_security_notice_after_commit(self, send_notice):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(self.user)}')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(reverse('authapp:user-me'), {'phone_number': '+254700000002'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        send_notice.assert_called_once()
        self.assertEqual(send_notice.call_args.args[0], self.user_email)

    @patch('authapp.views.send_account_notice_email')
    def test_password_change_sends_security_notice_after_commit(self, send_notice):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_access_token(self.user)}')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('authapp:change-password'), {
                'old_password': self.user_password,
                'new_password': 'NewStrongPassword123!',
                'new_password_confirm': 'NewStrongPassword123!',
            }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        send_notice.assert_called_once()
        self.assertNotIn('NewStrongPassword123!', str(send_notice.call_args))

    @patch('authapp.views.send_account_notice_email')
    @patch('authapp.utils.send_otp_email')
    def test_password_reset_sends_security_notice_after_commit(self, otp_email, send_notice):
        requested = self.client.post(reverse('authapp:password-reset-request'), {'email': self.user_email}, format='json')
        self.assertEqual(requested.status_code, status.HTTP_200_OK)
        code = otp_email.call_args.args[1]
        with self.captureOnCommitCallbacks(execute=True):
            reset = self.client.post(reverse('authapp:password-reset-confirm'), {
                'email': self.user_email,
                'otp_code': code,
                'new_password': 'ResetStrongPassword123!',
                'new_password_confirm': 'ResetStrongPassword123!',
            }, format='json')
        self.assertEqual(reset.status_code, status.HTTP_200_OK)
        send_notice.assert_called_once()
        self.assertNotIn('ResetStrongPassword123!', str(send_notice.call_args))

    def test_user_address_crud(self):
        """Test CRUD for UserAddress endpoints."""
        access_token = generate_access_token(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

        list_url = reverse('authapp:address-list-create')

        # Create address
        address_data = {
            'address_type': 'shipping',
            'recipient_name': 'John Doe',
            'phone_number': '+254700000001',
            'street_address': '123 Clay Street',
            'city': 'Nairobi',
            'country': 'Kenya',
            'country_code': 'KE',
            'is_default': True,
        }
        create_resp = self.client.post(list_url, address_data, format='json')
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        address_id = create_resp.data['id']

        # List addresses
        list_resp = self.client.get(list_url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_resp.data), 1)

        # Retrieve detail
        detail_url = reverse('authapp:address-detail', kwargs={'pk': address_id})
        detail_resp = self.client.get(detail_url)
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data['street_address'], '123 Clay Street')

        # Update address
        update_resp = self.client.patch(detail_url, {'street_address': '456 Pottery Lane'}, format='json')
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(update_resp.data['street_address'], '456 Pottery Lane')

        # Delete address
        del_resp = self.client.delete(detail_url)
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(UserAddress.objects.filter(id=address_id).count(), 0)
