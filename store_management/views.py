from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from authapp.permissions import IsSeller, IsVerifiedUser

from .models import Store, StoreStatus
from .serializers import StoreSerializer


class StoreListCreateView(generics.ListCreateAPIView):
    serializer_class = StoreSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsSeller(), IsVerifiedUser()]
        return [permissions.AllowAny()]

    def get_queryset(self):
        return Store.objects.filter(status=StoreStatus.ACTIVE).select_related('owner')

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                if Store.objects.filter(owner=self.request.user).exists():
                    raise ValidationError({'owner': 'This seller already has a store.'})
                serializer.save(owner=self.request.user)
        except IntegrityError as error:
            raise ValidationError({'store': 'A store with this owner or slug already exists.'}) from error


class StoreDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = StoreSerializer
    lookup_field = 'slug'

    def get_queryset(self):
        stores = Store.objects.select_related('owner')
        if self.request.method == 'GET':
            return stores.filter(status=StoreStatus.ACTIVE)
        return stores

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [IsSeller(), IsVerifiedUser(), permissions.IsAuthenticated()]

    def perform_update(self, serializer):
        if serializer.instance.owner_id != self.request.user.id and not self.request.user.is_admin_role:
            raise PermissionDenied('You do not own this store.')
        serializer.save()
