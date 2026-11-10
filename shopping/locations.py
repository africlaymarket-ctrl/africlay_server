"""Buyer-selected, server-signed Geoapify destinations for doorstep quotes."""

from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core import signing


LOCATION_SALT = 'shopping.doorstep.location.v1'


class LocationError(Exception):
    pass


def find_home_locations(*, text, pickup_point, user_id):
    if not settings.GEOAPIFY_API_KEY:
        raise LocationError('Doorstep location search is temporarily unavailable.')
    if len(text.strip()) < 5:
        raise LocationError('Enter at least five characters of a street address or landmark.')
    try:
        response = requests.get('https://api.geoapify.com/v1/geocode/autocomplete', params={
            'text': text.strip(), 'format': 'json', 'filter': 'countrycode:ke',
            'limit': 8, 'apiKey': settings.GEOAPIFY_API_KEY,
        }, timeout=6)
        response.raise_for_status()
        results = response.json()['results']
        if not isinstance(results, list):
            raise ValueError('Invalid location response')
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # Provider errors may contain the key in the URL.
        raise LocationError('Address search is temporarily unavailable. Try again later.') from None

    choices = []
    for result in results:
        if not isinstance(result, dict) or str(result.get('country_code') or '').lower() != 'ke':
            continue
        if result.get('result_type') not in {'amenity', 'building'}:
            continue
        city = result.get('city') or ''
        if city.strip().casefold() != pickup_point.city.strip().casefold():
            continue
        try:
            latitude = Decimal(str(result['lat'])).quantize(Decimal('0.000001'))
            longitude = Decimal(str(result['lon'])).quantize(Decimal('0.000001'))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        address = str(result.get('formatted') or '')[:500].strip()
        if len(address) < 5:
            continue
        payload = {
            'user_id': str(user_id), 'pickup_id': str(pickup_point.pk),
            'address': address, 'city': pickup_point.city,
            'latitude': str(latitude), 'longitude': str(longitude),
        }
        choices.append({'label': address, 'token': signing.dumps(payload, salt=LOCATION_SALT)})
    return choices


def read_home_location(token, *, pickup_point, user_id):
    try:
        location = signing.loads(token, salt=LOCATION_SALT, max_age=30 * 60)
        if location['user_id'] != str(user_id) or location['pickup_id'] != str(pickup_point.pk):
            raise ValueError('Location belongs to another checkout')
        if location['city'].strip().casefold() != pickup_point.city.strip().casefold():
            raise ValueError('Location is outside the pickup point city')
        latitude = Decimal(location['latitude'])
        longitude = Decimal(location['longitude'])
        if not latitude.is_finite() or not longitude.is_finite() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError('Invalid location coordinates')
        if len(location['address'].strip()) < 5 or len(location['address']) > 500:
            raise ValueError('Invalid address')
        return location
    except (signing.BadSignature, ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
        raise LocationError('This location expired or changed. Search for your address again.') from None
