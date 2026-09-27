from django.contrib import admin

from .models import PaymentAttempt, Wallet, WalletTransaction


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