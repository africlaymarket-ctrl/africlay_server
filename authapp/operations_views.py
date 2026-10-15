from django.db import IntegrityError, transaction
import uuid

from datetime import timedelta

from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import TruncDate
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

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
    AdminOrderSerializer,
    AdminProductSerializer,
    AdminServiceSerializer,
    AdminAuditLogSerializer,
    CatalogStatusChangeSerializer,
    DashboardAccessRequestSerializer,
    DashboardAccessReviewSerializer,
    OperationsUserSerializer,
    StaffRoleChangeSerializer,
    StaffRoleEmailAssignmentSerializer,
)
from .permissions import IsAuthenticated, IsSuperAdmin
from product_management.models import Product, ProductStatus
from service_management.models import Service, ServiceStatus
from shopping.models import Order, OrderStatus, PaymentStatus
from store_management.models import Store, StoreKYC, StoreKYCStatus, StoreStatus


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


class AdminStaffRoleByEmailView(generics.GenericAPIView):
    serializer_class = StaffRoleEmailAssignmentSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            matches = list(User.objects.select_for_update().filter(
                email__iexact=serializer.validated_data['email'],
            ).order_by('date_joined')[:2])
            if not matches:
                raise ValidationError({'email': 'No account exists with this email address.'})
            if len(matches) > 1:
                raise ValidationError({'email': 'Multiple accounts match this email. Resolve the account conflict first.'})
            target = matches[0]
            if target.pk == request.user.pk:
                raise PermissionDenied('You cannot change your own operational access.')
            if target.is_super_admin:
                raise PermissionDenied('Super-administrator access cannot be changed here.')

            role = serializer.validated_data['role']
            action = serializer.validated_data['action']
            if action == 'grant' and not target.is_active:
                raise ValidationError({'email': 'Restore this account before granting dashboard access.'})
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
                    metadata={'staff_role': role, 'assignment_method': 'email'},
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


class AdminOperationsSummaryView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, *args, **kwargs):
        try:
            days = int(request.query_params.get('days', 30))
        except (TypeError, ValueError):
            days = 30
        days = days if days in {7, 30, 90} else 30
        start_date = timezone.localdate() - timedelta(days=days - 1)
        accounts = User.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True)),
            suspended=Count('id', filter=Q(is_active=False)),
            verified=Count('id', filter=Q(is_verified=True)),
        )
        stores = Store.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status=StoreStatus.ACTIVE)),
            suspended=Count('id', filter=Q(status=StoreStatus.SUSPENDED)),
        )
        products = Product.objects.aggregate(
            total=Count('id'),
            draft=Count('id', filter=Q(status=ProductStatus.DRAFT)),
            published=Count('id', filter=Q(status=ProductStatus.PUBLISHED)),
            archived=Count('id', filter=Q(status=ProductStatus.ARCHIVED)),
        )
        services = Service.objects.aggregate(
            total=Count('id'),
            draft=Count('id', filter=Q(status=ServiceStatus.DRAFT)),
            published=Count('id', filter=Q(status=ServiceStatus.PUBLISHED)),
            archived=Count('id', filter=Q(status=ServiceStatus.ARCHIVED)),
        )
        orders = Order.objects.aggregate(
            total=Count('id'),
            pending=Count('id', filter=Q(status=OrderStatus.PENDING)),
            processing=Count('id', filter=Q(status=OrderStatus.PROCESSING)),
            shipped=Count('id', filter=Q(status=OrderStatus.SHIPPED)),
            delivered=Count('id', filter=Q(status=OrderStatus.DELIVERED)),
            cancelled=Count('id', filter=Q(status=OrderStatus.CANCELLED)),
            payment_pending=Count('id', filter=Q(payment_status=PaymentStatus.PENDING)),
            payment_failed=Count('id', filter=Q(payment_status=PaymentStatus.FAILED)),
        )

        def daily_counts(queryset, date_field):
            rows = queryset.filter(**{f'{date_field}__date__gte': start_date}).annotate(
                day=TruncDate(date_field),
            ).values('day').annotate(count=Count('id')).order_by('day')
            return {row['day']: row['count'] for row in rows}

        account_counts = daily_counts(User.objects.all(), 'date_joined')
        store_counts = daily_counts(Store.objects.all(), 'created_at')
        order_rows = Order.objects.filter(created_at__date__gte=start_date).annotate(
            day=TruncDate('created_at'),
        ).values('day').annotate(count=Count('id'), gross=Sum('total_amount')).order_by('day')
        order_counts = {row['day']: row for row in order_rows}
        trends = []
        for offset in range(days):
            day = start_date + timedelta(days=offset)
            order_day = order_counts.get(day, {})
            trends.append({
                'date': day.isoformat(),
                'accounts': account_counts.get(day, 0),
                'stores': store_counts.get(day, 0),
                'orders': order_day.get('count', 0),
                'gross_order_value': order_day.get('gross') or 0,
            })
        return Response({
            'accounts': accounts,
            'stores': {
                **stores,
                'kyc_pending': StoreKYC.objects.filter(status=StoreKYCStatus.PENDING).count(),
            },
            'products': products,
            'services': services,
            'orders': orders,
            'dashboard_access_pending': DashboardAccessRequest.objects.filter(
                status=DashboardAccessRequestStatus.PENDING,
            ).count(),
            'trend_days': days,
            'trends': trends,
        })


def catalog_configuration(kind):
    if kind == 'products':
        return Product, ProductStatus, AdminProductSerializer
    if kind == 'services':
        return Service, ServiceStatus, AdminServiceSerializer
    raise ValidationError({'kind': 'Catalog kind must be products or services.'})


class AdminCatalogListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = OperationsPagination

    def get_serializer_class(self):
        kind = self.kwargs.get('kind')
        return catalog_configuration(kind)[2] if kind else AdminProductSerializer

    def get_queryset(self):
        model, status_choices, _ = catalog_configuration(self.kwargs['kind'])
        queryset = model.objects.select_related('store', 'store__owner', 'category')
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(slug__icontains=search)
                | Q(store__name__icontains=search)
                | Q(store__owner__email__icontains=search)
            )
        catalog_status = self.request.query_params.get('status', '').strip()
        if catalog_status in status_choices.values:
            queryset = queryset.filter(status=catalog_status)
        return queryset.order_by('-updated_at')


class AdminCatalogStatusView(generics.GenericAPIView):
    serializer_class = CatalogStatusChangeSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        model, _, response_serializer = catalog_configuration(kwargs['kind'])
        with transaction.atomic():
            item = get_object_or_404(
                model.objects.select_for_update().select_related('store', 'store__owner', 'category'),
                pk=kwargs['pk'],
            )
            action = serializer.validated_data['action']
            target_status = 'archived' if action == 'archive' else 'draft'
            if action == 'restore_to_draft' and item.status != 'archived':
                raise ValidationError({'action': 'Only archived listings can be restored to draft.'})
            if item.status == target_status:
                raise ValidationError({'action': f'This listing is already {target_status}.'})
            previous_status = item.status
            item.status = target_status
            item.save(update_fields=['status', 'updated_at'])
            AdminAuditLog.objects.create(
                actor=request.user,
                action=f'{kwargs["kind"][:-1]}_{"archived" if action == "archive" else "restored_to_draft"}',
                target_type=kwargs['kind'][:-1],
                target_id=str(item.pk),
                reason=serializer.validated_data['reason'],
                metadata={
                    'name': item.name,
                    'store_id': str(item.store_id),
                    'previous_status': previous_status,
                    'status': target_status,
                },
            )
        return Response(response_serializer(item).data, status=status.HTTP_200_OK)


class AdminOrderListView(generics.ListAPIView):
    serializer_class = AdminOrderSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = OperationsPagination

    def get_queryset(self):
        queryset = Order.objects.select_related('buyer').prefetch_related('items__seller').annotate(
            item_count=Count('items', distinct=True),
        )
        search = self.request.query_params.get('search', '').strip()
        if search:
            query = Q(buyer__email__icontains=search) | Q(tracking_number__icontains=search)
            try:
                query |= Q(pk=uuid.UUID(search))
            except ValueError:
                pass
            queryset = queryset.filter(query)
        order_status = self.request.query_params.get('status', '').strip()
        if order_status in OrderStatus.values:
            queryset = queryset.filter(status=order_status)
        payment_status = self.request.query_params.get('payment_status', '').strip()
        if payment_status in PaymentStatus.values:
            queryset = queryset.filter(payment_status=payment_status)
        return queryset.order_by('-created_at')


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
