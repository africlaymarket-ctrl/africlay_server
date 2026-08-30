from django.urls import path

from .views import ProductDetailView, ProductListView, ProductManageListCreateView


app_name = 'product_management'

urlpatterns = [
    path('', ProductListView.as_view(), name='product-list'),
    path('manage/', ProductManageListCreateView.as_view(), name='product-manage-list'),
    path('manage/<uuid:pk>/', ProductDetailView.as_view(), name='product-manage-detail'),
    path('<slug:slug>/', ProductDetailView.as_view(), name='product-detail'),
]