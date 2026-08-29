from django.urls import path

from .views import CartDetailView, CartItemDetailView, CartItemListCreateView


app_name = 'shopping'

urlpatterns = [
    path('', CartDetailView.as_view(), name='cart-detail'),
    path('items/', CartItemListCreateView.as_view(), name='cart-item-list'),
    path('items/<uuid:pk>/', CartItemDetailView.as_view(), name='cart-item-detail'),
]
