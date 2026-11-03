import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
import uuid
from drf_spectacular.utils import extend_schema, OpenApiResponse

from authapp.permissions import IsSuperAdmin
from authapp.models import AdminAuditLog
from .serializers import SendSMSSerializer, SendSMSResponseSerializer
from .utils import SendSMS

logger = logging.getLogger(__name__)


@extend_schema(
    tags=['Utility Services'],
    summary='Send SMS notification',
    description='Dispatches an SMS through BlessedTexts. Success means accepted by the provider, not delivered.',
    request=SendSMSSerializer,
    responses={
        200: OpenApiResponse(
            response=SendSMSResponseSerializer,
            description='All SMS requests accepted by the provider; handset delivery is unconfirmed',
        ),
        202: OpenApiResponse(
            response=SendSMSResponseSerializer,
            description='Provider outcome is unknown; reconcile before sending again',
        ),
        400: OpenApiResponse(
            response=SendSMSResponseSerializer,
            description='Validation error or SMS sending failure',
        ),
    },
)
class SendSMSView(APIView):
    """
    API View to test and dispatch SMS messages.
    Can be used directly or called by internal services.
    """
    permission_classes = [IsSuperAdmin]
    throttle_scope = 'sms_dispatch'

    def post(self, request, *args, **kwargs):
        serializer = SendSMSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = serializer.validated_data['message']
        phone_numbers = serializer.validated_data.get('phone_numbers')
        phone_number = serializer.validated_data.get('phone_number')

        recipients = phone_numbers if phone_numbers else phone_number

        batch_id = str(uuid.uuid4())
        AdminAuditLog.objects.create(
            actor=request.user, action='sms_submission_requested',
            target_type='sms_batch', target_id=batch_id,
            metadata={'recipient_count': len(recipients) if isinstance(recipients, list) else 1},
        )

        success, result_message = SendSMS.send(
            message=message,
            phone_numbers=recipients,
        )

        outcome_unknown = 'outcome unknown' in result_message.lower()
        try:
            AdminAuditLog.objects.create(
                actor=request.user, action='sms_submission_result',
                target_type='sms_batch', target_id=batch_id,
                metadata={'accepted_all': success, 'outcome_unknown': outcome_unknown},
            )
        except Exception:
            # The request audit above is durable. A result-audit failure must not
            # turn a submitted SMS into HTTP 500 and encourage a duplicate send.
            logger.exception('Unable to record SMS submission outcome for batch %s.', batch_id)

        response_status = (status.HTTP_200_OK if success else
                           status.HTTP_202_ACCEPTED if outcome_unknown else status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "success": success,
                "message": result_message,
            },
            status=response_status,
        )
