from django.urls import path

from .views import (
    MpesaCallbackView,
    MpesaQueryTokenCallbackView,
    PaymentDetailView,
    PaymentInitiateView,
    MpesaStkPushView,
    WalletListCreateView,
    WalletTransactionListView,
)

app_name = 'payments'

urlpatterns = [
    path('wallets/', WalletListCreateView.as_view(), name='wallet-list'),
    path('wallets/<uuid:wallet_id>/transactions/', WalletTransactionListView.as_view(), name='wallet-transactions'),
    path('initiate/', PaymentInitiateView.as_view(), name='payment-initiate'),
    path('<uuid:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('mpesa/callback/', MpesaQueryTokenCallbackView.as_view(), name='mpesa-callback'),
    path('mpesa/stk-push/', MpesaStkPushView.as_view(), name='mpesa-stk-push'),
    path('mpesa/callback/<str:token>/', MpesaCallbackView.as_view(), name='mpesa-callback-token'),
]