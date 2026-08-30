from django.urls import path

from .views import CartDetailView, CartItemDetailView, CartItemListCreateView, CheckoutView, OrderDetailView, OrderListView


app_name = 'shopping'

urlpatterns = [
    path('', CartDetailView.as_view(), name='cart-detail'),
    path('items/', CartItemListCreateView.as_view(), name='cart-item-list'),
    path('items/<uuid:pk>/', CartItemDetailView.as_view(), name='cart-item-detail'),
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<uuid:pk>/', OrderDetailView.as_view(), name='order-detail'),
]
