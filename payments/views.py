import hashlib
import csv
import logging
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from requests import RequestException, Timeout
from collections.abc import Mapping
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from authapp.models import AdminAuditLog
from authapp.permissions import IsBuyer, IsFinance, IsSuperAdmin, IsVerifiedUser
from shopping.models import Order, OrderStatus, PaymentOption
from store_management.models import StoreKYCStatus

from .gateways.mpesa import (
    MpesaB2CGateway, MpesaConfigurationError, MpesaGateway, MpesaGatewayError,
    initiate_stk_push, validate_callback_signature,
)
from .models import (
    Payment, PaymentAttempt, PaymentAttemptStatus, PaymentPurpose, PaymentStatus,
    LedgerEntry, LedgerEntryType, Payout, PayoutAccount, PayoutStatus, Wallet,
    WalletBalanceSource,
    WalletTransaction,
)
from .serializers import (
    AdminPaymentSerializer,
    AdminPayoutSerializer,
    FinanceTransactionSerializer,
    MpesaStkPushSerializer,
    PaymentInitiateSerializer,
    PaymentAttemptSerializer,
    PaymentSerializer,
    PayoutAccountSerializer,
    PayoutAccountUpdateSerializer,
    PayoutRequestSerializer,
    PayoutResolutionSerializer,
    PayoutSerializer,
    WalletCreateSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .services import (
    PaymentService, WalletOperationError, credit_wallet, place_escrow_hold,
    resolve_payout,
)


logger = logging.getLogger(__name__)


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


def require_payout_eligible_seller(user):
    store = getattr(user, 'store', None)
    kyc = getattr(store, 'kyc', None) if store else None
    if not store or not kyc or kyc.status != StoreKYCStatus.APPROVED:
        raise ValidationError({
            'payout': 'Your store verification must be approved before requesting payouts.',
        })
    return store


class PayoutThrottle(UserRateThrottle):
    rate = '5/day'


def update_pending_payout(payout, *, payout_status, failure_code='', failure_message='', provider_response=None):
    with transaction.atomic():
        locked = Payout.objects.select_for_update().get(pk=payout.pk)
        if locked.status in {PayoutStatus.SUCCEEDED, PayoutStatus.FAILED}:
            return locked
        locked.status = payout_status
        locked.failure_code = str(failure_code)[:40]
        locked.failure_message = str(failure_message)[:255]
        update_fields = ['status', 'failure_code', 'failure_message', 'updated_at']
        if provider_response is not None:
            locked.provider_response = provider_response
            update_fields.append('provider_response')
        locked.save(update_fields=update_fields)
        return locked


class PayoutAccountView(generics.GenericAPIView):
    serializer_class = PayoutAccountSerializer
    permission_classes = [IsAuthenticated, IsVerifiedUser]

    @extend_schema(responses=PayoutAccountSerializer)
    def get(self, request, *args, **kwargs):
        account = PayoutAccount.objects.filter(user=request.user).first()
        return Response(PayoutAccountSerializer(account).data if account else None)

    @extend_schema(request=PayoutAccountUpdateSerializer, responses=PayoutAccountSerializer)
    def put(self, request, *args, **kwargs):
        serializer = PayoutAccountUpdateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data['phone_number']
        existing = PayoutAccount.objects.filter(user=request.user).first()
        if existing and existing.phone_number != phone_number and Payout.objects.filter(
            payout_account=existing,
            status__in=[PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN],
        ).exists():
            raise ValidationError({
                'phone_number': 'Wait for the active payout to be resolved before changing this number.',
            })
        account, _ = PayoutAccount.objects.update_or_create(
            user=request.user,
            defaults={
                'phone_number': phone_number,
                'account_name': request.user.full_name,
                'is_active': True,
                'confirmed_at': timezone.now(),
            },
        )
        return Response(PayoutAccountSerializer(account).data)


class PayoutListCreateView(generics.GenericAPIView):
    serializer_class = PayoutSerializer
    permission_classes = [IsAuthenticated, IsVerifiedUser]
    pagination_class = WalletTransactionPagination

    def get_throttles(self):
        return [PayoutThrottle()] if self.request.method == 'POST' else []

    def get(self, request, *args, **kwargs):
        payouts = Payout.objects.filter(wallet__user=request.user).select_related('payout_account')
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(payouts, request, view=self)
        return paginator.get_paginated_response(PayoutSerializer(page, many=True).data)

    @extend_schema(request=PayoutRequestSerializer, responses=PayoutSerializer)
    def post(self, request, *args, **kwargs):
        serializer = PayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        balance_source = serializer.validated_data['balance_source']
        if balance_source == WalletBalanceSource.EARNINGS:
            require_payout_eligible_seller(request.user)
        idempotency_key = request.headers.get('Idempotency-Key', '').strip()
        if not idempotency_key or len(idempotency_key) > 100:
            raise ValidationError({'Idempotency-Key': 'A key of 1 to 100 characters is required.'})

        account = PayoutAccount.objects.filter(user=request.user, is_active=True).first()
        if account is None:
            raise ValidationError({'payout_account': 'Add an M-Pesa payout number before requesting a payout.'})

        callback_token = secrets.token_urlsafe(32)
        with transaction.atomic():
            wallet, _ = Wallet.objects.get_or_create(user=request.user, currency='KES')
            wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            existing = Payout.objects.filter(wallet=wallet, idempotency_key=idempotency_key).first()
            if existing:
                if existing.balance_source != balance_source or existing.payout_account_id != account.pk:
                    return Response(
                        {'detail': 'Idempotency key was already used with different payout details.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                return Response(PayoutSerializer(existing).data)
            if wallet.is_frozen:
                raise ValidationError({'wallet': 'Wallet activity is temporarily unavailable.'})
            active_payout = Payout.objects.filter(
                wallet=wallet,
                balance_source=balance_source,
                status__in=[
                    PayoutStatus.REQUESTED,
                    PayoutStatus.PROCESSING,
                    PayoutStatus.UNKNOWN,
                ],
            ).order_by('-created_at').first()
            if active_payout:
                label = 'seller earnings' if balance_source == WalletBalanceSource.EARNINGS else 'shopping funds'
                state = 'being reconciled' if active_payout.status == PayoutStatus.UNKNOWN else 'being processed'
                raise ValidationError({
                    'balance_source': (
                        f'A {label} withdrawal is already {state}. '
                        'Wait for it to reach a final status before requesting another.'
                    ),
                })
            amount = (
                wallet.shopping_available_balance
                if balance_source == WalletBalanceSource.SHOPPING
                else wallet.earnings_available_balance
            )
            minimum = Decimal(str(settings.MPESA_B2C_MIN_AMOUNT))
            maximum = Decimal(str(settings.MPESA_B2C_MAX_AMOUNT))
            if amount < minimum:
                raise ValidationError({
                    'balance_source': f'M-Pesa withdrawals require at least KSh {minimum:,.0f}.',
                })
            if amount > maximum:
                raise ValidationError({
                    'balance_source': (
                        f'This balance exceeds the KSh {maximum:,.0f} M-Pesa transfer limit. '
                        'Contact support to arrange the withdrawal safely.'
                    ),
                })
            if amount != amount.to_integral_value():
                raise ValidationError({
                    'balance_source': 'M-Pesa can only withdraw whole KES amounts. Contact support for this balance.',
                })

            payout = Payout.objects.create(
                wallet=wallet,
                payout_account=account,
                amount=amount,
                balance_source=balance_source,
                currency=wallet.currency,
                destination_phone=account.phone_number,
                destination_name=account.account_name,
                idempotency_key=idempotency_key,
                callback_token_hash=hashlib.sha256(callback_token.encode()).hexdigest(),
                originator_conversation_id=f'AFRICLAY-{uuid.uuid4().hex}',
            )
            place_escrow_hold(
                wallet,
                amount,
                f'payout:{payout.pk}:reserve',
                balance_source,
            )

        try:
            provider_response = MpesaB2CGateway().initiate_payout(
                payout=payout,
                callback_token=callback_token,
            )
        except MpesaConfigurationError as error:
            logger.error('M-Pesa B2C configuration failure reason=%s', str(error))
            payout = resolve_payout(
                payout,
                succeeded=False,
                failure_code='configuration_error',
                failure_message='M-Pesa withdrawals are temporarily unavailable.',
            )
            return Response(PayoutSerializer(payout).data, status=status.HTTP_201_CREATED)
        except Timeout:
            payout = update_pending_payout(
                payout,
                payout_status=PayoutStatus.UNKNOWN,
                failure_code='provider_timeout',
                failure_message='The provider result needs reconciliation.',
            )
            return Response(PayoutSerializer(payout).data, status=status.HTTP_202_ACCEPTED)
        except MpesaGatewayError as error:
            logger.warning(
                'M-Pesa B2C failure stage=%s status=%s provider_code=%s provider_reason=%s',
                error.stage or 'unknown', error.status_code or 'none',
                error.provider_code or 'none', error.provider_reason or 'none',
            )
            if error.status_code and 400 <= error.status_code < 500:
                payout = resolve_payout(
                    payout,
                    succeeded=False,
                    failure_code=error.provider_code or 'provider_rejected',
                    failure_message='M-Pesa did not accept this payout request.',
                )
                return Response(PayoutSerializer(payout).data, status=status.HTTP_201_CREATED)
            payout = update_pending_payout(
                payout,
                payout_status=PayoutStatus.UNKNOWN,
                failure_code=error.provider_code or 'provider_unavailable',
                failure_message='The provider result needs reconciliation.',
            )
            return Response(PayoutSerializer(payout).data, status=status.HTTP_202_ACCEPTED)

        response_code = str(provider_response.get('ResponseCode', ''))
        if response_code != '0':
            payout = resolve_payout(
                payout,
                succeeded=False,
                provider_result=provider_response,
                failure_code=response_code or 'provider_rejected',
                failure_message=str(
                    provider_response.get('ResponseDescription', 'M-Pesa did not accept this payout request.'),
                ),
            )
            return Response(PayoutSerializer(payout).data, status=status.HTTP_201_CREATED)

        response_originator = str(provider_response.get('OriginatorConversationID', ''))
        conversation_id = str(provider_response.get('ConversationID', ''))
        if response_originator and response_originator != payout.originator_conversation_id:
            payout = update_pending_payout(
                payout,
                payout_status=PayoutStatus.UNKNOWN,
                failure_code='identifier_mismatch',
                failure_message='The provider result needs reconciliation.',
                provider_response=provider_response,
            )
        elif not conversation_id:
            payout = update_pending_payout(
                payout,
                payout_status=PayoutStatus.UNKNOWN,
                failure_code='incomplete_acknowledgement',
                failure_message='The provider result needs reconciliation.',
                provider_response=provider_response,
            )
        else:
            with transaction.atomic():
                locked = Payout.objects.select_for_update().get(pk=payout.pk)
                if locked.status not in {PayoutStatus.SUCCEEDED, PayoutStatus.FAILED}:
                    locked.provider_response = provider_response
                    locked.status = PayoutStatus.PROCESSING
                    locked.conversation_id = conversation_id[:100]
                    locked.failure_code = ''
                    locked.failure_message = ''
                    locked.save(update_fields=[
                        'provider_response', 'status', 'conversation_id', 'failure_code',
                        'failure_message', 'updated_at',
                    ])
                payout = locked
        return Response(PayoutSerializer(payout).data, status=status.HTTP_202_ACCEPTED)


def payout_result_parameters(result):
    values = result.get('ResultParameters', {}).get('ResultParameter', [])
    if not isinstance(values, list):
        return {}
    return {
        str(item.get('Key')): item.get('Value')
        for item in values
        if isinstance(item, dict) and item.get('Key')
    }


class PayoutProviderResultView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, *args, **kwargs):
        token_hash = hashlib.sha256(kwargs['token'].encode()).hexdigest()
        payout = Payout.objects.filter(callback_token_hash=token_hash).first()
        if payout is None:
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=404)
        result = request.data.get('Result')
        if not isinstance(result, dict):
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid payout result.'}, status=400)
        originator = str(result.get('OriginatorConversationID', ''))
        if originator and originator != payout.originator_conversation_id:
            return Response({'ResultCode': 1, 'ResultDesc': 'Callback does not match payout.'}, status=400)
        conversation_id = str(result.get('ConversationID', ''))
        if payout.conversation_id and conversation_id and conversation_id != payout.conversation_id:
            return Response({'ResultCode': 1, 'ResultDesc': 'Callback does not match payout.'}, status=400)

        result_code = str(result.get('ResultCode', ''))
        parameters = payout_result_parameters(result)
        try:
            if result_code == '0':
                callback_amount = parameters.get('TransactionAmount')
                if callback_amount is not None:
                    try:
                        parsed_amount = Decimal(str(callback_amount))
                    except (InvalidOperation, TypeError, ValueError):
                        return Response({'ResultCode': 1, 'ResultDesc': 'Payout amount is invalid.'}, status=400)
                    if parsed_amount != payout.amount:
                        return Response({'ResultCode': 1, 'ResultDesc': 'Payout amount does not match.'}, status=400)
                receipt = str(parameters.get('TransactionReceipt') or result.get('TransactionID') or '').strip()
                payout = resolve_payout(
                    payout,
                    succeeded=True,
                    provider_result=request.data,
                    transaction_receipt=receipt,
                )
            else:
                payout = resolve_payout(
                    payout,
                    succeeded=False,
                    provider_result=request.data,
                    failure_code=result_code or 'provider_failed',
                    failure_message=str(result.get('ResultDesc', 'M-Pesa payout was not completed.')),
                )
        except (IntegrityError, WalletOperationError) as error:
            logger.error('Unable to finalize payout %s: %s', payout.pk, str(error))
            return Response({'ResultCode': 1, 'ResultDesc': 'Payout could not be finalized.'}, status=409)
        return Response({'ResultCode': 0, 'ResultDesc': 'Payout result accepted.', 'PayoutId': str(payout.pk)})


class PayoutProviderTimeoutView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, *args, **kwargs):
        token_hash = hashlib.sha256(kwargs['token'].encode()).hexdigest()
        with transaction.atomic():
            payout = Payout.objects.select_for_update().filter(callback_token_hash=token_hash).first()
            if payout is None:
                return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=404)
            if payout.status not in {PayoutStatus.SUCCEEDED, PayoutStatus.FAILED}:
                payout.status = PayoutStatus.UNKNOWN
                payout.provider_result = request.data if isinstance(request.data, dict) else {}
                payout.failure_code = 'queue_timeout'
                payout.failure_message = 'The provider result needs reconciliation.'
                payout.save(update_fields=[
                    'status', 'provider_result', 'failure_code', 'failure_message', 'updated_at',
                ])
        return Response({'ResultCode': 0, 'ResultDesc': 'Timeout accepted.'})


class AdminPayoutListView(generics.ListAPIView):
    serializer_class = AdminPayoutSerializer
    permission_classes = [IsAuthenticated, IsFinance]
    pagination_class = WalletTransactionPagination

    def get_queryset(self):
        queryset = Payout.objects.select_related(
            'wallet__user', 'wallet__user__store', 'payout_account', 'resolved_by',
        )
        payout_status = self.request.query_params.get('status', '').strip()
        if payout_status in PayoutStatus.values:
            queryset = queryset.filter(status=payout_status)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(wallet__user__email__icontains=search)
                | Q(wallet__user__store__name__icontains=search)
                | Q(transaction_receipt__icontains=search),
            )
        return queryset


class AdminPaymentListView(generics.ListAPIView):
    serializer_class = AdminPaymentSerializer
    permission_classes = [IsAuthenticated, IsFinance]
    pagination_class = WalletTransactionPagination

    def get_queryset(self):
        queryset = Payment.objects.select_related('order', 'order__buyer')
        payment_status = self.request.query_params.get('status', '').strip()
        if payment_status in PaymentStatus.values:
            queryset = queryset.filter(status=payment_status)
        search = self.request.query_params.get('search', '').strip()
        if search:
            query = (
                Q(order__buyer__email__icontains=search)
                | Q(receipt_number__icontains=search)
                | Q(checkout_request_id__icontains=search)
                | Q(failure_code__icontains=search)
            )
            try:
                query |= Q(order_id=uuid.UUID(search))
            except ValueError:
                pass
            queryset = queryset.filter(query)
        date_from = parse_date(self.request.query_params.get('date_from', '').strip())
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        date_to = parse_date(self.request.query_params.get('date_to', '').strip())
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return queryset.order_by('-created_at')


class AdminFinanceSummaryView(generics.GenericAPIView):
    serializer_class = AdminPayoutSerializer
    permission_classes = [IsAuthenticated, IsFinance]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, *args, **kwargs):
        date_from = parse_date(request.query_params.get('date_from', '').strip())
        date_to = parse_date(request.query_params.get('date_to', '').strip())

        def period(queryset):
            if date_from:
                queryset = queryset.filter(created_at__date__gte=date_from)
            if date_to:
                queryset = queryset.filter(created_at__date__lte=date_to)
            return queryset

        wallet_totals = Wallet.objects.aggregate(
            liability=Sum('balance'),
            reserved=Sum('held_balance'),
        )
        payout_totals = period(Payout.objects.all()).aggregate(
            completed=Sum('amount', filter=Q(status=PayoutStatus.SUCCEEDED)),
        )
        active_payouts = Payout.objects.filter(status__in=[
            PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN,
        ])
        pending_payouts = active_payouts.aggregate(
            pending=Sum('amount', filter=Q(status__in=[
                PayoutStatus.REQUESTED, PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN,
            ])),
        )
        ledger_totals = period(LedgerEntry.objects.all()).aggregate(
            commission=Sum('amount', filter=Q(entry_type=LedgerEntryType.COMMISSION_EARNED)),
            seller_released=Sum('amount', filter=Q(entry_type=LedgerEntryType.SELLER_PROCEEDS_RELEASE)),
            customer_payments=Sum('amount', filter=Q(entry_type__in=[
                LedgerEntryType.CUSTOMER_PAYMENT, LedgerEntryType.DELIVERY_FEE,
            ])),
        )
        payments = period(Payment.objects.all())
        reconciliation = Payout.objects.filter(status=PayoutStatus.UNKNOWN).order_by('created_at')
        oldest_reconciliation = reconciliation.values_list('created_at', flat=True).first()
        zero = Decimal('0.00')
        return Response({
            'wallet_liability': wallet_totals['liability'] or zero,
            'wallet_reserved': wallet_totals['reserved'] or zero,
            'commission_earned': ledger_totals['commission'] or zero,
            'seller_proceeds_released': ledger_totals['seller_released'] or zero,
            'customer_payments': ledger_totals['customer_payments'] or zero,
            'payouts_pending': pending_payouts['pending'] or zero,
            'payouts_completed': payout_totals['completed'] or zero,
            'needs_reconciliation': reconciliation.count(),
            'reconciliation_over_24h': reconciliation.filter(
                created_at__lte=timezone.now() - timedelta(hours=24),
            ).count(),
            'oldest_reconciliation_at': oldest_reconciliation,
            'failed_payments': payments.filter(status=PaymentStatus.FAILED).count(),
            'pending_payments': payments.filter(status__in=[
                PaymentStatus.INITIATED, PaymentStatus.PENDING,
            ]).count(),
            'period': {
                'date_from': date_from.isoformat() if date_from else None,
                'date_to': date_to.isoformat() if date_to else None,
            },
        })


class AdminPayoutResolveView(generics.GenericAPIView):
    serializer_class = PayoutResolutionSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(request=PayoutResolutionSerializer, responses=AdminPayoutSerializer)
    def post(self, request, *args, **kwargs):
        serializer = PayoutResolutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payout = get_object_or_404(Payout, pk=kwargs['pk'])
        if payout.status not in {PayoutStatus.PROCESSING, PayoutStatus.UNKNOWN}:
            raise ValidationError({'status': 'Only processing or unreconciled payouts can be resolved manually.'})
        succeeded = serializer.validated_data['resolution'] == 'succeeded'
        try:
            payout = resolve_payout(
                payout,
                succeeded=succeeded,
                transaction_receipt=serializer.validated_data.get('transaction_receipt', ''),
                failure_code='' if succeeded else 'admin_reconciled_failed',
                failure_message='' if succeeded else 'Payout was not completed by M-Pesa.',
                resolved_by=request.user,
                resolution_note=serializer.validated_data['note'],
            )
        except (IntegrityError, WalletOperationError) as error:
            raise ValidationError({'payout': str(error)}) from error
        AdminAuditLog.objects.create(
            actor=request.user,
            action='payout_reconciled',
            target_type='payout',
            target_id=str(payout.pk),
            reason=serializer.validated_data['note'],
            metadata={
                'resolution': serializer.validated_data['resolution'],
                'has_receipt': bool(serializer.validated_data.get('transaction_receipt')),
            },
        )
        return Response(AdminPayoutSerializer(payout).data)


def finance_transactions(request):
    queryset = LedgerEntry.objects.select_related('order', 'payment', 'user')
    search = request.query_params.get('search', '').strip()
    if search:
        queryset = queryset.filter(
            Q(reference__icontains=search)
            | Q(order_id__icontains=search)
            | Q(payment_id__icontains=search)
            | Q(payment__receipt_number__icontains=search)
            | Q(user__email__icontains=search)
        )
    entry_type = request.query_params.get('entry_type', '').strip()
    if entry_type in LedgerEntryType.values:
        queryset = queryset.filter(entry_type=entry_type)
    provider = request.query_params.get('provider', '').strip()
    if provider:
        queryset = queryset.filter(payment__provider=provider)
    payment_status = request.query_params.get('payment_status', '').strip()
    if payment_status in PaymentStatus.values:
        queryset = queryset.filter(payment__status=payment_status)
    date_from = parse_date(request.query_params.get('date_from', '').strip())
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    date_to = parse_date(request.query_params.get('date_to', '').strip())
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset.order_by('-created_at', '-id')


class FinanceTransactionListView(generics.ListAPIView):
    serializer_class = FinanceTransactionSerializer
    permission_classes = [IsAuthenticated, IsFinance]
    pagination_class = WalletTransactionPagination

    def get_queryset(self):
        return finance_transactions(self.request)


class FinanceTransactionDetailView(generics.RetrieveAPIView):
    serializer_class = FinanceTransactionSerializer
    permission_classes = [IsAuthenticated, IsFinance]
    queryset = LedgerEntry.objects.select_related('order', 'payment', 'user')


class FinanceTransactionExportView(generics.GenericAPIView):
    serializer_class = FinanceTransactionSerializer
    permission_classes = [IsAuthenticated, IsFinance]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="africlay-finance-transactions.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'created_at', 'entry_type', 'amount', 'currency', 'reference', 'order_id',
            'payment_id', 'user_email', 'provider', 'payment_status', 'receipt_number',
        ])
        for entry in finance_transactions(request)[:10000]:
            writer.writerow([
                entry.created_at.isoformat(), entry.entry_type, entry.amount, entry.currency,
                entry.reference, entry.order_id, entry.payment_id,
                entry.user.email if entry.user else '', entry.payment.provider if entry.payment else '',
                entry.payment.status if entry.payment else '', entry.payment.receipt_number if entry.payment else '',
            ])
        return response


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

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
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
            requested_purpose = input_serializer.validated_data['purpose']
            if requested_purpose == PaymentPurpose.DELIVERY_BALANCE:
                if order.payment_option != PaymentOption.PAY_ON_DELIVERY:
                    raise ValidationError({'payment': 'This order does not have a pay-on-delivery balance.'})
                if order.status != OrderStatus.SHIPPED:
                    raise ValidationError({'order': 'The delivery balance can be paid after the order ships.'})
                if not order.delivery_fee_paid:
                    raise ValidationError({'payment': 'The delivery fee must be paid first.'})
                purpose = PaymentPurpose.DELIVERY_BALANCE
                amount = order.amount_due
            else:
                if order.status != OrderStatus.PENDING:
                    raise ValidationError({'order': 'The initial payment is only available for pending orders.'})
                if order.amount_paid > 0:
                    raise ValidationError({'payment': 'The initial payment has already been received.'})
                if order.payment_option == PaymentOption.PAY_ON_DELIVERY:
                    purpose = PaymentPurpose.DELIVERY_FEE
                    amount = order.shipping_cost
                else:
                    purpose = PaymentPurpose.ORDER_TOTAL
                    amount = order.total_amount

            if amount <= 0:
                raise ValidationError({'payment': 'There is no outstanding amount for this payment.'})
            existing_payment = Payment.objects.filter(
                order=order,
                purpose=purpose,
                status__in=[PaymentStatus.INITIATED, PaymentStatus.PENDING],
            ).first()
            if existing_payment:
                return Response(PaymentSerializer(existing_payment).data, status=status.HTTP_200_OK)

            if amount != amount.to_integral_value():
                raise ValidationError({'payment': 'M-Pesa payments require a whole-number KES amount.'})

            callback_token = secrets.token_urlsafe(32)
            payment = Payment.objects.create(
                order=order,
                provider='mpesa',
                purpose=purpose,
                status=PaymentStatus.INITIATED,
                amount=amount,
                currency=order.currency,
                phone_number=input_serializer.validated_data['phone_number'],
                callback_token_hash=hashlib.sha256(callback_token.encode()).hexdigest(),
            )

        try:
            response_data = MpesaGateway().initiate_payment(
                phone_number=payment.phone_number,
                amount=payment.amount,
                account_reference=str(order.id),
                callback_token=callback_token,
            )
        except Timeout:
            payment.status = PaymentStatus.PENDING
            payment.failure_message = 'Payment provider response is pending reconciliation.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment status is pending provider confirmation.'})
        except RequestException as error:
            logger.warning(
                'M-Pesa transport failure stage=unknown error_type=%s environment=%s',
                type(error).__name__,
                settings.MPESA_ENVIRONMENT,
            )
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'Payment provider is temporarily unavailable.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'Payment provider is temporarily unavailable.'})
        except MpesaConfigurationError as error:
            logger.error(
                'M-Pesa configuration failure environment=%s reason=%s',
                settings.MPESA_ENVIRONMENT,
                str(error),
            )
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'M-Pesa checkout is not configured.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'M-Pesa checkout is not configured.'})
        except MpesaGatewayError as error:
            logger.warning(
                'M-Pesa gateway failure stage=%s status=%s provider_code=%s provider_reason=%s environment=%s',
                error.stage or 'unknown',
                error.status_code or 'none',
                error.provider_code or 'none',
                error.provider_reason or 'none',
                settings.MPESA_ENVIRONMENT,
            )
            payment.status = PaymentStatus.FAILED
            payment.failure_message = 'M-Pesa could not start this payment.'
            payment.save(update_fields=['status', 'failure_message', 'updated_at'])
            raise ValidationError({'payment': 'M-Pesa could not start this payment. Please try again.'})
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


class MpesaOrderCallbackView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, *args, **kwargs):
        callback = request.data.get('Body', {}).get('stkCallback')
        if not isinstance(callback, dict):
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=400)

        token_hash = hashlib.sha256(kwargs['token'].encode()).hexdigest()
        payment = Payment.objects.filter(callback_token_hash=token_hash).first()
        if payment is None:
            return Response({'ResultCode': 1, 'ResultDesc': 'Invalid callback.'}, status=404)

        checkout_id = str(callback.get('CheckoutRequestID', ''))
        merchant_id = str(callback.get('MerchantRequestID', ''))
        if (
            (payment.checkout_request_id and payment.checkout_request_id != checkout_id)
            or (payment.merchant_request_id and payment.merchant_request_id != merchant_id)
        ):
            return Response({'ResultCode': 1, 'ResultDesc': 'Callback does not match payment.'}, status=400)

        processed = PaymentService.process_mpesa_callback(request.data)
        if processed.pk != payment.pk:
            return Response({'ResultCode': 1, 'ResultDesc': 'Callback does not match payment.'}, status=400)
        return Response({'ResultCode': 0, 'ResultDesc': 'Callback accepted.'})


class MpesaQueryTokenCallbackView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, *args, **kwargs):
        callback_secret = getattr(settings, 'MPESA_CALLBACK_SECRET', '')
        if not callback_secret or request.query_params.get('token') != callback_secret:
            return Response({'detail': 'Invalid callback token.'}, status=status.HTTP_403_FORBIDDEN)
        payment = PaymentService.process_mpesa_callback(request.data)
        return Response({'ResultCode': 0, 'PaymentId': str(payment.id)}, status=status.HTTP_200_OK)
