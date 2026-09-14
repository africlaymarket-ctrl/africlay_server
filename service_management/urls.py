from django.urls import path

from .views import (
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
    path('<slug:slug>/', ServiceDetailView.as_view(), name='service-detail'),
]
