from rest_framework.exceptions import ValidationError


def require_approved_kyc_for_publication(store, requested_status, published_status):
    """Prevent a store from exposing catalogue content before KYC approval."""
    if requested_status == published_status and not store.is_kyc_approved:
        raise ValidationError({'status': 'Store KYC must be approved before publishing.'})
