from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied

from authapp.permissions import IsAuthenticated
from .models import Review
from .serializers import ReviewSerializer


class ReviewListCreateView(generics.ListCreateAPIView):
    serializer_class = ReviewSerializer

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method == 'GET' else [IsAuthenticated()]

    def get_queryset(self):
        qs = Review.objects.select_related('reviewer')
        product_id = self.request.query_params.get('product')
        service_id = self.request.query_params.get('service')
        store_id = self.request.query_params.get('store')
        if product_id:
            qs = qs.filter(product_id=product_id)
        elif service_id:
            qs = qs.filter(service_id=service_id)
        elif store_id:
            qs = qs.filter(store_id=store_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(reviewer=self.request.user)


class ReviewDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete']

    def get_queryset(self):
        return Review.objects.select_related('reviewer')

    def perform_update(self, serializer):
        if serializer.instance.reviewer_id != self.request.user.id:
            raise PermissionDenied('You can only edit your own reviews.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.reviewer_id != self.request.user.id and not self.request.user.is_admin_role:
            raise PermissionDenied('You can only delete your own reviews.')
        instance.delete()
