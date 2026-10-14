import base64
import json
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
    def __init__(self, message, *, stage=None, status_code=None, provider_code=None, provider_reason=None):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_reason = provider_reason


def _gateway_error(message, stage, error):
    response = getattr(error, 'response', None)
    status_code = getattr(response, 'status_code', None)
    provider_code = None
    provider_reason = None
    if response is not None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            provider_code = payload.get('errorCode') or payload.get('error_code') or payload.get('ResponseCode')
            provider_reason = payload.get('errorMessage') or payload.get('error_description')
    return MpesaGatewayError(
        message,
        stage=stage,
        status_code=status_code,
        provider_code=str(provider_code)[:80] if provider_code else None,
        provider_reason=str(provider_reason).replace('\n', ' ')[:200] if provider_reason else None,
    )


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
        default_base_url = _configuration()
        self.base_url = getattr(settings, 'MPESA_BASE_URL', '').rstrip('/') or default_base_url
        self.consumer_key = settings.MPESA_CONSUMER_KEY
        self.consumer_secret = settings.MPESA_CONSUMER_SECRET
        self.short_code = getattr(settings, 'MPESA_SHORT_CODE', '') or settings.MPESA_SHORTCODE
        self.passkey = settings.MPESA_PASSKEY
        callback_base_url = getattr(settings, 'MPESA_CALLBACK_BASE_URL', '').rstrip('/')
        self.callback_base_url = callback_base_url
        self.timeout = getattr(settings, 'MPESA_REQUEST_TIMEOUT', 15)

    def initiate_payment(self, *, phone_number, amount, account_reference, callback_token):
        credentials = base64.b64encode(f'{self.consumer_key}:{self.consumer_secret}'.encode()).decode()
        try:
            token_response = requests.get(
                f'{self.base_url}/oauth/v1/generate?grant_type=client_credentials',
                headers={'Authorization': f'Basic {credentials}'},
                timeout=self.timeout,
            )
            token_response.raise_for_status()
            access_token = token_response.json()['access_token']
        except requests.Timeout:
            raise
        except requests.RequestException as error:
            raise _gateway_error('Unable to authenticate with M-Pesa.', 'authentication', error) from error
        except (KeyError, ValueError, TypeError) as error:
            raise MpesaGatewayError(
                'M-Pesa returned an invalid authentication response.',
                stage='authentication',
            ) from error

        timestamp = timezone.localtime(timezone.now(), ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')
        password = base64.b64encode(f'{self.short_code}{self.passkey}{timestamp}'.encode()).decode()
        callback_url = (
            f'{self.callback_base_url}/api/payments/provider-callback/{callback_token}/'
        )
        try:
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
        except requests.Timeout:
            raise
        except requests.RequestException as error:
            raise _gateway_error('M-Pesa rejected the payment request.', 'stk_push', error) from error
        except (ValueError, TypeError) as error:
            raise MpesaGatewayError(
                'M-Pesa returned an invalid payment response.',
                stage='stk_push',
            ) from error


class MpesaB2CGateway:
    def __init__(self):
        self.base_url = getattr(settings, 'MPESA_BASE_URL', '').rstrip('/') or _configuration()
        self.initiator_name = getattr(settings, 'MPESA_B2C_INITIATOR_NAME', '').strip()
        self.security_credential = getattr(settings, 'MPESA_B2C_SECURITY_CREDENTIAL', '').strip()
        self.shortcode = getattr(settings, 'MPESA_B2C_SHORTCODE', '').strip()
        self.command_id = getattr(settings, 'MPESA_B2C_COMMAND_ID', 'BusinessPayment').strip()
        self.callback_base_url = getattr(settings, 'MPESA_CALLBACK_BASE_URL', '').rstrip('/')
        self.timeout = getattr(settings, 'MPESA_REQUEST_TIMEOUT', 15)
        default_path = (
            '/mpesa/b2c/v1/paymentrequest'
            if settings.MPESA_ENVIRONMENT == 'production'
            else '/mpesa/b2c/v3/paymentrequest'
        )
        self.endpoint_path = getattr(settings, 'MPESA_B2C_PATH', default_path).strip()

    def _validate(self):
        if not all([
            settings.MPESA_CONSUMER_KEY,
            settings.MPESA_CONSUMER_SECRET,
            self.initiator_name,
            self.security_credential,
            self.shortcode,
            self.callback_base_url,
        ]):
            raise MpesaConfigurationError('Seller payouts are not configured.')
        if not self.callback_base_url.startswith('https://'):
            raise MpesaConfigurationError('The payout callback URL must use HTTPS.')
        if self.command_id not in {'BusinessPayment', 'SalaryPayment', 'PromotionPayment'}:
            raise MpesaConfigurationError('MPESA_B2C_COMMAND_ID is invalid.')
        if not self.endpoint_path.startswith('/'):
            raise MpesaConfigurationError('MPESA_B2C_PATH must begin with a slash.')

    def initiate_payout(self, *, payout, callback_token):
        self._validate()
        token = _access_token(self.base_url)
        result_url = (
            f'{self.callback_base_url}/api/payments/payouts/provider-result/{callback_token}/'
        )
        timeout_url = (
            f'{self.callback_base_url}/api/payments/payouts/provider-timeout/{callback_token}/'
        )
        payload = {
            'OriginatorConversationID': payout.originator_conversation_id,
            'InitiatorName': self.initiator_name,
            'SecurityCredential': self.security_credential,
            'CommandID': self.command_id,
            'Amount': int(payout.amount),
            'PartyA': self.shortcode,
            'PartyB': payout.destination_phone,
            'Remarks': f'AfriClay seller payout {str(payout.pk)[:8]}',
            'QueueTimeOutURL': timeout_url,
            'ResultURL': result_url,
            'Occassion': 'Seller payout',
        }
        try:
            response = requests.post(
                f'{self.base_url}{self.endpoint_path}',
                headers={'Authorization': f'Bearer {token}'},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            raise
        except requests.RequestException as error:
            raise _gateway_error('M-Pesa rejected the payout request.', 'b2c_payout', error) from error
        except (ValueError, TypeError) as error:
            raise MpesaGatewayError(
                'M-Pesa returned an invalid payout response.',
                stage='b2c_payout',
            ) from error
        if not isinstance(data, dict):
            raise MpesaGatewayError(
                'M-Pesa returned an invalid payout response.',
                stage='b2c_payout',
            )
        return data
