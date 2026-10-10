from django.contrib import admin
from .models import Cart, CartItem, Order, OrderFulfillment, OrderItem


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
        'delivery_fee_paid', 'status', 'created_at', 'updated_at',
    )
    fieldsets = (
        ('Order Info', {
            'fields': (
                'id', 'buyer', 'item_subtotal', 'shipping_cost', 'total_amount', 'currency',
                'payment_option', 'payment_method', 'payment_status', 'amount_paid',
                'delivery_fee_paid', 'status', 'created_at', 'updated_at',
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
