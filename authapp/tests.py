from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import UserProfile, UserAddress, OTPVerification, OTPType, UserRole, BlacklistedToken
from .utils import generate_access_token, generate_refresh_token

User = get_user_model()


class AuthTestCase(TestCase):
    def setUp(self):
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

    def test_user_registration(self):
        """Test POST /api/auth/register/ creates user, profile, and OTP."""
        url = reverse('authapp:register')
        data = {
            'email': 'newuser@example.com',
            'password': 'NewPassword123!',
            'password_confirm': 'NewPassword123!',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'role': 'buyer',
            'phone_number': '+254700000002',
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message'], 'Registration successful. Your account is ready.')

        user = User.objects.filter(email='newuser@example.com').first()
        self.assertIsNotNone(user)
        self.assertTrue(user.is_verified)
        self.assertEqual(user.profile.first_name, 'Jane')

        self.assertFalse(
            OTPVerification.objects.filter(user=user, otp_type=OTPType.EMAIL_VERIFICATION).exists()
        )

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

        # Attempting to refresh with the blacklisted token should now fail
        refresh_url = reverse('authapp:token-refresh')
        refresh_resp = self.client.post(refresh_url, {'refresh': refresh_token}, format='json')
        self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_password_reset_flow(self):
        """Test password reset request and confirmation with OTP."""
        # 1. Request reset
        req_url = reverse('authapp:password-reset-request')
        response = self.client.post(req_url, {'email': self.user_email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        otp_record = OTPVerification.objects.filter(user=self.user, otp_type=OTPType.PASSWORD_RESET).first()
        self.assertIsNotNone(otp_record)

        # 2. Confirm reset
        confirm_url = reverse('authapp:password-reset-confirm')
        confirm_data = {
            'email': self.user_email,
            'otp_code': otp_record.otp_code,
            'new_password': 'BrandNewPassword123!',
            'new_password_confirm': 'BrandNewPassword123!',
        }
        confirm_resp = self.client.post(confirm_url, confirm_data, format='json')
        self.assertEqual(confirm_resp.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPassword123!'))

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
