"""
Base Django settings for africlay_server project.

Contains settings common to all environments (local, dev, prod).
Environment-specific settings are defined in local.py, dev.py, and prod.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

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
    'rest_framework',
    # Core apps
    'authapp',
    'store_management',
    'product_management',
    'shopping',
    'utility_services',
    # API Documentation (Swagger / OpenAPI)
    'drf_spectacular',
    'drf_spectacular_sidecar',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# SMS Gateway Configuration (Cradle Voices / CodeYetuSMS)
SMS_URL = os.getenv('SMS_URL', 'https://api.cradlevoices.com/v1/sms/send')
SMS_TOKEN = os.getenv('SMS_TOKEN', '')
