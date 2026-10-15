from rest_framework import serializers

from .models import AdminAuditLog, DashboardAccessRequest, StaffRole, User
from product_management.models import Product
from service_management.models import Service
from shopping.models import Order


class OperationsUserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    staff_roles = serializers.SerializerMethodField()
    store = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'phone_number', 'full_name', 'role', 'staff_roles',
            'is_verified', 'is_active', 'is_staff', 'last_login', 'date_joined',
            'created_at', 'updated_at', 'store',
        ]
        read_only_fields = fields

    def get_staff_roles(self, user):
        assignments = getattr(user, 'prefetched_staff_roles', None)
        if assignments is not None:
            return [assignment.role for assignment in assignments]
        return list(user.staff_role_assignments.order_by('role').values_list('role', flat=True))

    def get_store(self, user):
        store = getattr(user, 'store', None)
        if store is None:
            return None
        return {'id': str(store.id), 'name': store.name, 'slug': store.slug, 'status': store.status}


class StaffRoleChangeSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=StaffRole.choices)
    action = serializers.ChoiceField(choices=['grant', 'revoke'])
    reason = serializers.CharField(max_length=1000)

    def validate_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('A reason is required.')
        return value


class StaffRoleEmailAssignmentSerializer(StaffRoleChangeSerializer):
    email = serializers.EmailField(max_length=255)

    def validate_email(self, value):
        return value.strip().lower()


class AccountStatusChangeSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['suspend', 'restore'])
    reason = serializers.CharField(max_length=1000)
    confirm = serializers.BooleanField()

    def validate_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('A reason is required.')
        return value

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError('Explicit confirmation is required.')
        return value


class AdminAuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source='actor.email', read_only=True, allow_null=True)

    class Meta:
        model = AdminAuditLog
        fields = [
            'id', 'actor', 'actor_email', 'action', 'target_type', 'target_id',
            'reason', 'metadata', 'created_at',
        ]
        read_only_fields = fields


class DashboardAccessRequestSerializer(serializers.ModelSerializer):
    requester_email = serializers.EmailField(source='requester.email', read_only=True)
    requester_name = serializers.CharField(source='requester.full_name', read_only=True)
    reviewer_email = serializers.EmailField(source='reviewed_by.email', read_only=True, allow_null=True)

    class Meta:
        model = DashboardAccessRequest
        fields = [
            'id', 'requester', 'requester_email', 'requester_name', 'requested_role',
            'request_reason', 'status', 'reviewed_by', 'reviewer_email', 'reviewed_at',
            'review_reason', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'requester', 'requester_email', 'requester_name', 'status',
            'reviewed_by', 'reviewer_email', 'reviewed_at', 'review_reason',
            'created_at', 'updated_at',
        ]

    def validate_request_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Explain why this dashboard access is needed.')
        return value


class DashboardAccessReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['approved', 'rejected'])
    reason = serializers.CharField(max_length=1000)

    def validate_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('A review reason is required.')
        return value


class AdminProductSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source='store.name', read_only=True)
    owner_email = serializers.EmailField(source='store.owner.email', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'sku', 'status', 'price', 'currency',
            'stock_quantity', 'store', 'store_name', 'owner_email', 'category',
            'category_name', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class AdminServiceSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source='store.name', read_only=True)
    owner_email = serializers.EmailField(source='store.owner.email', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = Service
        fields = [
            'id', 'name', 'slug', 'status', 'price', 'currency',
            'duration_minutes', 'store', 'store_name', 'owner_email', 'category',
            'category_name', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class CatalogStatusChangeSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['archive', 'restore_to_draft'])
    reason = serializers.CharField(max_length=1000)
    confirm = serializers.BooleanField()

    def validate_reason(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('A moderation reason is required.')
        return value

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError('Explicit confirmation is required.')
        return value


class AdminOrderSerializer(serializers.ModelSerializer):
    buyer_email = serializers.EmailField(source='buyer.email', read_only=True)
    item_count = serializers.IntegerField(read_only=True)
    seller_emails = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'buyer', 'buyer_email', 'item_count', 'seller_emails',
            'total_amount', 'item_subtotal', 'shipping_cost', 'currency',
            'status', 'payment_method', 'payment_status', 'payment_option',
            'amount_paid', 'delivery_fee_paid', 'shipping_city',
            'shipping_country', 'courier_name', 'tracking_number',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_seller_emails(self, order):
        return sorted({item.seller.email for item in order.items.all() if item.seller_id})
