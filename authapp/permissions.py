from rest_framework.permissions import BasePermission

from .models import StaffRole


class IsAuthenticated(BasePermission):
    """Allows access only to authenticated users."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_active)


class IsVerifiedUser(BasePermission):
    """Allows access only to verified users."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_verified
        )


class IsSeller(BasePermission):
    """Allows access only to sellers or admins."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_seller
        )


class IsBuyer(BasePermission):
    """Allows access only to buyers or admins."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_buyer
        )


class IsAdminRole(BasePermission):
    """Compatibility permission for super-admin-only administration."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_admin_role
        )


class IsKYCReviewer(BasePermission):
    """Allows sales reviewers and super administrators to review KYC."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.has_staff_role(StaffRole.SALES_REVIEWER)
        )


class IsSalesReviewer(IsKYCReviewer):
    pass


class IsFinance(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.has_staff_role(StaffRole.FINANCE)
        )


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.is_super_admin
        )


class IsOwner(BasePermission):
    """Allows access only to the owner of the object."""

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'user'):
            return obj.user == request.user
        return obj == request.user
