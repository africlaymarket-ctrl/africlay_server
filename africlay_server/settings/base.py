"""
Base Django settings for africlay_server project.

Contains settings common to all environments (local, dev, prod).
Environment-specific settings are defined in local.py, dev.py, and prod.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / '.env')

# Current running environment (local, dev, prod)
DEV_ENVIRONMENT = os.getenv('DEV_ENVIRONMENT', 'local').strip().lower()

# Security key
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-9g1%r+q*=*6y0_q1*+@s_arj_viup-%vk-x%%_@@ef7ibz+!wv')

# Custom user model
AUTH_USER_MODEL = 'authapp.User'

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'storages',
    'rest_framework',
    # Core apps
    'core',
    'authapp',
    'store_management',
    'product_management',
    'service_management',
    'shopping',
    'payments',
    'utility_services',
    'messaging',
    'notifications',
    'reviews',
    # API Documentation (Swagger / OpenAPI)
    'drf_spectacular',
    'drf_spectacular_sidecar',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'africlay_server.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'africlay_server.wsgi.application'

# CORS / frontend connectivity
_default_origins = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'CORS_ALLOWED_ORIGINS',
        ','.join(_default_origins),
    ).split(',')
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        ','.join(_default_origins),
    ).split(',')
    if origin.strip()
]

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'authapp.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# Swagger / OpenAPI Documentation Settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Africlay Server API',
    'DESCRIPTION': 'API documentation for Africlay Ecommerce Platform',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,   # don't put schema in the Swagger UI page
    'SWAGGER_UI_DIST': 'SIDECAR',    # use the sidecar package for Swagger UI
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',         # use the sidecar package for ReDoc
    'SECURITY': [{'BearerAuth': []}],
    'APPEND_COMPONENTS': {
        'securitySchemes': {
            'BearerAuth': {
                'type': 'http',
                'scheme': 'bearer',
                'bearerFormat': 'JWT',
                'description': 'Enter JWT Bearer token in format: Bearer <token>',
            }
        }
    },
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Supabase Storage S3 credentials are server-side only. A private bucket keeps
# KYC documents protected; Django returns temporary signed URLs for media.
SUPABASE_STORAGE_BUCKET = os.getenv('SUPABASE_STORAGE_BUCKET', '').strip()
SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
SUPABASE_SECRET_KEY = os.getenv('SUPABASE_SECRET_KEY', '').strip()
SUPABASE_STORAGE_TIMEOUT = float(os.getenv('SUPABASE_STORAGE_TIMEOUT', '20'))
SUPABASE_S3_ENDPOINT_URL = os.getenv('SUPABASE_S3_ENDPOINT_URL', '').strip().rstrip('/')
SUPABASE_S3_REGION = os.getenv('SUPABASE_S3_REGION', '').strip()
SUPABASE_S3_ACCESS_KEY_ID = os.getenv('SUPABASE_S3_ACCESS_KEY_ID', '').strip()
SUPABASE_S3_SECRET_ACCESS_KEY = os.getenv('SUPABASE_S3_SECRET_ACCESS_KEY', '').strip()

if SUPABASE_STORAGE_BUCKET:
    _supabase_s3_values = (
        SUPABASE_S3_ENDPOINT_URL,
        SUPABASE_S3_REGION,
        SUPABASE_S3_ACCESS_KEY_ID,
        SUPABASE_S3_SECRET_ACCESS_KEY,
    )
    if all(_supabase_s3_values):
        STORAGES = {
            'default': {'BACKEND': 'storages.backends.s3.S3Storage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        }
        AWS_STORAGE_BUCKET_NAME = SUPABASE_STORAGE_BUCKET
        AWS_S3_ENDPOINT_URL = SUPABASE_S3_ENDPOINT_URL
        AWS_S3_REGION_NAME = SUPABASE_S3_REGION
        AWS_ACCESS_KEY_ID = SUPABASE_S3_ACCESS_KEY_ID
        AWS_SECRET_ACCESS_KEY = SUPABASE_S3_SECRET_ACCESS_KEY
        AWS_S3_ADDRESSING_STYLE = 'path'
        AWS_S3_SIGNATURE_VERSION = 's3v4'
        AWS_DEFAULT_ACL = None
        AWS_QUERYSTRING_AUTH = True
        AWS_QUERYSTRING_EXPIRE = int(os.getenv('SUPABASE_SIGNED_URL_TTL', '3600'))
        AWS_S3_FILE_OVERWRITE = False
    elif SUPABASE_URL and SUPABASE_SECRET_KEY:
        STORAGES = {
            'default': {'BACKEND': 'core.storage.SupabaseStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        }
    elif any(_supabase_s3_values):
        raise ImproperlyConfigured(
            'Supabase S3 storage is partially configured. Supply all S3 values, '
            'or use SUPABASE_URL and SUPABASE_SECRET_KEY.'
        )
    else:
        raise ImproperlyConfigured(
            'Supabase Storage requires either REST API credentials or complete S3 credentials.'
        )

SUPABASE_SIGNED_URL_TTL = int(os.getenv('SUPABASE_SIGNED_URL_TTL', '3600'))

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# SMS Gateway Configuration (Cradle Voices / CodeYetuSMS)
SMS_URL = os.getenv('SMS_URL', 'https://api.cradlevoices.com/v1/sms/send')
SMS_TOKEN = os.getenv('SMS_TOKEN', '')

# M-Pesa Daraja credentials are supplied by the deployment environment.
MPESA_ENVIRONMENT = os.getenv('MPESA_ENVIRONMENT', 'sandbox').strip().lower()
MPESA_CONSUMER_KEY = os.getenv('MPESA_CONSUMER_KEY', '')
MPESA_CONSUMER_SECRET = os.getenv('MPESA_CONSUMER_SECRET', '')
MPESA_SHORTCODE = os.getenv('MPESA_SHORTCODE', '')
MPESA_PASSKEY = os.getenv('MPESA_PASSKEY', '')
MPESA_CALLBACK_BASE_URL = os.getenv('MPESA_CALLBACK_BASE_URL', '').rstrip('/')
MPESA_PUBLIC_CERT = os.getenv('MPESA_PUBLIC_CERT', '').strip()
MPESA_CALLBACK_SECRET = os.getenv('MPESA_CALLBACK_SECRET', '')
MPESA_REQUEST_TIMEOUT = float(os.getenv('MPESA_REQUEST_TIMEOUT', '15'))

# Backward-compatible aliases for payment gateway settings.
MPESA_BASE_URL = os.getenv(
    'MPESA_BASE_URL',
    'https://sandbox.safaricom.co.ke' if MPESA_ENVIRONMENT == 'sandbox' else 'https://api.safaricom.co.ke',
)
MPESA_SHORT_CODE = os.getenv('MPESA_SHORT_CODE', MPESA_SHORTCODE)
MPESA_CALLBACK_URL = os.getenv('MPESA_CALLBACK_URL', f'{MPESA_CALLBACK_BASE_URL}/api/payments/mpesa/callback/')
