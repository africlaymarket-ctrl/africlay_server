from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Count
from decimal import Decimal

from authapp.permissions import IsAuthenticated, IsSeller, IsVerifiedUser
from product_management.models import Product

from .models import Cart, CartItem, Order, OrderItem, OrderStatus, Wishlist, WishlistItem
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
                    status=OrderStatus.PENDING,
                    shipping_address=serializer.validated_data['shipping_address'],
                    shipping_city=serializer.validated_data['shipping_city'],
                    shipping_postal_code=serializer.validated_data['shipping_postal_code'],
                    shipping_country=serializer.validated_data['shipping_country'],
                )

                # Create order items and decrement stock
                for cart_item in cart_items:
                    product = products_locked[cart_item.product.id]
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=cart_item.quantity,
                        price_at_purchase=product.price,
                        seller=product.store.owner,
                    )
                    # Decrement stock
                    product.stock_quantity -= cart_item.quantity
                    product.save(update_fields=['stock_quantity', 'updated_at'])

                # Clear cart
                CartItem.objects.filter(cart=cart).delete()

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
