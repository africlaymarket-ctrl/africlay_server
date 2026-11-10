"""Server-side Geoapify routing for a separately priced pickup-to-home leg.

Do not call this for checkout until both locations are verified and the rider
handover flow is ready. The API key must never be sent to a client or logged.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests
from django.conf import settings
from django.core import signing


class DoorstepQuoteError(Exception):
    """A doorstep price cannot be determined safely."""


@dataclass(frozen=True)
class DoorstepLegQuote:
    distance_meters: Decimal
    fee: Decimal


QUOTE_SALT = 'shopping.doorstep.quote.v1'


def sign_doorstep_quote(*, quote, pickup_point, user_id, location_token):
    return signing.dumps({
        'user_id': str(user_id), 'pickup_id': str(pickup_point.pk),
        'pickup_latitude': str(pickup_point.latitude), 'pickup_longitude': str(pickup_point.longitude),
        'location_token': location_token,
        'distance_meters': str(quote.distance_meters), 'fee': str(quote.fee),
    }, salt=QUOTE_SALT)


def read_doorstep_quote(token, *, pickup_point, user_id, location_token):
    try:
        payload = signing.loads(token, salt=QUOTE_SALT, max_age=10 * 60)
        if (payload['user_id'] != str(user_id) or payload['pickup_id'] != str(pickup_point.pk)
                or payload['location_token'] != location_token
                or payload['pickup_latitude'] != str(pickup_point.latitude)
                or payload['pickup_longitude'] != str(pickup_point.longitude)):
            raise ValueError('Quote does not match this checkout')
        fee = Decimal(payload['fee'])
        distance = Decimal(payload['distance_meters'])
        if not fee.is_finite() or fee < Decimal('100') or not distance.is_finite() or distance <= 0:
            raise ValueError('Invalid doorstep quote')
        return DoorstepLegQuote(distance_meters=distance, fee=fee)
    except (signing.BadSignature, KeyError, TypeError, ValueError, InvalidOperation):
        raise DoorstepQuoteError('Your doorstep quote expired or changed. Refresh the quote before paying.') from None


def _coordinate(value, minimum, maximum):
    try:
        coordinate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DoorstepQuoteError('A verified location is required for doorstep delivery.') from error
    if not coordinate.is_finite() or not minimum <= coordinate <= maximum:
        raise DoorstepQuoteError('A verified location is required for doorstep delivery.')
    return coordinate


def quote_doorstep_leg(*, pickup_city, home_city, pickup_latitude,
                       pickup_longitude, home_latitude, home_longitude):
    """Quote the second leg only; the existing seller-to-pickup fee stays separate.

    Both positions must come from verified pickup data and a buyer-confirmed
    destination. Same-city eligibility is an explicit business rule.
    """
    if not pickup_city or not home_city or pickup_city.strip().casefold() != home_city.strip().casefold():
        raise DoorstepQuoteError('Doorstep delivery is available only in the pickup point city.')
    start_lat = _coordinate(pickup_latitude, -90, 90)
    start_lon = _coordinate(pickup_longitude, -180, 180)
    end_lat = _coordinate(home_latitude, -90, 90)
    end_lon = _coordinate(home_longitude, -180, 180)
    if start_lat == end_lat and start_lon == end_lon:
        raise DoorstepQuoteError('The destination must be different from the pickup point.')

    key = settings.GEOAPIFY_API_KEY
    if not key:
        raise DoorstepQuoteError('Doorstep route pricing is temporarily unavailable.')
    try:
        response = requests.get(
            'https://api.geoapify.com/v1/routing',
            params={
                'waypoints': f'{start_lat},{start_lon}|{end_lat},{end_lon}',
                'mode': 'drive', 'format': 'json', 'units': 'metric', 'apiKey': key,
            },
            timeout=6,
        )
        response.raise_for_status()
        route = response.json()['results'][0]
        if str(route.get('distance_units', '')).casefold() != 'meters':
            raise ValueError('Unexpected route units')
        distance = Decimal(str(route['distance']))
        if not distance.is_finite() or distance <= 0:
            raise ValueError('Invalid route distance')
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, InvalidOperation) as error:
        # Requests errors can contain the full URL, including the API key.
        # Never expose the provider exception in an API response or log.
        raise DoorstepQuoteError('A driving route could not be confirmed. Try another location later.') from None

    fee = (Decimal('100') + Decimal('35') * distance / Decimal('1000')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP,
    )
    return DoorstepLegQuote(distance_meters=distance, fee=fee)
