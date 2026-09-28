import re
from rest_framework import serializers

from .models import Payment, PaymentAttempt, Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Wallet
        fields = ['id', 'currency', 'balance', 'held_balance', 'available_balance', 'is_frozen', 'created_at']
        read_only_fields = ['id', 'balance', 'held_balance', 'available_balance', 'is_frozen', 'created_at']


class WalletCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ['currency']

    def validate_currency(self, value):
        value = value.upper()
        if Wallet.objects.filter(user=self.context['request'].user, currency=value).exists():
            raise serializers.ValidationError('A wallet for this currency already exists.')
        return value


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            'id', 'transaction_type', 'amount', 'balance_after',
            'held_balance_after', 'reference', 'created_at',
        ]
        read_only_fields = fields


class MpesaStkPushSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    phone_number = serializers.CharField(max_length=16)

    def validate_amount(self, value):
        if value < 1 or value != value.to_integral_value():
            raise serializers.ValidationError('M-Pesa wallet top-ups must be whole KES amounts of at least 1.')
        return value

    def validate_phone_number(self, value):
        normalized = re.sub(r'[\s-]', '', value)
        if normalized.startswith('+'):
            normalized = normalized[1:]
        if not re.fullmatch(r'254(?:7\d{8}|1\d{8})', normalized):
            raise serializers.ValidationError('Enter a Kenyan mobile number in 2547XXXXXXXX or 2541XXXXXXXX format.')
        return normalized


class PaymentAttemptSerializer(serializers.ModelSerializer):
    currency = serializers.CharField(source='wallet.currency', read_only=True)
    phone_number = serializers.SerializerMethodField()

    class Meta:
        model = PaymentAttempt
        fields = [
            'id', 'amount', 'currency', 'phone_number', 'status',
            'provider_receipt', 'result_description', 'created_at',
        ]
        read_only_fields = fields

    def get_phone_number(self, attempt):
        return f'{attempt.phone_number[:5]}***{attempt.phone_number[-3:]}'


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