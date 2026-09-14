from rest_framework import serializers

from .models import Store, StoreKYC, StoreKYCStatus


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
            'legal_name',
            'business_type',
            'phone_number',
            'address',
            'city',
            'country',
            'country_code',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'status', 'created_at', 'updated_at']


class StoreKYCSerializer(serializers.ModelSerializer):
    store = serializers.UUIDField(source='store_id', read_only=True)
    reviewed_by = serializers.UUIDField(source='reviewed_by_id', read_only=True)

    class Meta:
        model = StoreKYC
        fields = [
            'id',
            'store',
            'business_name',
            'business_registration_number',
            'tax_identification_number',
            'status',
            'submitted_at',
            'reviewed_at',
            'reviewed_by',
            'rejection_reason',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'store',
            'status',
            'submitted_at',
            'reviewed_at',
            'reviewed_by',
            'rejection_reason',
            'created_at',
            'updated_at',
        ]


class StoreKYCReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=[StoreKYCStatus.APPROVED, StoreKYCStatus.REJECTED])
    rejection_reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate(self, attrs):
        if attrs['decision'] == StoreKYCStatus.REJECTED and not attrs.get('rejection_reason', '').strip():
            raise serializers.ValidationError({'rejection_reason': 'A reason is required when rejecting KYC.'})
        return attrs