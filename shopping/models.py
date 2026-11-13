import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import TimestampedModel


class Cart(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart')

    def __str__(self):
        return f"Cart of {self.buyer.email}"


class CartItem(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('product_management.Product', on_delete=models.CASCADE)
    option_id = models.UUIDField(null=True, blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        unique_together = ('cart', 'product', 'option_id')
        constraints = [models.UniqueConstraint(fields=['cart', 'product'], condition=models.Q(option_id__isnull=True), name='unique_base_cart_product')]

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in {self.cart.buyer.email}'s cart"


class OrderStatus(models.TextChoices):
    PENDING = 'pending', 'Pending Payment'
    PROCESSING = 'processing', 'Processing'
    SHIPPED = 'shipped', 'Shipped'
    DELIVERED = 'delivered', 'Delivered'
    CANCELLED = 'cancelled', 'Cancelled'


class PaymentMethod(models.TextChoices):
    WALLET = 'wallet', 'Wallet'
    MPESA = 'mpesa', 'M-Pesa'
    CARD = 'card', 'Card'


class PaymentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PARTIAL = 'partial', 'Partially Paid'
    PAID = 'paid', 'Paid'
    FAILED = 'failed', 'Failed'
    REFUNDED = 'refunded', 'Refunded'


class PaymentOption(models.TextChoices):
    PAY_NOW = 'pay_now', 'Pay Now'
    PAY_ON_DELIVERY = 'pay_on_delivery', 'Pay on Delivery'


class SettlementStatus(models.TextChoices):
    PENDING = 'pending', 'Pending Payment'
    HELD = 'held', 'Held Until Delivery'
    RELEASED = 'released', 'Released to Seller'
    REVERSED = 'reversed', 'Reversed'


class DeliveryMethod(models.TextChoices):
    STANDARD = 'standard', 'Existing address delivery'
    PICKUP_POINT = 'pickup_point', 'AfriClay pickup point'
    DOORSTEP = 'doorstep', 'Pickup point to home'


class PickupPoint(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=500)
    postal_code = models.CharField(max_length=20, blank=True, default='')
    # Administrators enter these only after checking the exact entrance/map pin.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(-180), MaxValueValidator(180)])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['city', 'name']

    def clean(self):
        super().clean()
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError('Enter both latitude and longitude for a pickup point map pin.')

    def __str__(self):
        return f'{self.name} ({self.city})'


class Order(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    item_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='KES')
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    payment_option = models.CharField(max_length=24, choices=PaymentOption.choices, default=PaymentOption.PAY_NOW)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    delivery_fee_paid = models.BooleanField(default=False)
    delivery_method = models.CharField(max_length=20, choices=DeliveryMethod.choices, default=DeliveryMethod.STANDARD)
    pickup_point = models.ForeignKey(PickupPoint, on_delete=models.PROTECT, null=True, blank=True)

    # Shipping information
    shipping_address = models.CharField(max_length=500)
    shipping_city = models.CharField(max_length=100)
    shipping_postal_code = models.CharField(max_length=20)
    shipping_country = models.CharField(max_length=100)
    courier_name = models.CharField(max_length=100, blank=True, default='')
    tracking_number = models.CharField(max_length=100, blank=True, default='')
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    shipped_at = models.DateTimeField(null=True, blank=True)


    class Meta:
        indexes = [
            models.Index(fields=['buyer', '-created_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Order {self.id} for {self.buyer.email}"

    @property
    def amount_due(self):
        return max(self.total_amount - self.amount_paid, Decimal('0.00'))


class OrderItem(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('product_management.Product', on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2)
    price_unit_at_purchase = models.CharField(max_length=32, blank=True, default='')
    option_label_at_purchase = models.CharField(max_length=60, blank=True, default='')
    option_id_at_purchase = models.UUIDField(null=True, blank=True)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.1000'))
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    seller_proceeds = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='sold_items')


    class Meta:
        unique_together = ('order', 'product', 'option_id_at_purchase')
        constraints = [models.UniqueConstraint(fields=['order', 'product'], condition=models.Q(option_id_at_purchase__isnull=True), name='unique_base_order_product')]

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in Order {self.order.id}"


class OrderFulfillment(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='fulfillments')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='order_fulfillments')
    assigned_logistics = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_shipments')
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING)
    item_subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    seller_proceeds = models.DecimalField(max_digits=12, decimal_places=2)
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2)
    actual_weight_kg = models.DecimalField(max_digits=10, decimal_places=3)
    volumetric_weight_kg = models.DecimalField(max_digits=10, decimal_places=3)
    chargeable_weight_kg = models.DecimalField(max_digits=10, decimal_places=3)
    origin_city = models.CharField(max_length=100)
    origin_region = models.CharField(max_length=32)
    destination_city = models.CharField(max_length=100)
    destination_region = models.CharField(max_length=32)
    courier_name = models.CharField(max_length=100, blank=True, default='')
    tracking_number = models.CharField(max_length=100, blank=True, default='')
    shipped_at = models.DateTimeField(null=True, blank=True)
    settlement_status = models.CharField(
        max_length=20,
        choices=SettlementStatus.choices,
        default=SettlementStatus.PENDING,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'seller'], name='unique_order_seller_fulfillment'),
        ]
        indexes = [
            models.Index(fields=['seller', 'status']),
            models.Index(fields=['order', 'status']),
        ]

    def __str__(self):
        return f'{self.order_id} shipment for {self.seller_id}'


class ShipmentStage(models.TextChoices):
    ORDER_CREATED = 'order_created', 'Order created'
    PAYMENT_CONFIRMED = 'payment_confirmed', 'Payment confirmed'
    AWAITING_COLLECTION = 'awaiting_collection', 'Awaiting seller collection'
    COLLECTED_FROM_SELLER = 'collected_from_seller', 'Collected from seller'
    IN_TRANSIT = 'in_transit', 'In transit'
    ARRIVED_AT_PICKUP = 'arrived_at_pickup', 'Arrived at pickup point'
    COLLECTED_BY_BUYER = 'collected_by_buyer', 'Collected by buyer'
    SHIPPED = 'shipped', 'Shipped'
    DELIVERED = 'delivered', 'Delivered'
    CANCELLED = 'cancelled', 'Cancelled'


class ShipmentEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fulfillment = models.ForeignKey(OrderFulfillment, on_delete=models.CASCADE, related_name='events')
    stage = models.CharField(max_length=32, choices=ShipmentStage.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    source = models.CharField(max_length=20, choices=[('system', 'System'), ('seller', 'Seller'), ('buyer', 'Buyer'), ('logistics', 'Logistics')])
    note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['fulfillment', 'stage'], name='unique_shipment_stage_per_fulfillment')]


class HomeDeliveryStage(models.TextChoices):
    WAITING_FOR_PARCELS = 'waiting_for_parcels', 'Waiting for parcels at pickup point'
    RIDER_ASSIGNED = 'rider_assigned', 'Rider assigned'
    RIDER_ACCEPTED = 'rider_accepted', 'Rider accepted'
    RIDER_DECLINED = 'rider_declined', 'Rider declined'
    COLLECTED_FROM_PICKUP = 'collected_from_pickup', 'Collected from pickup point'
    RIDER_DELIVERED = 'rider_delivered', 'Rider reported delivery'
    BUYER_CONFIRMED = 'buyer_confirmed', 'Buyer confirmed delivery'
    CANCELLED = 'cancelled', 'Cancelled'


class HomeDeliveryLeg(models.Model):
    """A separately priced, tracked pickup-to-home journey for one order."""

    order = models.OneToOneField(Order, primary_key=True, on_delete=models.CASCADE, related_name='home_delivery')
    pickup_point = models.ForeignKey(PickupPoint, on_delete=models.PROTECT)
    pickup_address_snapshot = models.CharField(max_length=500)
    destination_address = models.CharField(max_length=500)
    destination_city = models.CharField(max_length=100)
    delivery_instructions = models.CharField(max_length=255, blank=True, default='')
    destination_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    distance_meters = models.DecimalField(max_digits=12, decimal_places=2)
    fee = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=32, choices=HomeDeliveryStage.choices,
                              default=HomeDeliveryStage.WAITING_FOR_PARCELS)
    assigned_rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='home_deliveries')
    accept_by = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class HomeDeliveryEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    leg = models.ForeignKey(HomeDeliveryLeg, on_delete=models.CASCADE, related_name='events')
    stage = models.CharField(max_length=32, choices=HomeDeliveryStage.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = []


class Wishlist(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist')

    def __str__(self):
        return f"Wishlist of {self.user.email}"


class WishlistItem(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('product_management.Product', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('wishlist', 'product')

    def __str__(self):
        return f"{self.product.name} in {self.wishlist.user.email}'s wishlist"
