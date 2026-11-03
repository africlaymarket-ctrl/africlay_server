"""BlessedTexts SMS transport. Provider acceptance is not handset delivery."""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple, Union

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def normalize_phone_number(phone_number: Union[str, int], default_country_code: str = '254') -> str:
    if not phone_number:
        return ''
    cleaned = re.sub(r'[^\d+]', '', str(phone_number).strip()).lstrip('+')
    country_code = str(default_country_code).lstrip('+')
    if cleaned.startswith('0') and len(cleaned) == 10:
        return country_code + cleaned[1:]
    if len(cleaned) == 9 and not cleaned.startswith(country_code):
        return country_code + cleaned
    return cleaned


def valid_sms_phone(phone_number: str) -> bool:
    return bool(re.fullmatch(r'254[17]\d{8}', phone_number))


@dataclass(frozen=True)
class SMSResult:
    phone: str
    status: str  # accepted, rejected, or unknown
    code: str = ''
    message_id: str = ''
    cost: Optional[Decimal] = None


def dispatch_sms(message: str, phone_numbers: Union[str, int, List[Union[str, int]]], *,
                 token: Optional[str] = None, url: Optional[str] = None) -> List[SMSResult]:
    """POST one batch; never retry an ambiguous response without provider reconciliation."""
    numbers = phone_numbers if isinstance(phone_numbers, (list, tuple, set)) else [phone_numbers]
    phones = list(dict.fromkeys(normalize_phone_number(number) for number in numbers if number))
    if not phones or any(not valid_sms_phone(phone) for phone in phones):
        return [SMSResult(phone, 'rejected', 'invalid_phone') for phone in phones] or [SMSResult('', 'rejected', 'missing_phone')]
    if not message or not message.strip() or len(message) > 500:
        return [SMSResult(phone, 'rejected', 'invalid_message') for phone in phones]
    api_key = token if token is not None else settings.BLESSEDTEXTS_API_KEY
    sender_id = settings.BLESSEDTEXTS_SENDER_ID
    if not api_key or not sender_id:
        return [SMSResult(phone, 'rejected', 'configuration_missing') for phone in phones]

    try:
        response = requests.post(
            url or settings.BLESSEDTEXTS_URL,
            json={'api_key': api_key, 'sender_id': sender_id, 'message': message, 'phone': ','.join(phones)},
            headers={'Accept': 'application/json'}, timeout=5,
        )
    except requests.RequestException:
        logger.warning('SMS provider request outcome unknown; recipient count: %s', len(phones))
        return [SMSResult(phone, 'unknown', 'network_error') for phone in phones]
    if response.status_code >= 500:
        return [SMSResult(phone, 'unknown', f'http_{response.status_code}') for phone in phones]
    if response.status_code >= 400:
        return [SMSResult(phone, 'rejected', f'http_{response.status_code}') for phone in phones]
    try:
        entries = response.json()
    except ValueError:
        return [SMSResult(phone, 'unknown', 'invalid_response') for phone in phones]
    if not isinstance(entries, list):
        return [SMSResult(phone, 'unknown', 'invalid_response') for phone in phones]
    by_phone = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        phone = normalize_phone_number(entry.get('phone', ''))
        if phone not in phones or phone in by_phone:
            continue
        code = str(entry.get('status_code', ''))
        cost = None
        try:
            if entry.get('message_cost') is not None:
                cost = Decimal(str(entry['message_cost']))
        except (InvalidOperation, ValueError):
            pass
        by_phone[phone] = SMSResult(
            phone, 'accepted' if code == '1000' else 'rejected', code,
            str(entry.get('message_id') or ''), cost,
        )
    return [by_phone.get(phone, SMSResult(phone, 'unknown', 'missing_result')) for phone in phones]


def send_sms(message: str, phone_numbers: Union[str, int, List[Union[str, int]]],
             token: Optional[str] = None, url: Optional[str] = None) -> Tuple[bool, str]:
    """Compatibility wrapper for the existing superadmin endpoint."""
    results = dispatch_sms(message, phone_numbers, token=token, url=url)
    if all(result.status == 'accepted' for result in results):
        return True, 'SMS accepted by provider; delivery is not confirmed.'
    if any(result.status == 'unknown' for result in results):
        return False, 'SMS outcome unknown. Check the provider dashboard before sending again.'
    return False, 'SMS rejected by provider or recipient validation.'


class SendSMS:
    def __init__(self, token: Optional[str] = None, url: Optional[str] = None):
        self.token = token
        self.url = url

    def send_message(self, message: str, phone_numbers: Union[str, int, List[Union[str, int]]]) -> Tuple[bool, str]:
        return send_sms(message, phone_numbers, token=self.token, url=self.url)

    @classmethod
    def normalize_phone(cls, phone_number: Union[str, int], default_country_code: str = '254') -> str:
        return normalize_phone_number(phone_number, default_country_code)

    @classmethod
    def send(cls, message: str, phone_numbers: Union[str, int, List[Union[str, int]]],
             token: Optional[str] = None, url: Optional[str] = None) -> Tuple[bool, str]:
        return send_sms(message, phone_numbers, token=token, url=url)

    @classmethod
    def send_otp(cls, phone_number: Union[str, int], otp_code: str, app_name: str = 'Africlay',
                 token: Optional[str] = None) -> Tuple[bool, str]:
        message = f'Your {app_name} verification code is: {otp_code}. Valid for 10 minutes. Do not share this code.'
        return cls.send(message, phone_number, token=token)
