from django.contrib import admin
from .models import Category, Product, ProductImage, Tag


admin.site.register(Category)
admin.site.register(Tag)
admin.site.register(ProductImage)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = ('name', 'store', 'sku', 'price', 'stock_quantity', 'weight_kg', 'status')
	list_filter = ('status', 'currency', 'created_at')
	search_fields = ('name', 'sku', 'store__name')
