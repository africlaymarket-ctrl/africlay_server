import hashlib
import secrets
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from requests import RequestException, Timeout
from collections.abc import Mapping
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from authapp.permissions import IsBuyer
from shopping.models import Order, OrderStatus

from .gateways.mpesa import MpesaConfigurationError, MpesaGateway, MpesaGatewayError, initiate_stk_push, validate_callback_signature
from .models import Payment, PaymentAttempt, PaymentAttemptStatus, PaymentStatus, Wallet, WalletTransaction
from .serializers import (
    MpesaStkPushSerializer,
    PaymentInitiateSerializer,
    PaymentAttemptSerializer,
    PaymentSerializer,
    WalletCreateSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .services import PaymentService, WalletOperationError, credit_wallet


class WalletTransactionPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 100


class WalletListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        Wallet.objects.get_or_create(user=self.request.user, currency='KES')
        return Wallet.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return WalletCreateSerializer
        return WalletSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class WalletTransactionListView(generics.ListAPIView):
    serializer_class = WalletTransactionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WalletTransactionPagination

    def get_queryset(self):
        get_object_or_404(Wallet, pk=self.kwargs['wallet_id'], user=self.request.user)
        return WalletTransaction.objects.filter(
            wallet_id=self.kwargs['wallet_id'],
            wallet__user=self.request.user,
        )


class MpesaStkThrottle(UserRateThrottle):
    rate = '5/hour'


class MpesaStkPushView(generics.GenericAPIView):
    serializer_class = MpesaStkPushSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [MpesaStkThrottle]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get('Idempotency-Key', '').strip()
        if not idempotency_key or len(idempotency_key) > 100:
            raise ValidationError({'Idempotency-Key': 'A key of 1 to 100 characters is required.'})

        callback_token = secrets.token_urlsafe(32)
        callback_token_hash = hashlib.sha256(callback_token.encode()).hexdigest()
        with transaction.atomic():
            wallet, _ = Wallet.objects.get_or_create(user=request.user, currency='KES')
            attempt, created = PaymentAttempt.objects.get_or_create(
                user=request.user,
                idempotency_key=idempotency_key,
                defaults={
                    'wallet': wallet,
                    'amount': serializer.validated_data['amount'],
                    'phone_number': serializer.validated_data['phone_number'],
                    'callback_token_hash': callback_token_hash,
                },
            )

        if not created:
            if (
                attempt.amount != serializer.validated_data['amount']
                or attempt.phone_number != serializer.validated_data['phone_number']
            ):
                return Response(
                    {'detail': 'Idempotency key was already used with different payment details.'},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(PaymentAttemptSerializer(attempt).data, status=status.HTTP_200_OK)

        try:
            provider_response = initiate_stk_push(attempt, callback_token)
        except MpesaConfigurationError:
            attempt.status = PaymentAttemptStatus.FAILED
            attempt.result_description = 'M-Pesa is not configured.'
            attempt.save(update_fields=['status', 'result_description', 'updated_at'])
            return Response({'detail': attempt.result_description}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except MpesaGatewayError:
            attempt.status = PaymentAttemptStatus.UNKNOWN
            attempt.result_description = 'Payment status is unknown; await the provider callback or reconciliation.'
            attempt.save(update_fields=['status', 'result_description', 'updated_at'])
            return Response(PaymentAttemptSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)

        if str(provider_response.get('ResponseCode')) != '0':
            attempt.status = PaymentAttemptStatus.FAILED
            attempt.result_description = str(
                provider_response.get('ResponseDescription', 'M-Pesa rejected the request.'),
            )[:255]
            attempt.save(update_fields=['status', 'result_description', 'updated_at'])
            return Response(PaymentAttemptSerializer(attempt).data, status=status.HTTP_502_BAD_GATEWAY)

        checkout_request_id = provider_response.get('CheckoutRequestID')
        merchant_request_id = provider_response.get('MerchantRequestID')
        if not checkout_request_id or not merchant_request_id:
            attempt.status = PaymentAttemptStatus.UNKNOWN
            attempt.result_description = 'Payment status is unknown; await the provider callback or reconciliation.'
            attempt.save(update_fields=['status', 'result_description', 'updated_at'])
            return Response(PaymentAttemptSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)

        attempt.checkout_request_id = str(checkout_request_id)[:100]
        attempt.merchant_request_id = str(merchant_request_id)[:100]
        attempt.status = PaymentAttemptStatus.PENDING
        attempt.result_description = str(provider_response.get('CustomerMessage', 'STK prompt sent.'))[:255]
        attempt.save(update_fields=[
            'checkout_request_id', 'merchant_request_id', 'status',
            'result_description', 'updated_at',
        ])
        return Response(PaymentAttemptSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)


class MpesaCallbackView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        if getattr(settings, 'MPESA_PUBLIC_CERT', '').strip() and not validate_callback_signature(request):
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback signature.'}, status=403)

        callback = request.data.get('Body', {}).get('stkCallback')
        if not isinstance(callback, dict):
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=400)

        token_hash = hashlib.sha256(kwargs['token'].encode()).hexdigest()
        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().filter(
                callback_token_hash=token_hash,
            ).first()
            if attempt is None:
                return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=404)

            checkout_id = str(callback.get('CheckoutRequestID', ''))
            merchant_id = str(callback.get('MerchantRequestID', ''))
            if (
                (attempt.checkout_request_id and attempt.checkout_request_id != checkout_id)
                or (attempt.merchant_request_id and attempt.merchant_request_id != merchant_id)
            ):
                return Response({'ResultCode': 1, 'ResultDesc': 'Callback does not match payment.'}, status=400)

            if attempt.status in {PaymentAttemptStatus.SUCCEEDED, PaymentAttemptStatus.FAILED}:
                return Response({'ResultCode': 0, 'ResultDesc': 'Callback already processed.'})

            if str(callback.get('ResultCode')) != '0':
                attempt.status = PaymentAttemptStatus.FAILED
                attempt.result_description = str(callback.get('ResultDesc', 'M-Pesa payment failed.'))[:255]
                attempt.save(update_fields=['status', 'result_description', 'updated_at'])
                return Response({'ResultCode': 0, 'ResultDesc': 'Callback accepted.'})

            metadata_items = callback.get('CallbackMetadata', {}).get('Item', [])
            metadata = {
                item.get('Name'): item.get('Value')
                for item in metadata_items
                if isinstance(item, dict) and item.get('Name')
            }
            try:
                paid_amount = Decimal(str(metadata['Amount']))
                paid_phone = str(metadata['PhoneNumber'])
                receipt = str(metadata['MpesaReceiptNumber'])
            except (KeyError, InvalidOperation, TypeError, ValueError):
                return Response({'ResultCode': 1, 'ResultDesc': 'Incomplete payment metadata.'}, status=400)

            if paid_amount != attempt.amount or paid_phone != attempt.phone_number or not receipt:
                return Response({'ResultCode': 1, 'ResultDesc': 'Payment details do not match.'}, status=400)
            if PaymentAttempt.objects.filter(provider_receipt=receipt).exclude(pk=attempt.pk).exists():
                return Response({'ResultCode': 1, 'ResultDesc': 'Receipt was already processed.'}, status=409)

            attempt.checkout_request_id = checkout_id[:100]
            attempt.merchant_request_id = merchant_id[:100]
            try:
                credit_wallet(attempt.wallet, attempt.amount, f'mpesa:{checkout_id}')
            except WalletOperationError:
                return Response({'ResultCode': 1, 'ResultDesc': 'Payment could not be credited.'}, status=409)
            attempt.provider_receipt = receipt[:32]
            attempt.status = PaymentAttemptStatus.SUCCEEDED
            attempt.result_description = 'Payment received.'
            attempt.save(update_fields=[
                'checkout_request_id', 'merchant_request_id', 'provider_receipt',
                'status', 'result_description', 'updated_at',
            ])

        return Response({'ResultCode': 0, 'ResultDesc': 'Callback accepted.'})


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


class PaymentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyer]

    def get_queryset(self):
        return Payment.objects.filter(order__buyer=self.request.user).select_related('order')


class MpesaQueryTokenCallbackView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        callback_secret = getattr(settings, 'MPESA_CALLBACK_SECRET', '')
        if not callback_secret or request.query_params.get('token') != callback_secret:
            return Response({'detail': 'Invalid callback token.'}, status=status.HTTP_403_FORBIDDEN)
        payment = PaymentService.process_mpesa_callback(request.data)
        return Response({'ResultCode': 0, 'PaymentId': str(payment.id)}, status=status.HTTP_200_OK)