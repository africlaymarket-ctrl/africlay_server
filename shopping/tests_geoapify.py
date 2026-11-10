from decimal import Decimal
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.exceptions import ValidationError

from .geoapify import DoorstepQuoteError, quote_doorstep_leg
from .models import PickupPoint


ROUTE = dict(
    pickup_city='Nairobi', home_city='nairobi',
    pickup_latitude=Decimal('-1.280000'), pickup_longitude=Decimal('36.810000'),
    home_latitude=Decimal('-1.290000'), home_longitude=Decimal('36.820000'),
)


class GeoapifyDoorstepQuoteTests(SimpleTestCase):
    def test_pickup_point_requires_complete_map_pin(self):
        point = PickupPoint(name='Test', city='Nairobi', address='Test address', latitude=Decimal('-1.28'))
        with self.assertRaises(ValidationError):
            point.clean()
        point.longitude = Decimal('36.81')
        point.clean()

    @override_settings(GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.geoapify.requests.get')
    def test_exact_road_distance_price_and_server_side_key(self, get):
        get.return_value.json.return_value = {'results': [{'distance': 8400, 'distance_units': 'Meters'}]}
        quote = quote_doorstep_leg(**ROUTE)
        self.assertEqual(quote.distance_meters, Decimal('8400'))
        self.assertEqual(quote.fee, Decimal('394.00'))  # 100 + 35 * 8.4
        _, kwargs = get.call_args
        self.assertEqual(kwargs['params']['mode'], 'drive')
        self.assertEqual(kwargs['params']['waypoints'], '-1.280000,36.810000|-1.290000,36.820000')
        self.assertEqual(kwargs['params']['apiKey'], 'test-only-key')
        self.assertEqual(kwargs['timeout'], 6)

    @override_settings(GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.geoapify.requests.get')
    def test_fractional_kilometres_use_exact_distance(self, get):
        get.return_value.json.return_value = {'results': [{'distance': 1250, 'distance_units': 'Meters'}]}
        self.assertEqual(quote_doorstep_leg(**ROUTE).fee, Decimal('143.75'))

    @override_settings(GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.geoapify.requests.get')
    def test_other_city_or_missing_pin_fails_closed_without_provider_call(self, get):
        with self.assertRaisesRegex(DoorstepQuoteError, 'only in the pickup point city'):
            quote_doorstep_leg(**(ROUTE | {'home_city': 'Eldoret'}))
        with self.assertRaisesRegex(DoorstepQuoteError, 'verified location'):
            quote_doorstep_leg(**(ROUTE | {'pickup_latitude': None}))
        get.assert_not_called()

    @override_settings(GEOAPIFY_API_KEY='')
    @patch('shopping.geoapify.requests.get')
    def test_missing_key_fails_closed(self, get):
        with self.assertRaisesRegex(DoorstepQuoteError, 'temporarily unavailable'):
            quote_doorstep_leg(**ROUTE)
        get.assert_not_called()

    @override_settings(GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.geoapify.requests.get')
    def test_provider_failure_never_exposes_key(self, get):
        get.side_effect = requests.Timeout('https://api.geoapify.com/v1/routing?apiKey=test-only-key')
        with self.assertRaises(DoorstepQuoteError) as result:
            quote_doorstep_leg(**ROUTE)
        self.assertNotIn('test-only-key', str(result.exception))

    @override_settings(GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.geoapify.requests.get')
    def test_missing_or_wrong_units_cannot_create_quote(self, get):
        get.return_value.json.return_value = {'results': [{'distance': 8400, 'distance_units': 'Miles'}]}
        with self.assertRaises(DoorstepQuoteError):
            quote_doorstep_leg(**ROUTE)
        get.return_value.json.return_value = {'results': []}
        with self.assertRaises(DoorstepQuoteError):
            quote_doorstep_leg(**ROUTE)


class PickupPointPinMigrationTests(TestCase):
    def test_business_supplied_pins_are_available_after_migration(self):
        self.assertEqual(
            list(PickupPoint.objects.filter(name='AfriClay Eldoret').values_list('latitude', 'longitude')),
            [(Decimal('0.519008'), Decimal('35.269290'))],
        )
        self.assertEqual(
            list(PickupPoint.objects.filter(name='AfriClay Nairobi').values_list('latitude', 'longitude')),
            [(Decimal('-1.284070'), Decimal('36.826002'))],
        )
