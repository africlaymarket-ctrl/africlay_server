from datetime import datetime, timezone as dt_timezone
import uuid
import secrets
import jwt
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from rest_framework.exceptions import AuthenticationFailed


User = get_user_model()


def generate_access_token(user):
    """Generate short-lived JWT access token for user."""
    payload = {
        'user_id': str(user.id),
        'email': user.email,
        'role': user.role,
        'token_type': 'access',
        'jti': str(uuid.uuid4()),
        'exp': timezone.now() + timezone.timedelta(days=1),
        'iat': timezone.now(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def generate_refresh_token(user):
    """Generate long-lived JWT refresh token for user."""
    payload = {
        'user_id': str(user.id),
        'email': user.email,
        'token_type': 'refresh',
        'jti': str(uuid.uuid4()),
        'exp': timezone.now() + timezone.timedelta(days=7),
        'iat': timezone.now(),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def decode_token(token):
    """Decode and validate JWT token signature and expiration."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed('Token has expired.')
    except jwt.InvalidTokenError:
        raise AuthenticationFailed('Invalid token.')


def is_token_blacklisted(jti):
    """Check if token jti is in the BlacklistedToken table."""
    from .models import BlacklistedToken
    return BlacklistedToken.objects.filter(token_id=jti).exists()


def blacklist_token(token):
    """Add a token to BlacklistedToken."""
    from .models import BlacklistedToken
    try:
        payload = decode_token(token)
        jti = payload.get('jti')
        user_id = payload.get('user_id')
        exp_timestamp = payload.get('exp')

        if not jti:
            return False

        expires_at = datetime.fromtimestamp(exp_timestamp, tz=dt_timezone.utc) if exp_timestamp else timezone.now() + timezone.timedelta(days=7)
        user = User.objects.filter(id=user_id).first()

        BlacklistedToken.objects.get_or_create(
            token_id=jti,
            defaults={'user': user, 'expires_at': expires_at}
        )
        return True
    except Exception:
        return False


def refresh_access_token(refresh_token):
    """Validate refresh token, blacklist the old one, and issue fresh tokens."""
    if not refresh_token:
        raise AuthenticationFailed('Refresh token is required.')

    payload = decode_token(refresh_token)

    if payload.get('token_type') != 'refresh':
        raise AuthenticationFailed('Invalid token type. Expected a refresh token.')

    jti = payload.get('jti')
    if jti and is_token_blacklisted(jti):
        raise AuthenticationFailed('Refresh token has been revoked.')

    user_id = payload.get('user_id')
    if not user_id:
        raise AuthenticationFailed('Invalid token payload.')

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        raise AuthenticationFailed('User not found.')

    if not user.is_active:
        raise AuthenticationFailed('User account is inactive.')

    # Blacklist used refresh token for rotation security
    if jti:
        blacklist_token(refresh_token)

    return {
        'access': generate_access_token(user),
        'refresh': generate_refresh_token(user),
    }


def generate_otp_code(length=6):
    """Generate secure numeric OTP code."""
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def send_otp_email(user, otp_code, otp_type):
    """Render email template and dispatch OTP code to user's email."""
    if otp_type == 'email_verification':
        subject = 'Verify Your Africlay Account'
        template_html = 'emails/email_verification.html'
        template_txt = 'emails/email_verification.txt'
    elif otp_type == 'password_reset':
        subject = 'Africlay Password Reset Code'
        template_html = 'emails/password_reset.html'
        template_txt = 'emails/password_reset.txt'
    else:
        subject = 'Africlay Verification Code'
        template_html = 'emails/email_verification.html'
        template_txt = 'emails/email_verification.txt'

    context = {
        'user': user,
        'otp_code': otp_code,
        'valid_minutes': 15,
        'app_name': 'Africlay',
    }

    try:
        html_message = render_to_string(template_html, context)
        plain_message = render_to_string(template_txt, context)
    except Exception:
        plain_message = f"Your Africlay verification code is: {otp_code}. It is valid for 15 minutes."
        html_message = None

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )