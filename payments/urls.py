from django.urls import path

from .views import MpesaCallbackView, PaymentDetailView, PaymentInitiateView

app_name = 'payments'

urlpatterns = [
    path('initiate/', PaymentInitiateView.as_view(), name='payment-initiate'),
    path('<uuid:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('mpesa/callback/', MpesaCallbackView.as_view(), name='mpesa-callback'),
]
