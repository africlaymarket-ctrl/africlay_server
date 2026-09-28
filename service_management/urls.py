from django.urls import path

from .views import (
    BookingDetailView, BookingListCreateView,
    SellerBookingListView, SellerBookingStatusUpdateView,
    ServiceCategoryDetailView, ServiceCategoryListCreateView, ServiceDetailView,
    ServiceImageListCreateView, ServiceListView, ServiceManageListCreateView,
)

app_name = 'service_management'

urlpatterns = [
    path('categories/', ServiceCategoryListCreateView.as_view(), name='category-list'),
    path('categories/<uuid:pk>/', ServiceCategoryDetailView.as_view(), name='category-detail'),
    path('', ServiceListView.as_view(), name='service-list'),
    path('manage/', ServiceManageListCreateView.as_view(), name='service-manage-list'),
    path('manage/<uuid:pk>/', ServiceDetailView.as_view(), name='service-manage-detail'),
    path('manage/<uuid:pk>/images/', ServiceImageListCreateView.as_view(), name='service-image-list'),
    # Bookings
    path('bookings/', BookingListCreateView.as_view(), name='booking-list'),
    path('bookings/<uuid:pk>/', BookingDetailView.as_view(), name='booking-detail'),
    path('seller/bookings/', SellerBookingListView.as_view(), name='seller-booking-list'),
    path('seller/bookings/<uuid:pk>/', SellerBookingStatusUpdateView.as_view(), name='seller-booking-status'),
    path('<slug:slug>/', ServiceDetailView.as_view(), name='service-detail'),
]
