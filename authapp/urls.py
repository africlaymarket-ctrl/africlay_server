from django.urls import path
from .views import (
    RegisterView,
    VerifyEmailView,
    ResendOTPView,
    LoginView,
    TokenRefreshView,
    LogoutView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    ChangePasswordView,
    UserMeView,
    UserAddressListCreateView,
    UserAddressDetailView,
)

app_name = 'authapp'

urlpatterns = [
    # Registration & Verification
    path('register/', RegisterView.as_view(), name='register'),
    path('verify-email/', VerifyEmailView.as_view(), name='verify-email'),
    path('resend-otp/', ResendOTPView.as_view(), name='resend-otp'),

    # Authentication & Session
    path('login/', LoginView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),

    # Password Management
    path('password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),

    # User Profile & Addresses
    path('me/', UserMeView.as_view(), name='user-me'),
    path('addresses/', UserAddressListCreateView.as_view(), name='address-list-create'),
    path('addresses/<uuid:pk>/', UserAddressDetailView.as_view(), name='address-detail'),
]
