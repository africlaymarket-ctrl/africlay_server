"""
Development environment settings for africlay_server project.

Configured for development deployed / remote development connecting
to a Google Cloud SQL PostgreSQL instance using its Public IP address.
"""

import os
from .base import *

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 't')

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv('DEV_ALLOWED_HOSTS', os.getenv('ALLOWED_HOSTS', '*')).split(',')
    if host.strip()
]

# Development Database (Cloud SQL with Public IP)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DEV_DB_NAME'),
        'USER': os.getenv('DEV_DB_USER'),
        'PASSWORD': os.getenv('DEV_DB_PASSWORD'),
        'HOST': os.getenv('DEV_DB_HOST'),
        'PORT': os.getenv('DEV_DB_PORT'),
        'OPTIONS': {
            'sslmode': os.getenv('DEV_DB_SSLMODE', 'prefer'),
            # 'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', 10)),
        },
    }
}

# Email Configuration
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND')
EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS')
EMAIL_PORT = int(os.getenv('EMAIL_PORT'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'no-reply@dev.africlay.com')
