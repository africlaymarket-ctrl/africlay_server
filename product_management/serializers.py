from rest_framework import serializers

from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'store', 'name', 'slug', 'description', 'sku', 'price',
            'currency', 'stock_quantity', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate_currency(self, value):
        return value.upper()