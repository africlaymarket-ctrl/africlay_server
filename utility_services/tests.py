from unittest.mock import patch, MagicMock
import requests
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from authapp.models import AdminAuditLog
from rest_framework.test import APIClient
from rest_framework import status

from utility_services.utils import (
    normalize_phone_number,
    send_sms,
    dispatch_sms,
    SendSMS,
)

User = get_user_model()


class PhoneNormalizationTests(TestCase):
    """Unit tests for phone number normalization."""

    def test_normalize_standard_kenyan_07(self):
        self.assertEqual(normalize_phone_number("0712345678"), "254712345678")

    def test_normalize_standard_kenyan_01(self):
        self.assertEqual(normalize_phone_number("0112345678"), "254112345678")

    def test_normalize_with_plus_prefix(self):
        self.assertEqual(normalize_phone_number("+254712345678"), "254712345678")

    def test_normalize_with_spaces_and_dashes(self):
        self.assertEqual(normalize_phone_number("+254 712-345-678"), "254712345678")

    def test_normalize_nine_digits(self):
        self.assertEqual(normalize_phone_number("712345678"), "254712345678")

    def test_normalize_already_formatted(self):
        self.assertEqual(normalize_phone_number("254712345678"), "254712345678")

    def test_normalize_integer_input(self):
        self.assertEqual(normalize_phone_number(712345678), "254712345678")

    def test_normalize_empty_or_none(self):
        self.assertEqual(normalize_phone_number(""), "")
        self.assertEqual(normalize_phone_number(None), "")


class SendSMSServiceTests(TestCase):
    """Unit tests for SendSMS class and send_sms helper."""

    @patch("utility_services.utils.requests.post")
    def test_send_sms_success_single_number(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'status_code': '1000', 'message_id': 'message-1', 'phone': '254712345678', 'message_cost': '0.5'}]
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Hello Africlay test",
            phone_numbers="0712345678",
            token="test_sms_token_123",
        )

        self.assertTrue(success)
        self.assertIn("accepted by provider", message)
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["phone"], "254712345678")
        self.assertEqual(payload["message"], "Hello Africlay test")
        self.assertEqual(payload["api_key"], "test_sms_token_123")
        self.assertEqual(payload["sender_id"], "AFRICLAYMKT")

    @patch("utility_services.utils.requests.post")
    def test_send_sms_success_multiple_numbers(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'status_code': '1000', 'phone': '254712345678'},
            {'status_code': '1000', 'phone': '254722000000'},
        ]
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Broadcast update",
            phone_numbers=["0712345678", "0722000000"],
            token="test_token",
        )

        self.assertTrue(success)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["phone"], "254712345678,254722000000")

    @patch("utility_services.utils.requests.post")
    def test_send_sms_failure_gateway_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Test msg",
            phone_numbers="0712345678",
            token="test_token",
        )

        self.assertFalse(success)
        self.assertIn("rejected", message)

    def test_send_sms_missing_token(self):
        success, message = SendSMS.send(
            message="Test msg",
            phone_numbers="0712345678",
            token="",
        )
        self.assertFalse(success)
        self.assertIn("rejected", message)

    @patch("utility_services.utils.requests.post")
    def test_provider_partial_rejection_is_not_reported_as_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {'status_code': '1000', 'phone': '254712345678', 'message_id': 'accepted-1'},
            {'status_code': '1009', 'phone': '254722000000'},
        ]
        results = dispatch_sms('Update', ['0712345678', '0722000000'], token='test-key')
        self.assertEqual([item.status for item in results], ['accepted', 'rejected'])
        self.assertEqual(results[0].message_id, 'accepted-1')

    @patch("utility_services.utils.requests.post", side_effect=__import__('requests').Timeout)
    def test_timeout_is_unknown_and_not_automatically_retried(self, mock_post):
        result = dispatch_sms('Update', '0712345678', token='test-key')[0]
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(mock_post.call_count, 1)

    @patch("utility_services.utils.requests.post")
    def test_invalid_phone_is_rejected_before_provider_call(self, mock_post):
        result = dispatch_sms('Update', 'not-a-phone', token='test-key')[0]
        self.assertEqual(result.status, 'rejected')
        mock_post.assert_not_called()

    @patch("utility_services.utils.requests.post")
    def test_send_otp_helper(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'status_code': '1000', 'phone': '254712345678'}]
        mock_post.return_value = mock_response

        success, message = SendSMS.send_otp(
            phone_number="0712345678",
            otp_code="654321",
            app_name="Africlay",
            token="test_token",
        )

        self.assertTrue(success)
        payload = mock_post.call_args[1]["json"]
        self.assertIn("654321", payload["message"])
        self.assertIn("Africlay", payload["message"])


class SendSMSViewAPITests(TestCase):
    """API endpoint tests for /api/utilities/sms/send/."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('utility_services:send-sms')
        self.admin = User.objects.create_superuser(
            email='sms-admin@example.com',
            password='AdminPassword123!',
        )
        self.client.force_authenticate(self.admin)

    def test_api_rejects_anonymous_dispatch(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.url,
            data={'phone_number': '0712345678', 'message': 'Must not send'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("utility_services.utils.requests.post")
    def test_api_send_sms_single_number(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        mock_response.json.return_value = [{'status_code': '1000', 'phone': '254712345678'}]
        with self.settings(BLESSEDTEXTS_API_KEY="test_env_token"):
            response = self.client.post(
                self.url,
                data={
                    "phone_number": "0712345678",
                    "message": "Testing API endpoint",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertIn("accepted by provider", response.data["message"])
        records = list(AdminAuditLog.objects.filter(target_type='sms_batch').order_by('created_at'))
        self.assertEqual([item.action for item in records], ['sms_submission_requested', 'sms_submission_result'])
        self.assertEqual(records[0].metadata['recipient_count'], 1)
        self.assertNotIn('phone', str(records[0].metadata))

    @patch("utility_services.utils.requests.post")
    def test_api_send_sms_list_numbers(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        mock_response.json.return_value = [
            {'status_code': '1000', 'phone': '254712345678'},
            {'status_code': '1000', 'phone': '254799112233'},
        ]
        with self.settings(BLESSEDTEXTS_API_KEY="test_env_token"):
            response = self.client.post(
                self.url,
                data={
                    "phone_numbers": ["0712345678", "0799112233"],
                    "message": "Testing multi-number send",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

    @patch("utility_services.utils.requests.post")
    def test_api_unknown_outcome_requires_reconciliation(self, mock_post):
        mock_post.side_effect = requests.Timeout('outcome unknown')
        with self.settings(BLESSEDTEXTS_API_KEY="test_env_token"):
            response = self.client.post(
                self.url,
                data={"phone_number": "0712345678", "message": "Testing uncertain result"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(response.data['success'])
        self.assertIn('Check the provider dashboard', response.data['message'])

    def test_api_validation_missing_phone(self):
        response = self.client.post(
            self.url,
            data={"message": "Message without recipient"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_validation_rejects_both_phone_fields(self):
        response = self.client.post(
            self.url,
            data={
                "phone_number": "0712345678",
                "phone_numbers": ["0712345678"],
                "message": "Ambiguous recipient input",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_validation_missing_message(self):
        response = self.client.post(
            self.url,
            data={"phone_number": "0712345678"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
