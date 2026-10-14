from rest_framework import serializers

from .models import AdminAuditLog, DashboardAccessRequest, StaffRole, User


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
