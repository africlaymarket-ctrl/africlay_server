from django.db import transaction
from django.db.models import Q
from rest_framework import generics, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date

from authapp.models import AdminAuditLog
from authapp.permissions import IsAuthenticated, IsSalesReviewer, IsSuperAdmin
from reviews.models import Review
from shopping.models import OrderItem

from .models import Store, StoreKYC, StoreKYCStatus, StoreStatus
from .serializers import StoreKYCSerializer


class StoreOperationsPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 100


class StoreKYCQueueSerializer(StoreKYCSerializer):
    store_name = serializers.CharField(source='store.name', read_only=True)
    store_slug = serializers.CharField(source='store.slug', read_only=True)
    owner_id = serializers.UUIDField(source='store.owner_id', read_only=True)
    owner_email = serializers.EmailField(source='store.owner.email', read_only=True)
    reviewer_email = serializers.EmailField(source='reviewed_by.email', read_only=True, allow_null=True)

    class Meta(StoreKYCSerializer.Meta):
        fields = StoreKYCSerializer.Meta.fields + [
            'store_name', 'store_slug', 'owner_id', 'owner_email', 'reviewer_email',
        ]
        read_only_fields = fields


class StoreKYCReviewQueueView(generics.ListAPIView):
    serializer_class = StoreKYCQueueSerializer
    permission_classes = [IsAuthenticated, IsSalesReviewer]
    pagination_class = StoreOperationsPagination

    def get_queryset(self):
        queryset = StoreKYC.objects.select_related('store', 'store__owner', 'reviewed_by')
        review_status = self.request.query_params.get('status', '').strip()
        if review_status in StoreKYCStatus.values:
            queryset = queryset.filter(status=review_status)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(store__name__icontains=search)
                | Q(store__owner__email__icontains=search)
                | Q(business_name__icontains=search)
                | Q(business_registration_number__icontains=search)
            )
        submitted_from = parse_date(self.request.query_params.get('submitted_from', '').strip())
        if submitted_from:
            queryset = queryset.filter(submitted_at__date__gte=submitted_from)
        submitted_to = parse_date(self.request.query_params.get('submitted_to', '').strip())
        if submitted_to:
            queryset = queryset.filter(submitted_at__date__lte=submitted_to)
        return queryset.order_by('-submitted_at')


class AdminStoreSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source='owner.email', read_only=True)
    activity = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = [
            'id', 'owner', 'owner_email', 'name', 'slug', 'status', 'city', 'country',
            'created_at', 'updated_at', 'activity',
        ]
        read_only_fields = fields

    def get_activity(self, store):
        products = store.products.all()
        services = store.services.all()
        sold_items = OrderItem.objects.filter(seller_id=store.owner_id)
        reviews = Review.objects.filter(
            Q(store=store) | Q(product__store=store) | Q(service__store=store),
        ).distinct()
        latest_product = products.order_by('-updated_at').values_list('updated_at', flat=True).first()
        latest_service = services.order_by('-updated_at').values_list('updated_at', flat=True).first()
        latest_order = sold_items.order_by('-order__created_at').values_list('order__created_at', flat=True).first()
        latest_review = reviews.order_by('-created_at').values_list('created_at', flat=True).first()
        timestamps = [value for value in [latest_product, latest_service, latest_order, latest_review] if value]
        return {
            'products': products.count(),
            'services': services.count(),
            'orders': sold_items.values('order_id').distinct().count(),
            'reviews': reviews.count(),
            'last_activity_at': max(timestamps).isoformat() if timestamps else None,
        }


class StoreStatusChangeSerializer(serializers.Serializer):
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


class AdminStoreListView(generics.ListAPIView):
    serializer_class = AdminStoreSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = StoreOperationsPagination

    def get_queryset(self):
        queryset = Store.objects.select_related('owner')
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(slug__icontains=search) | Q(owner__email__icontains=search),
            )
        store_status = self.request.query_params.get('status', '').strip()
        if store_status in StoreStatus.values:
            queryset = queryset.filter(status=store_status)
        return queryset.order_by('name')


class AdminStoreDetailView(generics.RetrieveAPIView):
    serializer_class = AdminStoreSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    queryset = Store.objects.select_related('owner')


class AdminStoreStatusView(generics.GenericAPIView):
    serializer_class = StoreStatusChangeSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            store = get_object_or_404(Store.objects.select_for_update().select_related('owner'), pk=kwargs['pk'])
            if store.owner_id == request.user.pk:
                raise PermissionDenied('You cannot suspend your own store from this dashboard.')
            action = serializer.validated_data['action']
            store.status = StoreStatus.ACTIVE if action == 'restore' else StoreStatus.SUSPENDED
            store.save(update_fields=['status', 'updated_at'])
            AdminAuditLog.objects.create(
                actor=request.user,
                action=f'store_{"restored" if store.status == StoreStatus.ACTIVE else "suspended"}',
                target_type='store',
                target_id=str(store.pk),
                reason=serializer.validated_data['reason'],
                metadata={'store_name': store.name, 'status': store.status},
            )
        return Response(AdminStoreSerializer(store).data, status=status.HTTP_200_OK)
