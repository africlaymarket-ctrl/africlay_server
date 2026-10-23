import re
from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import (
    LedgerEntry, Payment, PaymentAttempt, PaymentPurpose, Payout, PayoutAccount, PayoutStatus,
    Wallet, WalletBalanceSource, WalletTransaction,
)


def normalize_kenyan_phone(value):
    normalized = re.sub(r'[\s-]', '', str(value))
    if normalized.startswith('+'):
        normalized = normalized[1:]
    if not re.fullmatch(r'254(?:7\d{8}|1\d{8})', normalized):
        raise serializers.ValidationError(
            'Enter a Kenyan mobile number in 2547XXXXXXXX or 2541XXXXXXXX format.',
        )
    return normalized


def masked_phone(value):
    return f'{value[:5]}***{value[-3:]}' if value else ''


class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    pending_payout_balance = serializers.SerializerMethodField()
    shopping_pending_payout_balance = serializers.SerializerMethodField()
    earnings_pending_payout_balance = serializers.SerializerMethodField()
    order_held_balance = serializers.SerializerMethodField()
    shopping_available_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    earnings_available_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    withdrawal_minimum = serializers.SerializerMethodField()
    withdrawal_maximum = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            'id', 'currency', 'balance', 'held_balance', 'available_balance',
            'shopping_balance', 'shopping_held_balance', 'shopping_available_balance',
            'earnings_balance', 'earnings_held_balance', 'earnings_available_balance',
            'pending_payout_balance', 'shopping_pending_payout_balance',
            'earnings_pending_payout_balance', 'order_held_balance',
            'withdrawal_minimum', 'withdrawal_maximum',
            'is_frozen', 'created_at',
        ]
        read_only_fields = fields

    def get_pending_payout_balance(self, wallet) -> Decimal:
        return sum(
            (payout.amount for payout in wallet.payouts.filter(
                status__in=[PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN],
            )),
            Decimal('0.00'),
        )

    def get_order_held_balance(self, wallet) -> Decimal:
        pending = self.get_pending_payout_balance(wallet)
        return max(wallet.held_balance - pending, Decimal('0.00'))

    def get_shopping_pending_payout_balance(self, wallet) -> Decimal:
        return sum(
            (payout.amount for payout in wallet.payouts.filter(
                balance_source=WalletBalanceSource.SHOPPING,
                status__in=[PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN],
            )),
            Decimal('0.00'),
        )

    def get_earnings_pending_payout_balance(self, wallet) -> Decimal:
        return sum(
            (payout.amount for payout in wallet.payouts.filter(
                balance_source=WalletBalanceSource.EARNINGS,
                status__in=[PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN],
            )),
            Decimal('0.00'),
        )

    def get_withdrawal_minimum(self, wallet) -> Decimal:
        return Decimal(str(settings.MPESA_B2C_MIN_AMOUNT))

    def get_withdrawal_maximum(self, wallet) -> Decimal:
        return Decimal(str(settings.MPESA_B2C_MAX_AMOUNT))


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
            'balance_source', 'held_balance_after', 'source_balance_after',
            'source_held_balance_after', 'reference', 'created_at',
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
        return normalize_kenyan_phone(value)


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
    purpose = serializers.ChoiceField(
        choices=['initial', PaymentPurpose.DELIVERY_BALANCE],
        required=False,
        default='initial',
    )

    def validate_phone_number(self, value):
        normalized = value.replace('+', '').replace(' ', '')
        if not re.fullmatch(r'254(?:7|1)\d{8}', normalized):
            raise serializers.ValidationError('Use a valid Kenyan phone number, for example 254712345678.')
        return normalized


class PaymentSerializer(serializers.ModelSerializer):
    order_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'order_id', 'provider', 'purpose', 'status', 'amount', 'currency',
            'checkout_request_id', 'receipt_number',
        ]
        read_only_fields = fields


class AdminPaymentSerializer(PaymentSerializer):
    buyer_email = serializers.EmailField(source='order.buyer.email', read_only=True)
    phone_number = serializers.SerializerMethodField()

    class Meta(PaymentSerializer.Meta):
        fields = PaymentSerializer.Meta.fields + [
            'buyer_email', 'phone_number', 'failure_code', 'failure_message',
            'created_at', 'updated_at', 'completed_at',
        ]

    def get_phone_number(self, payment) -> str:
        return masked_phone(payment.phone_number)


class FinanceTransactionSerializer(serializers.ModelSerializer):
    order_id = serializers.UUIDField(read_only=True)
    payment_id = serializers.UUIDField(read_only=True, allow_null=True)
    user_id = serializers.UUIDField(read_only=True, allow_null=True)
    user_email = serializers.EmailField(source='user.email', read_only=True, allow_null=True)
    provider = serializers.CharField(source='payment.provider', read_only=True, allow_null=True)
    payment_status = serializers.CharField(source='payment.status', read_only=True, allow_null=True)
    receipt_number = serializers.CharField(source='payment.receipt_number', read_only=True, allow_null=True)

    class Meta:
        model = LedgerEntry
        fields = [
            'id', 'entry_type', 'amount', 'currency', 'reference', 'order_id',
            'payment_id', 'user_id', 'user_email', 'provider', 'payment_status',
            'receipt_number', 'created_at',
        ]
        read_only_fields = fields


class PayoutAccountSerializer(serializers.ModelSerializer):
    phone_number = serializers.SerializerMethodField()

    class Meta:
        model = PayoutAccount
        fields = [
            'id', 'provider', 'phone_number', 'account_name', 'is_active',
            'confirmed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_phone_number(self, account) -> str:
        return masked_phone(account.phone_number)


class PayoutAccountUpdateSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_phone_number(self, value):
        return normalize_kenyan_phone(value)

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('The account password is incorrect.')
        return value


class PayoutRequestSerializer(serializers.Serializer):
    balance_source = serializers.ChoiceField(choices=WalletBalanceSource.choices)


class PayoutSerializer(serializers.ModelSerializer):
    destination_phone = serializers.SerializerMethodField()

    class Meta:
        model = Payout
        fields = [
            'id', 'provider', 'amount', 'currency', 'status', 'destination_phone',
            'destination_name', 'balance_source', 'transaction_receipt', 'failure_message',
            'created_at', 'updated_at', 'processed_at',
        ]
        read_only_fields = fields

    def get_destination_phone(self, payout) -> str:
        return masked_phone(payout.destination_phone)


class AdminPayoutSerializer(PayoutSerializer):
    account_id = serializers.UUIDField(source='wallet.user_id', read_only=True)
    account_email = serializers.EmailField(source='wallet.user.email', read_only=True)
    account_name = serializers.CharField(source='wallet.user.full_name', read_only=True)
    seller_id = serializers.UUIDField(source='wallet.user_id', read_only=True)
    seller_email = serializers.EmailField(source='wallet.user.email', read_only=True)
    seller_name = serializers.CharField(source='wallet.user.full_name', read_only=True)
    store_name = serializers.SerializerMethodField()
    resolution_note = serializers.CharField(read_only=True)

    class Meta(PayoutSerializer.Meta):
        fields = PayoutSerializer.Meta.fields + [
            'account_id', 'account_email', 'account_name',
            'seller_id', 'seller_email', 'seller_name', 'store_name',
            'resolution_note',
        ]

    def get_store_name(self, payout) -> str:
        store = getattr(payout.wallet.user, 'store', None)
        return getattr(store, 'name', '')


class PayoutResolutionSerializer(serializers.Serializer):
    resolution = serializers.ChoiceField(choices=['succeeded', 'failed'])
    transaction_receipt = serializers.CharField(required=False, allow_blank=True, max_length=64)
    note = serializers.CharField(max_length=500)

    def validate(self, attrs):
        if attrs['resolution'] == 'succeeded' and not attrs.get('transaction_receipt', '').strip():
            raise serializers.ValidationError({
                'transaction_receipt': 'Enter the M-Pesa receipt before marking this payout as paid.',
            })
        return attrs
