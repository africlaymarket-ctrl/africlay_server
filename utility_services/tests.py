from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from utility_services.utils import (
    normalize_phone_number,
    send_sms,
    SendSMS,
)


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
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Hello Africlay test",
            phone_numbers="0712345678",
            token="test_sms_token_123",
        )

        self.assertTrue(success)
        self.assertEqual(message, "SMS sent successfully")
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["phone"], ["254712345678"])
        self.assertEqual(payload["message"], "CodeYetuSMS: Hello Africlay test")
        self.assertEqual(payload["token"], "test_sms_token_123")

    @patch("utility_services.utils.requests.post")
    def test_send_sms_success_multiple_numbers(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Broadcast update",
            phone_numbers=["0712345678", "0722000000"],
            token="test_token",
        )

        self.assertTrue(success)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["phone"], ["254712345678", "254722000000"])

    @patch("utility_services.utils.requests.post")
    def test_send_sms_failure_gateway_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Insufficient balance"
        mock_post.return_value = mock_response

        success, message = SendSMS.send(
            message="Test msg",
            phone_numbers="0712345678",
            token="test_token",
        )

        self.assertFalse(success)
        self.assertIn("Insufficient balance", message)

    def test_send_sms_missing_token(self):
        success, message = SendSMS.send(
            message="Test msg",
            phone_numbers="0712345678",
            token="",
        )
        self.assertFalse(success)
        self.assertEqual(message, "SMS token is not configured")

    @patch("utility_services.utils.requests.post")
    def test_send_otp_helper(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
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

    @patch("utility_services.utils.requests.post")
    def test_api_send_sms_single_number(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        with self.settings(SMS_TOKEN="test_env_token"):
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
        self.assertEqual(response.data["message"], "SMS sent successfully")

    @patch("utility_services.utils.requests.post")
    def test_api_send_sms_list_numbers(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        with self.settings(SMS_TOKEN="test_env_token"):
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
