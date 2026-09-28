from rest_framework import generics, permissions
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404

from authapp.permissions import IsAdminRole, IsSeller, IsVerifiedUser
from core.services import require_approved_kyc_for_publication
from store_management.models import Store, StoreKYCStatus, StoreStatus

from .models import Category, Product, ProductImage, ProductStatus, Tag
from .serializers import CategorySerializer, ProductImageSerializer, ProductSerializer, TagSerializer


class CategoryListCreateView(generics.ListCreateAPIView):
    queryset = Category.objects.select_related('parent')
    serializer_class = CategorySerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class CategoryDetailView(generics.RetrieveUpdateAPIView):
    queryset = Category.objects.select_related('parent')
    serializer_class = CategorySerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class TagListCreateView(generics.ListCreateAPIView):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class TagDetailView(generics.RetrieveUpdateAPIView):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = Product.objects.filter(
            status=ProductStatus.PUBLISHED,
            store__status=StoreStatus.ACTIVE,
            store__kyc__status=StoreKYCStatus.APPROVED,
        ).select_related('store', 'category').prefetch_related('tags', 'images')

        category_slug = self.request.query_params.get('category')
        tag_slug = self.request.query_params.get('tag')
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)
        if tag_slug:
            queryset = queryset.filter(tags__slug=tag_slug)
        return queryset.distinct()


class ProductManageListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return Product.objects.filter(store__owner=self.request.user).select_related('store', 'category').prefetch_related('tags', 'images')

    def perform_create(self, serializer):
        try:
            store = self.request.user.store
        except Store.DoesNotExist as error:
            raise ValidationError({'store': 'Create a store before adding products.'}) from error
        try:
            with transaction.atomic():
                require_approved_kyc_for_publication(store, serializer.validated_data.get('status'), ProductStatus.PUBLISHED)
                serializer.save(store=store)
        except IntegrityError as error:
            raise ValidationError({'product': 'A product with this slug or SKU already exists.'}) from error


class ProductDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ProductSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        products = Product.objects.select_related('store', 'category').prefetch_related('tags', 'images')
        if self.kwargs.get('slug'):
            return products.filter(status=ProductStatus.PUBLISHED, store__status=StoreStatus.ACTIVE, store__kyc__status=StoreKYCStatus.APPROVED)
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
        require_approved_kyc_for_publication(
            serializer.instance.store,
            serializer.validated_data.get('status', serializer.instance.status),
            ProductStatus.PUBLISHED,
        )
        serializer.save()


class ProductImageListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductImageSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        return ProductImage.objects.filter(product_id=self.kwargs['pk'], product__store__owner=self.request.user)

    def perform_create(self, serializer):
        product = get_object_or_404(Product, pk=self.kwargs['pk'], store__owner=self.request.user)
        serializer.save(product=product)

