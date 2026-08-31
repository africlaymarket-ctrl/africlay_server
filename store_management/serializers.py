from rest_framework import serializers

from .models import Store


class StoreSerializer(serializers.ModelSerializer):
    owner = serializers.UUIDField(source='owner_id', read_only=True)

    class Meta:
        model = Store
        fields = [
            'id',
            'owner',
            'name',
            'slug',
            'description',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'status', 'created_at', 'updated_at']