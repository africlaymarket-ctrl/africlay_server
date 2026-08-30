from rest_framework import generics, permissions
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404

from authapp.permissions import IsSeller, IsVerifiedUser
from store_management.models import Store, StoreStatus

from .models import Product, ProductStatus
from .serializers import ProductSerializer


class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return Product.objects.filter(
            status=ProductStatus.PUBLISHED,
            store__status=StoreStatus.ACTIVE,
        ).select_related('store')


class ProductManageListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return Product.objects.filter(store__owner=self.request.user).select_related('store')

    def perform_create(self, serializer):
        try:
            store = self.request.user.store
        except Store.DoesNotExist as error:
            raise ValidationError({'store': 'Create a store before adding products.'}) from error
        try:
            with transaction.atomic():
                serializer.save(store=store)
        except IntegrityError as error:
            raise ValidationError({'product': 'A product with this slug or SKU already exists.'}) from error


class ProductDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ProductSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        products = Product.objects.select_related('store')
        if self.kwargs.get('slug'):
            return products.filter(status=ProductStatus.PUBLISHED, store__status=StoreStatus.ACTIVE)
        if self.request.user.is_admin_role:
            return products
        return products.filter(store__owner=self.request.user)

    def get_object(self):
        if 'slug' in self.kwargs:
            return get_object_or_404(self.get_queryset(), slug=self.kwargs['slug'])
        return super().get_object()

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS and self.kwargs.get('slug'):
            return [permissions.AllowAny()]
        return [IsSeller(), IsVerifiedUser()]

    def update(self, request, *args, **kwargs):
        if 'slug' in self.kwargs:
            raise MethodNotAllowed(request.method)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if 'slug' in self.kwargs:
            raise MethodNotAllowed(request.method)
        return super().partial_update(request, *args, **kwargs)

    def perform_update(self, serializer):
        if serializer.instance.store.owner_id != self.request.user.id and not self.request.user.is_admin_role:
            raise PermissionDenied('You do not own this product.')
        serializer.save()
