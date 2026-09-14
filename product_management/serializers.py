from rest_framework import serializers

from .models import Category, Product, ProductImage, ProductVariant, Tag


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'parent', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'slug', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'product', 'image', 'alt_text', 'is_primary', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'product', 'created_at', 'updated_at']


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ['id', 'product', 'name', 'sku_suffix', 'attributes', 'price_modifier', 'stock_quantity', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'product', 'created_at', 'updated_at']


class ProductSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'store', 'category', 'tags', 'images', 'variants', 'name', 'slug', 'description', 'sku', 'price',
            'currency', 'stock_quantity', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate_currency(self, value):
        return value.upper()
