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
from .operations_views import (
    AdminAuditLogListView,
    AdminCatalogListView,
    AdminCatalogStatusView,
    AdminOperationsSummaryView,
    AdminOrderListView,
    AdminUserDetailView,
    AdminUserListView,
    AdminStaffRoleByEmailView,
    AdminUserStaffRoleView,
    AdminUserStatusView,
    AdminDashboardAccessRequestListView,
    AdminDashboardAccessRequestReviewView,
    DashboardAccessRequestListCreateView,
)

app_name = 'authapp'

urlpatterns = [
    path('admin/overview/', AdminOperationsSummaryView.as_view(), name='admin-operations-summary'),
    path('admin/catalog/<str:kind>/', AdminCatalogListView.as_view(), name='admin-catalog-list'),
    path('admin/catalog/<str:kind>/<uuid:pk>/status/', AdminCatalogStatusView.as_view(), name='admin-catalog-status'),
    path('admin/orders/', AdminOrderListView.as_view(), name='admin-order-list'),
    path('admin/users/', AdminUserListView.as_view(), name='admin-user-list'),
    path('admin/staff-assignments/', AdminStaffRoleByEmailView.as_view(), name='admin-staff-role-by-email'),
    path('admin/users/<uuid:pk>/', AdminUserDetailView.as_view(), name='admin-user-detail'),
    path('admin/users/<uuid:pk>/staff-roles/', AdminUserStaffRoleView.as_view(), name='admin-user-staff-role'),
    path('admin/users/<uuid:pk>/status/', AdminUserStatusView.as_view(), name='admin-user-status'),
    path('admin/audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-log-list'),
    path('admin/dashboard-access-requests/', AdminDashboardAccessRequestListView.as_view(), name='admin-dashboard-access-request-list'),
    path('admin/dashboard-access-requests/<uuid:pk>/review/', AdminDashboardAccessRequestReviewView.as_view(), name='admin-dashboard-access-request-review'),
    path('dashboard-access-requests/', DashboardAccessRequestListCreateView.as_view(), name='dashboard-access-request-list-create'),

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
