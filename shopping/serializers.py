from rest_framework import serializers

from .models import Cart, CartItem, Order, OrderItem, OrderStatus, Wishlist, WishlistItem


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
        fields = ['id', 'buyer_id', 'total_amount', 'currency', 'status', 'shipping_address', 'shipping_city', 'shipping_postal_code', 'shipping_country', 'items', 'created_at', 'updated_at']
        read_only_fields = fields


class CreateCheckoutSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(required=True, max_length=500)
    shipping_city = serializers.CharField(required=True, max_length=100)
    shipping_postal_code = serializers.CharField(required=True, max_length=20)
    shipping_country = serializers.CharField(required=True, max_length=100)

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
    class Meta:
        model = Order
        fields = ['status']

    def validate_status(self, value):
        allowed = [c[0] for c in OrderStatus.choices]
        if value not in allowed:
            raise serializers.ValidationError(f'Status must be one of: {allowed}')
        return value


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
