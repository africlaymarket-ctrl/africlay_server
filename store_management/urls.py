from django.urls import path

from .views import (
    StoreDetailView,
    StoreKYCDetailView,
    StoreKYCReviewView,
    StoreKYCSubmitView,
    StoreListCreateView,
)
from .operations import (
    AdminStoreDetailView,
    AdminStoreListView,
    AdminStoreStatusView,
    StoreKYCReviewQueueView,
)


app_name = 'store_management'

urlpatterns = [
    path('', StoreListCreateView.as_view(), name='store-list-create'),
    path('kyc/review-queue/', StoreKYCReviewQueueView.as_view(), name='store-kyc-review-queue'),
    path('admin/', AdminStoreListView.as_view(), name='admin-store-list'),
    path('admin/<uuid:pk>/', AdminStoreDetailView.as_view(), name='admin-store-detail'),
    path('admin/<uuid:pk>/status/', AdminStoreStatusView.as_view(), name='admin-store-status'),
    path('<slug:slug>/', StoreDetailView.as_view(), name='store-detail'),
    path('<slug:slug>/kyc/', StoreKYCDetailView.as_view(), name='store-kyc-detail'),
    path('<slug:slug>/kyc/submit/', StoreKYCSubmitView.as_view(), name='store-kyc-submit'),
    path('<slug:slug>/kyc/review/', StoreKYCReviewView.as_view(), name='store-kyc-review'),
]
