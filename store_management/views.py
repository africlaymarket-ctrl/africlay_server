from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.files.uploadedfile import UploadedFile
from django.core.files.storage import default_storage
from django.db.models.fields.files import FieldFile
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from authapp.models import UserRole
from authapp.permissions import IsKYCReviewer, IsSeller, IsVerifiedUser

from .models import Store, StoreKYC, StoreKYCStatus, StoreStatus
from .serializers import StoreKYCReviewSerializer, StoreKYCSerializer, StoreSerializer


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
        is_explicit_admin = self.request.user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]
        if serializer.instance.owner_id != self.request.user.id and not is_explicit_admin:
            raise PermissionDenied('You do not own this store.')
        serializer.save()


class StoreKYCDetailView(generics.RetrieveAPIView):
    serializer_class = StoreKYCSerializer

    def get_permissions(self):
        if getattr(self.request.user, 'role', None) in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
            return [IsKYCReviewer()]
        return [IsSeller(), IsVerifiedUser()]

    def get_queryset(self):
        queryset = StoreKYC.objects.select_related('store', 'reviewed_by')
        if self.request.user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
            return queryset
        return queryset.filter(store__owner=self.request.user)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), store__slug=self.kwargs['slug'])


class StoreKYCSubmitView(generics.CreateAPIView):
    serializer_class = StoreKYCSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def create(self, request, *args, **kwargs):
        previous_document_name = None
        with transaction.atomic():
            try:
                store = Store.objects.select_for_update().get(slug=self.kwargs['slug'], owner=request.user)
            except Store.DoesNotExist as error:
                raise ValidationError({'store': 'You do not own this store.'}) from error

            existing = StoreKYC.objects.filter(store=store).first()
            if existing and existing.status in [StoreKYCStatus.PENDING, StoreKYCStatus.APPROVED]:
                raise ValidationError({'kyc': 'This store already has an active KYC submission.'})

            if existing and existing.document:
                previous_document_name = existing.document.name

            payload = request.data.copy() if hasattr(request.data, 'copy') else request.data
            if existing and existing.status == StoreKYCStatus.REJECTED:
                raw_document = payload.get('document')
                if raw_document in (None, '', 'null') or not isinstance(raw_document, (UploadedFile, FieldFile, type(None))):
                    payload.pop('document', None)

                if 'document' not in payload and existing.document:
                    payload['document'] = existing.document

            partial = bool(existing and existing.status == StoreKYCStatus.REJECTED)
            serializer = self.get_serializer(instance=existing, data=payload, partial=partial)
            serializer.is_valid(raise_exception=True)
            serializer.save(
                store=store,
                status=StoreKYCStatus.PENDING,
                submitted_at=timezone.now(),
                reviewed_at=None,
                reviewed_by=None,
                rejection_reason='',
            )

        if previous_document_name and serializer.instance.document.name != previous_document_name:
            default_storage.delete(previous_document_name)
        return Response(
            serializer.data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )


class StoreKYCReviewView(generics.GenericAPIView):
    serializer_class = StoreKYCReviewSerializer
    permission_classes = [IsKYCReviewer]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        decision = serializer.validated_data['decision']
        with transaction.atomic():
            get_object_or_404(
                Store.objects.select_for_update(),
                slug=kwargs['slug'],
            )
            kyc = get_object_or_404(
                StoreKYC.objects.select_for_update().select_related('store'),
                store__slug=kwargs['slug'],
            )
            if kyc.status != StoreKYCStatus.PENDING:
                raise ValidationError({'status': 'Only pending KYC submissions can be reviewed.'})

            kyc.status = decision
            kyc.reviewed_by = request.user
            kyc.reviewed_at = timezone.now()
            kyc.rejection_reason = serializer.validated_data.get('rejection_reason', '') if decision == StoreKYCStatus.REJECTED else ''
            kyc.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason', 'updated_at'])

        return Response(StoreKYCSerializer(kyc).data)
