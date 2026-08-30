from django.contrib import admin
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = ('name', 'store', 'sku', 'price', 'stock_quantity', 'status')
	list_filter = ('status', 'currency', 'created_at')
	search_fields = ('name', 'sku', 'store__name')
