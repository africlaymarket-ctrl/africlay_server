from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'provider', 'status', 'amount', 'checkout_request_id', 'created_at')
    list_filter = ('provider', 'status', 'created_at')
    search_fields = ('order__id', 'checkout_request_id', 'merchant_request_id', 'receipt_number')
    readonly_fields = ('id', 'order', 'provider', 'status', 'amount', 'currency', 'phone_number',
                      'merchant_request_id', 'checkout_request_id', 'receipt_number', 'failure_code',
                      'failure_message', 'raw_callback_payload', 'created_at', 'updated_at', 'completed_at')
