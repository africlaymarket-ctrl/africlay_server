from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, status
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.permissions import IsAuthenticated
from .models import EmailDelivery, Notification, SMSDelivery
from .serializers import EmailDeliverySerializer, NotificationSerializer, SMSDeliverySerializer


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Notification.objects.filter(user=self.request.user)
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() == 'true')
        return qs


class NotificationDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch']

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


class NotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name='NotificationMarkAllReadResponse',
            fields={'marked_read': serializers.IntegerField()},
        ),
    )
    def post(self, request, *args, **kwargs):
        updated = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'marked_read': updated}, status=status.HTTP_200_OK)


class EmailDeliveryListView(generics.ListAPIView):
    serializer_class = EmailDeliverySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return EmailDelivery.objects.filter(notification__user=self.request.user).order_by('-created_at')[:30]


class SMSDeliveryListView(generics.ListAPIView):
    serializer_class = SMSDeliverySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return SMSDelivery.objects.filter(notification__user=self.request.user).select_related('notification').order_by('-created_at')[:30]
