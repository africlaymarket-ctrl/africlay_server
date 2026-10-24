from decimal import Decimal

from django.db import migrations, transaction


EARNINGS_TYPES = {'seller_earning'}
ESCROW_TYPES = {'escrow_hold', 'escrow_release', 'escrow_capture'}


def _payout_source(reference, payout_sources):
    parts = reference.split(':', 2)
    if len(parts) >= 2 and parts[0] == 'payout':
        return payout_sources.get(parts[1])
    return None


def _transaction_source(entry, payout_sources):
    if entry.transaction_type in EARNINGS_TYPES:
        return 'earnings'
    if entry.transaction_type in ESCROW_TYPES and ':seller:' in entry.reference:
        return 'earnings'

    payout_source = _payout_source(entry.reference, payout_sources)
    if payout_source:
        return payout_source

    if entry.source_balance_after or entry.source_held_balance_after:
        return entry.balance_source
    return 'shopping'


def _apply_earnings_entry(entry, balance, held):
    amount = entry.amount
    if entry.transaction_type == 'seller_earning':
        balance += amount
    elif entry.transaction_type in {'debit', 'withdrawal'}:
        balance -= amount
    elif entry.transaction_type == 'escrow_hold':
        held += amount
    elif entry.transaction_type == 'escrow_release':
        held -= amount
    elif entry.transaction_type == 'escrow_capture':
        held -= amount
        balance -= amount
    return balance, held


@transaction.atomic
def backfill_wallet_balance_pockets(apps, schema_editor):
    Wallet = apps.get_model('payments', 'Wallet')
    WalletTransaction = apps.get_model('payments', 'WalletTransaction')
    Payout = apps.get_model('payments', 'Payout')
    zero = Decimal('0.00')

    for wallet in Wallet.objects.select_for_update().all().iterator():
        if (
            wallet.balance == wallet.shopping_balance + wallet.earnings_balance
            and wallet.held_balance == wallet.shopping_held_balance + wallet.earnings_held_balance
        ):
            continue

        entries = list(
            WalletTransaction.objects.filter(wallet_id=wallet.pk).order_by('created_at', 'id')
        )
        if not entries:
            raise RuntimeError(f'Wallet {wallet.pk} has an unclassified balance without a transaction ledger.')

        payout_sources = {
            str(payout_id): balance_source
            for payout_id, balance_source in Payout.objects.filter(wallet_id=wallet.pk).values_list(
                'id', 'balance_source'
            )
        }
        earnings_balance = zero
        earnings_held = zero

        for entry in entries:
            source = _transaction_source(entry, payout_sources)
            if source == 'earnings':
                earnings_balance, earnings_held = _apply_earnings_entry(
                    entry, earnings_balance, earnings_held
                )

            shopping_balance = entry.balance_after - earnings_balance
            shopping_held = entry.held_balance_after - earnings_held
            if min(earnings_balance, earnings_held, shopping_balance, shopping_held) < zero:
                raise RuntimeError(f'Wallet {wallet.pk} ledger cannot be split safely by balance source.')

            entry.balance_source = source
            if source == 'earnings':
                entry.source_balance_after = earnings_balance
                entry.source_held_balance_after = earnings_held
            else:
                entry.source_balance_after = shopping_balance
                entry.source_held_balance_after = shopping_held
            entry.save(update_fields=[
                'balance_source', 'source_balance_after', 'source_held_balance_after'
            ])

        last_entry = entries[-1]
        if last_entry.balance_after != wallet.balance or last_entry.held_balance_after != wallet.held_balance:
            raise RuntimeError(f'Wallet {wallet.pk} balance does not match its latest transaction snapshot.')

        shopping_balance = wallet.balance - earnings_balance
        shopping_held = wallet.held_balance - earnings_held
        if min(earnings_balance, earnings_held, shopping_balance, shopping_held) < zero:
            raise RuntimeError(f'Wallet {wallet.pk} final balance cannot be split safely by source.')

        wallet.shopping_balance = shopping_balance
        wallet.shopping_held_balance = shopping_held
        wallet.earnings_balance = earnings_balance
        wallet.earnings_held_balance = earnings_held
        wallet.save(update_fields=[
            'shopping_balance', 'shopping_held_balance',
            'earnings_balance', 'earnings_held_balance',
        ])


class Migration(migrations.Migration):
    dependencies = [
        ('payments', '0006_wallet_balance_pockets'),
    ]

    operations = [
        migrations.RunPython(backfill_wallet_balance_pockets, migrations.RunPython.noop),
    ]
