from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch, Q
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from django.utils import timezone
from django.shortcuts import get_object_or_404

from .models import (
    AdminAuditLog,
    DashboardAccessRequest,
    DashboardAccessRequestStatus,
    User,
    UserStaffRole,
)
from .operations_serializers import (
    AccountStatusChangeSerializer,
    AdminAuditLogSerializer,
    DashboardAccessRequestSerializer,
    DashboardAccessReviewSerializer,
    OperationsUserSerializer,
    StaffRoleChangeSerializer,
)
from .permissions import IsAuthenticated, IsSuperAdmin


class OperationsPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 100


def operations_users():
    return User.objects.select_related('profile', 'store').prefetch_related(
        Prefetch(
            'staff_role_assignments',
            queryset=UserStaffRole.objects.order_by('role'),
            to_attr='prefetched_staff_roles',
        ),
    )


class AdminUserListView(generics.ListAPIView):
    serializer_class = OperationsUserSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = OperationsPagination

    def get_queryset(self):
        queryset = operations_users()
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search)
                | Q(phone_number__icontains=search)
                | Q(profile__first_name__icontains=search)
                | Q(profile__last_name__icontains=search)
                | Q(store__name__icontains=search)
            )
        marketplace_role = self.request.query_params.get('role', '').strip()
        if marketplace_role:
            queryset = queryset.filter(role=marketplace_role)
        staff_role = self.request.query_params.get('staff_role', '').strip()
        if staff_role:
            queryset = queryset.filter(staff_role_assignments__role=staff_role)
        active = self.request.query_params.get('active', '').strip().lower()
        if active in {'true', 'false'}:
            queryset = queryset.filter(is_active=active == 'true')
        return queryset.distinct().order_by('-date_joined')


class AdminUserDetailView(generics.RetrieveAPIView):
    serializer_class = OperationsUserSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    queryset = operations_users()


class AdminUserStaffRoleView(generics.GenericAPIView):
    serializer_class = StaffRoleChangeSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            target = get_object_or_404(User.objects.select_for_update(), pk=kwargs['pk'])
            if target.pk == request.user.pk:
                raise PermissionDenied('You cannot change your own operational access.')
            if target.is_super_admin:
                raise PermissionDenied('Super-administrator access cannot be changed here.')

            role = serializer.validated_data['role']
            action = serializer.validated_data['action']
            if action == 'grant':
                _, changed = UserStaffRole.objects.get_or_create(
                    user=target,
                    role=role,
                    defaults={'granted_by': request.user},
                )
            else:
                deleted, _ = UserStaffRole.objects.filter(user=target, role=role).delete()
                changed = deleted > 0

            if changed:
                User.objects.filter(pk=target.pk).update(auth_version=F('auth_version') + 1)
                AdminAuditLog.objects.create(
                    actor=request.user,
                    action=f'staff_role_{"granted" if action == "grant" else "revoked"}',
                    target_type='user',
                    target_id=str(target.pk),
                    reason=serializer.validated_data['reason'],
                    metadata={'staff_role': role},
                )

        return Response(OperationsUserSerializer(operations_users().get(pk=target.pk)).data)


class AdminUserStatusView(generics.GenericAPIView):
    serializer_class = AccountStatusChangeSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            target = get_object_or_404(User.objects.select_for_update(), pk=kwargs['pk'])
            if target.pk == request.user.pk:
                raise PermissionDenied('You cannot suspend your own account.')
            if target.is_super_admin:
                raise PermissionDenied('Super-administrator accounts cannot be changed here.')

            action = serializer.validated_data['action']
            target.is_active = action == 'restore'
            target.auth_version = F('auth_version') + 1
            target.save(update_fields=['is_active', 'auth_version', 'updated_at'])
            target.refresh_from_db()
            AdminAuditLog.objects.create(
                actor=request.user,
                action=f'account_{"restored" if target.is_active else "suspended"}',
                target_type='user',
                target_id=str(target.pk),
                reason=serializer.validated_data['reason'],
                metadata={'active': target.is_active},
            )

        return Response(
            OperationsUserSerializer(operations_users().get(pk=target.pk)).data,
            status=status.HTTP_200_OK,
        )


class AdminAuditLogListView(generics.ListAPIView):
    serializer_class = AdminAuditLogSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = OperationsPagination

    def get_queryset(self):
        queryset = AdminAuditLog.objects.select_related('actor')
        action = self.request.query_params.get('action', '').strip()
        if action:
            queryset = queryset.filter(action=action)
        target_type = self.request.query_params.get('target_type', '').strip()
        if target_type:
            queryset = queryset.filter(target_type=target_type)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(actor__email__icontains=search)
                | Q(target_id__icontains=search)
                | Q(reason__icontains=search)
            )
        return queryset


class DashboardAccessRequestListCreateView(generics.ListCreateAPIView):
    serializer_class = DashboardAccessRequestSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = OperationsPagination

    def get_queryset(self):
        return DashboardAccessRequest.objects.filter(requester=self.request.user).select_related(
            'requester', 'reviewed_by',
        )

    def perform_create(self, serializer):
        user = self.request.user
        role = serializer.validated_data['requested_role']
        if user.is_super_admin or user.has_staff_role(role):
            raise ValidationError({'requested_role': 'This account already has this dashboard access.'})
        try:
            serializer.save(requester=user)
        except IntegrityError as error:
            raise ValidationError({'requested_role': 'A request for this dashboard is already pending.'}) from error


class AdminDashboardAccessRequestListView(generics.ListAPIView):
    serializer_class = DashboardAccessRequestSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = OperationsPagination

    def get_queryset(self):
        queryset = DashboardAccessRequest.objects.select_related('requester', 'reviewed_by')
        request_status = self.request.query_params.get('status', '').strip()
        if request_status in DashboardAccessRequestStatus.values:
            queryset = queryset.filter(status=request_status)
        requested_role = self.request.query_params.get('requested_role', '').strip()
        if requested_role:
            queryset = queryset.filter(requested_role=requested_role)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(requester__email__icontains=search)
                | Q(requester__profile__first_name__icontains=search)
                | Q(requester__profile__last_name__icontains=search)
            )
        return queryset


class AdminDashboardAccessRequestReviewView(generics.GenericAPIView):
    serializer_class = DashboardAccessReviewSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            access_request = get_object_or_404(
                DashboardAccessRequest.objects.select_for_update().select_related('requester'),
                pk=kwargs['pk'],
            )
            if access_request.status != DashboardAccessRequestStatus.PENDING:
                raise ValidationError({'status': 'Only pending access requests can be reviewed.'})
            if not access_request.requester.is_active:
                raise ValidationError({'requester': 'Suspended accounts cannot receive dashboard access.'})

            decision = serializer.validated_data['decision']
            access_request.status = decision
            access_request.reviewed_by = request.user
            access_request.reviewed_at = timezone.now()
            access_request.review_reason = serializer.validated_data['reason']
            access_request.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'review_reason', 'updated_at',
            ])

            if decision == DashboardAccessRequestStatus.APPROVED:
                _, changed = UserStaffRole.objects.get_or_create(
                    user=access_request.requester,
                    role=access_request.requested_role,
                    defaults={'granted_by': request.user},
                )
                if changed:
                    User.objects.filter(pk=access_request.requester_id).update(auth_version=F('auth_version') + 1)

            AdminAuditLog.objects.create(
                actor=request.user,
                action=f'dashboard_access_{decision}',
                target_type='dashboard_access_request',
                target_id=str(access_request.pk),
                reason=access_request.review_reason,
                metadata={
                    'requester_id': str(access_request.requester_id),
                    'staff_role': access_request.requested_role,
                },
            )

        return Response(DashboardAccessRequestSerializer(access_request).data)
