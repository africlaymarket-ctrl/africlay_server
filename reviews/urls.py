from django.urls import path
from .views import ReviewDetailView, ReviewListCreateView

app_name = 'reviews'

urlpatterns = [
    path('', ReviewListCreateView.as_view(), name='review-list'),
    path('<uuid:pk>/', ReviewDetailView.as_view(), name='review-detail'),
]
