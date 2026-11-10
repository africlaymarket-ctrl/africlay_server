"""Server-side availability rule for the temporary doorstep test rollout."""

from django.conf import settings


def doorstep_available_for(user):
    if not settings.DOORSTEP_CHECKOUT_ENABLED or not settings.GEOAPIFY_API_KEY:
        return False
    allowed_emails = settings.DOORSTEP_TEST_EMAILS
    if not allowed_emails:
        return False
    if '*' in allowed_emails:
        return True
    return bool(user and user.is_authenticated and user.email.casefold() in allowed_emails)
