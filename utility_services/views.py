from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .serializers import SendSMSSerializer, SendSMSResponseSerializer
from .utils import SendSMS


@extend_schema(
    tags=['Utility Services'],
    summary='Send SMS notification',
    description='Dispatches an SMS message to one or more recipient phone numbers using the Cradle Voices SMS API.',
    request=SendSMSSerializer,
    responses={
        200: OpenApiResponse(
            response=SendSMSResponseSerializer,
            description='SMS sent successfully',
        ),
        400: OpenApiResponse(
            response=SendSMSResponseSerializer,
            description='Validation error or SMS sending failure',
        ),
    },
    auth=[],
)
class SendSMSView(APIView):
    """
    API View to test and dispatch SMS messages.
    Can be used directly or called by internal services.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SendSMSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = serializer.validated_data['message']
        phone_numbers = serializer.validated_data.get('phone_numbers')
        phone_number = serializer.validated_data.get('phone_number')

        recipients = phone_numbers if phone_numbers else phone_number

        success, result_message = SendSMS.send(
            message=message,
            phone_numbers=recipients,
        )

        response_status = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST
        return Response(
            {
                "success": success,
                "message": result_message,
            },
            status=response_status,
        )
