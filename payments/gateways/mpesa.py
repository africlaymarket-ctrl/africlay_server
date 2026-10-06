import base64
import json
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.utils import timezone


class MpesaConfigurationError(Exception):
    pass


class MpesaGatewayError(Exception):
    pass


def _configuration():
    required = [
        settings.MPESA_CONSUMER_KEY,
        settings.MPESA_CONSUMER_SECRET,
        settings.MPESA_SHORTCODE,
        settings.MPESA_PASSKEY,
        settings.MPESA_CALLBACK_BASE_URL,
    ]
    if not all(required):
        raise MpesaConfigurationError('M-Pesa credentials and callback URL must be configured.')

    if settings.MPESA_ENVIRONMENT == 'sandbox':
        host = 'https://sandbox.safaricom.co.ke'
    elif settings.MPESA_ENVIRONMENT == 'production':
        host = 'https://api.safaricom.co.ke'
    else:
        raise MpesaConfigurationError('MPESA_ENVIRONMENT must be sandbox or production.')

    if not settings.MPESA_CALLBACK_BASE_URL.startswith('https://'):
        raise MpesaConfigurationError('M-Pesa callback base URL must use HTTPS.')
    return host


def _access_token(host):
    try:
        response = requests.get(
            f'{host}/oauth/v1/generate?grant_type=client_credentials',
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
            timeout=10,
        )
        response.raise_for_status()
        token = response.json().get('access_token')
    except (requests.RequestException, ValueError) as error:
        raise MpesaGatewayError('Unable to authenticate with the M-Pesa service.') from error
    if not token:
        raise MpesaGatewayError('M-Pesa did not return an access token.')
    return token


def validate_callback_signature(request):
    public_cert = getattr(settings, 'MPESA_PUBLIC_CERT', '').strip()
    signature = request.META.get('HTTP_X_MPESA_SIGNATURE')
    if not public_cert:
        return True
    if not signature or not request.body:
        return False

    try:
        public_key = serialization.load_pem_public_key(public_cert.encode())
        raw_body = request.body
        normalized_body = json.dumps(request.data, separators=(',', ':')).encode()
        candidate_payloads = [raw_body, normalized_body]

        for payload in candidate_payloads:
            try:
                public_key.verify(
                    base64.b64decode(signature),
                    payload,
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
                return True
            except (TypeError, ValueError, InvalidSignature):
                continue
    except (TypeError, ValueError):
        return False
    return False


def initiate_stk_push(attempt, callback_token):
    host = _configuration()
    token = _access_token(host)
    timestamp = timezone.localtime(timezone.now(), ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')
    password = base64.b64encode(
        f'{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'.encode(),
    ).decode()
    callback_url = (
        f'{settings.MPESA_CALLBACK_BASE_URL}/api/payments/mpesa/callback/{callback_token}/'
    )
    payload = {
        'BusinessShortCode': settings.MPESA_SHORTCODE,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(attempt.amount),
        'PartyA': attempt.phone_number,
        'PartyB': settings.MPESA_SHORTCODE,
        'PhoneNumber': attempt.phone_number,
        'CallBackURL': callback_url,
        'AccountReference': 'Africlay',
        'TransactionDesc': 'Wallet top up',
    }
    try:
        response = requests.post(
            f'{host}/mpesa/stkpush/v1/processrequest',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise MpesaGatewayError('M-Pesa request outcome is unknown.') from error


class MpesaGateway:
    def __init__(self):
        self.base_url = getattr(settings, 'MPESA_BASE_URL', '').rstrip('/') or _configuration()
        self.consumer_key = settings.MPESA_CONSUMER_KEY
        self.consumer_secret = settings.MPESA_CONSUMER_SECRET
        self.short_code = getattr(settings, 'MPESA_SHORT_CODE', '') or settings.MPESA_SHORTCODE
        self.passkey = settings.MPESA_PASSKEY
        callback_url = getattr(settings, 'MPESA_CALLBACK_URL', '')
        if not callback_url:
            callback_url = f'{settings.MPESA_CALLBACK_BASE_URL}/api/payments/mpesa/callback/'
        self.callback_url = callback_url
        self.timeout = getattr(settings, 'MPESA_REQUEST_TIMEOUT', 15)

    def initiate_payment(self, *, phone_number, amount, account_reference):
        credentials = base64.b64encode(f'{self.consumer_key}:{self.consumer_secret}'.encode()).decode()
        token_response = requests.get(
            f'{self.base_url}/oauth/v1/generate?grant_type=client_credentials',
            headers={'Authorization': f'Basic {credentials}'},
            timeout=self.timeout,
        )
        token_response.raise_for_status()
        access_token = token_response.json()['access_token']
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = base64.b64encode(f'{self.short_code}{self.passkey}{timestamp}'.encode()).decode()
        callback_parts = urlsplit(self.callback_url)
        callback_url = urlunsplit(
            (
                callback_parts.scheme,
                callback_parts.netloc,
                callback_parts.path,
                urlencode({'token': settings.MPESA_CALLBACK_SECRET}),
                '',
            )
        )
        if not callback_parts.scheme or not callback_parts.netloc:
            callback_url = urlunsplit(
                (
                    '',
                    '',
                    callback_parts.path,
                    urlencode({'token': settings.MPESA_CALLBACK_SECRET}),
                    '',
                )
            )
        response = requests.post(
            f'{self.base_url}/mpesa/stkpush/v1/processrequest',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                'BusinessShortCode': self.short_code,
                'Password': password,
                'Timestamp': timestamp,
                'TransactionType': 'CustomerPayBillOnline',
                'Amount': int(amount),
                'PartyA': phone_number,
                'PartyB': self.short_code,
                'PhoneNumber': phone_number,
                'CallBackURL': callback_url,
                'AccountReference': account_reference,
                'TransactionDesc': 'Africlay order payment',
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()