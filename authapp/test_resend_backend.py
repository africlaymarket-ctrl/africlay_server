import json
from unittest.mock import patch

import requests
from django.core.mail import send_mail
from django.test import SimpleTestCase, override_settings


@override_settings(
    EMAIL_BACKEND='anymail.backends.resend.EmailBackend',
    ANYMAIL={'RESEND_API_KEY': 'test-only-key'},
    DEFAULT_FROM_EMAIL='AfriClay Market <updates@africlaymarket.com>',
)
class ResendEmailBackendTests(SimpleTestCase):
    @patch('requests.Session.request')
    def test_existing_django_mail_contract_uses_resend_https_api(self, request):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"id":"test-message-id"}'
        request.return_value = response

        sent = send_mail(
            subject='Order update',
            message='Your order has shipped.',
            from_email=None,
            recipient_list=['buyer@example.com'],
            html_message='<p>Your order has shipped.</p>',
            fail_silently=False,
        )

        self.assertEqual(sent, 1)
        params = request.call_args.kwargs
        self.assertEqual(params['url'], 'https://api.resend.com/emails')
        self.assertEqual(params['headers']['Authorization'], 'Bearer test-only-key')
        payload = json.loads(params['data'])
        self.assertEqual(payload['to'], ['buyer@example.com'])
        self.assertEqual(payload['subject'], 'Order update')
        self.assertEqual(payload['text'], 'Your order has shipped.')
        self.assertIn('Your order has shipped.', payload['html'])
