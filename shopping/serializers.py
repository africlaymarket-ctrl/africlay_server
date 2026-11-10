from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .locations import LocationError, read_home_location
from .doorstep_access import doorstep_available_for

from .models import (
    Cart, CartItem, DeliveryMethod, Order, OrderFulfillment, OrderItem, OrderStatus, PaymentMethod, PickupPoint,
    PaymentOption, ShipmentEvent, ShipmentStage, Wishlist, WishlistItem,
    HomeDeliveryLeg, HomeDeliveryEvent, HomeDeliveryStage,
)


class SupportedShippingTownListSerializer(serializers.Serializer):
    towns = serializers.ListField(child=serializers.CharField(), read_only=True)


class PickupPointSerializer(serializers.ModelSerializer):
    doorstep_available = serializers.SerializerMethodField()

    class Meta:
        model = PickupPoint
        fields = ['id', 'name', 'city', 'address', 'postal_code', 'doorstep_available']
        read_only_fields = fields

    def get_doorstep_available(self, point):
        request = self.context.get('request')
        return bool(doorstep_available_for(request.user if request else None)
                    and point.active and point.latitude is not None and point.longitude is not None)


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
        fields = [
            'id', 'product_id', 'product_name', 'product_slug', 'quantity',
            'price_at_purchase', 'seller_id',
        ]
        read_only_fields = fields


class SellerOrderItemDetailSerializer(OrderItemDetailSerializer):
    class Meta(OrderItemDetailSerializer.Meta):
        fields = OrderItemDetailSerializer.Meta.fields + [
            'commission_rate', 'commission_amount', 'seller_proceeds',
        ]
        read_only_fields = fields


class ShipmentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentEvent
        fields = ['id', 'stage', 'source', 'note', 'created_at']
        read_only_fields = fields


class HomeDeliveryEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeDeliveryEvent
        fields = ['id', 'stage', 'note', 'created_at']
        read_only_fields = fields


class HomeDeliveryLegSerializer(serializers.ModelSerializer):
    events = HomeDeliveryEventSerializer(many=True, read_only=True)

    class Meta:
        model = HomeDeliveryLeg
        fields = ['status', 'pickup_address_snapshot', 'destination_address', 'destination_city', 'delivery_instructions', 'distance_meters', 'fee', 'events']
        read_only_fields = fields


class RiderHomeDeliverySerializer(HomeDeliveryLegSerializer):
    order_id = serializers.UUIDField(source='order.id', read_only=True)
    pickup_point = PickupPointSerializer(read_only=True)
    buyer_phone = serializers.CharField(source='order.buyer.phone_number', read_only=True)

    class Meta(HomeDeliveryLegSerializer.Meta):
        fields = HomeDeliveryLegSerializer.Meta.fields + ['order_id', 'pickup_point', 'assigned_rider_id', 'buyer_phone', 'accept_by', 'created_at']
        read_only_fields = fields


class RiderHomeDeliveryEventInputSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=[
        HomeDeliveryStage.RIDER_ACCEPTED, HomeDeliveryStage.RIDER_DECLINED, HomeDeliveryStage.COLLECTED_FROM_PICKUP,
        HomeDeliveryStage.RIDER_DELIVERED,
    ])
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if attrs['stage'] == HomeDeliveryStage.RIDER_DECLINED and len(attrs.get('note', '').strip()) < 5:
            raise serializers.ValidationError({'note': 'Explain why this delivery is being declined.'})
        return attrs


class LogisticsEventInputSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=[
        ShipmentStage.COLLECTED_FROM_SELLER,
        ShipmentStage.IN_TRANSIT,
        ShipmentStage.ARRIVED_AT_PICKUP,
    ])
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class LogisticsAssignmentInputSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    reason = serializers.CharField(required=True, min_length=5, max_length=255)


class LogisticsShipmentSerializer(serializers.ModelSerializer):
    order_id = serializers.UUIDField(source='order.id', read_only=True)
    assigned_logistics_id = serializers.UUIDField(read_only=True)
    pickup_point = PickupPointSerializer(source='order.pickup_point', read_only=True)
    pickup_address = serializers.SerializerMethodField()
    pickup_city = serializers.CharField(source='order.shipping_city', read_only=True)
    seller_store_name = serializers.SerializerMethodField()
    seller_store_address = serializers.SerializerMethodField()
    seller_store_phone = serializers.SerializerMethodField()
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = OrderFulfillment
        fields = [
            'id', 'order_id', 'status', 'origin_city', 'pickup_point', 'pickup_address', 'pickup_city',
            'courier_name', 'tracking_number', 'seller_store_name',
            'seller_store_address', 'seller_store_phone', 'events',
            'assigned_logistics_id', 'created_at',
        ]
        read_only_fields = fields

    def _store(self, fulfillment):
        return getattr(fulfillment.seller, 'store', None)

    def get_pickup_address(self, fulfillment):
        if fulfillment.order.delivery_method == DeliveryMethod.DOORSTEP:
            leg = getattr(fulfillment.order, 'home_delivery', None)
            return leg.pickup_address_snapshot if leg else ''
        return fulfillment.order.shipping_address

    def get_seller_store_name(self, fulfillment):
        return getattr(self._store(fulfillment), 'name', '')

    def get_seller_store_address(self, fulfillment):
        return getattr(self._store(fulfillment), 'address', '')

    def get_seller_store_phone(self, fulfillment):
        return getattr(self._store(fulfillment), 'phone_number', '')


class OrderFulfillmentSerializer(serializers.ModelSerializer):
    seller_id = serializers.UUIDField(source='seller.id', read_only=True)
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = OrderFulfillment
        fields = [
            'id', 'seller_id', 'status', 'item_subtotal', 'commission_amount',
            'seller_proceeds', 'shipping_cost', 'actual_weight_kg',
            'volumetric_weight_kg', 'chargeable_weight_kg', 'origin_city',
            'origin_region', 'destination_city', 'destination_region', 'courier_name',
            'tracking_number', 'shipped_at', 'settlement_status', 'events',
        ]
        read_only_fields = fields


class OrderFulfillmentSummarySerializer(OrderFulfillmentSerializer):
    class Meta(OrderFulfillmentSerializer.Meta):
        fields = [field for field in OrderFulfillmentSerializer.Meta.fields if field != 'events']
        read_only_fields = fields


class BuyerOrderFulfillmentSerializer(serializers.ModelSerializer):
    seller_id = serializers.UUIDField(source='seller.id', read_only=True)
    events = ShipmentEventSerializer(many=True, read_only=True)

    class Meta:
        model = OrderFulfillment
        fields = [
            'id', 'seller_id', 'status', 'shipping_cost', 'actual_weight_kg',
            'volumetric_weight_kg', 'chargeable_weight_kg', 'origin_city',
            'destination_city', 'courier_name', 'tracking_number', 'shipped_at',
            'settlement_status', 'events',
        ]
        read_only_fields = fields


class BuyerOrderFulfillmentSummarySerializer(BuyerOrderFulfillmentSerializer):
    class Meta(BuyerOrderFulfillmentSerializer.Meta):
        fields = [field for field in BuyerOrderFulfillmentSerializer.Meta.fields if field != 'events']
        read_only_fields = fields


class OrderDetailSerializer(serializers.ModelSerializer):
    buyer_id = serializers.UUIDField(source='buyer.id', read_only=True)
    items = OrderItemDetailSerializer(many=True, read_only=True)
    fulfillments = BuyerOrderFulfillmentSerializer(many=True, read_only=True)
    amount_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    pickup_point = PickupPointSerializer(read_only=True)
    home_delivery = HomeDeliveryLegSerializer(read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'buyer_id', 'item_subtotal', 'shipping_cost', 'total_amount', 'currency',
            'status', 'payment_method', 'payment_status', 'payment_option', 'amount_paid',
            'amount_due', 'delivery_fee_paid', 'delivery_method', 'pickup_point', 'home_delivery',
            'shipping_address', 'shipping_city', 'shipping_postal_code', 'shipping_country',
            'courier_name', 'tracking_number', 'shipped_at',
            'items', 'fulfillments', 'created_at', 'updated_at'
        ]
        read_only_fields = fields


class OrderListSerializer(OrderDetailSerializer):
    fulfillments = BuyerOrderFulfillmentSummarySerializer(many=True, read_only=True)


class SellerOrderDetailSerializer(OrderDetailSerializer):
    items = serializers.SerializerMethodField()
    fulfillments = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    courier_name = serializers.SerializerMethodField()
    tracking_number = serializers.SerializerMethodField()
    shipping_cost = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()
    commission_amount = serializers.SerializerMethodField()
    seller_proceeds = serializers.SerializerMethodField()
    settlement_status = serializers.SerializerMethodField()
    home_delivery = serializers.SerializerMethodField()
    shipping_address = serializers.SerializerMethodField()

    class Meta(OrderDetailSerializer.Meta):
        fields = OrderDetailSerializer.Meta.fields + [
            'commission_amount', 'seller_proceeds', 'settlement_status',
        ]

    def _fulfillment(self, order):
        return order.fulfillments.filter(seller=self.context['request'].user).first()

    def get_home_delivery(self, order):
        return None

    def get_shipping_address(self, order):
        if order.delivery_method == DeliveryMethod.DOORSTEP:
            leg = getattr(order, 'home_delivery', None)
            return leg.pickup_address_snapshot if leg else ''
        return order.shipping_address

    def get_total_amount(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.item_subtotal if fulfillment else Decimal('0.00')

    def get_items(self, order):
        seller = self.context['request'].user
        seller_items = order.items.filter(seller=seller).select_related('product', 'seller')
        return SellerOrderItemDetailSerializer(seller_items, many=True).data

    def get_fulfillments(self, order):
        fulfillment = self._fulfillment(order)
        return OrderFulfillmentSerializer([fulfillment] if fulfillment else [], many=True).data

    def get_status(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.status if fulfillment else order.status

    def get_courier_name(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.courier_name if fulfillment else ''

    def get_tracking_number(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.tracking_number if fulfillment else ''

    def get_shipping_cost(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.shipping_cost if fulfillment else Decimal('0.00')

    def get_shipped_at(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.shipped_at if fulfillment else None

    def get_commission_amount(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.commission_amount if fulfillment else Decimal('0.00')

    def get_seller_proceeds(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.seller_proceeds if fulfillment else Decimal('0.00')

    def get_settlement_status(self, order):
        fulfillment = self._fulfillment(order)
        return fulfillment.settlement_status if fulfillment else 'pending'


class SellerOrderListSerializer(SellerOrderDetailSerializer):
    def get_fulfillments(self, order):
        fulfillment = self._fulfillment(order)
        return OrderFulfillmentSummarySerializer([fulfillment] if fulfillment else [], many=True).data


class SellerOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['processing', 'shipped', 'delivered'])


class CreateCheckoutSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(required=False, max_length=500)
    shipping_city = serializers.CharField(required=False, max_length=100)
    shipping_postal_code = serializers.CharField(required=False, max_length=20)
    shipping_country = serializers.CharField(required=False, max_length=100)
    delivery_method = serializers.ChoiceField(choices=DeliveryMethod.choices, required=False, default=DeliveryMethod.STANDARD)
    pickup_point_id = serializers.UUIDField(required=False)
    home_location_token = serializers.CharField(required=False, max_length=2048)
    home_delivery_instructions = serializers.CharField(required=False, allow_blank=True, max_length=255)
    payment_method = serializers.ChoiceField(
        choices=[PaymentMethod.MPESA, PaymentMethod.WALLET],
        required=False,
        default=PaymentMethod.MPESA,
    )
    payment_option = serializers.ChoiceField(
        choices=PaymentOption.choices,
        required=False,
        default=PaymentOption.PAY_NOW,
    )
    buy_now_product_id = serializers.UUIDField(required=False)
    buy_now_quantity = serializers.IntegerField(required=False, min_value=1, max_value=99)

    def validate(self, attrs):
        if 'buy_now_quantity' in attrs and 'buy_now_product_id' not in attrs:
            raise serializers.ValidationError({
                'buy_now_product_id': 'A product is required when a Buy Now quantity is provided.',
            })
        if attrs['delivery_method'] in {DeliveryMethod.PICKUP_POINT, DeliveryMethod.DOORSTEP}:
            point = PickupPoint.objects.filter(pk=attrs.get('pickup_point_id'), active=True).first()
            if point is None:
                raise serializers.ValidationError({'pickup_point_id': 'Select an available AfriClay pickup point.'})
            attrs['pickup_point'] = point
            attrs['shipping_city'] = point.city
            attrs['shipping_postal_code'] = point.postal_code or 'N/A'
            attrs['shipping_country'] = 'Kenya'
            if attrs['delivery_method'] == DeliveryMethod.DOORSTEP:
                if not doorstep_available_for(self.context['request'].user):
                    raise serializers.ValidationError({'delivery_method': 'Doorstep delivery is not available yet.'})
                if point.latitude is None or point.longitude is None:
                    raise serializers.ValidationError({'pickup_point_id': 'This pickup point has no confirmed map pin.'})
                token = attrs.get('home_location_token')
                if not token:
                    raise serializers.ValidationError({'home_location_token': 'Choose a confirmed home location.'})
                try:
                    attrs['home_location'] = read_home_location(
                        token, pickup_point=point, user_id=self.context['request'].user.pk,
                    )
                except LocationError as error:
                    raise serializers.ValidationError({'home_location_token': str(error)}) from error
                attrs['shipping_address'] = attrs['home_location']['address']
                attrs['shipping_postal_code'] = 'N/A'
            else:
                if attrs.get('home_location_token'):
                    raise serializers.ValidationError({'home_location_token': 'A home location is only valid for doorstep delivery.'})
                attrs['shipping_address'] = point.address
        else:
            if settings.PICKUP_ONLY_CHECKOUT:
                raise serializers.ValidationError({'delivery_method': 'New orders must use an AfriClay pickup point until doorstep pricing is available.'})
            if attrs.get('pickup_point_id'):
                raise serializers.ValidationError({'pickup_point_id': 'A pickup point is only valid for pickup delivery.'})
            for field in ('shipping_address', 'shipping_city', 'shipping_postal_code', 'shipping_country'):
                if not attrs.get(field):
                    raise serializers.ValidationError({field: 'A delivery address is required.'})
        return attrs

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


class ConfirmCheckoutSerializer(CreateCheckoutSerializer):
    """Checkout requires the prices the buyer reviewed in the shipping quote."""

    expected_item_subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    expected_shipping_cost = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    expected_total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    expected_pickup_point_address = serializers.CharField(required=False, max_length=500)
    expected_doorstep_quote_token = serializers.CharField(required=False, max_length=4096)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs['delivery_method'] in {DeliveryMethod.PICKUP_POINT, DeliveryMethod.DOORSTEP} and not attrs.get('expected_pickup_point_address'):
            raise serializers.ValidationError({'expected_pickup_point_address': 'Review the pickup point quote before placing the order.'})
        if attrs['delivery_method'] == DeliveryMethod.DOORSTEP and not attrs.get('expected_doorstep_quote_token'):
            raise serializers.ValidationError({'expected_doorstep_quote_token': 'Review the doorstep quote before placing the order.'})
        return attrs


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
            if not courier_name or not tracking_number:
                raise serializers.ValidationError({
                    'shipping': 'Courier name and tracking number are required before shipping the order.'
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
