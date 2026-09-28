from rest_framework import serializers

from .models import Booking, Service, ServiceCategory, ServiceImage


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'slug', 'description', 'parent', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ServiceImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceImage
        fields = ['id', 'service', 'image', 'alt_text', 'is_primary', 'display_order', 'created_at', 'updated_at']
        read_only_fields = ['id', 'service', 'created_at', 'updated_at']


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


class BookingSerializer(serializers.ModelSerializer):
    customer_id = serializers.UUIDField(source='customer_id', read_only=True)
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
