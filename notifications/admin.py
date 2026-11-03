from django.contrib import admin

from .models import EmailConsentEvent, EmailDelivery


@admin.register(EmailDelivery)
class EmailDeliveryAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient', 'status', 'attempts', 'created_at', 'sent_at', 'delivered_at')
    list_filter = ('status', 'created_at')
    search_fields = ('recipient', 'subject', 'provider_message_id')
    readonly_fields = [field.name for field in EmailDelivery._meta.fields]

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailConsentEvent)
class EmailConsentEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'consented', 'source', 'created_at')
    list_filter = ('consented', 'created_at')
    readonly_fields = [field.name for field in EmailConsentEvent._meta.fields]

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
