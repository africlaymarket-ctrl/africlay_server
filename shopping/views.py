from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP

from authapp.permissions import IsAuthenticated, IsBuyer, IsSeller, IsVerifiedUser
from product_management.models import Product

from payments.models import LedgerEntryType, Wallet
from payments.services import (
    capture_escrow_hold,
    hold_mpesa_seller_proceeds,
    record_ledger_entry,
    WalletOperationError,
    place_escrow_hold,
    release_escrow_hold,
    transfer_escrow_to_wallet,
)
from notifications.models import NotificationType, create_notification

from .models import (
    Cart, CartItem, Order, OrderFulfillment, OrderItem, OrderStatus, PaymentMethod,
    PaymentOption, PaymentStatus, SettlementStatus, Wishlist, WishlistItem,
)
from .serializers import (
    CartSerializer,
    CartItemSerializer,
    CreateCheckoutSerializer,
    OrderDetailSerializer,
    OrderStatusUpdateSerializer,
    SellerOrderDetailSerializer,
    WishlistSerializer,
    WishlistItemSerializer,
)
from .shipping import ShippingQuoteError, quote_cart_items


COMMISSION_RATE = Decimal('0.1000')
MONEY = Decimal('0.01')


def _cart_items(cart):
    return list(
        CartItem.objects.filter(cart=cart).select_related(
            'product', 'product__store', 'product__store__owner',
        )
    )


def _shipping_quote_payload(quotes):
    item_subtotal = sum((quote.item_subtotal for quote in quotes), Decimal('0.00'))
    shipping_cost = sum((quote.shipping_cost for quote in quotes), Decimal('0.00'))
    return {
        'item_subtotal': item_subtotal,
        'shipping_cost': shipping_cost,
        'total_amount': item_subtotal + shipping_cost,
        'currency': 'KES',
        'shipments': [
            {
                'seller_id': str(quote.seller.pk),
                'origin_city': quote.origin_city,
                'origin_region': quote.origin_region,
                'destination_city': quote.destination_city,
                'destination_region': quote.destination_region,
                'actual_weight_kg': quote.actual_weight_kg,
                'volumetric_weight_kg': quote.volumetric_weight_kg,
                'chargeable_weight_kg': quote.chargeable_weight_kg,
                'shipping_cost': quote.shipping_cost,
                'is_remote': quote.is_remote,
            }
            for quote in quotes
        ],
    }


class CartDetailView(generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated, IsBuyer]

    def get_object(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return cart

    def get(self, request, *args, **kwargs):
        cart = self.get_object()
        serializer = self.get_serializer(cart)
        return Response(serializer.data)


class CartItemListCreateView(generics.ListCreateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated, IsBuyer]

    def get_queryset(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return CartItem.objects.filter(cart=cart).select_related('product')

    def create(self, request, *args, **kwargs):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        product_id = request.data.get('product')
        
        try:
            quantity = int(request.data.get('quantity', 1))
        except (ValueError, TypeError):
            raise ValidationError({'quantity': 'Quantity must be an integer.'})
        if quantity < 1:
            raise ValidationError({'quantity': 'Quantity must be at least 1.'})

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise ValidationError({'product': 'Product not found.'})

        if product.stock_quantity == 0:
            raise ValidationError({'product': 'Product is out of stock.'})

        if quantity > product.stock_quantity:
            raise ValidationError({'quantity': f'Only {product.stock_quantity} items available.'})

        existing_item = CartItem.objects.filter(cart=cart, product=product).first()
        if existing_item:
            new_quantity = existing_item.quantity + quantity
            if new_quantity > product.stock_quantity:
                raise ValidationError({'quantity': f'Total quantity {new_quantity} exceeds available stock {product.stock_quantity}.'})
            existing_item.quantity = new_quantity
            existing_item.save()
            serializer = self.get_serializer(existing_item)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(cart=cart, product=product)
            return Response(serializer.data, status=status.HTTP_201_CREATED)


class CartItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated, IsBuyer]
    lookup_field = 'pk'

    def get_queryset(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return CartItem.objects.filter(cart=cart).select_related('product')

    def perform_update(self, serializer):
        product = serializer.instance.product
        quantity = serializer.validated_data.get('quantity', serializer.instance.quantity)

        if quantity > product.stock_quantity:
            raise ValidationError({'quantity': f'Only {product.stock_quantity} items available.'})

        serializer.save()


class ShippingQuoteView(generics.GenericAPIView):
    serializer_class = CreateCheckoutSerializer
    permission_classes = [IsAuthenticated, IsBuyer]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = Cart.objects.filter(buyer=request.user).first()
        if cart is None:
            raise ValidationError({'cart': 'Cart not found.'})
        cart_items = _cart_items(cart)
        if not cart_items:
            raise ValidationError({'cart': 'Cannot checkout with an empty cart.'})
        try:
            quotes = quote_cart_items(cart_items, serializer.validated_data['shipping_city'])
        except ShippingQuoteError as error:
            raise ValidationError({'shipping_city': str(error)}) from error
        return Response(_shipping_quote_payload(quotes))


class CheckoutView(generics.GenericAPIView):
    serializer_class = CreateCheckoutSerializer
    permission_classes = [IsAuthenticated, IsBuyer]

    def post(self, request, *args, **kwargs):
        """Create an immutable priced order while holding stock atomically."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment_method = serializer.validated_data['payment_method']
        payment_option = serializer.validated_data['payment_option']
        if payment_method == PaymentMethod.WALLET and payment_option != PaymentOption.PAY_NOW:
            raise ValidationError({'payment_option': 'Wallet checkout only supports Pay Now.'})

        buyer = request.user
        cart = Cart.objects.filter(buyer=buyer).first()
        if cart is None:
            raise ValidationError({'cart': 'Cart not found.'})
        cart_items = _cart_items(cart)
        if not cart_items:
            raise ValidationError({'cart': 'Cannot checkout with an empty cart.'})

        try:
            with transaction.atomic():
                product_ids = [item.product.id for item in cart_items]
                products_locked = {
                    product.id: product
                    for product in Product.objects.select_for_update().filter(
                        id__in=product_ids,
                    ).select_related('store', 'store__owner')
                }

                for item in cart_items:
                    product = products_locked.get(item.product.id)
                    if not product:
                        raise ValidationError({'product': f'Product {item.product.name} no longer exists.'})
                    if product.status != 'published':
                        raise ValidationError({'product': f'{product.name} is not available for purchase.'})
                    if product.store.status != 'active':
                        raise ValidationError({'product': f'Store for {product.name} is not active.'})
                    if product.stock_quantity < item.quantity:
                        raise ValidationError({'product': f'Insufficient stock for {product.name}. Only {product.stock_quantity} available.'})

                locked_cart_items = []
                for item in cart_items:
                    item.product = products_locked[item.product.id]
                    locked_cart_items.append(item)
                try:
                    quotes = quote_cart_items(locked_cart_items, serializer.validated_data['shipping_city'])
                except ShippingQuoteError as error:
                    raise ValidationError({'shipping_city': str(error)}) from error
                quote_totals = _shipping_quote_payload(quotes)

                order = Order.objects.create(
                    buyer=buyer,
                    item_subtotal=quote_totals['item_subtotal'],
                    shipping_cost=quote_totals['shipping_cost'],
                    total_amount=quote_totals['total_amount'],
                    currency='KES',
                    status=OrderStatus.PROCESSING if payment_method == PaymentMethod.WALLET else OrderStatus.PENDING,
                    payment_method=payment_method,
                    payment_status=PaymentStatus.PAID if payment_method == PaymentMethod.WALLET else PaymentStatus.PENDING,
                    payment_option=payment_option,
                    amount_paid=quote_totals['total_amount'] if payment_method == PaymentMethod.WALLET else Decimal('0.00'),
                    delivery_fee_paid=payment_method == PaymentMethod.WALLET,
                    shipping_address=serializer.validated_data['shipping_address'],
                    shipping_city=serializer.validated_data['shipping_city'],
                    shipping_postal_code=serializer.validated_data['shipping_postal_code'],
                    shipping_country=serializer.validated_data['shipping_country'],
                )

                if payment_method == PaymentMethod.WALLET:
                    wallet, _ = Wallet.objects.select_for_update().get_or_create(
                        user=buyer,
                        currency=order.currency,
                    )
                    place_escrow_hold(
                        wallet,
                        order.total_amount,
                        f'order:{order.pk}:escrow-hold',
                    )

                quote_by_seller = {quote.seller.pk: quote for quote in quotes}
                fulfillments = {}
                for seller_id, quote in quote_by_seller.items():
                    fulfillments[seller_id] = OrderFulfillment.objects.create(
                        order=order,
                        seller=quote.seller,
                        status=OrderStatus.PROCESSING if payment_method == PaymentMethod.WALLET else OrderStatus.PENDING,
                        item_subtotal=quote.item_subtotal,
                        commission_amount=quote.commission_amount,
                        seller_proceeds=quote.seller_proceeds,
                        shipping_cost=quote.shipping_cost,
                        actual_weight_kg=quote.actual_weight_kg,
                        volumetric_weight_kg=quote.volumetric_weight_kg,
                        chargeable_weight_kg=quote.chargeable_weight_kg,
                        origin_city=quote.origin_city,
                        origin_region=quote.origin_region,
                        destination_city=quote.destination_city,
                        destination_region=quote.destination_region,
                        settlement_status=(
                            SettlementStatus.HELD
                            if payment_method == PaymentMethod.WALLET
                            else SettlementStatus.PENDING
                        ),
                    )

                sellers = set()
                for cart_item in cart_items:
                    product = products_locked[cart_item.product.id]
                    line_total = (product.price * cart_item.quantity).quantize(MONEY)
                    commission = (line_total * COMMISSION_RATE).quantize(MONEY, rounding=ROUND_HALF_UP)
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=cart_item.quantity,
                        price_at_purchase=product.price,
                        seller=product.store.owner,
                        commission_rate=COMMISSION_RATE,
                        commission_amount=commission,
                        seller_proceeds=line_total - commission,
                    )
                    sellers.add(product.store.owner)
                    product.stock_quantity -= cart_item.quantity
                    product.save(update_fields=['stock_quantity', 'updated_at'])

                if payment_method == PaymentMethod.WALLET:
                    for fulfillment in fulfillments.values():
                        record_ledger_entry(
                            order=order,
                            user=fulfillment.seller,
                            entry_type=LedgerEntryType.SELLER_PROCEEDS_HOLD,
                            amount=fulfillment.seller_proceeds,
                            reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:proceeds-hold',
                            metadata={'source': 'buyer_wallet'},
                        )
                        record_ledger_entry(
                            order=order,
                            user=fulfillment.seller,
                            entry_type=LedgerEntryType.COMMISSION_HOLD,
                            amount=fulfillment.commission_amount,
                            reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:commission-hold',
                            metadata={'source': 'buyer_wallet'},
                        )
                    record_ledger_entry(
                        order=order,
                        user=buyer,
                        entry_type=LedgerEntryType.CUSTOMER_PAYMENT,
                        amount=order.total_amount,
                        reference=f'order:{order.pk}:wallet-payment',
                        metadata={'provider': 'wallet'},
                    )
                    record_ledger_entry(
                        order=order,
                        entry_type=LedgerEntryType.DELIVERY_FEE,
                        amount=order.shipping_cost,
                        reference=f'order:{order.pk}:delivery-fee-received',
                    )

                create_notification(
                    buyer,
                    title='Order placed',
                    message=(
                        f'Your order {order.id} has been placed and is being processed.'
                        if payment_method == PaymentMethod.WALLET
                        else f'Your order {order.id} is waiting for payment confirmation.'
                    ),
                    notification_type=NotificationType.ORDER,
                )
                for seller in sellers:
                    create_notification(
                        seller,
                        title='New order received',
                        message=f'You have a new order ({order.id}) waiting for fulfillment.',
                        notification_type=NotificationType.ORDER,
                    )

                CartItem.objects.filter(cart=cart).delete()

        except WalletOperationError as e:
            raise ValidationError({'payment': str(e)})
        except IntegrityError as e:
            # Handle database constraint violations
            if 'stock_quantity' in str(e):
                raise ValidationError({'product': 'Product stock level is invalid. This may indicate concurrent checkout attempts.'})
            raise

        order_serializer = OrderDetailSerializer(order)
        return Response(order_serializer.data, status=status.HTTP_201_CREATED)


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        return Order.objects.filter(buyer=self.request.user).prefetch_related('items')


class OrderListView(generics.ListAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(buyer=self.request.user).prefetch_related('items').order_by('-created_at')


class SellerOrderListView(generics.ListAPIView):
    """Lists all orders that contain items sold by the requesting seller."""
    serializer_class = SellerOrderDetailSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        order_ids = OrderItem.objects.filter(seller=self.request.user).values_list('order_id', flat=True)
        return Order.objects.filter(id__in=order_ids).prefetch_related(
            'items', 'fulfillments',
        ).distinct().order_by('-created_at')


class SellerOrderDetailView(generics.RetrieveAPIView):
    serializer_class = SellerOrderDetailSerializer
    permission_classes = [IsSeller, IsVerifiedUser]
    lookup_field = 'pk'

    def get_queryset(self):
        order_ids = OrderItem.objects.filter(seller=self.request.user).values_list('order_id', flat=True)
        return Order.objects.filter(id__in=order_ids).prefetch_related(
            'items', 'fulfillments',
        ).distinct()


class SellerOrderStatusUpdateView(generics.GenericAPIView):
    """Allows a seller to update the status of an order containing their items."""
    serializer_class = OrderStatusUpdateSerializer
    permission_classes = [IsSeller, IsVerifiedUser]
    http_method_names = ['patch']

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_status = serializer.validated_data['status']
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().filter(items__seller=request.user).distinct(),
                pk=kwargs['pk'],
            )
            fulfillment = get_object_or_404(
                OrderFulfillment.objects.select_for_update(),
                order=order,
                seller=request.user,
            )
            if fulfillment.status != OrderStatus.PROCESSING:
                raise ValidationError({'status': 'Only processing orders can be shipped or cancelled.'})
            if target_status not in {OrderStatus.SHIPPED, OrderStatus.CANCELLED}:
                raise ValidationError({'status': 'Sellers can only ship or cancel processing orders.'})

            if target_status == OrderStatus.SHIPPED:
                upfront_paid = (
                    order.payment_status == PaymentStatus.PAID
                    or (
                        order.payment_option == PaymentOption.PAY_ON_DELIVERY
                        and order.delivery_fee_paid
                    )
                )
                if not upfront_paid:
                    raise ValidationError({'payment': 'The required upfront payment must be confirmed before shipping.'})
                courier_name = (serializer.validated_data.get('courier_name') or '').strip()
                tracking_number = (serializer.validated_data.get('tracking_number') or '').strip()
                if not courier_name or not tracking_number:
                    raise ValidationError({
                        'shipping': 'Courier name and tracking number are required before shipping the order.'
                    })
                fulfillment.status = OrderStatus.SHIPPED
                fulfillment.courier_name = courier_name
                fulfillment.tracking_number = tracking_number
                fulfillment.shipped_at = timezone.now()
                fulfillment.save(update_fields=[
                    'status', 'courier_name', 'tracking_number', 'shipped_at', 'updated_at',
                ])
            else:
                cancelled_total = fulfillment.shipping_cost + fulfillment.item_subtotal
                refundable_amount = fulfillment.shipping_cost + (
                    fulfillment.item_subtotal
                    if order.payment_status == PaymentStatus.PAID
                    else Decimal('0.00')
                )
                if order.payment_method == PaymentMethod.WALLET and fulfillment.settlement_status == SettlementStatus.HELD:
                    try:
                        wallet = Wallet.objects.get(user=order.buyer, currency=order.currency)
                        release_escrow_hold(
                            wallet,
                            refundable_amount,
                            f'order:{order.pk}:seller:{request.user.pk}:buyer-refund',
                        )
                    except (Wallet.DoesNotExist, WalletOperationError) as error:
                        raise ValidationError({'payment': str(error)}) from error
                    record_ledger_entry(
                        order=order,
                        user=order.buyer,
                        entry_type=LedgerEntryType.REFUND_COMPLETED,
                        amount=refundable_amount,
                        reference=f'order:{order.pk}:seller:{request.user.pk}:refund-completed',
                        metadata={'provider': 'wallet'},
                    )
                elif order.amount_paid > 0:
                    if fulfillment.settlement_status == SettlementStatus.HELD:
                        try:
                            seller_wallet = Wallet.objects.get(user=request.user, currency=order.currency)
                            capture_escrow_hold(
                                seller_wallet,
                                fulfillment.seller_proceeds,
                                f'order:{order.pk}:seller:{request.user.pk}:hold-reversal',
                            )
                        except (Wallet.DoesNotExist, WalletOperationError) as error:
                            raise ValidationError({'payment': str(error)}) from error
                    record_ledger_entry(
                        order=order,
                        user=order.buyer,
                        entry_type=LedgerEntryType.REFUND_DUE,
                        amount=refundable_amount,
                        reference=f'order:{order.pk}:seller:{request.user.pk}:refund-due',
                        metadata={'provider': order.payment_method},
                    )

                order.item_subtotal = max(
                    Decimal('0.00'),
                    order.item_subtotal - fulfillment.item_subtotal,
                )
                order.shipping_cost = max(
                    Decimal('0.00'),
                    order.shipping_cost - fulfillment.shipping_cost,
                )
                order.total_amount = max(
                    Decimal('0.00'),
                    order.total_amount - cancelled_total,
                )
                order.amount_paid = max(
                    Decimal('0.00'),
                    order.amount_paid - refundable_amount,
                )

                items = list(
                    OrderItem.objects.select_for_update()
                    .filter(order=order, seller=request.user)
                    .select_related('product')
                )
                for item in items:
                    if item.product_id:
                        item.product.stock_quantity += item.quantity
                        item.product.save(update_fields=['stock_quantity', 'updated_at'])
                fulfillment.status = OrderStatus.CANCELLED
                fulfillment.settlement_status = SettlementStatus.REVERSED
                fulfillment.save(update_fields=['status', 'settlement_status', 'updated_at'])
                record_ledger_entry(
                    order=order,
                    user=request.user,
                    entry_type=LedgerEntryType.SELLER_PROCEEDS_REVERSAL,
                    amount=fulfillment.seller_proceeds,
                    reference=f'order:{order.pk}:seller:{request.user.pk}:proceeds-reversal',
                )
                record_ledger_entry(
                    order=order,
                    user=request.user,
                    entry_type=LedgerEntryType.COMMISSION_REVERSAL,
                    amount=fulfillment.commission_amount,
                    reference=f'order:{order.pk}:seller:{request.user.pk}:commission-reversal',
                )

            fulfillment_states = list(order.fulfillments.values_list('status', flat=True))
            active_states = [value for value in fulfillment_states if value != OrderStatus.CANCELLED]
            if not active_states:
                order.status = OrderStatus.CANCELLED
                if order.payment_method == PaymentMethod.WALLET:
                    order.payment_status = PaymentStatus.REFUNDED
            elif all(value == OrderStatus.SHIPPED for value in active_states):
                order.status = OrderStatus.SHIPPED
            else:
                order.status = OrderStatus.PROCESSING

            if active_states:
                if order.amount_paid >= order.total_amount:
                    order.payment_status = PaymentStatus.PAID
                elif order.amount_paid > 0:
                    order.payment_status = PaymentStatus.PARTIAL
                else:
                    order.payment_status = PaymentStatus.PENDING

            if len(fulfillment_states) == 1:
                order.courier_name = fulfillment.courier_name
                order.tracking_number = fulfillment.tracking_number
                order.shipped_at = fulfillment.shipped_at
            else:
                order.courier_name = 'Multiple carriers' if order.status == OrderStatus.SHIPPED else ''
                order.tracking_number = ''
                order.shipped_at = timezone.now() if order.status == OrderStatus.SHIPPED else None
            order.save(update_fields=[
                'status', 'payment_status', 'item_subtotal', 'shipping_cost',
                'total_amount', 'amount_paid', 'courier_name', 'tracking_number',
                'shipped_at', 'updated_at',
            ])

            if target_status == OrderStatus.SHIPPED:
                create_notification(
                    order.buyer,
                    title='Order shipped',
                    message=f'Items from {request.user.full_name} in order {order.id} have shipped via {fulfillment.courier_name}.',
                    notification_type=NotificationType.ORDER,
                )
            elif target_status == OrderStatus.CANCELLED:
                create_notification(
                    order.buyer,
                    title='Order cancelled',
                    message=f'Items from {request.user.full_name} in order {order.id} were cancelled.',
                    notification_type=NotificationType.ORDER,
                )

        return Response(SellerOrderDetailSerializer(order, context={'request': request}).data)


class SellerOrderStatusView(SellerOrderStatusUpdateView):
    serializer_class = OrderStatusUpdateSerializer


class OrderDeliveryConfirmationView(generics.GenericAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            order = get_object_or_404(
                Order.objects.select_for_update().filter(buyer=request.user),
                pk=kwargs['pk'],
            )
            if order.status == OrderStatus.DELIVERED:
                return Response(self.get_serializer(order).data)
            if order.status != OrderStatus.SHIPPED:
                raise ValidationError({'status': 'Only shipped orders can be confirmed as delivered.'})

            if order.payment_status != PaymentStatus.PAID:
                raise ValidationError({'payment': 'Pay the remaining order balance before confirming delivery.'})

            fulfillments = list(
                OrderFulfillment.objects.select_for_update().filter(order=order).exclude(
                    status=OrderStatus.CANCELLED,
                ).select_related('seller')
            )
            try:
                buyer_wallet = None
                if order.payment_method == PaymentMethod.WALLET:
                    buyer_wallet = Wallet.objects.get(user=request.user, currency=order.currency)
                else:
                    hold_mpesa_seller_proceeds(order)
                    fulfillments = list(
                        OrderFulfillment.objects.select_for_update().filter(order=order).exclude(
                            status=OrderStatus.CANCELLED,
                        ).select_related('seller')
                    )

                for fulfillment in fulfillments:
                    if fulfillment.settlement_status == SettlementStatus.RELEASED:
                        continue
                    seller_wallet, _ = Wallet.objects.get_or_create(
                        user=fulfillment.seller,
                        currency=order.currency,
                    )
                    if order.payment_method == PaymentMethod.WALLET:
                        transfer_escrow_to_wallet(
                            buyer_wallet,
                            seller_wallet,
                            fulfillment.seller_proceeds,
                            f'order:{order.pk}:seller:{fulfillment.seller_id}:settlement',
                        )
                    else:
                        release_escrow_hold(
                            seller_wallet,
                            fulfillment.seller_proceeds,
                            f'order:{order.pk}:seller:{fulfillment.seller_id}:release',
                        )
                    record_ledger_entry(
                        order=order,
                        user=fulfillment.seller,
                        entry_type=LedgerEntryType.SELLER_PROCEEDS_RELEASE,
                        amount=fulfillment.seller_proceeds,
                        reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:proceeds-release',
                    )
                    record_ledger_entry(
                        order=order,
                        user=fulfillment.seller,
                        entry_type=LedgerEntryType.COMMISSION_EARNED,
                        amount=fulfillment.commission_amount,
                        reference=f'order:{order.pk}:seller:{fulfillment.seller_id}:commission-earned',
                    )
                    fulfillment.status = OrderStatus.DELIVERED
                    fulfillment.settlement_status = SettlementStatus.RELEASED
                    fulfillment.save(update_fields=['status', 'settlement_status', 'updated_at'])

                if buyer_wallet is not None:
                    platform_amount = sum(
                        (item.shipping_cost + item.commission_amount for item in fulfillments),
                        Decimal('0.00'),
                    )
                    capture_escrow_hold(
                        buyer_wallet,
                        platform_amount,
                        f'order:{order.pk}:platform-capture',
                    )
            except (Wallet.DoesNotExist, WalletOperationError) as error:
                raise ValidationError({'payment': str(error)}) from error

            order.status = OrderStatus.DELIVERED
            order.save(update_fields=['status', 'updated_at'])

            create_notification(
                order.buyer,
                title='Order delivered',
                message=f'Your order {order.id} has been delivered. Please confirm the delivery.',
                notification_type=NotificationType.ORDER,
            )
            User = get_user_model()
            seller_ids = list(
                OrderItem.objects.filter(order=order).exclude(seller__isnull=True).values_list('seller_id', flat=True).distinct()
            )
            for seller_id in seller_ids:
                seller = User.objects.filter(pk=seller_id).first()
                if seller is not None:
                    create_notification(
                        seller,
                        title='Order delivered',
                        message=f'Order {order.id} has been delivered to the buyer.',
                        notification_type=NotificationType.ORDER,
                    )

        return Response(self.get_serializer(order).data)


class WishlistView(generics.GenericAPIView):
    serializer_class = WishlistSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        return wishlist

    def get(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)


class WishlistItemListCreateView(generics.ListCreateAPIView):
    serializer_class = WishlistItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        return WishlistItem.objects.filter(wishlist=wishlist).select_related('product')

    def create(self, request, *args, **kwargs):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save(wishlist=wishlist)
        except IntegrityError as exc:
            if 'wishlist_id' in str(exc) or 'product_id' in str(exc):
                raise ValidationError({'product': 'Product already in wishlist.'}) from exc
            raise
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class WishlistItemDeleteView(generics.DestroyAPIView):
    serializer_class = WishlistItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        return WishlistItem.objects.filter(wishlist=wishlist)
