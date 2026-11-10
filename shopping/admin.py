from django.contrib import admin
from .models import Cart, CartItem, Order, OrderFulfillment, OrderItem, PickupPoint, ShipmentEvent, HomeDeliveryLeg, HomeDeliveryEvent


@admin.register(PickupPoint)
class PickupPointAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'active', 'has_map_pin', 'updated_at')
    list_filter = ('active', 'city')
    search_fields = ('name', 'city', 'address')

    @admin.display(boolean=True, description='Map pin set')
    def has_map_pin(self, obj):
        return obj.latitude is not None and obj.longitude is not None


@admin.register(ShipmentEvent)
class ShipmentEventAdmin(admin.ModelAdmin):
    list_display = ('fulfillment', 'stage', 'source', 'actor', 'created_at')
    list_filter = ('stage', 'source', 'created_at')
    search_fields = ('fulfillment__order__id',)
    readonly_fields = [field.name for field in ShipmentEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HomeDeliveryLeg)
class HomeDeliveryLegAdmin(admin.ModelAdmin):
    list_display = ('order', 'pickup_point', 'status', 'assigned_rider', 'fee', 'distance_meters', 'created_at')
    list_filter = ('status', 'pickup_point')
    search_fields = ('order__id', 'destination_address')
    readonly_fields = [field.name for field in HomeDeliveryLeg._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HomeDeliveryEvent)
class HomeDeliveryEventAdmin(admin.ModelAdmin):
    list_display = ('leg', 'stage', 'actor', 'created_at')
    list_filter = ('stage', 'created_at')
    search_fields = ('leg__order__id',)
    readonly_fields = [field.name for field in HomeDeliveryEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    inlines = (CartItemInline,)
    list_display = ('buyer', 'created_at', 'updated_at')
    search_fields = ('buyer__email',)
    readonly_fields = ('id', 'buyer', 'created_at', 'updated_at')


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('cart', 'product', 'quantity', 'created_at')
    search_fields = ('cart__buyer__email', 'product__name')
    readonly_fields = ('id', 'cart', 'product', 'quantity', 'created_at', 'updated_at')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('id', 'product', 'quantity', 'price_at_purchase', 'seller', 'created_at')
    fields = ('product', 'quantity', 'price_at_purchase', 'seller', 'created_at')
    can_delete = False


class OrderFulfillmentInline(admin.TabularInline):
    model = OrderFulfillment
    extra = 0
    readonly_fields = [field.name for field in OrderFulfillment._meta.fields]
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = (OrderItemInline, OrderFulfillmentInline)
    list_display = ('id', 'buyer', 'total_amount', 'payment_option', 'payment_status', 'status', 'created_at')
    list_filter = ('status', 'payment_option', 'payment_status', 'created_at')
    search_fields = ('buyer__email', 'id')
    readonly_fields = (
        'id', 'buyer', 'item_subtotal', 'shipping_cost', 'total_amount', 'currency',
        'payment_option', 'payment_method', 'payment_status', 'amount_paid',
        'delivery_fee_paid', 'delivery_method', 'pickup_point', 'status', 'created_at', 'updated_at',
    )
    fieldsets = (
        ('Order Info', {
            'fields': (
                'id', 'buyer', 'item_subtotal', 'shipping_cost', 'total_amount', 'currency',
                'payment_option', 'payment_method', 'payment_status', 'amount_paid',
                'delivery_fee_paid', 'delivery_method', 'pickup_point', 'status', 'created_at', 'updated_at',
            )
        }),
        ('Shipping Address', {
            'fields': ('shipping_address', 'shipping_city', 'shipping_postal_code', 'shipping_country')
        }),
    )
    can_delete = False


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'price_at_purchase', 'seller', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('order__id', 'product__name', 'seller__email')
    readonly_fields = ('id', 'created_at')
