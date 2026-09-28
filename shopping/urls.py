from django.urls import path

from .views import (
    CartDetailView, CartItemDetailView, CartItemListCreateView, CheckoutView,
    OrderDeliveryConfirmationView, OrderDetailView, OrderListView,
    SellerOrderDetailView, SellerOrderListView, SellerOrderStatusUpdateView, SellerOrderStatusView,
    WishlistView, WishlistItemListCreateView, WishlistItemDeleteView,
)


app_name = 'shopping'

urlpatterns = [
    path('', CartDetailView.as_view(), name='cart-detail'),
    path('items/', CartItemListCreateView.as_view(), name='cart-item-list'),
    path('items/<uuid:pk>/', CartItemDetailView.as_view(), name='cart-item-detail'),
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<uuid:pk>/', OrderDetailView.as_view(), name='order-detail'),
    path('orders/<uuid:pk>/confirm-delivery/', OrderDeliveryConfirmationView.as_view(), name='order-confirm-delivery'),
    path('seller/orders/', SellerOrderListView.as_view(), name='seller-order-list'),
    path('seller/orders/<uuid:pk>/', SellerOrderStatusUpdateView.as_view(), name='seller-order-status'),
    path('seller/orders/<uuid:pk>/detail/', SellerOrderDetailView.as_view(), name='seller-order-detail'),
    path('seller/orders/<uuid:pk>/status/', SellerOrderStatusView.as_view(), name='seller-order-status-update'),
    path('wishlist/', WishlistView.as_view(), name='wishlist'),
    path('wishlist/items/', WishlistItemListCreateView.as_view(), name='wishlist-item-list'),
    path('wishlist/items/<uuid:pk>/', WishlistItemDeleteView.as_view(), name='wishlist-item-delete'),
]
