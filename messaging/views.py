from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Conversation, Message
from .serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)


class ConversationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        conversations = Conversation.objects.filter(Q(buyer=request.user) | Q(seller=request.user)).prefetch_related('messages__sender')
        serializer = ConversationSerializer(conversations, many=True, context={'request': request})
        return Response({'results': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        seller_id = payload.get('seller_id')
        store_id = payload.get('store_id')
        product_id = payload.get('product_id')
        service_id = payload.get('service_id')

        if seller_id is None:
            seller_id = request.user.id

        seller = request.user if seller_id == request.user.id else request.user.__class__.objects.filter(id=seller_id).first()
        if not seller:
            return Response({'detail': 'Seller not found.'}, status=status.HTTP_404_NOT_FOUND)

        conversation, created = Conversation.objects.get_or_create(
            buyer=request.user,
            seller=seller,
            store_id=store_id,
            product_id=product_id,
            service_id=service_id,
        )

        return Response(
            ConversationSerializer(conversation, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id, *args, **kwargs):
        conversation = Conversation.objects.filter(
            id=conversation_id,
        ).filter(Q(buyer=request.user) | Q(seller=request.user)).first()

        if not conversation:
            return Response({'detail': 'Conversation not found.'}, status=status.HTTP_404_NOT_FOUND)

        conversation.messages.filter(~Q(sender=request.user), read_at__isnull=True).update(read_at=timezone.now())

        serializer = ConversationSerializer(conversation, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class MessageListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id, *args, **kwargs):
        conversation = Conversation.objects.filter(
            id=conversation_id,
        ).filter(Q(buyer=request.user) | Q(seller=request.user)).first()

        if not conversation:
            return Response({'detail': 'Conversation not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = Message.objects.create(
            conversation=conversation,
            sender=request.user,
            body=serializer.validated_data['body'],
            attachment_url=serializer.validated_data.get('attachment_url'),
            attachment_name=serializer.validated_data.get('attachment_name', ''),
        )
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=['last_message_at', 'updated_at'])

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)
