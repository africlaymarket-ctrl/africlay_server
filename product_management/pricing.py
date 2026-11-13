from decimal import Decimal


def available_option_price(options, fallback):
    active = [option for option in options if option.get('active', True)]
    available = [option for option in active if option['stock_quantity'] > 0]
    priced = available or active
    return min(Decimal(option['price']) for option in priced) if priced else fallback


def selected_option(product, option_id, *, include_inactive=False):
    options = product.price_options or []
    if not any(option.get('active', True) for option in options):
        if option_id:
            raise ValueError('This price option is no longer available.')
        return None
    if not option_id:
        raise ValueError('Choose a price option before purchasing.')
    for option in options:
        if option['id'] == str(option_id) and (include_inactive or option.get('active', True)):
            return option
    raise ValueError('This price option is no longer available.')


def item_price(item):
    option = selected_option(item.product, item.option_id)
    return Decimal(option['price']) if option else item.product.price


def item_weight(item):
    option = selected_option(item.product, item.option_id)
    return Decimal(option['weight_kg']) if option else item.product.weight_kg


def item_stock(item):
    option = selected_option(item.product, item.option_id)
    return int(option['stock_quantity']) if option else item.product.stock_quantity
