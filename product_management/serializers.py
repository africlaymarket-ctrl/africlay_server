from decimal import Decimal

from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Product, ProductImage, Tag


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'parent', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class SellerCategoryPathSerializer(serializers.Serializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(parent__isnull=True),
        required=False,
    )
    parent_name = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    child_name = serializers.CharField(max_length=100, trim_whitespace=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        parent = attrs.get('parent')
        parent_name = ' '.join(attrs.get('parent_name', '').split())
        child_name = ' '.join(attrs['child_name'].split())
        if bool(parent) == bool(parent_name):
            raise serializers.ValidationError({
                'parent': 'Choose an existing main category or enter a new one.',
            })
        if parent_name and parent_name.casefold() == child_name.casefold():
            raise serializers.ValidationError({
                'child_name': 'The child category must be different from the main category.',
            })
        attrs['parent_name'] = parent_name
        attrs['child_name'] = child_name
        return attrs

    @staticmethod
    def _slug_for(name):
        base = slugify(name)[:110] or 'category'
        slug = base
        suffix = 2
        while Category.objects.filter(slug=slug).exists():
            ending = f'-{suffix}'
            slug = f'{base[:120 - len(ending)]}{ending}'
            suffix += 1
        return slug

    def create(self, validated_data):
        with transaction.atomic():
            parent = validated_data.get('parent')
            parent_created = False
            if parent is None:
                parent_name = validated_data['parent_name']
                parent = Category.objects.select_for_update().filter(name__iexact=parent_name).first()
                if parent and parent.parent_id:
                    raise serializers.ValidationError({
                        'parent_name': 'That name is already used by a child category.',
                    })
                if parent is None:
                    parent = Category.objects.create(
                        name=parent_name,
                        slug=self._slug_for(parent_name),
                    )
                    parent_created = True

            child_name = validated_data['child_name']
            child = Category.objects.select_for_update().filter(name__iexact=child_name).first()
            if child and child.parent_id != parent.id:
                raise serializers.ValidationError({
                    'child_name': 'That category already exists under a different main category.',
                })
            child_created = child is None
            if child is None:
                child = Category.objects.create(
                    name=child_name,
                    slug=self._slug_for(child_name),
                    parent=parent,
                )

        return {
            'parent': parent,
            'child': child,
            'created': parent_created or child_created,
        }


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
