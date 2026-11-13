"""Rider applications and private verification review."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from authapp.models import AdminAuditLog, RiderApplication, RiderApplicationStatus, StaffRole, UserStaffRole
from authapp.permissions import IsSuperAdmin, IsVerifiedUser
from authapp.utils import send_account_notice_email
from notifications.models import create_notification
from utility_services.utils import normalize_phone_number, valid_sms_phone


class RiderApplicationSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(write_only=True, required=False, max_length=20)
    identity_document = serializers.FileField(write_only=True, required=True)
    licence_document = serializers.FileField(write_only=True, required=True)
    has_identity_document = serializers.SerializerMethodField()
    has_licence_document = serializers.SerializerMethodField()
    identity_document_url = serializers.SerializerMethodField()
    licence_document_url = serializers.SerializerMethodField()
    applicant_email = serializers.EmailField(source='user.email', read_only=True)
    applicant_phone = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = RiderApplication
        fields = [
            'id', 'applicant_email', 'applicant_phone', 'phone_number', 'city', 'vehicle_type',
            'vehicle_registration', 'licence_number', 'identity_document',
            'licence_document', 'has_identity_document', 'has_licence_document',
            'identity_document_url', 'licence_document_url',
            'sms_alerts', 'status', 'review_reason', 'reviewed_at', 'created_at',
        ]
        read_only_fields = ['id', 'status', 'review_reason', 'reviewed_at', 'created_at']

    def get_has_identity_document(self, obj):
        return bool(obj.identity_document)

    def get_has_licence_document(self, obj):
        return bool(obj.licence_document)

    def get_identity_document_url(self, obj):
        request = self.context.get('request')
        return obj.identity_document.url if request and request.user.is_super_admin and obj.identity_document else None

    def get_licence_document_url(self, obj):
        request = self.context.get('request')
        return obj.licence_document.url if request and request.user.is_super_admin and obj.licence_document else None

    def validate(self, attrs):
        for name in ('identity_document', 'licence_document'):
            upload = attrs.get(name)
            if upload:
                from core.models import validate_document_upload
                validate_document_upload(upload)
        phone = attrs.get('phone_number') or normalize_phone_number(self.context['request'].user.phone_number)
        if not valid_sms_phone(phone):
            raise serializers.ValidationError({'phone_number': 'Enter a valid Kenyan mobile number to apply.'})
        return attrs

    def validate_phone_number(self, value):
        phone = normalize_phone_number(value)
        if not valid_sms_phone(phone):
            raise serializers.ValidationError('Enter a valid Kenyan mobile number, such as 0712345678.')
        if get_user_model().objects.filter(phone_number=phone).exclude(pk=self.context['request'].user.pk).exists():
            raise serializers.ValidationError('This phone number belongs to another account.')
        return phone

    def validate_city(self, value):
        from shopping.models import PickupPoint
        city = value.strip()
        if not PickupPoint.objects.filter(city__iexact=city, active=True).exists():
            raise serializers.ValidationError('Choose a city with an active AfriClay pickup point.')
        return city


class RiderApplicationReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=RiderApplicationStatus.choices)
    reason = serializers.CharField(max_length=1000)

    def validate_decision(self, value):
        if value == RiderApplicationStatus.PENDING:
            raise serializers.ValidationError('Choose approved or rejected.')
        return value


class RiderApplicationMineView(generics.GenericAPIView):
    serializer_class = RiderApplicationSerializer
    permission_classes = [IsVerifiedUser]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        application = RiderApplication.objects.filter(user=request.user).first()
        if not application:
            return Response(None)
        return Response(self.get_serializer(application).data)

    def post(self, request):
        with transaction.atomic():
            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            existing = RiderApplication.objects.filter(user=request.user).first()
            if existing and existing.status != RiderApplicationStatus.REJECTED:
                raise ValidationError({'application': 'This application is already under review or approved.'})
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            phone = serializer.validated_data.pop('phone_number', None)
            if phone and user.phone_number != phone:
                user.phone_number = phone
                user.save(update_fields=['phone_number'])
                request.user.phone_number = phone
                profile = user.profile
                preferences = profile.notification_preferences if isinstance(profile.notification_preferences, dict) else {}
                profile.notification_preferences = {
                    **preferences,
                    **{key: False for key in preferences if key.startswith('sms_')},
                }
                profile.save(update_fields=['notification_preferences'])
                transaction.on_commit(lambda: send_account_notice_email(
                    user.email, 'AfriClay phone number changed',
                    'The phone number on your AfriClay account was changed during rider application. If this was not you, contact AfriClay support immediately.',
                ))
            application = serializer.save(
                user=request.user, status=RiderApplicationStatus.PENDING,
                reviewed_at=None, reviewed_by=None, review_reason='',
            ) if existing is None else self._replace(existing, serializer)
        return Response(self.get_serializer(application).data, status=status.HTTP_201_CREATED)

    def _replace(self, existing, serializer):
        old_files = [existing.identity_document.name, existing.licence_document.name]
        for key, value in serializer.validated_data.items():
            setattr(existing, key, value)
        existing.status = RiderApplicationStatus.PENDING
        existing.reviewed_at = None
        existing.reviewed_by = None
        existing.review_reason = ''
        existing.save()
        from django.core.files.storage import default_storage
        for name in old_files:
            if name and name not in (existing.identity_document.name, existing.licence_document.name):
                transaction.on_commit(lambda path=name: default_storage.delete(path))
        return existing


class RiderApplicationPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 100


class RiderApplicationQueueView(generics.ListAPIView):
    serializer_class = RiderApplicationSerializer
    permission_classes = [IsSuperAdmin]
    pagination_class = RiderApplicationPagination

    def get_queryset(self):
        queryset = RiderApplication.objects.select_related('user').order_by('created_at')
        requested = self.request.query_params.get('status', 'pending')
        return queryset.filter(status=requested) if requested in RiderApplicationStatus.values else queryset.none()


class RiderApplicationDocumentView(generics.GenericAPIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request, pk, kind):
        application = get_object_or_404(RiderApplication, pk=pk)
        if kind not in ('identity', 'licence'):
            raise ValidationError({'kind': 'Unknown document.'})
        field = application.identity_document if kind == 'identity' else application.licence_document
        return FileResponse(field.open('rb'), as_attachment=True, filename=f'rider-{kind}-{application.pk}{field.name[field.name.rfind("."):]}')


class RiderApplicationReviewView(generics.GenericAPIView):
    serializer_class = RiderApplicationReviewSerializer
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            application = get_object_or_404(RiderApplication.objects.select_for_update().select_related('user'), pk=pk)
            if application.status != RiderApplicationStatus.PENDING:
                raise ValidationError({'status': 'Only pending applications can be reviewed.'})
            decision = serializer.validated_data['decision']
            application.status = decision
            application.reviewed_by = request.user
            application.reviewed_at = timezone.now()
            application.review_reason = serializer.validated_data['reason'].strip()
            application.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_reason', 'updated_at'])
            if decision == RiderApplicationStatus.APPROVED:
                _, created = UserStaffRole.objects.get_or_create(
                    user=application.user, role=StaffRole.LOGISTICS, defaults={'granted_by': request.user},
                )
                if created:
                    get_user_model().objects.filter(pk=application.user_id).update(auth_version=F('auth_version') + 1)
            AdminAuditLog.objects.create(
                actor=request.user, action=f'rider_application_{decision}',
                target_type='rider_application', target_id=str(application.pk),
                reason=application.review_reason, metadata={'user_id': str(application.user_id)},
            )
            create_notification(
                application.user, title='Rider application reviewed',
                message='Your rider application was approved. Sign in to Operations to view assigned deliveries.' if decision == RiderApplicationStatus.APPROVED
                else 'Your rider application needs changes. Review the reason in your application.',
            )
        return Response(RiderApplicationSerializer(application).data)
