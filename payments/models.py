import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from core.models import TimestampedModel
from shopping.models import Order


currency_code_validator = RegexValidator(
    regex=r'^[A-Z]{3}$',
    message='Currency must be a three-letter uppercase code.',
)


class Wallet(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallets')
    currency = models.CharField(max_length=3, validators=[currency_code_validator])
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    held_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shopping_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shopping_held_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    earnings_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    earnings_held_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_frozen = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'currency'], name='unique_user_wallet_currency'),
            models.CheckConstraint(condition=models.Q(balance__gte=0), name='wallet_balance_nonnegative'),
            models.CheckConstraint(condition=models.Q(held_balance__gte=0), name='wallet_held_nonnegative'),
            models.CheckConstraint(condition=models.Q(held_balance__lte=models.F('balance')), name='wallet_holds_within_balance'),
            models.CheckConstraint(condition=models.Q(shopping_balance__gte=0), name='wallet_shopping_nonnegative'),
            models.CheckConstraint(condition=models.Q(shopping_held_balance__gte=0), name='wallet_shopping_held_nonnegative'),
            models.CheckConstraint(
                condition=models.Q(shopping_held_balance__lte=models.F('shopping_balance')),
                name='wallet_shopping_holds_within_balance',
            ),
            models.CheckConstraint(condition=models.Q(earnings_balance__gte=0), name='wallet_earnings_nonnegative'),
            models.CheckConstraint(condition=models.Q(earnings_held_balance__gte=0), name='wallet_earnings_held_nonnegative'),
            models.CheckConstraint(
                condition=models.Q(earnings_held_balance__lte=models.F('earnings_balance')),
                name='wallet_earnings_holds_within_balance',
            ),
        ]
        ordering = ['currency']

    @property
    def available_balance(self):
        return self.balance - self.held_balance

    @property
    def shopping_available_balance(self):
        return self.shopping_balance - self.shopping_held_balance

    @property
    def earnings_available_balance(self):
        return self.earnings_balance - self.earnings_held_balance

    def __str__(self):
        return f'{self.user.email} {self.currency} wallet'


class WalletTransactionType(models.TextChoices):
    CREDIT = 'credit', 'Credit'
    DEBIT = 'debit', 'Debit'
    ESCROW_HOLD = 'escrow_hold', 'Escrow Hold'
    ESCROW_RELEASE = 'escrow_release', 'Escrow Release'
    ESCROW_CAPTURE = 'escrow_capture', 'Escrow Capture'
    REFUND = 'refund', 'Refund'
    SELLER_EARNING = 'seller_earning', 'Seller Earning'
    WITHDRAWAL = 'withdrawal', 'Withdrawal'


class WalletBalanceSource(models.TextChoices):
    SHOPPING = 'shopping', 'Shopping Funds'
    EARNINGS = 'earnings', 'Seller Earnings'


class WalletTransaction(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    transaction_type = models.CharField(max_length=24, choices=WalletTransactionType.choices)
    balance_source = models.CharField(
        max_length=16,
        choices=WalletBalanceSource.choices,
        default=WalletBalanceSource.SHOPPING,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0.01)])
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    held_balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    source_balance_after = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    source_held_balance_after = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=128, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.transaction_type} {self.amount} {self.wallet.currency}'


class PayoutProvider(models.TextChoices):
    MPESA = 'mpesa', 'M-Pesa'


class PayoutStatus(models.TextChoices):
    REQUESTED = 'requested', 'Requested'
    PROCESSING = 'processing', 'Processing'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    UNKNOWN = 'unknown', 'Needs Reconciliation'


class PayoutAccount(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payout_account',
    )
    provider = models.CharField(
        max_length=20,
        choices=PayoutProvider.choices,
        default=PayoutProvider.MPESA,
    )
    phone_number = models.CharField(max_length=12)
    account_name = models.CharField(max_length=255, blank=True, default='')
    is_active = models.BooleanField(default=True)
    confirmed_at = models.DateTimeField()

    class Meta:
        ordering = ['user__email']

    def __str__(self):
        return f'{self.user.email} payout account'


class Payout(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='payouts')
    payout_account = models.ForeignKey(PayoutAccount, on_delete=models.PROTECT, related_name='payouts')
    provider = models.CharField(
        max_length=20,
        choices=PayoutProvider.choices,
        default=PayoutProvider.MPESA,
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(1)])
    balance_source = models.CharField(
        max_length=16,
        choices=WalletBalanceSource.choices,
        default=WalletBalanceSource.EARNINGS,
    )
    currency = models.CharField(max_length=3, validators=[currency_code_validator], default='KES')
    status = models.CharField(
        max_length=16,
        choices=PayoutStatus.choices,
        default=PayoutStatus.REQUESTED,
    )
    destination_phone = models.CharField(max_length=12)
    destination_name = models.CharField(max_length=255, blank=True, default='')
    idempotency_key = models.CharField(max_length=100)
    callback_token_hash = models.CharField(max_length=64, unique=True)
    originator_conversation_id = models.CharField(max_length=64, unique=True)
    conversation_id = models.CharField(max_length=100, blank=True, default='')
    transaction_receipt = models.CharField(max_length=64, blank=True, default='')
    provider_response = models.JSONField(default=dict, blank=True)
    provider_result = models.JSONField(default=dict, blank=True)
    failure_code = models.CharField(max_length=40, blank=True, default='')
    failure_message = models.CharField(max_length=255, blank=True, default='')
    processed_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_payouts',
    )
    resolution_note = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['wallet', 'idempotency_key'],
                name='unique_wallet_payout_idempotency_key',
            ),
            models.UniqueConstraint(
                fields=['provider', 'transaction_receipt'],
                condition=Q(transaction_receipt__gt=''),
                name='unique_payout_provider_receipt',
            ),
            models.CheckConstraint(condition=Q(amount__gte=1), name='payout_amount_minimum'),
        ]
        indexes = [
            models.Index(fields=['status', 'created_at'], name='payments_po_status_42b194_idx'),
            models.Index(fields=['wallet', 'created_at'], name='payments_po_wallet_04a128_idx'),
        ]

    def __str__(self):
        return f'{self.amount} {self.currency} payout to {self.destination_phone}'


class PaymentAttemptStatus(models.TextChoices):
    INITIATED = 'initiated', 'Initiated'
    PENDING = 'pending', 'Pending'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    UNKNOWN = 'unknown', 'Unknown'


class PaymentStatus(models.TextChoices):
    INITIATED = 'initiated', 'Initiated'
    PENDING = 'pending', 'Pending'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'


class PaymentPurpose(models.TextChoices):
    ORDER_TOTAL = 'order_total', 'Order Total'
    DELIVERY_FEE = 'delivery_fee', 'Delivery Fee'
    DELIVERY_BALANCE = 'delivery_balance', 'Pay on Delivery Balance'


class LedgerEntryType(models.TextChoices):
    CUSTOMER_PAYMENT = 'customer_payment', 'Customer Payment Received'
    DELIVERY_FEE = 'delivery_fee', 'Delivery Fee Received'
    SELLER_PROCEEDS_HOLD = 'seller_proceeds_hold', 'Seller Proceeds Held'
    SELLER_PROCEEDS_RELEASE = 'seller_proceeds_release', 'Seller Proceeds Released'
    SELLER_PROCEEDS_REVERSAL = 'seller_proceeds_reversal', 'Seller Proceeds Reversed'
    COMMISSION_HOLD = 'commission_hold', 'Commission Held'
    COMMISSION_EARNED = 'commission_earned', 'Commission Earned'
    COMMISSION_REVERSAL = 'commission_reversal', 'Commission Reversed'
    REFUND_DUE = 'refund_due', 'Refund Due'
    REFUND_COMPLETED = 'refund_completed', 'Refund Completed'


class PaymentAttempt(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payment_attempts')
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='payment_attempts')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(1)])
    phone_number = models.CharField(max_length=12)
    idempotency_key = models.CharField(max_length=100)
    callback_token_hash = models.CharField(max_length=64, unique=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    provider_receipt = models.CharField(max_length=32, null=True, blank=True, unique=True)
    status = models.CharField(
        max_length=12,
        choices=PaymentAttemptStatus.choices,
        default=PaymentAttemptStatus.INITIATED,
    )
    result_description = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'idempotency_key'],
                name='unique_user_payment_idempotency_key',
            ),
            models.CheckConstraint(condition=models.Q(amount__gte=1), name='payment_attempt_amount_minimum'),
        ]
        indexes = [models.Index(fields=['status', 'created_at'], name='payments_pa_status_f4857f_idx')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.amount} {self.wallet.currency} M-Pesa {self.status}'


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    provider = models.CharField(max_length=30, default='mpesa')
    purpose = models.CharField(max_length=24, choices=PaymentPurpose.choices, default=PaymentPurpose.ORDER_TOTAL)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='KES')
    phone_number = models.CharField(max_length=20)
    callback_token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, blank=True)
    receipt_number = models.CharField(max_length=100, blank=True)
    failure_code = models.CharField(max_length=30, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    raw_callback_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'checkout_request_id'],
                condition=Q(checkout_request_id__gt=''),
                name='unique_payment_provider_checkout',
            ),
            models.UniqueConstraint(
                fields=['provider', 'receipt_number'],
                condition=Q(receipt_number__gt=''),
                name='unique_payment_provider_receipt',
            ),
        ]
        indexes = [models.Index(fields=['order', 'status'], name='payments_pa_order_i_a76289_idx')]

    def __str__(self):
        return f'{self.provider} payment for order {self.order_id}'


class LedgerEntry(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='ledger_entries')
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        null=True,
        blank=True,
    )
    entry_type = models.CharField(max_length=32, choices=LedgerEntryType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    currency = models.CharField(max_length=3, validators=[currency_code_validator], default='KES')
    reference = models.CharField(max_length=160, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['order', 'entry_type']),
            models.Index(fields=['user', 'entry_type']),
        ]

    def __str__(self):
        return f'{self.entry_type} {self.amount} {self.currency} for {self.order_id}'
