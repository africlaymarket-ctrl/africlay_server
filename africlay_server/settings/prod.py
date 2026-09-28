"""
Production settings for africlay_server project.

Configured for production deployment with dedicated production database instance,
DEBUG disabled, and strict security settings enabled.
"""

import os
from django.core.exceptions import ImproperlyConfigured
from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

if not GS_BUCKET_NAME:
    GS_BUCKET_NAME = ''

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv('PROD_ALLOWED_HOSTS', os.getenv('ALLOWED_HOSTS', '')).split(',')
    if host.strip()
]

# Production Database Configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('PROD_DB_NAME', os.getenv('DB_NAME')),
        'USER': os.getenv('PROD_DB_USER', os.getenv('DB_USER')),
        'PASSWORD': os.getenv('PROD_DB_PASSWORD', os.getenv('DB_PASSWORD')),
        'HOST': os.getenv('PROD_DB_HOST', os.getenv('DB_HOST')),  # Production DB instance host / IP
        'PORT': os.getenv('PROD_DB_PORT', os.getenv('DB_PORT', '5432')),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', 600)),
        'OPTIONS': {
            'sslmode': os.getenv('PROD_DB_SSLMODE', 'require'),
            'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', 10)),
        },
    }
}

# Production Security Settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 't')
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'True').lower() in ('true', '1', 't')
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'True').lower() in ('true', '1', 't')
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', 31536000))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Production Email Configuration
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'no-reply@africlay.com')
