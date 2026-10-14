import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone

from core.models import TimestampedModel, avatar_upload_path, validate_image_upload


class UserRole(models.TextChoices):
    BUYER = 'buyer', 'Buyer'
    SELLER = 'seller', 'Seller'
    BOTH = 'both', 'Both'
    ADMIN = 'admin', 'Admin'
    SUPER_ADMIN = 'super_admin', 'Super Admin'


class StaffRole(models.TextChoices):
    SALES_REVIEWER = 'sales_reviewer', 'Sales reviewer'
    FINANCE = 'finance', 'Finance'


class DashboardAccessRequestStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        extra_fields.setdefault('role', UserRole.BUYER)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)

        # Create associated UserProfile if not already created
        UserProfile.objects.get_or_create(user=user)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('role', UserRole.SUPER_ADMIN)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class User(TimestampedModel, AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True, max_length=255)
    phone_number = models.CharField(max_length=20, blank=True, null=True, unique=True)
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.BUYER)

    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    auth_version = models.PositiveIntegerField(default=0)

    date_joined = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(blank=True, null=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        if hasattr(self, 'profile'):
            name = f"{self.profile.first_name} {self.profile.last_name}".strip()
            return name if name else self.email
        return self.email

    @property
    def is_buyer(self):
        return self.role in [
            UserRole.BUYER,
            UserRole.SELLER,
            UserRole.BOTH,
            UserRole.ADMIN,
            UserRole.SUPER_ADMIN,
        ]

    @property
    def is_seller(self):
        return self.role in [UserRole.SELLER, UserRole.BOTH, UserRole.ADMIN, UserRole.SUPER_ADMIN]

    @property
    def is_admin_role(self):
        return self.role == UserRole.SUPER_ADMIN or self.is_superuser

    @property
    def is_super_admin(self):
        return self.role == UserRole.SUPER_ADMIN or self.is_superuser

    def has_staff_role(self, role):
        return self.is_super_admin or self.staff_role_assignments.filter(role=role).exists()


class UserStaffRole(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='staff_role_assignments')
    role = models.CharField(max_length=30, choices=StaffRole.choices)
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='granted_staff_roles',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'role'], name='unique_user_staff_role'),
        ]
        ordering = ['role']

    def __str__(self):
        return f'{self.user.email}: {self.role}'


class DashboardAccessRequest(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dashboard_access_requests')
    requested_role = models.CharField(max_length=30, choices=StaffRole.choices)
    request_reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=DashboardAccessRequestStatus.choices,
        default=DashboardAccessRequestStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_dashboard_access_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['requester', 'requested_role'],
                condition=models.Q(status=DashboardAccessRequestStatus.PENDING),
                name='unique_pending_dashboard_access_request',
            ),
        ]

    def __str__(self):
        return f'{self.requester.email}: {self.requested_role} ({self.status})'


class AdminAuditLog(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_audit_actions',
    )
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=50, db_index=True)
    target_id = models.CharField(max_length=100, db_index=True)
    reason = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['target_type', 'target_id', 'created_at'])]

    def save(self, *args, **kwargs):
        if self.pk and AdminAuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError('Administrative audit records are append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Administrative audit records are append-only.')

    def __str__(self):
        return f'{self.action} {self.target_type}:{self.target_id}'


class UserProfile(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    first_name = models.CharField(max_length=150, blank=True, default='')
    last_name = models.CharField(max_length=150, blank=True, default='')
    avatar = models.ImageField(upload_to=avatar_upload_path, blank=True, null=True, validators=[validate_image_upload])
    avatar_url = models.URLField(blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    notification_preferences = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"Profile of {self.user.email}"


class AddressType(models.TextChoices):
    SHIPPING = 'shipping', 'Shipping'
    BILLING = 'billing', 'Billing'
    BOTH = 'both', 'Both'


class UserAddress(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='addresses')
    address_type = models.CharField(max_length=20, choices=AddressType.choices, default=AddressType.SHIPPING)
    recipient_name = models.CharField(max_length=255, blank=True, default='')
    phone_number = models.CharField(max_length=20, blank=True, default='')
    street_address = models.CharField(max_length=255)
    apartment_suite = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state_province = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100)
    country_code = models.CharField(max_length=10, blank=True, null=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'User Address'
        verbose_name_plural = 'User Addresses'
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.recipient_name or self.user.email} - {self.street_address}, {self.city}"

    def save(self, *args, **kwargs):
        if self.is_default:
            # Unset default on other addresses for the user
            UserAddress.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class OTPType(models.TextChoices):
    EMAIL_VERIFICATION = 'email_verification', 'Email Verification'
    PASSWORD_RESET = 'password_reset', 'Password Reset'


class OTPVerification(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_codes')
    otp_code = models.CharField(max_length=6)
    otp_type = models.CharField(max_length=30, choices=OTPType.choices)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'OTP Verification'
        verbose_name_plural = 'OTP Verifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.otp_type} - {self.otp_code}"

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.expires_at


class BlacklistedToken(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_id = models.CharField(max_length=255, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='blacklisted_tokens')
    blacklisted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = 'Blacklisted Token'
        verbose_name_plural = 'Blacklisted Tokens'
        ordering = ['-blacklisted_at']

    def __str__(self):
        return f"Revoked Token {self.token_id[:16]}... for {self.user.email if self.user else 'Unknown'}"
