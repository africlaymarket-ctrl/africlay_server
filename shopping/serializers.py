from decimal import Decimal

from rest_framework import serializers

from .models import Cart, CartItem, Order, OrderItem, OrderStatus, PaymentMethod, Wishlist, WishlistItem


class CartItemSerializer(serializers.ModelSerializer):
    product = serializers.UUIDField(read_only=True, source='product_id')
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    product_price = serializers.DecimalField(source='product.price', read_only=True, max_digits=10, decimal_places=2)

    class Meta:
        model = CartItem
        fields = ['id', 'product', 'product_name', 'product_slug', 'product_price', 'quantity']
        read_only_fields = ['id', 'product']


class CartSerializer(serializers.ModelSerializer):
    buyer = serializers.UUIDField(source='buyer_id', read_only=True)
    items = CartItemSerializer(many=True, read_only=True)

    class Meta:
        model = Cart
        fields = ['id', 'buyer', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'buyer', 'items', 'created_at', 'updated_at']


class OrderItemDetailSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source='product.id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    seller_id = serializers.UUIDField(source='seller.id', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product_id', 'product_name', 'product_slug', 'quantity', 'price_at_purchase', 'seller_id']
        read_only_fields = fields


class OrderDetailSerializer(serializers.ModelSerializer):
    buyer_id = serializers.UUIDField(source='buyer.id', read_only=True)
    items = OrderItemDetailSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'buyer_id', 'total_amount', 'currency', 'status', 'payment_method', 'payment_status',
            'shipping_address', 'shipping_city', 'shipping_postal_code', 'shipping_country',
            'courier_name', 'tracking_number', 'shipping_cost', 'shipped_at',
            'items', 'created_at', 'updated_at'
        ]
        read_only_fields = fields


class SellerOrderDetailSerializer(OrderDetailSerializer):
    items = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    def get_total_amount(self, order):
        seller = self.context['request'].user
        return sum(
            (item.price_at_purchase * item.quantity for item in order.items.filter(seller=seller)),
            Decimal('0.00'),
        )

    def get_items(self, order):
        seller = self.context['request'].user
        seller_items = order.items.filter(seller=seller).select_related('product', 'seller')
        return OrderItemDetailSerializer(seller_items, many=True).data


class SellerOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['processing', 'shipped', 'delivered'])


class CreateCheckoutSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(required=True, max_length=500)
    shipping_city = serializers.CharField(required=True, max_length=100)
    shipping_postal_code = serializers.CharField(required=True, max_length=20)
    shipping_country = serializers.CharField(required=True, max_length=100)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, required=False)

    def validate_shipping_address(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError('Shipping address cannot be empty.')
        if len(value.strip()) < 5:
            raise serializers.ValidationError('Shipping address must be at least 5 characters.')
        return value

    def validate_shipping_city(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError('Shipping city cannot be empty.')
        if len(value.strip()) < 2:
            raise serializers.ValidationError('Shipping city must be at least 2 characters.')
        return value

    def validate_shipping_postal_code(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError('Shipping postal code cannot be empty.')
        if len(value.strip()) < 2:
            raise serializers.ValidationError('Shipping postal code must be at least 2 characters.')
        return value

    def validate_shipping_country(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError('Shipping country cannot be empty.')
        if len(value.strip()) < 2:
            raise serializers.ValidationError('Shipping country must be at least 2 characters.')
        return value


class OrderStatusUpdateSerializer(serializers.ModelSerializer):
    courier_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    tracking_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    shipping_cost = serializers.DecimalField(required=False, max_digits=12, decimal_places=2, min_value=0)

    class Meta:
        model = Order
        fields = ['status', 'courier_name', 'tracking_number', 'shipping_cost']

    def validate_status(self, value):
        allowed = [c[0] for c in OrderStatus.choices]
        if value not in allowed:
            raise serializers.ValidationError(f'Status must be one of: {allowed}')
        return value

    def validate(self, attrs):
        if attrs.get('status') == OrderStatus.SHIPPED:
            courier_name = (attrs.get('courier_name') or '').strip()
            tracking_number = (attrs.get('tracking_number') or '').strip()
            shipping_cost = attrs.get('shipping_cost')
            if not courier_name or not tracking_number or shipping_cost is None:
                raise serializers.ValidationError({
                    'shipping': 'Courier name, tracking number, and shipping cost are required before shipping the order.'
                })
        return attrs


class WishlistItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_slug = serializers.CharField(source='product.slug', read_only=True)
    product_price = serializers.DecimalField(source='product.price', read_only=True, max_digits=12, decimal_places=2)

    class Meta:
        model = WishlistItem
        fields = ['id', 'product', 'product_name', 'product_slug', 'product_price', 'created_at']
        read_only_fields = ['id', 'product_name', 'product_slug', 'product_price', 'created_at']


class WishlistSerializer(serializers.ModelSerializer):
    items = WishlistItemSerializer(many=True, read_only=True)

    class Meta:
        model = Wishlist
        fields = ['id', 'items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'items', 'created_at', 'updated_at']
