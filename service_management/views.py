from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.permissions import IsAdminRole, IsSeller, IsVerifiedUser
from core.services import require_approved_kyc_for_publication
from store_management.models import Store, StoreKYCStatus, StoreStatus

from .models import Service, ServiceCategory, ServiceImage, ServiceStatus
from .serializers import ServiceCategorySerializer, ServiceImageSerializer, ServiceSerializer


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
