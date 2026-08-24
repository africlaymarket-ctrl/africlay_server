from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth import get_user_model
from .utils import decode_token, is_token_blacklisted

User = get_user_model()


class JWTAuthentication(BaseAuthentication):
    """Custom JWT Authentication supporting Authorization: Bearer <token>."""

    def authenticate(self, request):
        auth_header = get_authorization_header(request).split()

        if not auth_header:
            return None

        if len(auth_header) == 1:
            raise AuthenticationFailed('Invalid token header. No credentials provided.')
        elif len(auth_header) > 2:
            raise AuthenticationFailed('Invalid token header. Token string should not contain spaces.')

        prefix = auth_header[0].decode('utf-8')
        if prefix.lower() != 'bearer':
            return None

        token = auth_header[1].decode('utf-8')
        return self.authenticate_credentials(token)

    def authenticate_credentials(self, token):
        payload = decode_token(token)

        if payload.get('token_type') != 'access':
            raise AuthenticationFailed('Invalid token type. Expected access token.')

        jti = payload.get('jti')
        if jti and is_token_blacklisted(jti):
            raise AuthenticationFailed('Token has been revoked.')

        user_id = payload.get('user_id')
        if not user_id:
            raise AuthenticationFailed('Invalid token payload: missing user ID.')

        try:
            user = User.objects.select_related('profile').get(id=user_id)
        except User.DoesNotExist:
            raise AuthenticationFailed('User not found.')

        if not user.is_active:
            raise AuthenticationFailed('User account is inactive.')

        return (user, payload)

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
