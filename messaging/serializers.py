from rest_framework import serializers

from authapp.models import User
from product_management.models import Product
from service_management.models import Service
from store_management.models import Store
from .models import Conversation, Message


class UserLightSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'phone_number']

    def get_full_name(self, obj):
        return obj.full_name


class ConversationSerializer(serializers.ModelSerializer):
    buyer = UserLightSerializer(read_only=True)
    seller = UserLightSerializer(read_only=True)
    store = serializers.SerializerMethodField()
    product = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id', 'buyer', 'seller', 'store', 'product', 'service',
            'last_message_at', 'updated_at', 'created_at',
            'unread_count', 'last_message', 'messages',
        ]

    def get_store(self, obj):
        if not obj.store:
            return None
        return {'id': str(obj.store.id), 'name': obj.store.name}

    def get_product(self, obj):
        if not obj.product:
            return None
        return {'id': str(obj.product.id), 'name': obj.product.name}

    def get_service(self, obj):
        if not obj.service:
            return None
        return {'id': str(obj.service.id), 'name': obj.service.name}

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0
        return obj.messages.exclude(sender=request.user).filter(read_at__isnull=True).count()

    def get_last_message(self, obj):
        last_message = obj.messages.order_by('-created_at').first()
        if not last_message:
            return None
        return {
            'id': str(last_message.id),
            'sender_id': str(last_message.sender_id),
            'body': last_message.body,
            'created_at': last_message.created_at,
        }

    def get_messages(self, obj):
        messages = obj.messages.select_related('sender').order_by('created_at')
        return MessageSerializer(messages, many=True, context=self.context).data


class MessageSerializer(serializers.ModelSerializer):
    sender = UserLightSerializer(read_only=True)
    is_from_me = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'conversation_id', 'sender', 'body', 'attachment_url', 'attachment_name', 'created_at', 'read_at', 'is_from_me']

    def get_is_from_me(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return obj.sender_id == request.user.id


class MessageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['body', 'attachment_url', 'attachment_name']

    def validate_body(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Message body cannot be empty.')
        if len(value) > 2000:
            raise serializers.ValidationError('Message is too long.')
        return value


class ConversationCreateSerializer(serializers.Serializer):
    seller_id = serializers.UUIDField(required=False, allow_null=True)
    store_id = serializers.UUIDField(required=False, allow_null=True)
    product_id = serializers.UUIDField(required=False, allow_null=True)
    service_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        seller_id = attrs.get('seller_id')
        store_id = attrs.get('store_id')
        product_id = attrs.get('product_id')
        service_id = attrs.get('service_id')

        if not seller_id and not store_id and not product_id and not service_id:
            raise serializers.ValidationError('Provide at least one addressable conversation target.')
        return attrs
