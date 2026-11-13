from rest_framework import generics, permissions, status
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.response import Response
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from uuid import UUID
from collections import defaultdict

from authapp.permissions import IsAdminRole, IsSeller, IsVerifiedUser
from core.services import require_approved_kyc_for_publication
from core.pagination import CatalogPagination
from store_management.models import Store, StoreKYCStatus, StoreStatus

from .models import Category, Product, ProductImage, ProductStatus, Tag
from .serializers import (
    CategorySerializer, ProductCatalogSerializer, ProductImageSerializer, ProductSerializer,
    SellerCategoryPathSerializer, TagSerializer,
)


def category_branch_ids(slug):
    """Return a category and all descendants, regardless of tree depth."""
    children = defaultdict(list)
    root = None
    for category_id, parent_id, category_slug in Category.objects.values_list('id', 'parent_id', 'slug'):
        children[parent_id].append(category_id)
        if category_slug == slug:
            root = category_id
    if root is None:
        return []
    branch = set()
    stack = [root]
    while stack:
        category_id = stack.pop()
        if category_id in branch:
            continue
        branch.add(category_id)
        stack.extend(children[category_id])
    return list(branch)


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


class SellerCategoryPathCreateView(generics.GenericAPIView):
    serializer_class = SellerCategoryPathSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(
            {
                'parent': CategorySerializer(result['parent']).data,
                'child': CategorySerializer(result['child']).data,
                'created': result['created'],
            },
            status=status.HTTP_201_CREATED if result['created'] else status.HTTP_200_OK,
        )


class TagListCreateView(generics.ListCreateAPIView):
    queryset = Tag.objects.prefetch_related('categories')
    serializer_class = TagSerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class TagDetailView(generics.RetrieveUpdateAPIView):
    queryset = Tag.objects.prefetch_related('categories')
    serializer_class = TagSerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAdminRole()]


class ProductListView(generics.ListAPIView):
    serializer_class = ProductCatalogSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = CatalogPagination

    def get_queryset(self):
        queryset = Product.objects.filter(
            status=ProductStatus.PUBLISHED,
            store__status=StoreStatus.ACTIVE,
            store__kyc__status=StoreKYCStatus.APPROVED,
        ).select_related('store', 'category').prefetch_related(
            'tags',
            Prefetch(
                'images',
                queryset=ProductImage.objects.order_by(
                    '-is_primary', 'display_order', 'created_at',
                )[:1],
                to_attr='catalog_images',
            ),
        )

        category_slug = self.request.query_params.get('category')
        tag_slug = self.request.query_params.get('tag')
        store_key = self.request.query_params.get('store')
        if category_slug:
            queryset = queryset.filter(category_id__in=category_branch_ids(category_slug))
        if tag_slug:
            queryset = queryset.filter(tags__slug=tag_slug)
        search = self.request.query_params.get('search', '').strip()[:100]
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(category__name__icontains=search)
                | Q(category__parent__name__icontains=search)
                | Q(category__parent__parent__name__icontains=search)
                | Q(tags__name__icontains=search)
            )
        if store_key:
            try:
                queryset = queryset.filter(store_id=UUID(store_key))
            except ValueError:
                queryset = queryset.filter(store__slug=store_key)
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
        if self.request.method not in permissions.SAFE_METHODS:
            products = products.select_for_update(of=('self',))
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
        with transaction.atomic():
            return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if 'slug' in self.kwargs:
            raise MethodNotAllowed(request.method)
        return self.update(request, *args, partial=True, **kwargs)

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
        with transaction.atomic():
            product = get_object_or_404(
                Product.objects.select_for_update(), pk=self.kwargs['pk'], store__owner=self.request.user,
            )
            if product.images.count() >= 8:
                raise ValidationError({'image': 'A product can have at most eight images. Remove one before adding another.'})
            if serializer.validated_data.get('is_primary'):
                product.images.filter(is_primary=True).update(is_primary=False)
            elif not product.images.exists():
                serializer.validated_data['is_primary'] = True
            serializer.save(product=product)


class ProductImageDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductImageSerializer
    permission_classes = [IsSeller, IsVerifiedUser]
    lookup_url_kwarg = 'image_id'

    def get_queryset(self):
        return ProductImage.objects.filter(product_id=self.kwargs['pk'], product__store__owner=self.request.user)

    def update(self, request, *args, **kwargs):
        if 'image' in request.data or 'thumbnail' in request.data:
            raise ValidationError({'image': 'Use Add images to upload a replacement photo.'})
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=serializer.instance.product_id)
            if serializer.validated_data.get('is_primary'):
                ProductImage.objects.filter(product_id=serializer.instance.product_id, is_primary=True).exclude(
                    pk=serializer.instance.pk,
                ).update(is_primary=False)
            serializer.save()

    def perform_destroy(self, instance):
        for file in (instance.image, instance.thumbnail):
            if file and file.name:
                try:
                    file.storage.delete(file.name)
                except Exception:
                    raise ValidationError({'image': 'This photo could not be removed from storage. Please try again.'}) from None
        with transaction.atomic():
            was_primary = instance.is_primary
            product_id = instance.product_id
            instance.delete()
            if was_primary:
                next_image = ProductImage.objects.filter(product_id=product_id).order_by('display_order', 'created_at').first()
                if next_image:
                    next_image.is_primary = True
                    next_image.save(update_fields=['is_primary'])

