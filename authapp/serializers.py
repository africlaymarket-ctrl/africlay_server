from rest_framework import serializers
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import validate_password
from .models import UserProfile, UserAddress, OTPVerification, OTPType, UserRole
from .utils import (
    generate_access_token,
    generate_refresh_token,
    refresh_access_token,
    blacklist_token,
)

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            'id',
            'first_name',
            'last_name',
            'avatar',
            'avatar_url',
            'bio',
            'notification_preferences',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            'first_name',
            'last_name',
            'avatar',
            'avatar_url',
            'bio',
            'notification_preferences',
        ]


class UserAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAddress
        fields = [
            'id',
            'address_type',
            'recipient_name',
            'phone_number',
            'street_address',
            'apartment_suite',
            'city',
            'state_province',
            'postal_code',
            'country',
            'country_code',
            'is_default',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class UserSummarySerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'phone_number',
            'role',
            'is_verified',
            'is_active',
            'full_name',
            'profile',
            'date_joined',
        ]
        read_only_fields = ['id', 'email', 'is_verified', 'is_active', 'date_joined']


class UserRegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    last_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    role = serializers.ChoiceField(choices=UserRole.choices, default=UserRole.BUYER)

    def validate_email(self, value):
        normalized_email = value.lower().strip()
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return normalized_email

    def validate_phone_number(self, value):
        if value:
            clean_phone = value.strip()
            if User.objects.filter(phone_number=clean_phone).exists():
                raise serializers.ValidationError('A user with this phone number already exists.')
            return clean_phone
        return None

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': 'Passwords do not match.'})

        # Run Django password validators
        validate_password(attrs['password'])
        return attrs

    def create(self, validated_data):
        email = validated_data['email']
        password = validated_data['password']
        role = validated_data.get('role', UserRole.BUYER)
        phone_number = validated_data.get('phone_number')
        first_name = validated_data.get('first_name', '')
        last_name = validated_data.get('last_name', '')

        user = User.objects.create_user(
            email=email,
            password=password,
            role=role,
            phone_number=phone_number,
            is_verified=True,
        )

        # Update profile with names
        profile = user.profile
        profile.first_name = first_name
        profile.last_name = last_name
        profile.save()

        return user


class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        email = attrs.get('email').lower().strip()
        otp_code = attrs.get('otp_code').strip()

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({'email': 'User not found.'})

        if user.is_verified:
            raise serializers.ValidationError({'email': 'Email is already verified.'})

        otp_record = OTPVerification.objects.filter(
            user=user,
            otp_code=otp_code,
            otp_type=OTPType.EMAIL_VERIFICATION,
            is_used=False,
        ).first()

        if not otp_record:
            raise serializers.ValidationError({'otp_code': 'Invalid verification code.'})

        if not otp_record.is_valid():
            raise serializers.ValidationError({'otp_code': 'Verification code has expired.'})

        attrs['user'] = user
        attrs['otp_record'] = otp_record
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_type = serializers.ChoiceField(choices=OTPType.choices, default=OTPType.EMAIL_VERIFICATION)

    def validate(self, attrs):
        email = attrs.get('email').lower().strip()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({'email': 'User not found.'})

        otp_type = attrs.get('otp_type')
        if otp_type == OTPType.EMAIL_VERIFICATION and user.is_verified:
            raise serializers.ValidationError({'email': 'Email is already verified.'})

        attrs['user'] = user
        return attrs


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get('email').lower().strip()
        password = attrs.get('password')

        user = authenticate(username=email, password=password)
        if not user:
            raise serializers.ValidationError('Invalid email or password.')

        if not user.is_active:
            raise serializers.ValidationError('User account is inactive. Please contact support.')

        attrs['user'] = user
        return attrs


class TokenRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate_refresh(self, value):
        if not value:
            raise serializers.ValidationError('Refresh token is required.')
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        email = value.lower().strip()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError('User with this email does not exist.')
        if not user.is_active:
            raise serializers.ValidationError('User account is inactive.')
        return email


class PasswordResetVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        email = attrs.get('email').lower().strip()
        otp_code = attrs.get('otp_code').strip()

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({'email': 'User not found.'})

        otp_record = OTPVerification.objects.filter(
            user=user,
            otp_code=otp_code,
            otp_type=OTPType.PASSWORD_RESET,
            is_used=False,
        ).first()

        if not otp_record or not otp_record.is_valid():
            raise serializers.ValidationError({'otp_code': 'Invalid or expired password reset code.'})

        attrs['user'] = user
        attrs['otp_record'] = otp_record
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, min_length=8)
    new_password_confirm = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({'new_password_confirm': 'Passwords do not match.'})

        validate_password(attrs['new_password'])

        email = attrs.get('email').lower().strip()
        otp_code = attrs.get('otp_code').strip()

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({'email': 'User not found.'})

        otp_record = OTPVerification.objects.filter(
            user=user,
            otp_code=otp_code,
            otp_type=OTPType.PASSWORD_RESET,
            is_used=False,
        ).first()

        if not otp_record or not otp_record.is_valid():
            raise serializers.ValidationError({'otp_code': 'Invalid or expired password reset code.'})

        attrs['user'] = user
        attrs['otp_record'] = otp_record
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    new_password_confirm = serializers.CharField(write_only=True, min_length=8)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({'new_password_confirm': 'New passwords do not match.'})

        if attrs['old_password'] == attrs['new_password']:
            raise serializers.ValidationError({'new_password': 'New password must be different from current password.'})

        validate_password(attrs['new_password'])
        return attrs



class TokensSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class AuthResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    user = UserSummarySerializer()
    tokens = TokensSerializer()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
