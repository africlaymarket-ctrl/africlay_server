import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY = Decimal('0.01')
WEIGHT = Decimal('0.001')
VOLUMETRIC_FACTOR = Decimal('166.7')
BASE_WEIGHT_KG = Decimal('5')
STANDARD_HEAVY_ITEM_FEE = Decimal('1000')
REMOTE_HEAVY_ITEM_FEE = Decimal('3000')


REGIONS = (
    'nairobi',
    'eastern',
    'western_a',
    'western_b',
    'mt_kenya',
    'coastal_a',
    'coastal_b',
)


STANDARD_RATES = {
    'nairobi': {
        'nairobi': (220, 30), 'eastern': (240, 40), 'western_a': (300, 40),
        'western_b': (350, 40), 'mt_kenya': (300, 40), 'coastal_a': (350, 45),
        'coastal_b': (400, 45),
    },
    'eastern': {
        'nairobi': (220, 30), 'eastern': (220, 30), 'western_a': (400, 50),
        'western_b': (450, 60), 'mt_kenya': (400, 50), 'coastal_a': (450, 50),
        'coastal_b': (500, 60),
    },
    'western_a': {
        'nairobi': (220, 30), 'eastern': (400, 50), 'western_a': (220, 30),
        'western_b': (260, 30), 'mt_kenya': (420, 50), 'coastal_a': (500, 50),
        'coastal_b': (550, 60),
    },
    'western_b': {
        'nairobi': (220, 30), 'eastern': (450, 60), 'western_a': (260, 30),
        'western_b': (220, 30), 'mt_kenya': (480, 60), 'coastal_a': (550, 60),
        'coastal_b': (600, 60),
    },
    'mt_kenya': {
        'nairobi': (220, 30), 'eastern': (400, 50), 'western_a': (420, 50),
        'western_b': (480, 60), 'mt_kenya': (220, 30), 'coastal_a': (450, 50),
        'coastal_b': (500, 60),
    },
    'coastal_a': {
        'nairobi': (220, 30), 'eastern': (450, 50), 'western_a': (500, 50),
        'western_b': (550, 60), 'mt_kenya': (500, 50), 'coastal_a': (220, 30),
        'coastal_b': (260, 30),
    },
    'coastal_b': {
        'nairobi': (220, 30), 'eastern': (500, 60), 'western_a': (550, 60),
        'western_b': (600, 60), 'mt_kenya': (500, 60), 'coastal_a': (260, 30),
        'coastal_b': (220, 30),
    },
}


REGION_TOWNS = {
    'nairobi': (
        'Athi River', 'Juja', 'Kiambu', 'Kikuyu', 'Kitengela', 'Nairobi', 'Ngong',
        'Ongata Rongai', 'Ruiru',
    ),
    'eastern': (
        'Emali', 'Isinya', 'Kajiado', 'Kangundo', 'Kibwezi', 'Kitui', 'Machakos',
        'Makindu', 'Wote', 'Matuu', 'Mwingi',
    ),
    'western_a': (
        'Ahero', 'Awasi', 'Awendo', 'Bahati', 'Bomet', 'Gilgil', 'Kericho', 'Keroka',
        'Kisii', 'Kisumu', 'Kabarak', 'Kijabe', 'Limuru', 'Lanet', 'Nyamira',
        'Oyugis', 'Rongo', 'Subukia', 'Salgaa', 'Sigalagala', 'Soy', 'Rongai Salgaa',
        'Naivasha', 'Nakuru', 'Narok', 'Nyahururu', 'Eldoret', 'Molo', 'Njoro', 'Olkalau',
    ),
    'western_b': (
        'Busia', 'Bungoma', 'Burnt Forest', 'Chewele', 'Eldama Ravine', 'Iten',
        'Kabarnet', 'Kapsabet', 'Kitale', 'Malaba', "Moi's Bridge", 'Mumias', 'Nambale',
        'Nzoia', 'Talek', 'Turbo', 'Webuye', 'Bondo', 'Homabay', 'Luanda', 'Maseno',
        'Mbale', 'Migori', 'Siaya', 'Ugunja', 'Yala', 'Malava', 'Kakamega',
    ),
    'mt_kenya': (
        'Chuka', 'Embu', 'Kenol', 'Karatina', 'Kerugoya', 'Meru', "Murang'a", 'Mwea',
        'Nanyuki', 'Nkubu', 'Nyeri', 'Sabasaba', 'Thika',
    ),
    'coastal_a': ('Mariakani', 'Mombasa', 'Sultan Hamud', 'Voi', 'Mtito Andei'),
    'coastal_b': ('Diani', 'Kilifi', 'Malindi', 'Mtwapa', 'Watamu'),
}


REMOTE_RATES = {
    'Mwala': (500, 45), 'Murarandia': (500, 45), 'Githunguri': (500, 45),
    'Gatundu': (500, 45), 'Runyenjes': (550, 45), 'Naromoru': (550, 45),
    'Maua': (560, 60), 'Isiolo': (560, 50), 'Kendu Bay': (560, 60),
    'Ogembo': (560, 60), 'Litein': (560, 50), 'Timau': (560, 50),
    'Ukwala': (600, 60), 'Kilgoris': (600, 50), 'Burnt Forest': (600, 50),
    'Sondu': (600, 60), 'Loitoktok': (600, 60), 'Nandi Hills': (650, 60),
    'Rachuonyo': (650, 60), 'Kimilili': (700, 60), 'Kiminini': (700, 60),
    'Taveta': (700, 60), 'Mwatate': (700, 60), 'Wundanyi': (700, 60),
    'Kapenguria': (700, 60), 'Namanga': (700, 50), 'Mbita': (700, 50),
    'Mutomo': (700, 60), 'Usenge': (700, 60), 'Rumuruti': (700, 60),
    'Kwale': (720, 60), 'Port Victoria': (800, 60), 'Isibania': (800, 60),
    'Lokichar': (1000, 100), 'Lamu': (1800, 60), 'Lodwar': (1800, 60),
}


REMOTE_ORIGIN_REGIONS = {
    'Mwala': 'eastern', 'Murarandia': 'mt_kenya', 'Githunguri': 'nairobi',
    'Gatundu': 'nairobi', 'Runyenjes': 'mt_kenya', 'Naromoru': 'mt_kenya',
    'Maua': 'mt_kenya', 'Isiolo': 'mt_kenya', 'Kendu Bay': 'western_a',
    'Ogembo': 'western_a', 'Litein': 'western_a', 'Timau': 'mt_kenya',
    'Ukwala': 'western_b', 'Kilgoris': 'western_a', 'Burnt Forest': 'western_b',
    'Sondu': 'western_a', 'Loitoktok': 'eastern', 'Nandi Hills': 'western_b',
    'Rachuonyo': 'western_a', 'Kimilili': 'western_b', 'Kiminini': 'western_b',
    'Taveta': 'coastal_a', 'Mwatate': 'coastal_a', 'Wundanyi': 'coastal_a',
    'Kapenguria': 'western_b', 'Namanga': 'eastern', 'Mbita': 'western_a',
    'Mutomo': 'eastern', 'Usenge': 'western_b', 'Rumuruti': 'mt_kenya',
    'Kwale': 'coastal_b', 'Port Victoria': 'western_b', 'Isibania': 'western_a',
    'Lokichar': 'western_b', 'Lamu': 'coastal_b', 'Lodwar': 'western_b',
}


class ShippingQuoteError(ValueError):
    pass


def normalize_town(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').casefold()).strip()


TOWN_TO_REGION = {
    normalize_town(town): region
    for region, towns in REGION_TOWNS.items()
    for town in towns
}
REMOTE_BY_TOWN = {normalize_town(town): (town, rate) for town, rate in REMOTE_RATES.items()}
REMOTE_ORIGIN_BY_TOWN = {
    normalize_town(town): region for town, region in REMOTE_ORIGIN_REGIONS.items()
}


def region_for_town(city):
    normalized = normalize_town(city)
    if not normalized:
        return None
    if normalized in TOWN_TO_REGION:
        return TOWN_TO_REGION[normalized]
    if normalized in REMOTE_ORIGIN_BY_TOWN:
        return REMOTE_ORIGIN_BY_TOWN[normalized]
    for town, region in TOWN_TO_REGION.items():
        if len(town) >= 5 and re.search(rf'(^| ){re.escape(town)}( |$)', normalized):
            return region
    return None


def remote_rate_for_town(city):
    normalized = normalize_town(city)
    if normalized in REMOTE_BY_TOWN:
        return REMOTE_BY_TOWN[normalized][1]
    return None


def product_volumetric_weight(product):
    dimensions = (product.length_cm, product.width_cm, product.height_cm)
    if any(value is None for value in dimensions):
        return Decimal('0.000')
    length_m, width_m, height_m = (Decimal(value) / Decimal('100') for value in dimensions)
    return (length_m * width_m * height_m * VOLUMETRIC_FACTOR).quantize(WEIGHT, rounding=ROUND_HALF_UP)


def rate_for_shipment(origin_region, destination_region, destination_city, chargeable_weight, heavy_pieces=0):
    remote_rate = remote_rate_for_town(destination_city)
    is_remote = remote_rate is not None
    if is_remote:
        base, extra = remote_rate
    else:
        base, extra = STANDARD_RATES[origin_region][destination_region]

    extra_kg = max(0, math.ceil(chargeable_weight - BASE_WEIGHT_KG))
    heavy_fee = (REMOTE_HEAVY_ITEM_FEE if is_remote else STANDARD_HEAVY_ITEM_FEE) * heavy_pieces
    return (Decimal(base) + Decimal(extra) * extra_kg + heavy_fee).quantize(MONEY), is_remote


@dataclass(frozen=True)
class SellerShippingQuote:
    seller: object
    item_subtotal: Decimal
    commission_amount: Decimal
    seller_proceeds: Decimal
    shipping_cost: Decimal
    actual_weight_kg: Decimal
    volumetric_weight_kg: Decimal
    chargeable_weight_kg: Decimal
    origin_city: str
    origin_region: str
    destination_city: str
    destination_region: str
    is_remote: bool


def quote_cart_items(cart_items, destination_city):
    destination_region = region_for_town(destination_city)
    if destination_region is None:
        raise ShippingQuoteError(
            f'Standard delivery is not configured for {destination_city}. Choose a listed town or contact support.',
        )

    grouped = {}
    for item in cart_items:
        grouped.setdefault(item.product.store.owner_id, []).append(item)

    quotes = []
    for seller_items in grouped.values():
        first_product = seller_items[0].product
        store = first_product.store
        origin_region = region_for_town(store.city)
        if origin_region is None:
            raise ShippingQuoteError(
                f'{store.name} must add a supported dispatch town before this order can be delivered.',
            )

        item_subtotal = Decimal('0.00')
        actual_weight = Decimal('0.000')
        volumetric_weight = Decimal('0.000')
        heavy_pieces = 0
        for item in seller_items:
            product = item.product
            quantity = item.quantity
            item_subtotal += product.price * quantity
            unit_actual = Decimal(product.weight_kg or Decimal('1.000'))
            unit_volumetric = product_volumetric_weight(product)
            actual_weight += unit_actual * quantity
            volumetric_weight += unit_volumetric * quantity
            if max(unit_actual, unit_volumetric) > Decimal('50'):
                heavy_pieces += quantity

        actual_weight = actual_weight.quantize(WEIGHT, rounding=ROUND_HALF_UP)
        volumetric_weight = volumetric_weight.quantize(WEIGHT, rounding=ROUND_HALF_UP)
        chargeable_weight = max(actual_weight, volumetric_weight).quantize(WEIGHT, rounding=ROUND_HALF_UP)
        shipping_cost, is_remote = rate_for_shipment(
            origin_region,
            destination_region,
            destination_city,
            chargeable_weight,
            heavy_pieces,
        )
        commission = (item_subtotal * Decimal('0.10')).quantize(MONEY, rounding=ROUND_HALF_UP)
        quotes.append(SellerShippingQuote(
            seller=store.owner,
            item_subtotal=item_subtotal.quantize(MONEY),
            commission_amount=commission,
            seller_proceeds=(item_subtotal - commission).quantize(MONEY),
            shipping_cost=shipping_cost,
            actual_weight_kg=actual_weight,
            volumetric_weight_kg=volumetric_weight,
            chargeable_weight_kg=chargeable_weight,
            origin_city=store.city,
            origin_region=origin_region,
            destination_city=destination_city,
            destination_region=destination_region,
            is_remote=is_remote,
        ))
    return quotes
