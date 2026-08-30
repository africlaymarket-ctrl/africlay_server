from django.contrib import admin
from .models import Store


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
	list_display = ('name', 'owner', 'status', 'created_at')
	list_filter = ('status', 'created_at')
	search_fields = ('name', 'slug', 'owner__email')
	readonly_fields = ('created_at', 'updated_at')
