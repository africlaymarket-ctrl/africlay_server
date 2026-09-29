from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.utils import timezone
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiResponse

from .models import OTPVerification, OTPType, UserAddress
from .permissions import IsAuthenticated, IsOwner
from .serializers import (
    UserRegistrationSerializer,
    UserLoginSerializer,
    VerifyEmailSerializer,
    ResendOTPSerializer,
    TokenRefreshSerializer,
    LogoutSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    ChangePasswordSerializer,
    UserSummarySerializer,
    UserProfileUpdateSerializer,
    UserAddressSerializer,
    AuthResponseSerializer,
    TokensSerializer,
    MessageResponseSerializer,
)
from .utils import (
    generate_access_token,
    generate_refresh_token,
    refresh_access_token,
    blacklist_token,
    generate_otp_code,
    send_otp_email,
)

User = get_user_model()

# Registration & Verification Views

@extend_schema(
    tags=['Authentication'],
    summary='Register a new user account',
    description='Creates a new user with the specified role (buyer/seller/both). The account is available immediately.',
    request=UserRegistrationSerializer,
    responses={
        201: OpenApiResponse(
            response=MessageResponseSerializer,
            description='User registered successfully.',
        ),
        400: OpenApiResponse(description='Validation error / email already exists'),
    },
    auth=[],
)
class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                'message': 'Registration successful. Your account is ready.',
                'user': UserSummarySerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=['Authentication'],
    summary='Verify email address with OTP',
    description='Verifies user email using the 6-digit OTP code sent during registration and returns access & refresh tokens upon successful verification.',
    request=VerifyEmailSerializer,
    responses={
        200: OpenApiResponse(response=AuthResponseSerializer, description='Email verified successfully.'),
        400: OpenApiResponse(description='Invalid or expired OTP code.'),
    },
    auth=[],
)
class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    serializer_class = VerifyEmailSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        otp_record = serializer.validated_data['otp_record']

        # Mark OTP as used and user as verified
        otp_record.is_used = True
        otp_record.save()

        user.is_verified = True
        user.save()

        tokens = {
            'access': generate_access_token(user),
            'refresh': generate_refresh_token(user),
        }

        return Response(
            {
                'message': 'Email successfully verified! Your account is now active.',
                'user': UserSummarySerializer(user).data,
                'tokens': tokens,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Authentication'],
    summary='Resend verification or password reset OTP',
    description='Generates and dispatches a new 6-digit OTP code to the provided email address.',
    request=ResendOTPSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='New OTP code dispatched.'),
        400: OpenApiResponse(description='Invalid email or user already verified.'),
    },
    auth=[],
)
class ResendOTPView(APIView):
    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        otp_type = serializer.validated_data.get('otp_type', OTPType.EMAIL_VERIFICATION)

        # Invalidate old unused OTPs
        OTPVerification.objects.filter(user=user, otp_type=otp_type, is_used=False).update(is_used=True)

        # Generate fresh OTP
        otp_code = generate_otp_code()
        OTPVerification.objects.create(
            user=user,
            otp_code=otp_code,
            otp_type=otp_type,
            expires_at=timezone.now() + timezone.timedelta(minutes=15),
        )
        send_otp_email(user, otp_code, otp_type)

        return Response(
            {'message': 'A new verification code has been sent to your email address.'},
            status=status.HTTP_200_OK,
        )


# Login, Token Refresh & Logout Views

@extend_schema(
    tags=['Authentication'],
    summary='Login with email and password',
    description='Authenticates user credentials and returns user profile along with access and refresh JWT tokens.',
    request=UserLoginSerializer,
    responses={
        200: OpenApiResponse(response=AuthResponseSerializer, description='Login successful.'),
        400: OpenApiResponse(description='Invalid email or password.'),
    },
    auth=[],
)
class LoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = UserLoginSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        tokens = {
            'access': generate_access_token(user),
            'refresh': generate_refresh_token(user),
        }

        return Response(
            {
                'message': 'Login successful.',
                'user': UserSummarySerializer(user).data,
                'tokens': tokens,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Authentication'],
    summary='Refresh access token',
    description='Submits a valid refresh token to obtain a fresh access token and rotated refresh token.',
    request=TokenRefreshSerializer,
    responses={
        200: OpenApiResponse(response=TokensSerializer, description='New token pair issued.'),
        400: OpenApiResponse(description='Invalid or expired refresh token.'),
    },
    auth=[],
)
class TokenRefreshView(APIView):
    permission_classes = [AllowAny]
    serializer_class = TokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data['refresh']
        new_tokens = refresh_access_token(refresh_token)

        return Response(new_tokens, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Authentication'],
    summary='Logout and revoke refresh token',
    description='Revokes the provided refresh token and adds its ID to the token blacklist.',
    request=LogoutSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Logged out successfully.'),
        400: OpenApiResponse(description='Missing or invalid refresh token.'),
    },
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data['refresh']
        blacklist_token(refresh_token)

        return Response(
            {'message': 'Successfully logged out and session revoked.'},
            status=status.HTTP_200_OK,
        )

# Password Management Views

@extend_schema(
    tags=['Authentication'],
    summary='Request password reset OTP',
    description='Sends a 6-digit password reset OTP to the user\'s registered email address.',
    request=PasswordResetRequestSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Password reset code sent.'),
        400: OpenApiResponse(description='User not found or inactive.'),
    },
    auth=[],
)
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        user = User.objects.filter(email__iexact=email).first()

        if user:
            # Invalidate old reset OTPs
            OTPVerification.objects.filter(
                user=user,
                otp_type=OTPType.PASSWORD_RESET,
                is_used=False,
            ).update(is_used=True)

            otp_code = generate_otp_code()
            OTPVerification.objects.create(
                user=user,
                otp_code=otp_code,
                otp_type=OTPType.PASSWORD_RESET,
                expires_at=timezone.now() + timezone.timedelta(minutes=15),
            )
            send_otp_email(user, otp_code, OTPType.PASSWORD_RESET)

        return Response(
            {'message': 'If an account with this email exists, a password reset code has been sent.'},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Authentication'],
    summary='Confirm password reset with OTP',
    description='Verifies the password reset OTP code and sets the new password for the user.',
    request=PasswordResetConfirmSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Password successfully reset.'),
        400: OpenApiResponse(description='Invalid OTP code or password validation error.'),
    },
    auth=[],
)
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        otp_record = serializer.validated_data['otp_record']
        new_password = serializer.validated_data['new_password']

        # Update password
        user.set_password(new_password)
        user.save()

        # Mark OTP as used
        otp_record.is_used = True
        otp_record.save()

        return Response(
            {'message': 'Password has been successfully reset. You can now log in with your new password.'},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Authentication'],
    summary='Change password (authenticated)',
    description='Allows an authenticated user to change their current password by providing the old password and new password.',
    request=ChangePasswordSerializer,
    responses={
        200: OpenApiResponse(response=MessageResponseSerializer, description='Password updated successfully.'),
        400: OpenApiResponse(description='Incorrect old password or validation failure.'),
    },
)
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        new_password = serializer.validated_data['new_password']
        user.set_password(new_password)
        user.save()

        return Response(
            {'message': 'Password changed successfully.'},
            status=status.HTTP_200_OK,
        )


# Current User Profile & Address Views

@extend_schema_view(
    get=extend_schema(
        tags=['Authentication'],
        summary='Get current user profile',
        description='Returns complete user details and associated profile for the currently authenticated user.',
        responses={200: UserSummarySerializer},
    ),
    patch=extend_schema(
        tags=['Authentication'],
        summary='Update current user profile (partial)',
        description='Updates profile details (first_name, last_name, avatar, bio, notification_preferences) for the current user.',
        request=UserProfileUpdateSerializer,
        responses={200: UserSummarySerializer},
    ),
    put=extend_schema(
        tags=['Authentication'],
        summary='Update current user profile',
        description='Replaces profile details for the currently authenticated user.',
        request=UserProfileUpdateSerializer,
        responses={200: UserSummarySerializer},
    ),
)
class UserMeView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSummarySerializer

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        profile = user.profile

        # Update profile fields
        profile_serializer = UserProfileUpdateSerializer(profile, data=request.data, partial=True)
        profile_serializer.is_valid(raise_exception=True)
        profile_serializer.save()

        # Update user-level phone number if provided
        phone_number = request.data.get('phone_number')
        if phone_number is not None:
            user.phone_number = phone_number.strip() if phone_number else None
            user.save(update_fields=['phone_number'])

        return Response(
            {
                'message': 'Profile updated successfully.',
                'user': UserSummarySerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    get=extend_schema(
        tags=['User Addresses'],
        summary='List user addresses',
        description='Returns all saved addresses for the authenticated user.',
        responses={200: UserAddressSerializer(many=True)},
    ),
    post=extend_schema(
        tags=['User Addresses'],
        summary='Add a new address',
        description='Creates a new shipping/billing address for the authenticated user.',
        request=UserAddressSerializer,
        responses={201: UserAddressSerializer},
    ),
)
class UserAddressListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserAddressSerializer

    def get_queryset(self):
        return UserAddress.objects.filter(user=self.request.user)


@extend_schema_view(
    get=extend_schema(
        tags=['User Addresses'],
        summary='Retrieve an address',
        description='Fetches details of a specific address owned by the authenticated user.',
        responses={200: UserAddressSerializer},
    ),
    put=extend_schema(
        tags=['User Addresses'],
        summary='Update an address',
        description='Updates an existing address owned by the authenticated user.',
        request=UserAddressSerializer,
        responses={200: UserAddressSerializer},
    ),
    patch=extend_schema(
        tags=['User Addresses'],
        summary='Partial update of an address',
        description='Partially updates an address owned by the authenticated user.',
        request=UserAddressSerializer,
        responses={200: UserAddressSerializer},
    ),
    delete=extend_schema(
        tags=['User Addresses'],
        summary='Delete an address',
        description='Deletes an address owned by the authenticated user.',
        responses={204: OpenApiResponse(description='Address deleted successfully.')},
    ),
)
class UserAddressDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsOwner]
    serializer_class = UserAddressSerializer

    def get_queryset(self):
        return UserAddress.objects.filter(user=self.request.user)
