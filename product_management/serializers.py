from decimal import Decimal

from rest_framework import serializers

from .models import Category, Product, ProductImage, Tag


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'parent', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'slug', 'categories', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'product', 'image', 'alt_text', 'is_primary', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'product', 'created_at', 'updated_at']


class ProductSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'store', 'category', 'tags', 'images', 'name', 'slug', 'description', 'sku', 'price',
            'currency', 'stock_quantity', 'weight_kg', 'length_cm', 'width_cm', 'height_cm',
            'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate_currency(self, value):
        return value.upper()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        status = attrs.get('status', getattr(self.instance, 'status', None))
        measurements = {
            field: attrs.get(field, getattr(self.instance, field, None))
            for field in ('weight_kg', 'length_cm', 'width_cm', 'height_cm')
        }

        if status == 'published':
            missing = [field for field, value in measurements.items() if value is None]
            if missing:
                raise serializers.ValidationError({
                    'shipping': 'Weight and all package dimensions are required before publishing.',
                })

        for field, value in measurements.items():
            if value is not None and value <= 0:
                raise serializers.ValidationError({field: 'Enter a value greater than zero.'})

        for field in ('length_cm', 'width_cm', 'height_cm'):
            value = measurements[field]
            if value is not None and value >= Decimal('100'):
                raise serializers.ValidationError({
                    field: 'Standard delivery only accepts packages with every side under 100 cm.',
                })
        return attrs
