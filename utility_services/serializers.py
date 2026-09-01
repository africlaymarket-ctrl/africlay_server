from rest_framework import serializers


class SendSMSSerializer(serializers.Serializer):
    """Serializer for SMS dispatch request."""
    phone_numbers = serializers.ListField(
        child=serializers.CharField(max_length=30),
        required=False,
        help_text="List of recipient phone numbers (e.g. ['0712345678', '0722000000'])",
    )
    phone_number = serializers.CharField(
        max_length=30,
        required=False,
        help_text="Single recipient phone number (optional if phone_numbers is provided)",
    )
    message = serializers.CharField(
        max_length=500,
        required=True,
        help_text="SMS text message content",
    )

    def validate(self, attrs):
        phone_numbers = attrs.get('phone_numbers')
        phone_number = attrs.get('phone_number')

        if not phone_numbers and not phone_number:
            raise serializers.ValidationError(
                "Either 'phone_number' (string) or 'phone_numbers' (list) must be provided."
            )
        return attrs


class SendSMSResponseSerializer(serializers.Serializer):
    """Serializer for SMS dispatch response."""
    success = serializers.BooleanField(help_text="Whether the SMS dispatch succeeded")
    message = serializers.CharField(help_text="Status message or error description")
