from django.urls import path

from .views import (
    AdminFinanceSummaryView,
    AdminPayoutListView,
    AdminPayoutResolveView,
    FinanceTransactionDetailView,
    FinanceTransactionExportView,
    FinanceTransactionListView,
    MpesaCallbackView,
    MpesaOrderCallbackView,
    MpesaQueryTokenCallbackView,
    PaymentDetailView,
    PaymentInitiateView,
    MpesaStkPushView,
    PayoutAccountView,
    PayoutListCreateView,
    PayoutProviderResultView,
    PayoutProviderTimeoutView,
    WalletListCreateView,
    WalletTransactionListView,
)

app_name = 'payments'

urlpatterns = [
    path('wallets/', WalletListCreateView.as_view(), name='wallet-list'),
    path('wallets/<uuid:wallet_id>/transactions/', WalletTransactionListView.as_view(), name='wallet-transactions'),
    path('payout-account/', PayoutAccountView.as_view(), name='payout-account'),
    path('payouts/', PayoutListCreateView.as_view(), name='payout-list'),
    path('payouts/provider-result/<str:token>/', PayoutProviderResultView.as_view(), name='payout-provider-result'),
    path('payouts/provider-timeout/<str:token>/', PayoutProviderTimeoutView.as_view(), name='payout-provider-timeout'),
    path('admin/finance-summary/', AdminFinanceSummaryView.as_view(), name='admin-finance-summary'),
    path('admin/payouts/', AdminPayoutListView.as_view(), name='admin-payout-list'),
    path('admin/payouts/<uuid:pk>/resolve/', AdminPayoutResolveView.as_view(), name='admin-payout-resolve'),
    path('finance/transactions/', FinanceTransactionListView.as_view(), name='finance-transaction-list'),
    path('finance/transactions/export/', FinanceTransactionExportView.as_view(), name='finance-transaction-export'),
    path('finance/transactions/<uuid:pk>/', FinanceTransactionDetailView.as_view(), name='finance-transaction-detail'),
    path('initiate/', PaymentInitiateView.as_view(), name='payment-initiate'),
    path('<uuid:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('mpesa/callback/', MpesaQueryTokenCallbackView.as_view(), name='mpesa-callback'),
    path('provider-callback/<str:token>/', MpesaOrderCallbackView.as_view(), name='order-provider-callback'),
    path('mpesa/stk-push/', MpesaStkPushView.as_view(), name='mpesa-stk-push'),
    path('mpesa/callback/<str:token>/', MpesaCallbackView.as_view(), name='mpesa-callback-token'),
]
