from django.core.files.storage import default_storage
from rest_framework import serializers


def marketplace_media_url(value, request=None):
    if not value or not getattr(value, 'name', ''):
        return None
    name = value.name
    storage = value.storage if name.startswith('public/') else default_storage
    try:
        url = storage.url(name)
    except (OSError, ValueError):
        return None
    if request is not None and url and not url.startswith(('http://', 'https://')):
        return request.build_absolute_uri(url)
    return url


class MarketplaceImageField(serializers.ImageField):
    """Writable image field that can still represent legacy private media safely."""

    def to_representation(self, value):
        request = self.context.get('request') if hasattr(self, 'context') else None
        return marketplace_media_url(value, request)
