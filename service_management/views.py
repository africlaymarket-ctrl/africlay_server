from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.permissions import IsAdminRole, IsAuthenticated, IsSeller, IsVerifiedUser
from core.services import require_approved_kyc_for_publication
from store_management.models import Store, StoreKYCStatus, StoreStatus

from .models import Booking, BookingStatus, Service, ServiceCategory, ServiceImage, ServiceStatus
from .serializers import BookingSerializer, BookingStatusUpdateSerializer, ServiceCategorySerializer, ServiceImageSerializer, ServiceSerializer


class ServiceCategoryListCreateView(generics.ListCreateAPIView):
    queryset = ServiceCategory.objects.select_related('parent')
    serializer_class = ServiceCategorySerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class ServiceCategoryDetailView(generics.RetrieveUpdateAPIView):
    queryset = ServiceCategory.objects.select_related('parent')
    serializer_class = ServiceCategorySerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class ServiceListView(generics.ListAPIView):
    serializer_class = ServiceSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Service.objects.filter(
            status=ServiceStatus.PUBLISHED, store__status=StoreStatus.ACTIVE,
            store__kyc__status=StoreKYCStatus.APPROVED,
        ).select_related('store', 'category').prefetch_related('tags', 'images')


class ServiceManageListCreateView(generics.ListCreateAPIView):
    serializer_class = ServiceSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return Service.objects.filter(store__owner=self.request.user).select_related('store', 'category').prefetch_related('tags', 'images')

    def perform_create(self, serializer):
        try:
            store = self.request.user.store
        except Store.DoesNotExist as error:
            raise ValidationError({'store': 'Create a store before adding services.'}) from error
        try:
            with transaction.atomic():
                require_approved_kyc_for_publication(store, serializer.validated_data.get('status'), ServiceStatus.PUBLISHED)
                serializer.save(store=store)
        except IntegrityError as error:
            raise ValidationError({'service': 'A service with this slug already exists.'}) from error


class ServiceDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ServiceSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        services = Service.objects.select_related('store', 'category').prefetch_related('tags', 'images')
        if self.kwargs.get('slug'):
            return services.filter(status=ServiceStatus.PUBLISHED, store__status=StoreStatus.ACTIVE, store__kyc__status=StoreKYCStatus.APPROVED)
        if self.request.user.is_admin_role:
            return services
        return services.filter(store__owner=self.request.user)

    def get_object(self):
        if 'slug' in self.kwargs:
            return get_object_or_404(self.get_queryset(), slug=self.kwargs['slug'])
        return super().get_object()

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method in permissions.SAFE_METHODS and self.kwargs.get('slug') else [IsSeller(), IsVerifiedUser()]

    def perform_update(self, serializer):
        if serializer.instance.store.owner_id != self.request.user.id and not self.request.user.is_admin_role:
            raise PermissionDenied('You do not own this service.')
        require_approved_kyc_for_publication(
            serializer.instance.store,
            serializer.validated_data.get('status', serializer.instance.status),
            ServiceStatus.PUBLISHED,
        )
        serializer.save()

    def perform_destroy(self, instance):
        for image in instance.images.all():
            image.image.delete(save=False)
        instance.delete()


class ServiceImageListCreateView(generics.ListCreateAPIView):
    serializer_class = ServiceImageSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return ServiceImage.objects.filter(service_id=self.kwargs['pk'], service__store__owner=self.request.user)

    def perform_create(self, serializer):
        service = get_object_or_404(Service, pk=self.kwargs['pk'], store__owner=self.request.user)
        serializer.save(service=service)


class BookingListCreateView(generics.ListCreateAPIView):
    """Customers create and list their own bookings."""
    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Booking.objects.filter(customer=self.request.user).select_related('service')

    def perform_create(self, serializer):
        service = get_object_or_404(
            Service,
            pk=serializer.validated_data['service'].pk,
            status=ServiceStatus.PUBLISHED,
            store__status=StoreStatus.ACTIVE,
            store__kyc__status=StoreKYCStatus.APPROVED,
        )
        serializer.save(customer=self.request.user, service=service)


class BookingDetailView(generics.RetrieveDestroyAPIView):
    """Customer can view or cancel their booking."""
    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Booking.objects.filter(customer=self.request.user).select_related('service')

    def perform_destroy(self, instance):
        if instance.status not in [BookingStatus.PENDING, BookingStatus.CONFIRMED]:
            raise ValidationError({'status': 'Only pending or confirmed bookings can be cancelled.'})
        instance.status = BookingStatus.CANCELLED
        instance.save(update_fields=['status', 'updated_at'])


class SellerBookingListView(generics.ListAPIView):
    """Sellers list bookings for their services."""
    serializer_class = BookingSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return Booking.objects.filter(service__store__owner=self.request.user).select_related('service', 'customer')


class SellerBookingStatusUpdateView(generics.UpdateAPIView):
    """Sellers update booking status."""
    serializer_class = BookingStatusUpdateSerializer
    permission_classes = [IsSeller, IsVerifiedUser]
    http_method_names = ['patch']

    def get_queryset(self):
        return Booking.objects.filter(service__store__owner=self.request.user)
