from rest_framework import serializers
from .models import EmailDelivery, Notification, SMSDelivery


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'notification_type', 'title', 'message', 'is_read', 'created_at']
        read_only_fields = ['id', 'notification_type', 'title', 'message', 'created_at']


class EmailDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailDelivery
        fields = ['id', 'subject', 'status', 'created_at', 'sent_at', 'delivered_at']
        read_only_fields = fields


class SMSDeliverySerializer(serializers.ModelSerializer):
    title = serializers.CharField(source='notification.title', read_only=True)

    class Meta:
        model = SMSDelivery
        fields = ['id', 'title', 'status', 'created_at']
        read_only_fields = fields
