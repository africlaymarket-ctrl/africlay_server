from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from .models import Wallet, WalletTransaction, WalletTransactionType


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