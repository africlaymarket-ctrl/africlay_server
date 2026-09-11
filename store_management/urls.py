from django.urls import path

from .views import (
    StoreDetailView,
    StoreKYCDetailView,
    StoreKYCReviewView,
    StoreKYCSubmitView,
    StoreListCreateView,
)


app_name = 'store_management'

urlpatterns = [
    path('', StoreListCreateView.as_view(), name='store-list-create'),
    path('<slug:slug>/', StoreDetailView.as_view(), name='store-detail'),
    path('<slug:slug>/kyc/', StoreKYCDetailView.as_view(), name='store-kyc-detail'),
    path('<slug:slug>/kyc/submit/', StoreKYCSubmitView.as_view(), name='store-kyc-submit'),
    path('<slug:slug>/kyc/review/', StoreKYCReviewView.as_view(), name='store-kyc-review'),
]