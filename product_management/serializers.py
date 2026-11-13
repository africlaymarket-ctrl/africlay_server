from decimal import Decimal

from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from core.images import marketplace_image_variants
from core.serializer_fields import MarketplaceImageField, marketplace_media_url
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
    image = MarketplaceImageField()
    thumbnail = MarketplaceImageField(read_only=True)

    class Meta:
        model = ProductImage
        fields = [
            'id', 'product', 'image', 'thumbnail', 'alt_text', 'is_primary',
            'display_order', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'product', 'created_at', 'updated_at']

    def create(self, validated_data):
        upload = validated_data.pop('image')
        detail, thumbnail = marketplace_image_variants(upload)
        instance = ProductImage(**validated_data)
        try:
            instance.image.save(detail.name, detail, save=False)
            instance.thumbnail.save(thumbnail.name, thumbnail, save=False)
            instance.save()
        except Exception:
            if instance.image.name:
                instance.image.storage.delete(instance.image.name)
            if instance.thumbnail.name:
                instance.thumbnail.storage.delete(instance.thumbnail.name)
            raise
        return instance


class ProductCatalogImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ['id', 'image', 'alt_text', 'is_primary', 'display_order']

    def get_image(self, obj):
        request = self.context.get('request')
        return marketplace_media_url(obj.thumbnail or obj.image, request)


class ProductSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'store', 'category', 'tags', 'images', 'name', 'slug', 'description', 'sku', 'price', 'price_unit', 'price_options',
            'currency', 'stock_quantity', 'weight_kg', 'length_cm', 'width_cm', 'height_cm',
            'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate_currency(self, value):
        return value.upper()

    def validate_price_unit(self, value):
        unit = value.strip()
        if unit.lower().startswith('per '):
            unit = unit[4:].strip()
        return unit

    def validate_price_options(self, value):
        import uuid
        from decimal import Decimal, InvalidOperation

        if not isinstance(value, list) or len(value) > 12:
            raise serializers.ValidationError('Add no more than 12 price options.')
        seen_ids, seen_labels, cleaned = set(), set(), []
        for raw in value:
            if not isinstance(raw, dict):
                raise serializers.ValidationError('Each price option needs a label, price, stock and weight.')
            label = str(raw.get('label', '')).strip()
            if not label or len(label) > 60 or label.casefold() in seen_labels:
                raise serializers.ValidationError('Use a distinct label of up to 60 characters for each option.')
            try:
                option_id = str(uuid.UUID(str(raw.get('id') or uuid.uuid4())))
                price = Decimal(str(raw['price']))
                weight = Decimal(str(raw['weight_kg']))
                stock = int(raw['stock_quantity'])
                if (price <= 0 or price > Decimal('9999999999.99') or price.as_tuple().exponent < -2
                        or weight <= 0 or weight > Decimal('99999.999') or weight.as_tuple().exponent < -3
                        or stock < 0 or stock > 2147483647 or str(raw['stock_quantity']) != str(stock)):
                    raise ValueError()
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise serializers.ValidationError('Enter a positive price, whole-number stock and positive shipping weight for every option.')
            if option_id in seen_ids:
                raise serializers.ValidationError('Duplicate price option.')
            seen_ids.add(option_id)
            seen_labels.add(label.casefold())
            cleaned.append({'id': option_id, 'label': label, 'price': str(price), 'stock_quantity': stock, 'weight_kg': str(weight), 'active': True})
        if self.instance:
            old = {option['id']: option for option in self.instance.price_options or []}
            for option in cleaned:
                if option['id'] in old and not old[option['id']].get('active', True):
                    raise serializers.ValidationError('An inactive option cannot be reused. Add a new option instead.')
            for option_id, option in old.items():
                if option_id not in seen_ids:
                    cleaned.append({**option, 'active': False})
        return cleaned

    def _priced_values(self, validated_data):
        from .pricing import available_option_price
        options = validated_data.get('price_options', self.instance.price_options if self.instance else [])
        active = [option for option in options if option.get('active', True)]
        if active:
            validated_data['price'] = available_option_price(options, validated_data.get('price', self.instance.price if self.instance else None))
            validated_data['stock_quantity'] = sum(option['stock_quantity'] for option in active)
        return validated_data

    def create(self, validated_data):
        return super().create(self._priced_values(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._priced_values(validated_data))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        status = attrs.get('status', getattr(self.instance, 'status', None))
        category = attrs.get('category', getattr(self.instance, 'category', None))
        measurements = {
            field: attrs.get(field, getattr(self.instance, field, None))
            for field in ('weight_kg', 'length_cm', 'width_cm', 'height_cm')
        }
        options = attrs.get('price_options', getattr(self.instance, 'price_options', []))
        if any(option.get('active', True) for option in options) and any(measurements[field] is not None for field in ('length_cm', 'width_cm', 'height_cm')):
            raise serializers.ValidationError({'shipping': 'Price choices use their own shipping weights. Clear shared package dimensions before saving.'})

        if status == 'published':
            if category is None:
                raise serializers.ValidationError({'category': 'Choose a category before publishing.'})
            if measurements['weight_kg'] is None:
                raise serializers.ValidationError({
                    'weight_kg': 'Enter an estimated packaged weight before publishing.',
                })
            dimensions = [measurements[field] for field in ('length_cm', 'width_cm', 'height_cm')]
            if any(value is not None for value in dimensions) and any(value is None for value in dimensions):
                raise serializers.ValidationError({
                    'shipping': 'Enter all three package dimensions or leave all three blank. Without dimensions, delivery uses the listed weight.',
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


class ProductCatalogSerializer(ProductSerializer):
    images = serializers.SerializerMethodField()

    def get_images(self, obj):
        images = getattr(obj, 'catalog_images', None)
        if images is None:
            images = obj.images.order_by('-is_primary', 'display_order', 'created_at')[:1]
        image = next(iter(images), None)
        if image is None:
            return []
        return ProductCatalogImageSerializer([image], many=True, context=self.context).data
