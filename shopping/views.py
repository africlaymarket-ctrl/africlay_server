from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.db.models import Count
from decimal import Decimal

from authapp.permissions import IsAuthenticated, IsSeller, IsVerifiedUser
from product_management.models import Product

from payments.models import Wallet
from payments.services import (
    WalletOperationError,
    place_escrow_hold,
    release_escrow_hold,
    transfer_escrow_to_wallet,
)
from notifications.models import NotificationType, create_notification

from .models import Cart, CartItem, Order, OrderItem, OrderStatus, PaymentMethod, PaymentStatus, Wishlist, WishlistItem
from .serializers import CartSerializer, CartItemSerializer, CreateCheckoutSerializer, OrderDetailSerializer, OrderStatusUpdateSerializer, WishlistSerializer, WishlistItemSerializer


class CartDetailView(generics.GenericAPIView):
    serializer_class = CartSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        cart, _ = Cart.objects.get_or_create(buyer=self.request.user)
        return cart

    def get(self, request, *args, **kwargs):
        cart = self.get_object()
        serializer = self.get_serializer(cart)
        return Response(serializer.data)


class CartItemListCreateView(generics.ListCreateAPIView):
    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]
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


class CheckoutView(generics.GenericAPIView):
    serializer_class = CreateCheckoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        """Atomic checkout transaction: validate cart, lock stock, create order, clear cart."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment_method = serializer.validated_data.get('payment_method', '')
        if payment_method and payment_method != PaymentMethod.WALLET:
            raise ValidationError({'payment_method': 'This payment provider is not configured.'})

        buyer = request.user

        try:
            cart = Cart.objects.get(buyer=buyer)
        except Cart.DoesNotExist:
            raise ValidationError({'cart': 'Cart not found.'})

        cart_items = list(CartItem.objects.filter(cart=cart).select_related('product'))
        if not cart_items:
            raise ValidationError({'cart': 'Cannot checkout with an empty cart.'})

        # Start atomic transaction
        try:
            with transaction.atomic():
                # Lock products for stock validation
                product_ids = [item.product.id for item in cart_items]
                products_locked = {
                    product.id: product
                    for product in Product.objects.select_for_update().filter(id__in=product_ids)
                }

                # Validate all items have sufficient stock and are purchasable
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

                # Calculate total amount
                total_amount = Decimal('0.00')
                for item in cart_items:
                    total_amount += item.product.price * item.quantity

                # Create order
                order = Order.objects.create(
                    buyer=buyer,
                    total_amount=total_amount,
                    currency='KES',
                    status=OrderStatus.PROCESSING if payment_method == PaymentMethod.WALLET else OrderStatus.PENDING,
                    payment_method=payment_method,
                    payment_status=PaymentStatus.PAID if payment_method == PaymentMethod.WALLET else PaymentStatus.PENDING,
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
                        total_amount,
                        f'order:{order.pk}:escrow-hold',
                    )

                # Create order items and decrement stock
                sellers = set()
                for cart_item in cart_items:
                    product = products_locked[cart_item.product.id]
                    order_item = OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=cart_item.quantity,
                        price_at_purchase=product.price,
                        seller=product.store.owner,
                    )
                    sellers.add(product.store.owner)
                    # Decrement stock
                    product.stock_quantity -= cart_item.quantity
                    product.save(update_fields=['stock_quantity', 'updated_at'])

                create_notification(
                    buyer,
                    title='Order placed',
                    message=f'Your order {order.id} has been placed successfully and is being processed.',
                    notification_type=NotificationType.ORDER,
                )
                for seller in sellers:
                    create_notification(
                        seller,
                        title='New order received',
                        message=f'You have a new order ({order.id}) waiting for fulfillment.',
                        notification_type=NotificationType.ORDER,
                    )

                # Clear cart
                CartItem.objects.filter(cart=cart).delete()

        except WalletOperationError as e:
            raise ValidationError({'payment': str(e)})
        except IntegrityError as e:
            # Handle database constraint violations
            if 'stock_quantity' in str(e):
                raise ValidationError({'product': 'Product stock level is invalid. This may indicate concurrent checkout attempts.'})
            raise

        # Serialize and return the created order
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
    serializer_class = OrderDetailSerializer
    permission_classes = [IsSeller, IsVerifiedUser]

    def get_queryset(self):
        order_ids = OrderItem.objects.filter(seller=self.request.user).values_list('order_id', flat=True)
        return Order.objects.filter(id__in=order_ids).annotate(
            seller_count=Count('items__seller', distinct=True),
        ).filter(seller_count=1).prefetch_related('items').order_by('-created_at')


class SellerOrderStatusUpdateView(generics.UpdateAPIView):
    """Allows a seller to update the status of an order containing their items."""
    serializer_class = OrderStatusUpdateSerializer
    permission_classes = [IsSeller, IsVerifiedUser]
    http_method_names = ['patch']

    def get_queryset(self):
        order_ids = OrderItem.objects.filter(seller=self.request.user).values_list('order_id', flat=True)
        return Order.objects.filter(id__in=order_ids).annotate(
            seller_count=Count('items__seller', distinct=True),
        ).filter(seller_count=1)

    def perform_update(self, serializer):
        target_status = serializer.validated_data['status']
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=serializer.instance.pk)
            if order.status != OrderStatus.PROCESSING:
                raise ValidationError({'status': 'Only processing orders can be shipped or cancelled.'})
            if target_status not in {OrderStatus.SHIPPED, OrderStatus.CANCELLED}:
                raise ValidationError({'status': 'Sellers can only ship or cancel processing orders.'})

            payment_status = order.payment_status
            if target_status == OrderStatus.SHIPPED:
                if payment_status != PaymentStatus.PAID:
                    raise ValidationError({'payment': 'An order must be paid before it can ship.'})
                courier_name = (serializer.validated_data.get('courier_name') or '').strip()
                tracking_number = (serializer.validated_data.get('tracking_number') or '').strip()
                shipping_cost = serializer.validated_data.get('shipping_cost')
                if not courier_name or not tracking_number or shipping_cost is None:
                    raise ValidationError({
                        'shipping': 'Courier name, tracking number, and shipping cost are required before shipping the order.'
                    })
            else:
                if order.payment_method == PaymentMethod.WALLET and payment_status == PaymentStatus.PAID:
                    try:
                        wallet = Wallet.objects.get(user=order.buyer, currency=order.currency)
                        release_escrow_hold(
                            wallet,
                            order.total_amount,
                            f'order:{order.pk}:escrow-release',
                        )
                    except (Wallet.DoesNotExist, WalletOperationError) as error:
                        raise ValidationError({'payment': str(error)}) from error
                    payment_status = PaymentStatus.REFUNDED

                items = list(
                    OrderItem.objects.select_for_update()
                    .filter(order=order)
                    .select_related('product')
                )
                for item in items:
                    if item.product_id:
                        item.product.stock_quantity += item.quantity
                        item.product.save(update_fields=['stock_quantity', 'updated_at'])

            serializer.instance = order
            update_fields = {'status': target_status, 'payment_status': payment_status}
            if target_status == OrderStatus.SHIPPED:
                update_fields.update({
                    'courier_name': (serializer.validated_data.get('courier_name') or '').strip(),
                    'tracking_number': (serializer.validated_data.get('tracking_number') or '').strip(),
                    'shipping_cost': serializer.validated_data.get('shipping_cost'),
                    'shipped_at': order.updated_at,
                })
            serializer.save(**update_fields)

            if target_status == OrderStatus.SHIPPED:
                create_notification(
                    order.buyer,
                    title='Order shipped',
                    message=f'Your order {order.id} has been shipped via {update_fields["courier_name"]} and is on the way.',
                    notification_type=NotificationType.ORDER,
                )
            elif target_status == OrderStatus.CANCELLED:
                create_notification(
                    order.buyer,
                    title='Order cancelled',
                    message=f'Your order {order.id} has been cancelled and any escrow is being released.',
                    notification_type=NotificationType.ORDER,
                )


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

            if order.payment_method == PaymentMethod.WALLET and order.payment_status == PaymentStatus.PAID:
                seller_ids = set(
                    OrderItem.objects.filter(order=order).values_list('seller_id', flat=True).distinct()
                )
                if len(seller_ids) != 1 or None in seller_ids:
                    raise ValidationError({'seller': 'Settlement requires exactly one active seller.'})
                try:
                    buyer_wallet = Wallet.objects.get(user=request.user, currency=order.currency)
                    seller_wallet, _ = Wallet.objects.get_or_create(
                        user_id=seller_ids.pop(),
                        currency=order.currency,
                    )
                    transfer_escrow_to_wallet(
                        buyer_wallet,
                        seller_wallet,
                        order.total_amount,
                        f'order:{order.pk}:seller-settlement',
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
