from django.contrib import admin

from .models import LedgerEntry, Payment, PaymentAttempt, Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['user', 'currency', 'balance', 'held_balance', 'is_frozen', 'updated_at']
    list_filter = ['currency', 'is_frozen']
    search_fields = ['user__email']
    readonly_fields = ['user', 'currency', 'balance', 'held_balance', 'created_at', 'updated_at']


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['wallet', 'transaction_type', 'amount', 'reference', 'created_at']
    list_filter = ['transaction_type', 'wallet__currency']
    search_fields = ['reference', 'wallet__user__email']
    readonly_fields = [field.name for field in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'wallet', 'status', 'provider_receipt', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['user__email', 'checkout_request_id', 'provider_receipt']
    readonly_fields = [field.name for field in PaymentAttempt._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'provider', 'purpose', 'status', 'amount', 'checkout_request_id', 'created_at')
    list_filter = ('provider', 'purpose', 'status', 'created_at')
    search_fields = ('order__id', 'checkout_request_id', 'merchant_request_id', 'receipt_number')
    readonly_fields = ('id', 'order', 'provider', 'purpose', 'status', 'amount', 'currency', 'phone_number',
                      'merchant_request_id', 'checkout_request_id', 'receipt_number', 'failure_code',
                      'failure_message', 'raw_callback_payload', 'created_at', 'updated_at', 'completed_at')


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ('reference', 'order', 'entry_type', 'amount', 'currency', 'user', 'created_at')
    list_filter = ('entry_type', 'currency', 'created_at')
    search_fields = ('reference', 'order__id', 'payment__receipt_number', 'user__email')
    readonly_fields = [field.name for field in LedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
