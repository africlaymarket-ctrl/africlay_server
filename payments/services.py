from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from shopping.models import (
    OrderFulfillment,
    OrderStatus,
    PaymentMethod as OrderPaymentMethod,
    PaymentOption,
    PaymentStatus as OrderPaymentStatus,
    SettlementStatus,
)

from .models import (
    LedgerEntry, LedgerEntryType, Payment, PaymentPurpose, PaymentStatus, Wallet,
    WalletTransaction, WalletTransactionType,
)


class WalletOperationError(Exception):
    pass


def _validated_amount(amount):
    try:
        value = Decimal(str(amount))
        normalized = value.quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise WalletOperationError('Amount must be a valid monetary value.') from None
    if not value.is_finite() or value <= 0 or value != normalized:
        raise WalletOperationError('Amount must be positive and have at most two decimal places.')
    return normalized


def _apply_operation(wallet, amount, reference, transaction_type):
    if not reference or len(reference) > 128:
        raise WalletOperationError('A reference of 1 to 128 characters is required.')
    amount = _validated_amount(amount)

    try:
        with transaction.atomic():
            locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            existing = WalletTransaction.objects.filter(reference=reference).first()
            if existing:
                if (
                    existing.wallet_id != locked_wallet.pk
                    or existing.transaction_type != transaction_type
                    or existing.amount != amount
                ):
                    raise WalletOperationError('Reference was already used for a different wallet operation.')
                return existing

            if transaction_type in {
                WalletTransactionType.DEBIT,
                WalletTransactionType.WITHDRAWAL,
                WalletTransactionType.ESCROW_HOLD,
                WalletTransactionType.ESCROW_CAPTURE,
            } and locked_wallet.is_frozen:
                raise WalletOperationError('Wallet is frozen.')

            if transaction_type in {WalletTransactionType.CREDIT, WalletTransactionType.REFUND, WalletTransactionType.SELLER_EARNING}:
                locked_wallet.balance += amount
            elif transaction_type in {WalletTransactionType.DEBIT, WalletTransactionType.WITHDRAWAL}:
                if locked_wallet.available_balance < amount:
                    raise WalletOperationError('Insufficient available wallet balance.')
                locked_wallet.balance -= amount
            elif transaction_type == WalletTransactionType.ESCROW_HOLD:
                if locked_wallet.available_balance < amount:
                    raise WalletOperationError('Insufficient available wallet balance.')
                locked_wallet.held_balance += amount
            elif transaction_type == WalletTransactionType.ESCROW_RELEASE:
                if locked_wallet.held_balance < amount:
                    raise WalletOperationError('Insufficient held wallet balance.')
                locked_wallet.held_balance -= amount
            elif transaction_type == WalletTransactionType.ESCROW_CAPTURE:
                if locked_wallet.held_balance < amount:
                    raise WalletOperationError('Insufficient held wallet balance.')
                locked_wallet.held_balance -= amount
                locked_wallet.balance -= amount

            locked_wallet.save(update_fields=['balance', 'held_balance', 'updated_at'])
            return WalletTransaction.objects.create(
                wallet=locked_wallet,
                transaction_type=transaction_type,
                amount=amount,
                balance_after=locked_wallet.balance,
                held_balance_after=locked_wallet.held_balance,
                reference=reference,
            )
    except IntegrityError:
        existing = WalletTransaction.objects.filter(reference=reference).first()
        if existing is None:
            raise
        if (
            existing.wallet_id == wallet.pk
            and existing.transaction_type == transaction_type
            and existing.amount == amount
        ):
            return existing
        raise WalletOperationError('Reference was already used for a different wallet operation.') from None


def credit_wallet(wallet, amount, reference, transaction_type=WalletTransactionType.CREDIT):
    allowed_types = {
        WalletTransactionType.CREDIT,
        WalletTransactionType.REFUND,
        WalletTransactionType.SELLER_EARNING,
    }
    if transaction_type not in allowed_types:
        raise WalletOperationError('Invalid wallet credit type.')
    return _apply_operation(wallet, amount, reference, transaction_type)


def debit_wallet(wallet, amount, reference, transaction_type=WalletTransactionType.DEBIT):
    if transaction_type not in {WalletTransactionType.DEBIT, WalletTransactionType.WITHDRAWAL}:
        raise WalletOperationError('Invalid wallet debit type.')
    return _apply_operation(wallet, amount, reference, transaction_type)


def place_escrow_hold(wallet, amount, reference):
    return _apply_operation(wallet, amount, reference, WalletTransactionType.ESCROW_HOLD)


def release_escrow_hold(wallet, amount, reference):
    return _apply_operation(wallet, amount, reference, WalletTransactionType.ESCROW_RELEASE)


def capture_escrow_hold(wallet, amount, reference):
    return _apply_operation(wallet, amount, reference, WalletTransactionType.ESCROW_CAPTURE)


def transfer_escrow_to_wallet(source_wallet, destination_wallet, amount, reference):
    amount = _validated_amount(amount)
    if not reference or len(reference) > 110:
        raise WalletOperationError('A reference of 1 to 110 characters is required.')
    if source_wallet.pk == destination_wallet.pk:
        raise WalletOperationError('Escrow source and seller wallets must be different.')
    if source_wallet.currency != destination_wallet.currency:
        raise WalletOperationError('Escrow transfers require matching wallet currencies.')

    source_reference = f'{reference}:capture'
    destination_reference = f'{reference}:earning'

    with transaction.atomic():
        locked_wallets = {
            wallet.pk: wallet
            for wallet in Wallet.objects.select_for_update()
            .filter(pk__in=[source_wallet.pk, destination_wallet.pk])
            .order_by('pk')
        }
        source = locked_wallets.get(source_wallet.pk)
        destination = locked_wallets.get(destination_wallet.pk)
        if source is None or destination is None:
            raise WalletOperationError('Escrow wallet no longer exists.')

        existing = {
            entry.reference: entry
            for entry in WalletTransaction.objects.filter(
                reference__in=[source_reference, destination_reference],
            )
        }
        if existing:
            source_entry = existing.get(source_reference)
            destination_entry = existing.get(destination_reference)
            if (
                source_entry
                and destination_entry
                and source_entry.wallet_id == source.pk
                and source_entry.transaction_type == WalletTransactionType.ESCROW_CAPTURE
                and source_entry.amount == amount
                and destination_entry.wallet_id == destination.pk
                and destination_entry.transaction_type == WalletTransactionType.SELLER_EARNING
                and destination_entry.amount == amount
            ):
                return source_entry, destination_entry
            raise WalletOperationError('Reference was already used for a different wallet operation.')

        if source.is_frozen:
            raise WalletOperationError('Wallet is frozen.')
        if source.held_balance < amount:
            raise WalletOperationError('Insufficient held wallet balance.')

        source.balance -= amount
        source.held_balance -= amount
        destination.balance += amount
        source.save(update_fields=['balance', 'held_balance', 'updated_at'])
        destination.save(update_fields=['balance', 'updated_at'])

        source_entry = WalletTransaction.objects.create(
            wallet=source,
            transaction_type=WalletTransactionType.ESCROW_CAPTURE,
            amount=amount,
            balance_after=source.balance,
            held_balance_after=source.held_balance,
            reference=source_reference,
        )
        destination_entry = WalletTransaction.objects.create(
            wallet=destination,
            transaction_type=WalletTransactionType.SELLER_EARNING,
            amount=amount,
            balance_after=destination.balance,
            held_balance_after=destination.held_balance,
            reference=destination_reference,
        )
        return source_entry, destination_entry


def record_ledger_entry(*, order, entry_type, amount, reference, payment=None, user=None, metadata=None):
    amount = _validated_amount(amount)
    entry, created = LedgerEntry.objects.get_or_create(
        reference=reference,
        defaults={
            'order': order,
            'payment': payment,
            'user': user,
            'entry_type': entry_type,
            'amount': amount,
            'currency': order.currency,
            'metadata': metadata or {},
        },
    )
    if not created and (
        entry.order_id != order.pk
        or entry.payment_id != getattr(payment, 'pk', None)
        or entry.user_id != getattr(user, 'pk', None)
        or entry.entry_type != entry_type
        or entry.amount != amount
    ):
        raise WalletOperationError('Ledger reference was already used for a different transaction.')
    return entry


def hold_mpesa_seller_proceeds(order):
    fulfillments = OrderFulfillment.objects.select_for_update().filter(
        order=order,
    ).exclude(status=OrderStatus.CANCELLED).select_related('seller')
    for fulfillment in fulfillments:
        if fulfillment.settlement_status in {SettlementStatus.HELD, SettlementStatus.RELEASED}:
            continue
        wallet, _ = Wallet.objects.get_or_create(user=fulfillment.seller, currency=order.currency)
        credit_wallet(
            wallet,
            fulfillment.seller_proceeds,
            f'order:{order.pk}:seller:{fulfillment.seller_id}:earning',
            WalletTransactionType.SELLER_EARNING,
        )
        place_escrow_hold(
            wallet,
            fulfillment.seller_proceeds,
            f'order:{order.pk}:seller:{fulfillment.seller_id}:hold',
        )
        record_ledger_entry(
            order=order,
            user=fulfillment.seller,
            entry_type=LedgerEntryType.SELLER_PROCEEDS_HOLD,
            amount=fulfillment.seller_proceeds,
            reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:proceeds-hold',
        )
        record_ledger_entry(
            order=order,
            user=fulfillment.seller,
            entry_type=LedgerEntryType.COMMISSION_HOLD,
            amount=fulfillment.commission_amount,
            reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:commission-hold',
        )
        fulfillment.settlement_status = SettlementStatus.HELD
        fulfillment.save(update_fields=['settlement_status', 'updated_at'])


class PaymentService:
    @staticmethod
    @transaction.atomic
    def process_mpesa_callback(payload):
        if not isinstance(payload, dict):
            raise ValidationError({'callback': 'Callback payload must be an object.'})
        body = payload.get('Body')
        callback = body.get('stkCallback') if isinstance(body, dict) else None
        if not isinstance(callback, dict):
            raise ValidationError({'callback': 'Malformed callback payload.'})
        checkout_request_id = callback.get('CheckoutRequestID')
        if not checkout_request_id:
            raise ValidationError({'callback': 'Missing checkout request identifier.'})

        payment = Payment.objects.select_for_update().select_related('order').filter(
            checkout_request_id=checkout_request_id,
        ).first()
        if not payment:
            raise ValidationError({'callback': 'Unknown checkout request identifier.'})
        if payment.status == PaymentStatus.SUCCEEDED:
            return payment

        result_code = callback.get('ResultCode')
        metadata_items = callback.get('CallbackMetadata', {}).get('Item', [])
        if not isinstance(metadata_items, list):
            raise ValidationError({'callback': 'Malformed callback metadata.'})
        metadata = {
            item.get('Name'): item.get('Value')
            for item in metadata_items
            if isinstance(item, dict)
        }
        if payment.status in [PaymentStatus.FAILED, PaymentStatus.CANCELLED]:
            return payment
        payment.raw_callback_payload = payload

        if result_code == 0:
            if metadata.get('Amount') is None or not metadata.get('MpesaReceiptNumber'):
                raise ValidationError({'callback': 'Successful callback is missing payment metadata.'})
            valid_statuses = {OrderStatus.PENDING, OrderStatus.PROCESSING}
            if payment.purpose == PaymentPurpose.DELIVERY_BALANCE:
                valid_statuses.add(OrderStatus.SHIPPED)
            if payment.order.status not in valid_statuses:
                raise ValidationError({'callback': 'Payment is not valid for the current order status.'})
            try:
                callback_amount = Decimal(str(metadata['Amount']))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({'callback': 'Callback amount is invalid.'})
            if callback_amount != payment.amount:
                raise ValidationError({'callback': 'Callback amount does not match the order amount.'})
            callback_phone = metadata.get('PhoneNumber')
            if callback_phone is not None and str(callback_phone).replace('+', '') != payment.phone_number:
                raise ValidationError({'callback': 'Callback phone number does not match the payment request.'})
            callback_merchant_id = str(callback.get('MerchantRequestID', ''))
            if payment.merchant_request_id and callback_merchant_id != payment.merchant_request_id:
                raise ValidationError({'callback': 'Callback merchant request identifier does not match.'})
            payment.status = PaymentStatus.SUCCEEDED
            payment.receipt_number = str(metadata.get('MpesaReceiptNumber', ''))
            payment.completed_at = timezone.now()
            record_ledger_entry(
                order=payment.order,
                payment=payment,
                user=payment.order.buyer,
                entry_type=LedgerEntryType.CUSTOMER_PAYMENT,
                amount=payment.amount,
                reference=f'payment:{payment.pk}:received',
                metadata={
                    'provider': payment.provider,
                    'purpose': payment.purpose,
                    'receipt_number': payment.receipt_number,
                },
            )
            if (
                payment.purpose in {PaymentPurpose.ORDER_TOTAL, PaymentPurpose.DELIVERY_FEE}
                and payment.order.shipping_cost > 0
            ):
                record_ledger_entry(
                    order=payment.order,
                    payment=payment,
                    entry_type=LedgerEntryType.DELIVERY_FEE,
                    amount=payment.order.shipping_cost,
                    reference=f'order:{payment.order_id}:delivery-fee-received',
                )
            already_paid = Payment.objects.filter(
                order=payment.order,
                status=PaymentStatus.SUCCEEDED,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            amount_paid = min(payment.order.total_amount, already_paid + payment.amount)
            payment.order.amount_paid = amount_paid
            payment.order.delivery_fee_paid = (
                payment.purpose == PaymentPurpose.ORDER_TOTAL
                or Payment.objects.filter(
                    order=payment.order,
                    purpose=PaymentPurpose.DELIVERY_FEE,
                    status=PaymentStatus.SUCCEEDED,
                ).exists()
                or payment.purpose == PaymentPurpose.DELIVERY_FEE
            )
            payment.order.payment_method = OrderPaymentMethod.MPESA
            payment.order.payment_status = (
                OrderPaymentStatus.PAID
                if amount_paid >= payment.order.total_amount
                else OrderPaymentStatus.PARTIAL
            )
            if payment.order.status == OrderStatus.PENDING:
                payment.order.status = OrderStatus.PROCESSING
            payment.order.save(update_fields=[
                'amount_paid', 'delivery_fee_paid', 'payment_method', 'payment_status',
                'status', 'updated_at',
            ])
            OrderFulfillment.objects.filter(
                order=payment.order,
                status=OrderStatus.PENDING,
            ).update(status=OrderStatus.PROCESSING)
            if payment.order.payment_status == OrderPaymentStatus.PAID:
                hold_mpesa_seller_proceeds(payment.order)
        else:
            payment.status = PaymentStatus.FAILED
            payment.failure_code = str(result_code or '')
            payment.failure_message = callback.get('ResultDesc', 'Payment failed.')[:255]
            payment.completed_at = timezone.now()
            if payment.order.amount_paid == 0:
                payment.order.payment_status = OrderPaymentStatus.FAILED
                payment.order.save(update_fields=['payment_status', 'updated_at'])

        payment.save()
        return payment
