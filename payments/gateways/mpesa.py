import base64
from datetime import datetime

import requests
from django.conf import settings


class MpesaGateway:
    def __init__(self):
        self.base_url = settings.MPESA_BASE_URL.rstrip('/')
        self.consumer_key = settings.MPESA_CONSUMER_KEY
        self.consumer_secret = settings.MPESA_CONSUMER_SECRET
        self.short_code = settings.MPESA_SHORT_CODE
        self.passkey = settings.MPESA_PASSKEY
        self.callback_url = settings.MPESA_CALLBACK_URL
        self.timeout = settings.MPESA_REQUEST_TIMEOUT

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
                'CallBackURL': self.callback_url,
                'AccountReference': account_reference,
                'TransactionDesc': 'Africlay order payment',
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
