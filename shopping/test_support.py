"""Test client that follows the public quote-then-checkout API workflow."""

from django.urls import reverse
from rest_framework.test import APIClient


class QuotedCheckoutClient(APIClient):
    def post(self, path, data=None, format=None, **extra):
        if path == reverse('shopping:checkout') and isinstance(data, dict):
            payload = dict(data)
            if 'expected_total_amount' not in payload:
                quote = super().post(reverse('shopping:shipping-quote'), payload, format=format, **extra)
                for expected, actual in (
                    ('expected_item_subtotal', 'item_subtotal'),
                    ('expected_shipping_cost', 'shipping_cost'),
                    ('expected_total_amount', 'total_amount'),
                ):
                    payload[expected] = str(quote.data[actual]) if quote.status_code == 200 else '0.00'
                if payload.get('delivery_method') in {'pickup_point', 'doorstep'} and quote.status_code == 200:
                    payload['expected_pickup_point_address'] = quote.data['pickup_point_address']
                if payload.get('delivery_method') == 'doorstep' and quote.status_code == 200:
                    payload['expected_doorstep_quote_token'] = quote.data['expected_doorstep_quote_token']
            data = payload
        return super().post(path, data, format=format, **extra)
