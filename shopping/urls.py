from django.urls import path

from .views import (
    CartDetailView, CartItemDetailView, CartItemListCreateView, CheckoutView, ShippingQuoteView,
    OrderDeliveryConfirmationView, OrderDetailView, OrderListView,
    SellerOrderDetailView, SellerOrderListView, SellerOrderStatusUpdateView, SellerOrderStatusView,
    SupportedShippingTownListView, PickupPointListView, DoorstepLocationSearchView, LogisticsPickupEventView, LogisticsAssignmentView,
    LogisticsShipmentListView, RiderHomeDeliveryListView, RiderHomeDeliveryAssignmentView,
    RiderHomeDeliveryEventView,
    WishlistView, WishlistItemListCreateView, WishlistItemDeleteView,
)


app_name = 'shopping'

urlpatterns = [
    path('shipping-towns/', SupportedShippingTownListView.as_view(), name='shipping-town-list'),
    path('pickup-points/', PickupPointListView.as_view(), name='pickup-point-list'),
    path('doorstep/locations/', DoorstepLocationSearchView.as_view(), name='doorstep-locations'),
    path('', CartDetailView.as_view(), name='cart-detail'),
    path('items/', CartItemListCreateView.as_view(), name='cart-item-list'),
    path('items/<uuid:pk>/', CartItemDetailView.as_view(), name='cart-item-detail'),
    path('checkout/', CheckoutView.as_view(), name='checkout'),
    path('shipping-quote/', ShippingQuoteView.as_view(), name='shipping-quote'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<uuid:pk>/', OrderDetailView.as_view(), name='order-detail'),
    path('orders/<uuid:pk>/confirm-delivery/', OrderDeliveryConfirmationView.as_view(), name='order-confirm-delivery'),
    path('logistics/fulfillments/<uuid:pk>/events/', LogisticsPickupEventView.as_view(), name='logistics-pickup-event'),
    path('logistics/fulfillments/<uuid:pk>/assign/', LogisticsAssignmentView.as_view(), name='logistics-assign'),
    path('logistics/fulfillments/', LogisticsShipmentListView.as_view(), name='logistics-shipments'),
    path('logistics/home-deliveries/', RiderHomeDeliveryListView.as_view(), name='home-deliveries'),
    path('logistics/home-deliveries/<uuid:pk>/assign/', RiderHomeDeliveryAssignmentView.as_view(), name='home-delivery-assign'),
    path('logistics/home-deliveries/<uuid:pk>/events/', RiderHomeDeliveryEventView.as_view(), name='home-delivery-event'),
    path('seller/orders/', SellerOrderListView.as_view(), name='seller-order-list'),
    path('seller/orders/<uuid:pk>/', SellerOrderStatusUpdateView.as_view(), name='seller-order-status'),
    path('seller/orders/<uuid:pk>/detail/', SellerOrderDetailView.as_view(), name='seller-order-detail'),
    path('seller/orders/<uuid:pk>/status/', SellerOrderStatusView.as_view(), name='seller-order-status-update'),
    path('wishlist/', WishlistView.as_view(), name='wishlist'),
    path('wishlist/items/', WishlistItemListCreateView.as_view(), name='wishlist-item-list'),
    path('wishlist/items/<uuid:pk>/', WishlistItemDeleteView.as_view(), name='wishlist-item-delete'),
]
