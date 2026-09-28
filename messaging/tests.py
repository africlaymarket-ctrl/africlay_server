from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import UserRole
from core.test_utils import authenticate_client, create_verified_user
from .models import Conversation, Message

User = get_user_model()


class MessagingContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = create_verified_user(UserRole.BUYER, 'buyer')
        self.seller = create_verified_user(UserRole.SELLER, 'seller')
        self.conversation = Conversation.objects.create(
            buyer=self.buyer,
            seller=self.seller,
        )

    def authenticate(self, user):
        authenticate_client(self.client, user)

    def test_conversation_messages_are_oldest_first_and_sender_flags_match_current_user(self):
        self.authenticate(self.buyer)
        Message.objects.create(conversation=self.conversation, sender=self.buyer, body='First message')
        Message.objects.create(conversation=self.conversation, sender=self.seller, body='Reply')

        response = self.client.get(
            reverse('messages:conversation-detail', kwargs={'conversation_id': self.conversation.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['body'] for item in response.data['messages']], ['First message', 'Reply'])
        self.assertTrue(response.data['messages'][0]['is_from_me'])
        self.assertFalse(response.data['messages'][1]['is_from_me'])
