import re

from rest_framework import serializers

from .models import Payment


class PaymentInitiateSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    phone_number = serializers.CharField(max_length=20)

    def validate_phone_number(self, value):
        normalized = value.replace('+', '').replace(' ', '')
        if not re.fullmatch(r'2547\d{8}', normalized):
            raise serializers.ValidationError('Use a valid Kenyan phone number, for example 254712345678.')
        return normalized


class PaymentSerializer(serializers.ModelSerializer):
    order_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'order_id', 'provider', 'status', 'amount', 'currency', 'checkout_request_id', 'receipt_number']
        read_only_fields = fields
