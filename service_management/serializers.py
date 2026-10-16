from rest_framework import serializers

from core.images import marketplace_image_variants
from core.serializer_fields import MarketplaceImageField, marketplace_media_url
from .models import Booking, Service, ServiceCategory, ServiceImage


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'slug', 'description', 'parent', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ServiceImageSerializer(serializers.ModelSerializer):
    image = MarketplaceImageField()
    thumbnail = MarketplaceImageField(read_only=True)

    class Meta:
        model = ServiceImage
        fields = [
            'id', 'service', 'image', 'thumbnail', 'alt_text', 'is_primary',
            'display_order', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'service', 'created_at', 'updated_at']

    def create(self, validated_data):
        upload = validated_data.pop('image')
        detail, thumbnail = marketplace_image_variants(upload)
        instance = ServiceImage(**validated_data)
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


class ServiceCatalogImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ServiceImage
        fields = ['id', 'image', 'alt_text', 'is_primary', 'display_order']

    def get_image(self, obj):
        request = self.context.get('request')
        return marketplace_media_url(obj.thumbnail or obj.image, request)


class ServiceSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)
    images = ServiceImageSerializer(many=True, read_only=True)

    class Meta:
        model = Service
        fields = [
            'id', 'store', 'category', 'tags', 'images', 'name', 'slug', 'description',
            'price', 'currency', 'duration_minutes', 'booking_buffer_minutes', 'status',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate_currency(self, value):
        return value.upper()


class ServiceCatalogSerializer(ServiceSerializer):
    images = serializers.SerializerMethodField()

    def get_images(self, obj):
        images = getattr(obj, 'catalog_images', None)
        if images is None:
            images = obj.images.order_by('-is_primary', 'display_order', 'created_at')[:1]
        image = next(iter(images), None)
        if image is None:
            return []
        return ServiceCatalogImageSerializer([image], many=True, context=self.context).data


class BookingSerializer(serializers.ModelSerializer):
    customer_id = serializers.UUIDField(read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = Booking
        fields = ['id', 'service', 'customer_id', 'service_name', 'scheduled_at', 'notes', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'customer_id', 'service_name', 'status', 'created_at', 'updated_at']


class BookingStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['status']

    def validate_status(self, value):
        current_status = self.instance.status
        allowed_transitions = {
            'pending': {'confirmed', 'cancelled'},
            'confirmed': {'completed', 'cancelled'},
            'cancelled': set(),
            'completed': set(),
        }
        if value not in allowed_transitions.get(current_status, set()):
            raise serializers.ValidationError(
                f'Bookings cannot transition from {current_status} to {value}.'
            )
        return value
