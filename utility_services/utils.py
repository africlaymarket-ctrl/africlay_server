import os
import re
import logging
from typing import List, Union, Tuple, Optional, Any
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Default configuration from settings or environment
SMS_URL = getattr(settings, 'SMS_URL', os.getenv('SMS_URL', 'https://api.cradlevoices.com/v1/sms/send'))
SMS_TOKEN = getattr(settings, 'SMS_TOKEN', os.getenv('SMS_TOKEN', ''))


def normalize_phone_number(phone_number: Union[str, int], default_country_code: str = '254') -> str:
    """
    Normalizes a phone number into standard international format without leading '+'.

    Examples:
        '0712345678'        -> '254712345678'
        '0112345678'        -> '254112345678'
        '+254 712 345 678'  -> '254712345678'
        '254712345678'      -> '254712345678'
        '712345678'         -> '254712345678'
        '+1 (202) 555-0125' -> '12025550125'

    :param phone_number: Raw phone number string or integer.
    :param default_country_code: Country dial code (default: '254').
    :return: Normalized phone number string containing only digits.
    """
    if not phone_number:
        return ''

    raw_str = str(phone_number).strip()

    # Remove all characters except digits and leading '+'
    cleaned = re.sub(r'[^\d+]', '', raw_str)

    # Strip leading '+'
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]

    default_code = str(default_country_code).lstrip('+')

    # Kenyan numbers (or default country code) normalization
    # If starts with '0' and standard 10-digit format (e.g. 0712345678 or 0112345678)
    if cleaned.startswith('0') and len(cleaned) == 10:
        cleaned = default_code + cleaned[1:]
    # If 9 digits (e.g. 712345678 or 112345678)
    elif len(cleaned) == 9 and not cleaned.startswith(default_code):
        cleaned = default_code + cleaned

    return cleaned


def send_sms(
    message: str,
    phone_numbers: Union[str, int, List[Union[str, int]]],
    token: Optional[str] = None,
    url: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Send SMS using Cradle Voices / CodeYetuSMS API.

    :param message: Message text to send
    :param phone_numbers: Single phone number or list of phone numbers e.g. ["254712345678", "254712345679"]
    :param token: Optional API token override
    :param url: Optional API URL override
    :return: Tuple (success: bool, message: str)
    """
    api_token = token if token is not None else getattr(settings, 'SMS_TOKEN', os.getenv('SMS_TOKEN', ''))
    api_url = url if url is not None else getattr(settings, 'SMS_URL', os.getenv('SMS_URL', 'https://api.cradlevoices.com/v1/sms/send'))

    if not api_token:
        logger.warning("SMS_TOKEN is not configured.")
        return False, "SMS token is not configured"

    # Normalize phone numbers
    if isinstance(phone_numbers, (list, tuple, set)):
        phone_list = [normalize_phone_number(p) for p in phone_numbers if p]
    else:
        phone_list = [normalize_phone_number(phone_numbers)] if phone_numbers else []

    if not phone_list:
        return False, "No valid recipient phone numbers provided"

    payload = {
        "token": api_token,
        "message": f"CodeYetuSMS: {message}",
        "phone": phone_list,
    }
    headers = {
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=15)
        if response.status_code in (200, 201):
            logger.info("SMS successfully sent to %s recipient(s).", len(phone_list))
            return True, "SMS sent successfully"
        logger.error("Failed to send SMS. Status %s: %s", response.status_code, response.text)
        return False, f"SMS gateway returned status {response.status_code}: {response.text}"
    except Exception as e:
        logger.error("Error sending SMS: %s", e)
        return False, f"Error sending SMS: {str(e)}"


class SendSMS:
    """
    Reusable SMS service class for sending SMS messages, OTP codes, and bulk notifications.
    Can be called directly as class methods (e.g. SendSMS.send(...)) or as an instance.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        url: Optional[str] = None,
    ):
        self.token = token if token is not None else getattr(settings, 'SMS_TOKEN', os.getenv('SMS_TOKEN', ''))
        self.url = url if url is not None else getattr(settings, 'SMS_URL', os.getenv('SMS_URL', 'https://api.cradlevoices.com/v1/sms/send'))

    def send_message(
        self,
        message: str,
        phone_numbers: Union[str, int, List[Union[str, int]]],
    ) -> Tuple[bool, str]:
        """Send an SMS using this instance's configured token/url."""
        return send_sms(
            message=message,
            phone_numbers=phone_numbers,
            token=self.token,
            url=self.url,
        )

    @classmethod
    def normalize_phone(cls, phone_number: Union[str, int], default_country_code: str = '254') -> str:
        """Class method helper to normalize phone numbers."""
        return normalize_phone_number(phone_number, default_country_code=default_country_code)

    @classmethod
    def send(
        cls,
        message: str,
        phone_numbers: Union[str, int, List[Union[str, int]]],
        token: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Send an SMS message to one or multiple recipients.

        :param message: Message text to send
        :param phone_numbers: Single phone number or list of phone numbers
        :param token: Optional API token override
        :param url: Optional API URL override
        :return: Tuple (success: bool, status_message: str)
        """
        return send_sms(
            message=message,
            phone_numbers=phone_numbers,
            token=token,
            url=url,
        )

    @classmethod
    def send_otp(
        cls,
        phone_number: Union[str, int],
        otp_code: str,
        app_name: str = "Africlay",
        token: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Convenience method to dispatch a verification OTP code via SMS.

        :param phone_number: Recipient phone number
        :param otp_code: Numeric or alphanumeric OTP code
        :param app_name: Application name
        :param token: Optional API token override
        :return: Tuple (success: bool, status_message: str)
        """
        message = f"Your {app_name} verification code is: {otp_code}. Valid for 10 minutes. Do not share this code."
        return cls.send(message=message, phone_numbers=phone_number, token=token)


class SendEmail:
    """Placeholder for Email sending service (to be implemented)."""
    pass