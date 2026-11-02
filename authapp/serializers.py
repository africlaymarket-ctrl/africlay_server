from rest_framework import serializers
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from .models import (
    CURRENT_POLICY_VERSIONS, PolicyAcceptance, PolicyCode, UserProfile,
    UserAddress, OTPVerification, OTPType, UserRole, required_policy_codes,
)
from .utils import (
    generate_access_token,
    generate_refresh_token,
    refresh_access_token,
    blacklist_token,
)

User = get_user_model()

PUBLIC_REGISTRATION_ROLE_CHOICES = [
    (UserRole.BUYER, UserRole.BUYER.label),
    (UserRole.SELLER, UserRole.SELLER.label),
    (UserRole.BOTH, UserRole.BOTH.label),
]


def _consume_otp(user, otp_type, raw_code):
    """Atomically consume the newest valid OTP and count failed guesses."""
    error = None
    consumed = None
    with transaction.atomic():
        otp_record = (
            OTPVerification.objects.select_for_update()
            .filter(user=user, otp_type=otp_type, is_used=False)
            .order_by('-created_at')
            .first()
        )
        if not otp_record or not otp_record.is_valid():
            if otp_record and not otp_record.is_used:
                otp_record.is_used = True
                otp_record.save(update_fields=['is_used', 'updated_at'])
            error = 'Invalid or expired verification code.'
        elif not otp_record.matches_code(raw_code):
            otp_record.failed_attempts += 1
            if otp_record.failed_attempts >= OTPVerification.MAX_FAILED_ATTEMPTS:
                otp_record.is_used = True
            otp_record.save(update_fields=['failed_attempts', 'is_used', 'updated_at'])
            error = 'Invalid or expired verification code.'
        else:
            otp_record.is_used = True
            otp_record.save(update_fields=['is_used', 'updated_at'])
            consumed = otp_record

    if error:
        raise serializers.ValidationError({'otp_code': error})
    return consumed


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
    staff_roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'phone_number',
            'role',
            'staff_roles',
            'is_verified',
            'is_active',
            'full_name',
            'profile',
            'date_joined',
        ]
        read_only_fields = ['id', 'email', 'is_verified', 'is_active', 'date_joined']

    def get_staff_roles(self, user):
        return list(user.staff_role_assignments.order_by('role').values_list('role', flat=True))


class UserRegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    last_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    role = serializers.ChoiceField(choices=PUBLIC_REGISTRATION_ROLE_CHOICES, default=UserRole.BUYER)
    accepted_policies = serializers.ListField(
        child=serializers.ChoiceField(choices=PolicyCode.choices),
        allow_empty=False,
        write_only=True,
    )

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
        role = attrs.get('role', UserRole.BUYER)
        accepted = set(attrs.get('accepted_policies', []))
        required = required_policy_codes(role)
        if accepted != required:
            missing = sorted(required - accepted)
            unexpected = sorted(accepted - required)
            details = []
            if missing:
                details.append(f'Missing required policies: {", ".join(missing)}.')
            if unexpected:
                details.append(f'Policies not applicable to this role: {", ".join(unexpected)}.')
            raise serializers.ValidationError({'accepted_policies': ' '.join(details)})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data['email']
        password = validated_data['password']
        role = validated_data.get('role', UserRole.BUYER)
        phone_number = validated_data.get('phone_number')
        first_name = validated_data.get('first_name', '')
        last_name = validated_data.get('last_name', '')
        accepted_policies = validated_data.pop('accepted_policies')

        user = User.objects.create_user(
            email=email,
            password=password,
            role=role,
            phone_number=phone_number,
            is_verified=False,
        )

        # Update profile with names
        profile = user.profile
        profile.first_name = first_name
        profile.last_name = last_name
        profile.save()

        PolicyAcceptance.objects.bulk_create([
            PolicyAcceptance(
                user=user,
                policy_code=policy_code,
                policy_version=CURRENT_POLICY_VERSIONS[policy_code],
                accepted_role=role,
            )
            for policy_code in sorted(accepted_policies)
        ])

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

        otp_record = _consume_otp(user, OTPType.EMAIL_VERIFICATION, otp_code)

        attrs['user'] = user
        attrs['otp_record'] = otp_record
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_type = serializers.ChoiceField(choices=OTPType.choices, default=OTPType.EMAIL_VERIFICATION)

    def validate(self, attrs):
        attrs['email'] = attrs.get('email').lower().strip()
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
        return value.lower().strip()


class PasswordResetVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp_code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        email = attrs.get('email').lower().strip()
        otp_code = attrs.get('otp_code').strip()

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({'email': 'User not found.'})

        otp_record = (
            OTPVerification.objects.filter(
                user=user,
                otp_type=OTPType.PASSWORD_RESET,
                is_used=False,
            )
            .order_by('-created_at')
            .first()
        )

        if not otp_record or not otp_record.is_valid() or not otp_record.matches_code(otp_code):
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

        otp_record = _consume_otp(user, OTPType.PASSWORD_RESET, otp_code)

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
