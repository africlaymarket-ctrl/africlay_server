import mimetypes
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class SupabaseStorage(Storage):
    """Private Supabase Storage backend using the server-side REST API."""

    def __init__(self, bucket_name=None, base_url=None, secret_key=None, timeout=None):
        self.bucket_name = bucket_name or settings.SUPABASE_STORAGE_BUCKET
        self.base_url = (base_url or settings.SUPABASE_URL).rstrip('/')
        self.secret_key = secret_key or settings.SUPABASE_SECRET_KEY
        self.timeout = timeout or settings.SUPABASE_STORAGE_TIMEOUT

        if not all((self.bucket_name, self.base_url, self.secret_key)):
            raise ImproperlyConfigured(
                'Supabase REST storage requires SUPABASE_STORAGE_BUCKET, '
                'SUPABASE_URL, and SUPABASE_SECRET_KEY.'
            )

    def _headers(self, **extra):
        return {
            'apikey': self.secret_key,
            'Authorization': f'Bearer {self.secret_key}',
            **extra,
        }

    def _encoded_name(self, name):
        return quote(name.lstrip('/'), safe='/')

    def _object_url(self, route, name=''):
        path = f'/{self._encoded_name(name)}' if name else ''
        return f'{self.base_url}/storage/v1/{route}/{self.bucket_name}{path}'

    @staticmethod
    def _is_not_found(response):
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return str(payload.get('statusCode')) == '404' or payload.get('code') == 'NoSuchKey'

    def _save(self, name, content):
        content_type = getattr(content, 'content_type', None)
        if not content_type:
            content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        payload = b''.join(content.chunks())
        response = requests.post(
            self._object_url('object', name),
            headers=self._headers(**{'Content-Type': content_type, 'x-upsert': 'false'}),
            data=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return name

    def _open(self, name, mode='rb'):
        if mode not in {'r', 'rb'}:
            raise ValueError('SupabaseStorage only supports read mode when opening files.')
        response = requests.get(
            self._object_url('object/authenticated', name),
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def delete(self, name):
        response = requests.delete(
            self._object_url('object'),
            headers=self._headers(**{'Content-Type': 'application/json'}),
            json={'prefixes': [name.lstrip('/')]},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def exists(self, name):
        response = requests.get(
            self._object_url('object/authenticated', name),
            headers=self._headers(),
            stream=True,
            timeout=self.timeout,
        )
        try:
            if self._is_not_found(response):
                return False
            response.raise_for_status()
            return True
        finally:
            response.close()

    def size(self, name):
        response = requests.get(
            self._object_url('object/authenticated', name),
            headers=self._headers(),
            stream=True,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
            return int(response.headers.get('Content-Length', 0))
        finally:
            response.close()

    def url(self, name):
        response = requests.post(
            self._object_url('object/sign', name),
            headers=self._headers(**{'Content-Type': 'application/json'}),
            json={'expiresIn': settings.SUPABASE_SIGNED_URL_TTL},
            timeout=self.timeout,
        )
        response.raise_for_status()
        signed_url = response.json().get('signedURL') or response.json().get('signedUrl')
        if not signed_url:
            raise ValueError('Supabase Storage did not return a signed URL.')
        if signed_url.startswith(('http://', 'https://')):
            return signed_url
        if not signed_url.startswith('/'):
            signed_url = f'/{signed_url}'
        if not signed_url.startswith('/storage/v1/'):
            signed_url = f'/storage/v1{signed_url}'
        return f'{self.base_url}{signed_url}'
