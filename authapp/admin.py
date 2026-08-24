from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserProfile, UserAddress, OTPVerification, BlacklistedToken


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'


class UserAddressInline(admin.TabularInline):
    model = UserAddress
    extra = 0


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, UserAddressInline)
    list_display = ('email', 'role', 'is_verified', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('role', 'is_verified', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('email', 'phone_number', 'profile__first_name', 'profile__last_name')
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('phone_number', 'role')}),
        ('Permissions', {'fields': ('is_verified', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login', 'date_joined', 'updated_at')}),
    )
    readonly_fields = ('date_joined', 'updated_at', 'last_login')

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'role', 'phone_number', 'is_verified', 'is_active', 'is_staff'),
        }),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'first_name', 'last_name', 'created_at')
    search_fields = ('user__email', 'first_name', 'last_name')


@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address_type', 'recipient_name', 'city', 'country', 'is_default', 'created_at')
    list_filter = ('address_type', 'is_default', 'country')
    search_fields = ('user__email', 'recipient_name', 'street_address', 'city')


@admin.register(OTPVerification)
class OTPVerificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'otp_code', 'otp_type', 'is_used', 'expires_at', 'created_at')
    list_filter = ('otp_type', 'is_used', 'created_at')
    search_fields = ('user__email', 'otp_code')
    readonly_fields = ('created_at',)


@admin.register(BlacklistedToken)
class BlacklistedTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'token_id', 'blacklisted_at', 'expires_at')
    search_fields = ('user__email', 'token_id')
    readonly_fields = ('blacklisted_at',)
