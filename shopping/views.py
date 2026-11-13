from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.conf import settings
from datetime import timedelta
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP

from authapp.models import AdminAuditLog, StaffRole, RiderApplicationStatus, UserRole
from authapp.permissions import IsAuthenticated, IsBuyer, IsLogistics, IsSeller, IsSuperAdmin, IsVerifiedUser
from product_management.models import Product
from product_management.pricing import selected_option, item_price, item_stock, available_option_price

from payments.models import LedgerEntryType, Wallet, WalletBalanceSource, WalletTransactionType
from payments.services import (
    capture_escrow_hold,
    credit_wallet,
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
    PaymentOption, PaymentStatus, PickupPoint, SettlementStatus, ShipmentEvent, ShipmentStage, Wishlist, WishlistItem,
    DeliveryMethod, HomeDeliveryLeg, HomeDeliveryEvent, HomeDeliveryStage,
)
from .serializers import (
    CartSerializer,
    CartItemSerializer,
    ConfirmCheckoutSerializer,
    CreateCheckoutSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderStatusUpdateSerializer,
    SellerOrderDetailSerializer,
    SellerOrderListSerializer,
    PickupPointSerializer,
    LogisticsEventInputSerializer,
    LogisticsAssignmentInputSerializer,
    LogisticsShipmentSerializer,
    RiderHomeDeliverySerializer,
    RiderHomeDeliveryEventInputSerializer,
    SupportedShippingTownListSerializer,
    WishlistSerializer,
    WishlistItemSerializer,
)
from .shipping import SUPPORTED_TOWNS, ShippingQuoteError, quote_cart_items
from .geoapify import DoorstepQuoteError, quote_doorstep_leg, sign_doorstep_quote, read_doorstep_quote
from .locations import LocationError, find_current_home_location, find_home_locations
from .doorstep_access import doorstep_available_for


COMMISSION_RATE = Decimal('0.1000')
MONEY = Decimal('0.01')


class SupportedShippingTownListView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = SupportedShippingTownListSerializer

    def get(self, request, *args, **kwargs):
        return Response({'towns': list(SUPPORTED_TOWNS)})


class PickupPointListView(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PickupPointSerializer
    pagination_class = None

    def get_queryset(self):
        return PickupPoint.objects.filter(active=True)


class DoorstepLocationSearchView(generics.GenericAPIView):
    """Return signed, precise Kenyan place choices; never expose the provider key."""

    permission_classes = [IsAuthenticated, IsBuyer]
    throttle_scope = 'doorstep_address_search'

    def post(self, request, *args, **kwargs):
        if not doorstep_available_for(request.user):
            raise ValidationError({'delivery_method': 'Doorstep delivery is not available yet.'})
        point_id = request.data.get('pickup_point_id')
        try:
            point = PickupPoint.objects.filter(pk=point_id, active=True).first()
        except (ValueError, TypeError):
            point = None
        if point is None or point.latitude is None or point.longitude is None:
            raise ValidationError({'pickup_point_id': 'Choose a pickup point with a confirmed map pin.'})
        if 'latitude' in request.data or 'longitude' in request.data:
            try:
                location = find_current_home_location(
                    latitude=request.data.get('latitude'), longitude=request.data.get('longitude'),
                    accuracy_meters=request.data.get('accuracy_meters'),
                    pickup_point=point, user_id=request.user.pk,
                )
            except LocationError as error:
                raise ValidationError({'location': str(error)}) from error
            return Response({'locations': [location]})
        search = str(request.data.get('query') or '').strip()
        if len(search) < 5 or len(search) > 200:
            raise ValidationError({'query': 'Enter 5 to 200 characters of a street address or landmark.'})
        try:
            locations = find_home_locations(text=search, pickup_point=point, user_id=request.user.pk)
        except LocationError as error:
            raise ValidationError({'query': str(error)}) from error
        return Response({'locations': locations})


def _cart_items(cart):
    return list(
        CartItem.objects.filter(cart=cart).select_related(
            'product', 'product__store', 'product__store__owner',
        )
    )


def _checkout_items(cart, validated_data):
    product_id = validated_data.get('buy_now_product_id')
    if not product_id:
        return _cart_items(cart)
    product = Product.objects.filter(pk=product_id).select_related(
        'store', 'store__owner',
    ).first()
    if product is None:
        raise ValidationError({'buy_now_product_id': 'This product is no longer available.'})
    quantity = validated_data.get('buy_now_quantity', 1)
    option_id = validated_data.get('buy_now_option_id')
    try:
        option = selected_option(product, option_id)
    except ValueError as error:
        raise ValidationError({'buy_now_option_id': str(error)}) from error
    if product.status != 'published' or product.store.status != 'active':
        raise ValidationError({'buy_now_product_id': 'This product is not available for purchase.'})
    available = option['stock_quantity'] if option else product.stock_quantity
    if available < quantity:
        raise ValidationError({
            'buy_now_quantity': f'Only {available} items are available.',
        })
    return [CartItem(
        cart=cart,
        product=product,
        option_id=option_id,
        quantity=quantity,
    )]


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

        option_id = request.data.get('option_id') or None
        try:
            option = selected_option(product, option_id)
        except ValueError as error:
            raise ValidationError({'option_id': str(error)}) from error
        available = option['stock_quantity'] if option else product.stock_quantity
        if available == 0:
            raise ValidationError({'product': 'Product is out of stock.'})

        if quantity > available:
            raise ValidationError({'quantity': f'Only {available} items available.'})

        existing_item = CartItem.objects.filter(cart=cart, product=product, option_id=option_id).first()
        if existing_item:
            new_quantity = existing_item.quantity + quantity
            if new_quantity > available:
                raise ValidationError({'quantity': f'Total quantity {new_quantity} exceeds available stock {available}.'})
            existing_item.quantity = new_quantity
            existing_item.save()
            serializer = self.get_serializer(existing_item)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(cart=cart, product=product, option_id=option_id)
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

        try:
            available = item_stock(serializer.instance)
        except ValueError as error:
            raise ValidationError({'option_id': str(error)}) from error
        if quantity > available:
            raise ValidationError({'quantity': f'Only {available} items available.'})

        serializer.save()


class ShippingQuoteView(generics.GenericAPIView):
    serializer_class = CreateCheckoutSerializer
    permission_classes = [IsAuthenticated, IsBuyer]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart, _ = Cart.objects.get_or_create(buyer=request.user)
        cart_items = _checkout_items(cart, serializer.validated_data)
        if not cart_items:
            raise ValidationError({'cart': 'Cannot checkout with an empty cart.'})
        try:
            quotes = quote_cart_items(cart_items, serializer.validated_data['shipping_city'])
        except ValueError as error:
            raise ValidationError({'shipping_city': str(error)}) from error
        quote_payload = _shipping_quote_payload(quotes)
        point = serializer.validated_data.get('pickup_point')
        quote_payload['pickup_point_address'] = point.address if point else None
        quote_payload['pickup_delivery_cost'] = quote_payload['shipping_cost']
        quote_payload['doorstep_delivery_cost'] = Decimal('0.00')
        if serializer.validated_data['delivery_method'] == DeliveryMethod.DOORSTEP:
            home = serializer.validated_data['home_location']
            try:
                leg_quote = quote_doorstep_leg(
                    pickup_city=point.city, home_city=home['city'],
                    pickup_latitude=point.latitude, pickup_longitude=point.longitude,
                    home_latitude=home['latitude'], home_longitude=home['longitude'],
                )
            except DoorstepQuoteError as error:
                raise ValidationError({'home_location_token': str(error)}) from error
            quote_payload['doorstep_delivery_cost'] = leg_quote.fee
            quote_payload['doorstep_distance_meters'] = leg_quote.distance_meters
            quote_payload['shipping_cost'] += leg_quote.fee
            quote_payload['total_amount'] += leg_quote.fee
            quote_payload['expected_doorstep_quote_token'] = sign_doorstep_quote(
                quote=leg_quote, pickup_point=point, user_id=request.user.pk,
                location_token=serializer.validated_data['home_location_token'],
            )
        return Response(quote_payload)


class CheckoutView(generics.GenericAPIView):
    serializer_class = ConfirmCheckoutSerializer
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
        cart, _ = Cart.objects.get_or_create(buyer=buyer)
        cart_items = _checkout_items(cart, serializer.validated_data)
        if not cart_items:
            raise ValidationError({'cart': 'Cannot checkout with an empty cart.'})

        try:
            with transaction.atomic():
                pickup_point = serializer.validated_data.get('pickup_point')
                doorstep_quote = None
                if pickup_point:
                    pickup_point = PickupPoint.objects.select_for_update().filter(pk=pickup_point.pk, active=True).first()
                    if pickup_point is None:
                        raise ValidationError({'pickup_point_id': 'This pickup point is no longer available. Refresh your quote.'})
                    if serializer.validated_data['delivery_method'] == DeliveryMethod.PICKUP_POINT:
                        serializer.validated_data['shipping_address'] = pickup_point.address
                    serializer.validated_data['shipping_city'] = pickup_point.city
                    serializer.validated_data['shipping_postal_code'] = (
                        pickup_point.postal_code or 'N/A'
                        if serializer.validated_data['delivery_method'] == DeliveryMethod.PICKUP_POINT else 'N/A'
                    )
                    if serializer.validated_data['delivery_method'] == DeliveryMethod.DOORSTEP:
                        try:
                            doorstep_quote = read_doorstep_quote(
                                serializer.validated_data['expected_doorstep_quote_token'],
                                pickup_point=pickup_point, user_id=buyer.pk,
                                location_token=serializer.validated_data['home_location_token'],
                            )
                        except DoorstepQuoteError as error:
                            raise ValidationError({'quote': str(error)}) from error
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
                    item.product = product
                    try:
                        available = item_stock(item)
                    except ValueError as error:
                        raise ValidationError({'option_id': str(error)}) from error
                    if available < item.quantity:
                        raise ValidationError({'product': f'Insufficient stock for {product.name}. Only {available} available.'})

                locked_cart_items = []
                for item in cart_items:
                    item.product = products_locked[item.product.id]
                    locked_cart_items.append(item)
                try:
                    quotes = quote_cart_items(locked_cart_items, serializer.validated_data['shipping_city'])
                except ValueError as error:
                    raise ValidationError({'shipping_city': str(error)}) from error
                quote_totals = _shipping_quote_payload(quotes)
                if doorstep_quote is not None:
                    quote_totals['shipping_cost'] += doorstep_quote.fee
                    quote_totals['total_amount'] += doorstep_quote.fee
                if any(
                    serializer.validated_data[expected_field] != quote_totals[actual_field]
                    for expected_field, actual_field in (
                        ('expected_item_subtotal', 'item_subtotal'),
                        ('expected_shipping_cost', 'shipping_cost'),
                        ('expected_total_amount', 'total_amount'),
                    )
                ):
                    raise ValidationError({
                        'quote': 'The item or delivery price changed. Refresh the quote and review the new total before placing the order.',
                    })
                if pickup_point and serializer.validated_data['expected_pickup_point_address'] != pickup_point.address:
                    raise ValidationError({'quote': 'The pickup point changed. Refresh the quote and review the destination before placing the order.'})

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
                    delivery_method=serializer.validated_data['delivery_method'],
                    pickup_point=pickup_point,
                    shipping_address=serializer.validated_data['shipping_address'],
                    shipping_city=serializer.validated_data['shipping_city'],
                    shipping_postal_code=serializer.validated_data['shipping_postal_code'],
                    shipping_country=serializer.validated_data['shipping_country'],
                )

                if doorstep_quote is not None:
                    home = serializer.validated_data['home_location']
                    home_leg = HomeDeliveryLeg.objects.create(
                        order=order, pickup_point=pickup_point,
                        pickup_address_snapshot=pickup_point.address,
                        destination_address=home['address'], destination_city=home['city'],
                        delivery_instructions=serializer.validated_data.get('home_delivery_instructions', ''),
                        destination_latitude=Decimal(home['latitude']),
                        destination_longitude=Decimal(home['longitude']),
                        distance_meters=doorstep_quote.distance_meters,
                        fee=doorstep_quote.fee,
                    )
                    HomeDeliveryEvent.objects.create(
                        leg=home_leg, stage=HomeDeliveryStage.WAITING_FOR_PARCELS,
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
                    ShipmentEvent.objects.create(
                        fulfillment=fulfillments[seller_id], stage=ShipmentStage.ORDER_CREATED, source='system',
                    )
                    if payment_method == PaymentMethod.WALLET:
                        ShipmentEvent.objects.create(
                            fulfillment=fulfillments[seller_id], stage=ShipmentStage.PAYMENT_CONFIRMED, source='system',
                        )

                sellers = set()
                for cart_item in cart_items:
                    product = products_locked[cart_item.product.id]
                    option = selected_option(product, cart_item.option_id)
                    unit_price = item_price(cart_item)
                    line_total = (unit_price * cart_item.quantity).quantize(MONEY)
                    commission = (line_total * COMMISSION_RATE).quantize(MONEY, rounding=ROUND_HALF_UP)
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=cart_item.quantity,
                        price_at_purchase=unit_price,
                        price_unit_at_purchase=product.price_unit,
                        option_label_at_purchase=option['label'] if option else '',
                        option_id_at_purchase=cart_item.option_id,
                        seller=product.store.owner,
                        commission_rate=COMMISSION_RATE,
                        commission_amount=commission,
                        seller_proceeds=line_total - commission,
                    )
                    sellers.add(product.store.owner)
                    if option:
                        option['stock_quantity'] -= cart_item.quantity
                        product.stock_quantity -= cart_item.quantity
                        product.price = available_option_price(product.price_options, product.price)
                        product.save(update_fields=['price_options', 'price', 'stock_quantity', 'updated_at'])
                    else:
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
                        f'Your order {order.id} for KSh {order.total_amount:,.2f} '
                        f'(including KSh {order.shipping_cost:,.2f} delivery) has been placed and is being processed.'
                        if payment_method == PaymentMethod.WALLET
                        else f'Your order {order.id} is waiting for payment confirmation.'
                    ),
                    notification_type=NotificationType.ORDER,
                    email_preference='email_order_updates',
                    action_path=f'/account/orders/{order.pk}',
                )
                for seller in sellers:
                    create_notification(
                        seller,
                        title='New order received',
                        message=(
                            (f'You have a new paid order ({order.id}) ready for fulfillment. '
                             f'Logistics will collect from your dispatch location for {pickup_point.name}, '
                             f'{order.shipping_address}, {order.shipping_city}.')
                            if payment_method == PaymentMethod.WALLET and pickup_point else
                            f'You have a new paid order ({order.id}) ready for fulfillment.'
                            if payment_method == PaymentMethod.WALLET
                            else f'You have a new order ({order.id}) awaiting payment confirmation.'
                        ),
                        notification_type=NotificationType.ORDER,
                        email_preference='email_seller_sales' if payment_method == PaymentMethod.WALLET else None,
                        action_path=f'/sell/orders/{order.pk}',
                    )

                if not serializer.validated_data.get('buy_now_product_id'):
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
        return Order.objects.filter(buyer=self.request.user).prefetch_related('items', 'fulfillments__events')


class OrderListView(generics.ListAPIView):
    serializer_class = OrderListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(buyer=self.request.user).prefetch_related('items', 'fulfillments__events').order_by('-created_at')


class SellerOrderListView(generics.ListAPIView):
    """Lists all orders that contain items sold by the requesting seller."""
    serializer_class = SellerOrderListSerializer
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
            seller_order_ids = OrderItem.objects.filter(
                seller=request.user,
            ).values('order_id')
            order = get_object_or_404(
                Order.objects.select_for_update().filter(pk__in=seller_order_ids),
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
                ShipmentEvent.objects.create(
                    fulfillment=fulfillment,
                    stage=ShipmentStage.AWAITING_COLLECTION if order.pickup_point_id else ShipmentStage.SHIPPED,
                    actor=request.user, source='seller',
                )
            else:
                cancelled_total = fulfillment.shipping_cost + fulfillment.item_subtotal
                refundable_amount = fulfillment.shipping_cost + (
                    fulfillment.item_subtotal
                    if order.payment_status == PaymentStatus.PAID
                    else Decimal('0.00')
                )
                refund_credited = False
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
                    refund_credited = True
                elif order.amount_paid > 0:
                    if fulfillment.settlement_status == SettlementStatus.HELD:
                        try:
                            seller_wallet = Wallet.objects.get(user=request.user, currency=order.currency)
                            capture_escrow_hold(
                                seller_wallet,
                                fulfillment.seller_proceeds,
                                f'order:{order.pk}:seller:{request.user.pk}:hold-reversal',
                                WalletBalanceSource.EARNINGS,
                            )
                        except (Wallet.DoesNotExist, WalletOperationError) as error:
                            raise ValidationError({'payment': str(error)}) from error
                    buyer_wallet, _ = Wallet.objects.get_or_create(
                        user=order.buyer,
                        currency=order.currency,
                    )
                    credit_wallet(
                        buyer_wallet,
                        refundable_amount,
                        f'order:{order.pk}:seller:{request.user.pk}:refund-credit',
                        WalletTransactionType.REFUND,
                        WalletBalanceSource.SHOPPING,
                    )
                    record_ledger_entry(
                        order=order,
                        user=order.buyer,
                        entry_type=LedgerEntryType.REFUND_COMPLETED,
                        amount=refundable_amount,
                        reference=f'order:{order.pk}:seller:{request.user.pk}:refund-completed',
                        metadata={'provider': order.payment_method, 'destination': 'shopping_wallet'},
                    )
                    refund_credited = True

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
                restock_products = {
                    product.id: product for product in Product.objects.select_for_update().filter(
                        id__in=[item.product_id for item in items if item.product_id],
                    )
                }
                for item in items:
                    if item.product_id:
                        product = restock_products[item.product_id]
                        if item.option_id_at_purchase:
                            option = next((option for option in product.price_options if option['id'] == str(item.option_id_at_purchase)), None)
                            if option:
                                option['stock_quantity'] += item.quantity
                                if option.get('active', True):
                                    product.stock_quantity += item.quantity
                        else:
                            product.stock_quantity += item.quantity
                for product in restock_products.values():
                    if product.price_options:
                        product.price = available_option_price(product.price_options, product.price)
                    product.save(update_fields=['price_options', 'price', 'stock_quantity', 'updated_at'])
                fulfillment.status = OrderStatus.CANCELLED
                fulfillment.settlement_status = SettlementStatus.REVERSED
                fulfillment.save(update_fields=['status', 'settlement_status', 'updated_at'])
                ShipmentEvent.objects.create(
                    fulfillment=fulfillment, stage=ShipmentStage.CANCELLED,
                    actor=request.user, source='seller',
                )
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
            if not active_states and order.delivery_method == DeliveryMethod.DOORSTEP:
                home_leg = HomeDeliveryLeg.objects.select_for_update().filter(order=order).first()
                if home_leg and home_leg.status != HomeDeliveryStage.CANCELLED:
                    home_refund = min(home_leg.fee, order.amount_paid)
                    if home_refund > 0:
                        if order.payment_method == PaymentMethod.WALLET:
                            try:
                                buyer_wallet = Wallet.objects.get(user=order.buyer, currency=order.currency)
                                release_escrow_hold(
                                    buyer_wallet, home_refund,
                                    f'order:{order.pk}:home-leg:buyer-refund',
                                )
                            except (Wallet.DoesNotExist, WalletOperationError) as error:
                                raise ValidationError({'payment': str(error)}) from error
                        else:
                            buyer_wallet, _ = Wallet.objects.get_or_create(user=order.buyer, currency=order.currency)
                            try:
                                credit_wallet(
                                    buyer_wallet, home_refund,
                                    f'order:{order.pk}:home-leg:refund-credit',
                                    WalletTransactionType.REFUND, WalletBalanceSource.SHOPPING,
                                )
                            except WalletOperationError as error:
                                raise ValidationError({'payment': str(error)}) from error
                        record_ledger_entry(
                            order=order, user=order.buyer, entry_type=LedgerEntryType.REFUND_COMPLETED,
                            amount=home_refund, reference=f'order:{order.pk}:home-leg:refund-completed',
                            metadata={'provider': order.payment_method, 'destination': 'shopping_wallet'},
                        )
                        refund_credited = True
                    order.shipping_cost = max(Decimal('0.00'), order.shipping_cost - home_leg.fee)
                    order.total_amount = max(Decimal('0.00'), order.total_amount - home_leg.fee)
                    order.amount_paid = max(Decimal('0.00'), order.amount_paid - home_refund)
                    refundable_amount += home_refund
                    home_leg.status = HomeDeliveryStage.CANCELLED
                    home_leg.save(update_fields=['status'])
                    HomeDeliveryEvent.objects.create(
                        leg=home_leg, stage=HomeDeliveryStage.CANCELLED, actor=request.user,
                    )
            if not active_states:
                order.status = OrderStatus.CANCELLED
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
                    message=(f'Items from {request.user.full_name} in order {order.id} have shipped '
                             f'via {fulfillment.courier_name}. Tracking number: {fulfillment.tracking_number}.'),
                    notification_type=NotificationType.ORDER,
                    email_preference='email_order_updates',
                    action_path=f'/account/orders/{order.pk}',
                )
            elif target_status == OrderStatus.CANCELLED:
                create_notification(
                    order.buyer,
                    title='Order cancelled',
                    message=f'Items from {request.user.full_name} in order {order.id} were cancelled.',
                    notification_type=NotificationType.ORDER,
                    email_preference='email_order_updates',
                    action_path=f'/account/orders/{order.pk}',
                )
                if refund_credited and refundable_amount > 0:
                    create_notification(
                        order.buyer,
                        title='Refund credited to shopping wallet',
                        message=f'KSh {refundable_amount:,.2f} from order {order.pk} is available in your shopping wallet.',
                        notification_type=NotificationType.SYSTEM,
                        email_preference='email_wallet_updates',
                        action_path='/account/wallet',
                    )

        return Response(SellerOrderDetailSerializer(order, context={'request': request}).data)


class SellerOrderStatusView(SellerOrderStatusUpdateView):
    serializer_class = OrderStatusUpdateSerializer


class LogisticsPickupEventView(generics.GenericAPIView):
    """Record verified first-leg milestones without assuming physical movement."""
    serializer_class = LogisticsEventInputSerializer
    permission_classes = [IsLogistics]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = serializer.validated_data['stage']
        previous_stage = {
            ShipmentStage.COLLECTED_FROM_SELLER: ShipmentStage.AWAITING_COLLECTION,
            ShipmentStage.IN_TRANSIT: ShipmentStage.COLLECTED_FROM_SELLER,
            ShipmentStage.ARRIVED_AT_PICKUP: ShipmentStage.IN_TRANSIT,
        }[stage]
        with transaction.atomic():
            fulfillment = get_object_or_404(
                OrderFulfillment.objects.select_for_update().select_related('order'), pk=kwargs['pk'],
            )
            if not fulfillment.order.pickup_point_id or fulfillment.status != OrderStatus.SHIPPED:
                raise ValidationError({'stage': 'This is not an active pickup-point shipment.'})
            if not request.user.is_super_admin and fulfillment.assigned_logistics_id != request.user.pk:
                raise PermissionDenied('This shipment is not assigned to you.')
            stages = set(fulfillment.events.values_list('stage', flat=True))
            if stage in stages:
                return Response({'stage': stage, 'already_recorded': True})
            if previous_stage not in stages:
                raise ValidationError({'stage': 'Record the preceding shipment milestone first.'})
            event = ShipmentEvent.objects.create(
                fulfillment=fulfillment, stage=stage, actor=request.user,
                source='logistics', note=serializer.validated_data.get('note', ''),
            )
        return Response({'id': event.id, 'stage': event.stage, 'created_at': event.created_at}, status=status.HTTP_201_CREATED)


class LogisticsShipmentPagination(LimitOffsetPagination):
    default_limit = 30
    max_limit = 100


class LogisticsShipmentListView(generics.ListAPIView):
    serializer_class = LogisticsShipmentSerializer
    permission_classes = [IsLogistics]
    pagination_class = LogisticsShipmentPagination

    def get_queryset(self):
        queryset = OrderFulfillment.objects.filter(
            order__pickup_point__isnull=False,
            status__in=[OrderStatus.PROCESSING, OrderStatus.SHIPPED],
        ).select_related('order__pickup_point', 'order__home_delivery', 'seller__store').prefetch_related('events').order_by('-created_at')
        if not self.request.user.is_super_admin:
            queryset = queryset.filter(assigned_logistics=self.request.user)
        return queryset


class LogisticsAssignmentView(generics.GenericAPIView):
    serializer_class = LogisticsAssignmentInputSerializer
    permission_classes = [IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            fulfillment = get_object_or_404(
                OrderFulfillment.objects.select_for_update().select_related('order'), pk=kwargs['pk'],
            )
            if not fulfillment.order.pickup_point_id or fulfillment.status in {OrderStatus.CANCELLED, OrderStatus.DELIVERED}:
                raise ValidationError({'fulfillment': 'Only active pickup-point shipments can be assigned.'})
            matches = list(get_user_model().objects.filter(
                email__iexact=serializer.validated_data['email'], is_active=True,
            ).order_by('date_joined')[:2])
            if len(matches) != 1:
                raise ValidationError({'email': 'Enter the email of one active logistics staff account.'})
            assignee = matches[0]
            if not assignee.staff_role_assignments.filter(role=StaffRole.LOGISTICS).exists():
                raise ValidationError({'email': 'Assign the logistics staff role before assigning a shipment.'})
            previous_id = fulfillment.assigned_logistics_id
            if previous_id != assignee.pk:
                fulfillment.assigned_logistics = assignee
                fulfillment.save(update_fields=['assigned_logistics', 'updated_at'])
                AdminAuditLog.objects.create(
                    actor=request.user, action='shipment_logistics_assigned',
                    target_type='order_fulfillment', target_id=str(fulfillment.pk),
                    reason=serializer.validated_data['reason'],
                    metadata={'previous_assignee_id': str(previous_id) if previous_id else '', 'assignee_id': str(assignee.pk)},
                )
                create_notification(assignee, title='Seller collection assigned',
                    message=f'Order {str(fulfillment.order_id)[:8]}: open Logistics for your collection details.')
        return Response({'fulfillment_id': fulfillment.pk, 'assigned_logistics_id': assignee.pk})


class RiderHomeDeliveryListView(generics.ListAPIView):
    serializer_class = RiderHomeDeliverySerializer
    permission_classes = [IsLogistics]
    pagination_class = LogisticsShipmentPagination

    def get_queryset(self):
        queryset = HomeDeliveryLeg.objects.filter(order__delivery_fee_paid=True).exclude(
            status__in=[HomeDeliveryStage.CANCELLED, HomeDeliveryStage.BUYER_CONFIRMED],
        ).select_related('order__buyer', 'pickup_point').prefetch_related('events').order_by('-created_at')
        if not self.request.user.is_super_admin:
            queryset = queryset.filter(assigned_rider=self.request.user)
        return queryset


class RiderHomeDeliveryAssignmentView(generics.GenericAPIView):
    serializer_class = LogisticsAssignmentInputSerializer
    permission_classes = [IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            leg = get_object_or_404(HomeDeliveryLeg.objects.select_for_update().select_related('order'),
                                    order_id=kwargs['pk'])
            if leg.status not in {HomeDeliveryStage.WAITING_FOR_PARCELS, HomeDeliveryStage.RIDER_ASSIGNED} or leg.order.status in {OrderStatus.CANCELLED, OrderStatus.DELIVERED}:
                raise ValidationError({'status': 'Only waiting or unaccepted home deliveries can be assigned.'})
            if not leg.order.delivery_fee_paid:
                raise ValidationError({'payment': 'Wait for confirmed delivery payment before assigning a rider.'})
            matches = list(get_user_model().objects.filter(
                email__iexact=serializer.validated_data['email'], is_active=True,
            ).order_by('date_joined')[:2])
            if len(matches) != 1 or not matches[0].staff_role_assignments.filter(role=StaffRole.LOGISTICS).exists():
                raise ValidationError({'email': 'Choose one active logistics staff account.'})
            rider = matches[0]
            if not rider.is_verified or not getattr(getattr(rider, 'rider_application', None), 'status', None) == RiderApplicationStatus.APPROVED:
                raise ValidationError({'email': 'This rider must have an approved rider application.'})
            if rider.rider_application.city.strip().casefold() != leg.pickup_point.city.strip().casefold():
                raise ValidationError({'email': 'The rider must be approved for this pickup point city.'})
            if leg.assigned_rider_id == rider.pk and leg.status == HomeDeliveryStage.RIDER_ASSIGNED and leg.accept_by and leg.accept_by > timezone.now():
                return Response({'order_id': leg.order_id, 'assigned_rider_id': rider.pk, 'already_assigned': True})
            previous_id = leg.assigned_rider_id
            leg.assigned_rider = rider
            leg.status = HomeDeliveryStage.RIDER_ASSIGNED
            leg.accept_by = timezone.now() + timedelta(minutes=30)
            leg.save(update_fields=['assigned_rider', 'status', 'accept_by'])
            HomeDeliveryEvent.objects.create(leg=leg, stage=HomeDeliveryStage.RIDER_ASSIGNED, actor=request.user)
            AdminAuditLog.objects.create(
                actor=request.user, action='home_delivery_rider_assigned',
                target_type='home_delivery_leg', target_id=str(leg.order_id),
                reason=serializer.validated_data['reason'],
                metadata={'assignee_id': str(rider.pk), 'previous_assignee_id': str(previous_id) if previous_id else ''},
            )
            if previous_id and previous_id != rider.pk:
                create_notification(get_user_model().objects.get(pk=previous_id), title='Delivery assignment changed',
                                    message='A delivery previously assigned to you has been reassigned.')
            rider_notice = create_notification(rider, title='New delivery assigned',
                message=f'Order {str(leg.order_id)[:8]}: a paid doorstep delivery is ready for your review. Open Logistics and accept it before the deadline.')
            from notifications.sms import queue_rider_assignment_sms, dispatch_notification_sms
            if settings.SMS_NOTIFICATIONS_ENABLED:
                sms = queue_rider_assignment_sms(rider_notice)
                if sms:
                    transaction.on_commit(lambda delivery_id=sms.pk: dispatch_notification_sms(delivery_id))
        return Response({'order_id': leg.order_id, 'assigned_rider_id': rider.pk})


class RiderHomeDeliveryEventView(generics.GenericAPIView):
    serializer_class = RiderHomeDeliveryEventInputSerializer
    permission_classes = [IsLogistics]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = serializer.validated_data['stage']
        preceding = {
            HomeDeliveryStage.RIDER_ACCEPTED: HomeDeliveryStage.RIDER_ASSIGNED,
            HomeDeliveryStage.RIDER_DECLINED: HomeDeliveryStage.RIDER_ASSIGNED,
            HomeDeliveryStage.COLLECTED_FROM_PICKUP: HomeDeliveryStage.RIDER_ACCEPTED,
            HomeDeliveryStage.RIDER_DELIVERED: HomeDeliveryStage.COLLECTED_FROM_PICKUP,
        }[stage]
        with transaction.atomic():
            leg = get_object_or_404(HomeDeliveryLeg.objects.select_for_update().select_related('order'),
                                    order_id=kwargs['pk'])
            if leg.assigned_rider_id != request.user.pk:
                raise PermissionDenied('This home delivery is not assigned to you.')
            if not leg.order.delivery_fee_paid:
                raise ValidationError({'payment': 'Wait for confirmed delivery payment before updating this delivery.'})
            if leg.status == stage:
                return Response({'stage': stage, 'already_recorded': True})
            if stage == HomeDeliveryStage.RIDER_DECLINED and leg.status == HomeDeliveryStage.RIDER_ACCEPTED:
                preceding = HomeDeliveryStage.RIDER_ACCEPTED
            if leg.status != preceding or leg.order.status in {OrderStatus.CANCELLED, OrderStatus.DELIVERED}:
                raise ValidationError({'stage': 'Complete the preceding delivery milestone first.'})
            if stage == HomeDeliveryStage.RIDER_ACCEPTED and leg.accept_by and timezone.now() > leg.accept_by:
                raise ValidationError({'stage': 'The acceptance window has expired. Contact logistics for reassignment.'})
            if stage == HomeDeliveryStage.COLLECTED_FROM_PICKUP:
                active = OrderFulfillment.objects.filter(order=leg.order).exclude(status=OrderStatus.CANCELLED)
                if not active.exists() or any(
                    not fulfillment.events.filter(stage=ShipmentStage.ARRIVED_AT_PICKUP).exists()
                    for fulfillment in active
                ):
                    raise ValidationError({'stage': 'All parcels must arrive at the pickup point first.'})
            if stage == HomeDeliveryStage.RIDER_DECLINED:
                leg.status = HomeDeliveryStage.WAITING_FOR_PARCELS
                leg.assigned_rider = None
                leg.accept_by = None
                leg.save(update_fields=['status', 'assigned_rider', 'accept_by'])
                AdminAuditLog.objects.create(actor=request.user, action='home_delivery_rider_declined',
                    target_type='home_delivery_leg', target_id=str(leg.order_id),
                    reason=serializer.validated_data.get('note', ''), metadata={})
                for administrator in get_user_model().objects.filter(is_active=True, role=UserRole.SUPER_ADMIN):
                    create_notification(administrator, title='Delivery declined',
                        message=f'Order {str(leg.order_id)[:8]} needs another rider. Open Logistics to reassign it.')
            else:
                leg.status = stage
                leg.save(update_fields=['status'])
            event = HomeDeliveryEvent.objects.create(
                leg=leg, stage=stage, actor=request.user,
                note=serializer.validated_data.get('note', ''),
            )
            if stage in {HomeDeliveryStage.COLLECTED_FROM_PICKUP, HomeDeliveryStage.RIDER_DELIVERED}:
                create_notification(
                    leg.order.buyer,
                    title='Doorstep delivery update',
                    message=(
                        'Your parcel is out for doorstep delivery.' if stage == HomeDeliveryStage.COLLECTED_FROM_PICKUP
                        else 'The rider reported delivery. Confirm receipt in your order details after checking your parcel.'
                    ),
                    notification_type=NotificationType.ORDER,
                    email_preference='email_order_updates',
                    action_path=f'/account/orders/{leg.order_id}',
                )
        return Response({'id': event.id, 'stage': event.stage, 'created_at': event.created_at}, status=status.HTTP_201_CREATED)


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
            if order.pickup_point_id and any(
                not fulfillment.events.filter(stage=ShipmentStage.ARRIVED_AT_PICKUP).exists()
                for fulfillment in fulfillments
            ):
                raise ValidationError({'status': 'All parcels must be recorded at the pickup point before collection can be confirmed.'})
            home_leg = None
            if order.delivery_method == DeliveryMethod.DOORSTEP:
                home_leg = HomeDeliveryLeg.objects.select_for_update().filter(order=order).first()
                if home_leg is None or home_leg.status != HomeDeliveryStage.RIDER_DELIVERED:
                    raise ValidationError({'status': 'Wait for the rider to report doorstep delivery before confirming.'})
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
                            WalletBalanceSource.EARNINGS,
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
                    ShipmentEvent.objects.create(
                        fulfillment=fulfillment,
                        stage=ShipmentStage.COLLECTED_BY_BUYER if order.delivery_method == DeliveryMethod.PICKUP_POINT else ShipmentStage.DELIVERED,
                        actor=request.user, source='buyer',
                    )
                    create_notification(
                        fulfillment.seller,
                        title='Seller earnings released',
                        message=(f'KSh {fulfillment.seller_proceeds:,.2f} from order {order.pk} '
                                 'is now available in your seller earnings wallet.'),
                        notification_type=NotificationType.ORDER,
                        email_preference='email_wallet_updates',
                        action_path='/sell/earnings',
                    )

                if buyer_wallet is not None:
                    platform_amount = sum(
                        (item.shipping_cost + item.commission_amount for item in fulfillments),
                        Decimal('0.00'),
                    )
                    if home_leg is not None:
                        platform_amount += home_leg.fee
                    capture_escrow_hold(
                        buyer_wallet,
                        platform_amount,
                        f'order:{order.pk}:platform-capture',
                    )
                if home_leg is not None:
                    home_leg.status = HomeDeliveryStage.BUYER_CONFIRMED
                    home_leg.save(update_fields=['status'])
                    HomeDeliveryEvent.objects.create(
                        leg=home_leg, stage=HomeDeliveryStage.BUYER_CONFIRMED, actor=request.user,
                    )
            except (Wallet.DoesNotExist, WalletOperationError) as error:
                raise ValidationError({'payment': str(error)}) from error

            order.status = OrderStatus.DELIVERED
            order.save(update_fields=['status', 'updated_at'])

            create_notification(
                order.buyer,
                title='Order delivered',
                message=f'Delivery was confirmed for your order {order.id}.',
                notification_type=NotificationType.ORDER,
                email_preference='email_order_updates',
                action_path=f'/account/orders/{order.pk}',
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
