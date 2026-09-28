from rest_framework import serializers
from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    reviewer_email = serializers.EmailField(source='reviewer.email', read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'reviewer_email', 'rating', 'comment', 'product', 'service', 'store', 'created_at', 'updated_at']
        read_only_fields = ['id', 'reviewer_email', 'created_at', 'updated_at']

    def validate(self, attrs):
        targets = [attrs.get('product'), attrs.get('service'), attrs.get('store')]
        if sum(t is not None for t in targets) != 1:
            raise serializers.ValidationError('Exactly one of product, service, or store must be provided.')
        return attrs
