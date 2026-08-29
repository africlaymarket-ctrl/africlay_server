from django.urls import path

from .views import StoreDetailView, StoreListCreateView


app_name = 'store_management'

urlpatterns = [
    path('', StoreListCreateView.as_view(), name='store-list-create'),
    path('<slug:slug>/', StoreDetailView.as_view(), name='store-detail'),
]