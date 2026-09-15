from requests import RequestException, Timeout
from collections.abc import Mapping

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from authapp.permissions import IsBuyer
from shopping.models import Order, OrderStatus

from .gateways.mpesa import MpesaGateway
from .models import Payment, PaymentStatus
from .serializers import PaymentInitiateSerializer, PaymentSerializer
from .services import PaymentService


class PaymentInitiateView(generics.GenericAPIView):
    serializer_class = PaymentInitiateSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyer]

    def post(self, request, *args, **kwargs):
        input_serializer = self.get_serializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        order = get_object_or_404(Order, id=input_serializer.validated_data['order_id'], buyer=request.user)
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order.id)
            if order.status != OrderStatus.PENDING:
                raise ValidationError({'order': 'Only pending orders can be paid.'})
            existing_payment = Payment.objects.filter(
                order=order,
                status__in=[PaymentStatus.INITIATED, PaymentStatus.PENDING],
            ).first()
            if existing_payment:
                return Response(PaymentSerializer(existing_payment).data, status=status.HTTP_200_OK)

            if order.total_amount != order.total_amount.to_integral_value():
                raise ValidationError({'payment': 'M-Pesa payments require a whole-number KES amount.'})

            payment = Payment.objects.create(
                order=order,
                provider='mpesa',
                status=PaymentStatus.INITIATED,
                amount=order.total_amount,
                currency=order.currency,
                phone_number=input_serializer.validated_data['phone_number'],
            )

        try:
            response_data = MpesaGateway().initiate_payment(
                phone_number=payment.phone_number,
                amount=payment.amount,
                account_reference=str(order.id),
            )
        except Timeout:
            payment.status = PaymentStatus.PENDING
            payment.failure_message = 'Payment provider response is pending reconciliation.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment status is pending provider confirmation.'})
        except RequestException:
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'Payment provider is temporarily unavailable.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment provider is temporarily unavailable.'})
        except (KeyError, ValueError):
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'Payment provider returned an invalid response.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment provider returned an invalid response.'})

        if not isinstance(response_data, Mapping):
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'Payment provider returned an invalid response.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment provider returned an invalid response.'})

        merchant_request_id = response_data.get('MerchantRequestID')
        checkout_request_id = response_data.get('CheckoutRequestID')
        if not merchant_request_id or not checkout_request_id:
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'Payment provider returned incomplete identifiers.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment provider returned incomplete identifiers.'})

        payment.status = PaymentStatus.PENDING
        payment.merchant_request_id = merchant_request_id
        payment.checkout_request_id = checkout_request_id
        payment.save(update_fields=['status', 'merchant_request_id', 'checkout_request_id', 'updated_at'])
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class MpesaCallbackView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        callback_secret = getattr(settings, 'MPESA_CALLBACK_SECRET', '')
        if not callback_secret or request.query_params.get('token') != callback_secret:
            return Response({'detail': 'Invalid callback token.'}, status=status.HTTP_403_FORBIDDEN)
        payment = PaymentService.process_mpesa_callback(request.data)
        return Response({'ResultCode': 0, 'PaymentId': str(payment.id)}, status=status.HTTP_200_OK)


class PaymentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyer]

    def get_queryset(self):
        return Payment.objects.filter(order__buyer=self.request.user).select_related('order')
