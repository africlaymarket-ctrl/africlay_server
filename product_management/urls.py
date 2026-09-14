from django.urls import path

from .views import (
    CategoryDetailView, CategoryListCreateView, ProductDetailView, ProductImageListCreateView, ProductVariantListCreateView,
    ProductListView, ProductManageListCreateView, TagDetailView, TagListCreateView,
)


app_name = 'product_management'

urlpatterns = [
	path('categories/', CategoryListCreateView.as_view(), name='category-list'),
	path('categories/<uuid:pk>/', CategoryDetailView.as_view(), name='category-detail'),
	path('tags/', TagListCreateView.as_view(), name='tag-list'),
	path('tags/<uuid:pk>/', TagDetailView.as_view(), name='tag-detail'),
	path('', ProductListView.as_view(), name='product-list'),
	path('manage/', ProductManageListCreateView.as_view(), name='product-manage-list'),
	path('manage/<uuid:pk>/', ProductDetailView.as_view(), name='product-manage-detail'),
	path('manage/<uuid:pk>/images/', ProductImageListCreateView.as_view(), name='product-image-list'),
	path('manage/<uuid:pk>/variants/', ProductVariantListCreateView.as_view(), name='product-variant-list'),
    path('<slug:slug>/', ProductDetailView.as_view(), name='product-detail'),
]
