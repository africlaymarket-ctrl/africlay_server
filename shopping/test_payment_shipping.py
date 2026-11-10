from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import AdminAuditLog, StaffRole, UserRole, UserStaffRole
from payments.models import LedgerEntry, LedgerEntryType, Payment, PaymentPurpose, Wallet
from payments.services import PaymentService, credit_wallet
from notifications.models import Notification, SMSDelivery
from product_management.models import Product
from store_management.models import Store
from utility_services.utils import SMSResult

from .models import Cart, CartItem, Order, PaymentStatus, PickupPoint, SettlementStatus, ShipmentEvent, ShipmentStage, HomeDeliveryLeg, HomeDeliveryStage
from .geoapify import DoorstepLegQuote
from .locations import LOCATION_SALT
from .shipping import quote_cart_items
from .test_support import QuotedCheckoutClient


class ShippingTariffTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            email='tariff-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        self.buyer = User.objects.create_user(
            email='tariff-buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.store = Store.objects.create(
            owner=self.seller,
            name='Nairobi Store',
            slug='nairobi-store',
            city='Nairobi',
            country='Kenya',
        )
        self.product = Product.objects.create(
            store=self.store,
            name='Six kilogram parcel',
            slug='six-kilogram-parcel',
            sku='SHIP-006',
            price=Decimal('1000.00'),
            stock_quantity=5,
            weight_kg=Decimal('6.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        self.cart = Cart.objects.create(buyer=self.buyer)
        self.item = CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)

    def test_standard_rate_uses_base_and_extra_kilogram(self):
        quote = quote_cart_items([self.item], 'Nairobi')[0]

        self.assertEqual(quote.chargeable_weight_kg, Decimal('6.000'))
        self.assertEqual(quote.shipping_cost, Decimal('250.00'))
        self.assertEqual(quote.commission_amount, Decimal('100.00'))
        self.assertEqual(quote.seller_proceeds, Decimal('900.00'))

    def test_product_without_dimensions_uses_listed_weight_for_delivery_quote(self):
        self.product.length_cm = None
        self.product.width_cm = None
        self.product.height_cm = None
        self.product.save(update_fields=['length_cm', 'width_cm', 'height_cm'])
        quote = quote_cart_items([self.item], 'Nairobi')[0]
        self.assertEqual(quote.actual_weight_kg, Decimal('6.000'))
        self.assertEqual(quote.volumetric_weight_kg, Decimal('0.000'))
        self.assertEqual(quote.chargeable_weight_kg, Decimal('6.000'))
        self.assertEqual(quote.shipping_cost, Decimal('250.00'))

    def test_remote_destination_uses_remote_tariff(self):
        quote = quote_cart_items([self.item], 'Lodwar')[0]

        self.assertTrue(quote.is_remote)
        self.assertEqual(quote.shipping_cost, Decimal('1860.00'))


@override_settings(
    PICKUP_ONLY_CHECKOUT=False,
    GEOAPIFY_API_KEY='test-only-key',
    DOORSTEP_TEST_EMAILS=frozenset({'pod-buyer@example.com'}),
    MPESA_ENVIRONMENT='sandbox',
    MPESA_CONSUMER_KEY='consumer-key',
    MPESA_CONSUMER_SECRET='consumer-secret',
    MPESA_SHORTCODE='174379',
    MPESA_PASSKEY='passkey',
    MPESA_CALLBACK_BASE_URL='https://api.africlaymarket.com',
)
class PayOnDeliveryFlowTests(TestCase):
    def setUp(self):
        self.client = QuotedCheckoutClient()
        User = get_user_model()
        self.buyer = User.objects.create_user(
            email='pod-buyer@example.com',
            password='StrongPassword123!',
            role=UserRole.BUYER,
            is_verified=True,
        )
        self.seller = User.objects.create_user(
            email='pod-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        self.store = Store.objects.create(
            owner=self.seller,
            name='POD Store',
            slug='pod-store',
            city='Nairobi',
            country='Kenya',
        )
        self.product = Product.objects.create(
            store=self.store,
            name='POD Product',
            slug='pod-product',
            sku='POD-001',
            price=Decimal('1000.00'),
            stock_quantity=2,
            weight_kg=Decimal('1.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        cart = Cart.objects.create(buyer=self.buyer)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)

    def _home_token(self, point):
        return signing.dumps({
            'user_id': str(self.buyer.pk), 'pickup_id': str(point.pk),
            'address': 'Test Building, Nairobi, Kenya', 'city': 'Nairobi',
            'latitude': '-1.290000', 'longitude': '36.820000',
        }, salt=LOCATION_SALT)

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True, GEOAPIFY_API_KEY='test-only-key')
    def test_doorstep_test_rollout_is_limited_to_allowed_account(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        url = reverse('shopping:pickup-point-list')
        with override_settings(DOORSTEP_TEST_EMAILS=frozenset()):
            self.client.force_authenticate(self.buyer)
            self.assertFalse(next(item for item in self.client.get(url).data if str(item['id']) == str(point.pk))['doorstep_available'])
        with override_settings(DOORSTEP_TEST_EMAILS=frozenset({self.buyer.email})):
            anonymous = APIClient().get(url)
            self.assertFalse(next(item for item in anonymous.data if str(item['id']) == str(point.pk))['doorstep_available'])
            self.client.force_authenticate(self.buyer)
            allowed = self.client.get(url)
            self.assertTrue(next(item for item in allowed.data if str(item['id']) == str(point.pk))['doorstep_available'])
            other = get_user_model().objects.create_user(
                email='other-doorstep-buyer@example.com', password='StrongPassword123!',
                role=UserRole.BUYER, is_verified=True,
            )
            self.client.force_authenticate(other)
            restricted = self.client.get(url)
            self.assertFalse(next(item for item in restricted.data if str(item['id']) == str(point.pk))['doorstep_available'])
            quote = self.client.post(reverse('shopping:shipping-quote'), {
                'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
                'home_location_token': self._home_token(point),
            }, format='json')
            self.assertEqual(quote.status_code, 400)
            search = self.client.post(reverse('shopping:doorstep-locations'), {
                'pickup_point_id': str(point.pk), 'query': 'Test Building',
            }, format='json')
            self.assertEqual(search.status_code, 400)

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True, GEOAPIFY_API_KEY='test-only-key')
    @patch('shopping.locations.requests.get')
    def test_location_search_returns_only_signed_precise_places_in_pickup_city(self, get):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        get.return_value.json.return_value = {'results': [
            {'result_type': 'building', 'country_code': 'ke', 'city': 'Nairobi', 'lat': -1.29, 'lon': 36.82,
             'formatted': 'Test Building, Nairobi, Kenya'},
            {'result_type': 'city', 'country_code': 'ke', 'city': 'Nairobi', 'lat': -1.28, 'lon': 36.81,
             'formatted': 'Nairobi, Kenya'},
            {'result_type': 'building', 'country_code': 'ke', 'city': 'Eldoret', 'lat': 0.52, 'lon': 35.27,
             'formatted': 'Test Building, Eldoret, Kenya'},
        ]}
        url = reverse('shopping:doorstep-locations')
        anonymous = self.client.post(url, {'pickup_point_id': str(point.pk), 'query': 'Test Building'}, format='json')
        self.assertIn(anonymous.status_code, (401, 403))
        self.client.force_authenticate(self.buyer)
        response = self.client.post(url, {'pickup_point_id': str(point.pk), 'query': 'Test Building'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['locations']), 1)
        self.assertEqual(response.data['locations'][0]['label'], 'Test Building, Nairobi, Kenya')
        self.assertNotIn('test-only-key', str(response.data))
        self.assertEqual(get.call_args.kwargs['params']['filter'], 'countrycode:ke')

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_doorstep_quote_is_separate_and_persisted_before_payment(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        payload = {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point),
        }
        self.client.force_authenticate(self.buyer)
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data['doorstep_delivery_cost'], Decimal('394.00'))
        self.assertEqual(quote.data['shipping_cost'], quote.data['pickup_delivery_cost'] + Decimal('394.00'))
        self.assertEqual(quote.data['total_amount'], quote.data['item_subtotal'] + quote.data['shipping_cost'])
        checkout = self.client.post(reverse('shopping:checkout'), payload, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        self.assertEqual(order.shipping_address, 'Test Building, Nairobi, Kenya')
        self.assertEqual(order.shipping_cost, quote.data['shipping_cost'])
        self.assertEqual(order.fulfillments.first().shipping_cost, quote.data['pickup_delivery_cost'])
        self.assertEqual(order.home_delivery.fee, Decimal('394.00'))
        self.assertEqual(order.home_delivery.distance_meters, Decimal('8400'))
        self.assertEqual(order.home_delivery.pickup_address_snapshot, point.address)
        self.assertEqual(order.home_delivery.events.count(), 1)
        self.assertEqual(route.call_count, 2)  # Two displayed quotes; checkout trusts its signed quote.

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_unpaid_doorstep_order_is_not_dispatchable(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point),
        }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        self.assertFalse(order.delivery_fee_paid)
        rider = get_user_model().objects.create_user(email='unpaid-home-rider@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=rider, role=StaffRole.LOGISTICS)
        admin = get_user_model().objects.create_superuser(email='unpaid-home-admin@example.com', password='StrongPassword123!')
        self.client.force_authenticate(admin)
        jobs = self.client.get(reverse('shopping:home-deliveries'))
        self.assertEqual(jobs.status_code, 200, jobs.data)
        self.assertEqual(jobs.data['results'], [])
        assignment = self.client.post(reverse('shopping:home-delivery-assign', args=[order.pk]), {
            'email': rider.email, 'reason': 'Test unpaid dispatch prevention.'
        }, format='json')
        self.assertEqual(assignment.status_code, 400, assignment.data)
        self.assertEqual(str(assignment.data['payment']), 'Wait for confirmed delivery payment before assigning a rider.')
        self.assertEqual(order.home_delivery.status, HomeDeliveryStage.WAITING_FOR_PARCELS)

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True, NOTIFICATION_EMAILS_ENABLED=False)
    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    @patch('shopping.views.quote_doorstep_leg')
    def test_paid_doorstep_seller_notification_uses_pickup_address_only(self, route, initiate_payment):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        initiate_payment.return_value = {
            'MerchantRequestID': 'merchant-home-address',
            'CheckoutRequestID': 'checkout-home-address',
        }
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point),
        }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        initiated = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': checkout.data['id'], 'phone_number': '254712345678',
        }, format='json')
        self.assertEqual(initiated.status_code, 201, initiated.data)
        payment = Payment.objects.get(pk=initiated.data['id'])
        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.process_mpesa_callback(self.callback(payment, 'HOME-ADDR-1'))
        notification = Notification.objects.get(user=self.seller, title='New paid order')
        self.assertIn(point.address, notification.message)
        self.assertNotIn('Test Building', notification.message)

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_doorstep_checkout_rejects_tampered_total_and_location(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        payload = {'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
                   'home_location_token': self._home_token(point)}
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        direct = APIClient()
        direct.force_authenticate(self.buyer)
        confirmed = {
            **payload, 'expected_item_subtotal': str(quote.data['item_subtotal']),
            'expected_shipping_cost': str(quote.data['pickup_delivery_cost']),
            'expected_total_amount': str(quote.data['item_subtotal'] + quote.data['pickup_delivery_cost']),
            'expected_pickup_point_address': point.address,
            'expected_doorstep_quote_token': quote.data['expected_doorstep_quote_token'],
        }
        self.assertEqual(direct.post(reverse('shopping:checkout'), confirmed, format='json').status_code, 400)
        confirmed['expected_shipping_cost'] = str(quote.data['shipping_cost'])
        confirmed['expected_total_amount'] = str(quote.data['total_amount'])
        confirmed['home_location_token'] += 'tampered'
        self.assertEqual(direct.post(reverse('shopping:checkout'), confirmed, format='json').status_code, 400)
        self.assertFalse(Order.objects.exists())

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_doorstep_rider_and_buyer_confirmation_release_seller_only_after_delivery(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('2500.00'), 'provider:home-delivery-test')
        self.client.force_authenticate(self.buyer)
        response = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point), 'payment_method': 'wallet',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data['id'])
        fulfillment = order.fulfillments.get()
        original_pickup_address = point.address
        point.address = 'Edited pickup point address after order'
        point.save(update_fields=['address'])
        staff = get_user_model().objects.create_user(email='home-rider@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=staff, role=StaffRole.LOGISTICS)
        other_staff = get_user_model().objects.create_user(email='other-home-rider@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=other_staff, role=StaffRole.LOGISTICS)
        superadmin = get_user_model().objects.create_superuser(email='home-admin@example.com', password='StrongPassword123!')
        self.client.force_authenticate(self.seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped', 'courier_name': 'Test Logistics', 'tracking_number': 'HOME-001',
        }, format='json')
        self.assertEqual(shipped.status_code, 200, shipped.data)
        self.assertEqual(shipped.data['shipping_address'], original_pickup_address)
        self.assertIsNone(shipped.data['home_delivery'])
        self.client.force_authenticate(superadmin)
        for url in (
            reverse('shopping:logistics-assign', args=[fulfillment.pk]),
            reverse('shopping:home-delivery-assign', args=[order.pk]),
        ):
            assigned = self.client.post(url, {'email': staff.email, 'reason': 'Assigned for the confirmed route.'}, format='json')
            self.assertEqual(assigned.status_code, 200, assigned.data)
        self.client.force_authenticate(other_staff)
        other_jobs = self.client.get(reverse('shopping:home-deliveries'))
        self.assertEqual(other_jobs.status_code, 200, other_jobs.data)
        self.assertEqual(other_jobs.data['results'], [])
        self.assertEqual(self.client.post(reverse('shopping:home-delivery-event', args=[order.pk]),
                                          {'stage': HomeDeliveryStage.RIDER_ACCEPTED}, format='json').status_code, 403)
        self.client.force_authenticate(staff)
        jobs = self.client.get(reverse('shopping:home-deliveries'))
        self.assertEqual(jobs.status_code, 200, jobs.data)
        self.assertEqual(len(jobs.data['results']), 1)
        self.assertEqual(jobs.data['results'][0]['pickup_address_snapshot'], original_pickup_address)
        event_url = reverse('shopping:home-delivery-event', args=[order.pk])
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.COLLECTED_FROM_PICKUP}, format='json').status_code, 400)
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.RIDER_ACCEPTED}, format='json').status_code, 201)
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.COLLECTED_FROM_PICKUP}, format='json').status_code, 400)
        first_leg_url = reverse('shopping:logistics-pickup-event', args=[fulfillment.pk])
        for stage in (ShipmentStage.COLLECTED_FROM_SELLER, ShipmentStage.IN_TRANSIT, ShipmentStage.ARRIVED_AT_PICKUP):
            self.assertEqual(self.client.post(first_leg_url, {'stage': stage}, format='json').status_code, 201)
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.COLLECTED_FROM_PICKUP}, format='json').status_code, 201)
        self.client.force_authenticate(self.buyer)
        self.assertEqual(self.client.post(reverse('shopping:order-confirm-delivery', args=[order.pk])).status_code, 400)
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.RIDER_DELIVERED}, format='json').status_code, 201)
        self.assertEqual(self.client.post(event_url, {'stage': HomeDeliveryStage.RIDER_DELIVERED}, format='json').status_code, 200)
        self.client.force_authenticate(self.buyer)
        delivered = self.client.post(reverse('shopping:order-confirm-delivery', args=[order.pk]))
        self.assertEqual(delivered.status_code, 200, delivered.data)
        order.refresh_from_db()
        self.assertEqual(order.home_delivery.status, HomeDeliveryStage.BUYER_CONFIRMED)
        fulfillment.refresh_from_db()
        self.assertEqual(fulfillment.settlement_status, SettlementStatus.RELEASED)
        self.assertEqual(fulfillment.events.filter(stage=ShipmentStage.DELIVERED).count(), 1)
        buyer_wallet.refresh_from_db()
        self.assertEqual(buyer_wallet.held_balance, Decimal('0.00'))

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_cancelling_last_home_parcel_refunds_both_delivery_legs_once(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(wallet, Decimal('2500.00'), 'provider:home-cancel-test')
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point), 'payment_method': 'wallet',
        }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        original_total = order.total_amount
        self.client.force_authenticate(self.seller)
        cancelled = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]),
                                      {'status': 'cancelled'}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        order.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal('0.00'))
        self.assertEqual(order.shipping_cost, Decimal('0.00'))
        self.assertEqual(order.amount_paid, Decimal('0.00'))
        self.assertEqual(order.home_delivery.status, HomeDeliveryStage.CANCELLED)
        self.assertEqual(wallet.held_balance, Decimal('0.00'))
        self.assertEqual(wallet.available_balance, Decimal('2500.00'))
        self.assertEqual(
            sum(LedgerEntry.objects.filter(order=order, entry_type=LedgerEntryType.REFUND_COMPLETED)
                .values_list('amount', flat=True)), original_total,
        )
        duplicate = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]),
                                      {'status': 'cancelled'}, format='json')
        self.assertEqual(duplicate.status_code, 400)

    @override_settings(DOORSTEP_CHECKOUT_ENABLED=True)
    @patch('shopping.views.quote_doorstep_leg')
    def test_multi_seller_cancellation_keeps_home_leg_until_last_parcel(self, route):
        route.return_value = DoorstepLegQuote(Decimal('8400'), Decimal('394.00'))
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        other_seller = get_user_model().objects.create_user(
            email='other-home-seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        other_store = Store.objects.create(
            owner=other_seller, name='Other Home Store', slug='other-home-store',
            city='Nairobi', country='Kenya',
        )
        other_product = Product.objects.create(
            store=other_store, name='Second Home Parcel', slug='second-home-parcel',
            sku='HOME-SECOND', price=Decimal('500.00'), stock_quantity=2,
            weight_kg=Decimal('1.000'), length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'), height_cm=Decimal('10.00'), status='published',
        )
        CartItem.objects.create(cart=Cart.objects.get(buyer=self.buyer), product=other_product, quantity=1)
        wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(wallet, Decimal('3000.00'), 'provider:multi-home-cancel-test')
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'doorstep', 'pickup_point_id': str(point.pk),
            'home_location_token': self._home_token(point), 'payment_method': 'wallet',
        }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        initial_total = order.total_amount
        self.assertEqual(order.fulfillments.count(), 2)
        self.client.force_authenticate(self.seller)
        first = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]),
                                  {'status': 'cancelled'}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        order.refresh_from_db()
        self.assertEqual(order.home_delivery.status, HomeDeliveryStage.WAITING_FOR_PARCELS)
        self.assertEqual(order.shipping_cost,
                         order.fulfillments.filter(seller=other_seller).get().shipping_cost + Decimal('394.00'))
        self.client.force_authenticate(other_seller)
        last = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]),
                                 {'status': 'cancelled'}, format='json')
        self.assertEqual(last.status_code, 200, last.data)
        order.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(order.home_delivery.status, HomeDeliveryStage.CANCELLED)
        self.assertEqual(order.shipping_cost, Decimal('0.00'))
        self.assertEqual(wallet.available_balance, Decimal('3000.00'))
        self.assertEqual(sum(LedgerEntry.objects.filter(
            order=order, entry_type=LedgerEntryType.REFUND_COMPLETED,
        ).values_list('amount', flat=True)), initial_total)

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_pickup_quote_is_reviewed_and_persisted_without_buyer_address(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        payload = {'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk)}
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        self.assertEqual(quote.data['shipments'][0]['destination_city'], point.city)
        checkout = self.client.post(reverse('shopping:checkout'), payload, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        self.assertEqual(order.pickup_point, point)
        self.assertEqual(order.delivery_method, 'pickup_point')
        self.assertEqual(order.shipping_address, point.address)
        self.assertEqual(order.shipping_cost, Decimal(quote.data['shipping_cost']))
        self.assertEqual(order.fulfillments.first().destination_city, point.city)

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_inactive_pickup_point_is_rejected_before_order_creation(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        payload = {'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk)}
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 200)
        point.active = False
        point.save(update_fields=['active'])
        direct = APIClient()
        direct.force_authenticate(self.buyer)
        response = direct.post(reverse('shopping:checkout'), {
            **payload,
            'expected_item_subtotal': quote.data['item_subtotal'],
            'expected_shipping_cost': quote.data['shipping_cost'],
            'expected_total_amount': quote.data['total_amount'],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_pickup_address_change_requires_a_new_quote_even_if_price_is_same(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        payload = {'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk)}
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 200)
        point.address = 'Simara Mall, 3rd floor, Shop No. 42'
        point.save(update_fields=['address'])
        response = self.client.post(reverse('shopping:checkout'), {
            **payload,
            'expected_item_subtotal': quote.data['item_subtotal'],
            'expected_shipping_cost': quote.data['shipping_cost'],
            'expected_total_amount': quote.data['total_amount'],
            'expected_pickup_point_address': quote.data['pickup_point_address'],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('quote', response.data)
        self.assertFalse(Order.objects.exists())

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_pickup_point_list_only_includes_active_locations(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        public = APIClient().get(reverse('shopping:pickup-point-list'))
        self.assertEqual(public.status_code, 200)
        self.assertIn(str(point.pk), [str(item['id']) for item in public.data])
        point.active = False
        point.save(update_fields=['active'])
        refreshed = APIClient().get(reverse('shopping:pickup-point-list'))
        self.assertNotIn(str(point.pk), [str(item['id']) for item in refreshed.data])

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_multi_seller_pickup_has_separate_quoted_fulfillments(self):
        other_seller = get_user_model().objects.create_user(
            email='second-pickup-seller@example.com', password='StrongPassword123!',
            role=UserRole.SELLER, is_verified=True,
        )
        other_store = Store.objects.create(
            owner=other_seller, name='Other Pickup Store', slug='other-pickup-store',
            city='Eldoret', country='Kenya',
        )
        other_product = Product.objects.create(
            store=other_store, name='Other parcel', slug='other-parcel', sku='OTHER-PICKUP-001',
            price=Decimal('500.00'), stock_quantity=2, weight_kg=Decimal('1.000'),
            length_cm=Decimal('10.00'), width_cm=Decimal('10.00'), height_cm=Decimal('10.00'),
            status='published',
        )
        CartItem.objects.create(cart=self.buyer.cart, product=other_product, quantity=1)
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        payload = {'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk)}
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        checkout = self.client.post(reverse('shopping:checkout'), payload, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        self.assertEqual(order.fulfillments.count(), 2)
        self.assertEqual(sum((item.shipping_cost for item in order.fulfillments.all()), Decimal('0.00')), Decimal(quote.data['shipping_cost']))
        for fulfillment in order.fulfillments.all():
            self.assertEqual(fulfillment.destination_city, point.city)
            self.assertEqual(list(fulfillment.events.values_list('stage', flat=True)), [ShipmentStage.ORDER_CREATED])

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_pickup_tracking_requires_staff_milestones_before_collection(self):
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        buyer_wallet = Wallet.objects.create(user=self.buyer, currency='KES')
        credit_wallet(buyer_wallet, Decimal('2000.00'), 'provider:pickup-test')
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk),
            'payment_method': 'wallet',
        }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        fulfillment = order.fulfillments.get()
        self.assertEqual(list(fulfillment.events.values_list('stage', flat=True)), [
            ShipmentStage.ORDER_CREATED, ShipmentStage.PAYMENT_CONFIRMED,
        ])
        self.client.force_authenticate(self.seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped', 'courier_name': 'Test Logistics', 'tracking_number': 'TEST-001',
        }, format='json')
        self.assertEqual(shipped.status_code, 200, shipped.data)
        self.client.force_authenticate(self.buyer)
        premature = self.client.post(reverse('shopping:order-confirm-delivery', args=[order.pk]))
        self.assertEqual(premature.status_code, 400)
        event_url = reverse('shopping:logistics-pickup-event', args=[fulfillment.pk])
        forbidden = self.client.post(event_url, {'stage': ShipmentStage.COLLECTED_FROM_SELLER}, format='json')
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(ShipmentEvent.objects.filter(fulfillment=fulfillment).count(), 3)
        staff = get_user_model().objects.create_user(email='logistics@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=staff, role=StaffRole.LOGISTICS)
        self.client.force_authenticate(staff)
        unassigned = self.client.post(event_url, {'stage': ShipmentStage.COLLECTED_FROM_SELLER}, format='json')
        self.assertEqual(unassigned.status_code, 403)
        superadmin = get_user_model().objects.create_superuser(email='pickup-admin@example.com', password='StrongPassword123!')
        self.client.force_authenticate(superadmin)
        assigned = self.client.post(reverse('shopping:logistics-assign', args=[fulfillment.pk]), {
            'email': staff.email, 'reason': 'Assigned to the first-leg logistics team.',
        }, format='json')
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertTrue(AdminAuditLog.objects.filter(action='shipment_logistics_assigned', target_id=str(fulfillment.pk)).exists())
        original_pickup_address = order.shipping_address
        point.address = 'Updated pickup point street address'
        point.save(update_fields=['address'])
        self.client.force_authenticate(staff)
        assigned_list = self.client.get(reverse('shopping:logistics-shipments'))
        self.assertEqual(assigned_list.status_code, 200, assigned_list.data)
        self.assertEqual(len(assigned_list.data['results']), 1)
        self.assertEqual(str(assigned_list.data['results'][0]['id']), str(fulfillment.pk))
        self.assertEqual(assigned_list.data['results'][0]['pickup_address'], original_pickup_address)
        other_staff = get_user_model().objects.create_user(email='other-logistics@example.com', password='StrongPassword123!')
        UserStaffRole.objects.create(user=other_staff, role=StaffRole.LOGISTICS)
        self.client.force_authenticate(other_staff)
        other_list = self.client.get(reverse('shopping:logistics-shipments'))
        self.assertEqual(other_list.status_code, 200)
        self.assertEqual(other_list.data['results'], [])
        self.client.force_authenticate(staff)
        skipped = self.client.post(event_url, {'stage': ShipmentStage.ARRIVED_AT_PICKUP}, format='json')
        self.assertEqual(skipped.status_code, 400)
        for stage in (ShipmentStage.COLLECTED_FROM_SELLER, ShipmentStage.IN_TRANSIT, ShipmentStage.ARRIVED_AT_PICKUP):
            result = self.client.post(event_url, {'stage': stage}, format='json')
            self.assertEqual(result.status_code, 201, result.data)
        duplicate = self.client.post(event_url, {'stage': ShipmentStage.ARRIVED_AT_PICKUP}, format='json')
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.data['already_recorded'])
        self.assertEqual(ShipmentEvent.objects.filter(fulfillment=fulfillment, stage=ShipmentStage.ARRIVED_AT_PICKUP).count(), 1)
        self.client.force_authenticate(self.buyer)
        collected = self.client.post(reverse('shopping:order-confirm-delivery', args=[order.pk]))
        self.assertEqual(collected.status_code, 200, collected.data)
        self.assertTrue(fulfillment.events.filter(stage=ShipmentStage.COLLECTED_BY_BUYER, actor=self.buyer).exists())

    @override_settings(SMS_NOTIFICATIONS_ENABLED=True, NOTIFICATION_EMAILS_ENABLED=False)
    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    @patch('notifications.sms.dispatch_sms')
    def test_pickup_seller_sms_waits_for_payment_and_duplicate_callback_does_not_resend(self, send_sms, initiate_payment):
        send_sms.return_value = [SMSResult('254712345678', 'accepted', '1000', 'pickup-message')]
        initiate_payment.return_value = {'MerchantRequestID': 'merchant-pickup', 'CheckoutRequestID': 'checkout-pickup'}
        self.seller.phone_number = '0712345678'
        self.seller.save(update_fields=['phone_number'])
        self.seller.profile.notification_preferences = {'sms_seller_sales': True}
        self.seller.profile.save(update_fields=['notification_preferences'])
        point = PickupPoint.objects.get(name='AfriClay Nairobi')
        self.client.force_authenticate(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            checkout = self.client.post(reverse('shopping:checkout'), {
                'delivery_method': 'pickup_point', 'pickup_point_id': str(point.pk),
                'payment_method': 'mpesa', 'payment_option': 'pay_now',
            }, format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        self.assertFalse(SMSDelivery.objects.filter(notification__user=self.seller).exists())
        initiated = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': checkout.data['id'], 'phone_number': '254712345678',
        }, format='json')
        self.assertEqual(initiated.status_code, 201, initiated.data)
        payment = Payment.objects.get(pk=initiated.data['id'])
        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.process_mpesa_callback(self.callback(payment, 'PICKUP-SMS-1'))
        self.assertEqual(send_sms.call_count, 1)
        delivery = SMSDelivery.objects.get(notification__user=self.seller)
        self.assertIn('Seller Orders', delivery.message)
        self.assertNotIn(point.address, delivery.message)
        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.process_mpesa_callback(self.callback(payment, 'PICKUP-SMS-1'))
        self.assertEqual(send_sms.call_count, 1)

    def test_checkout_requires_a_reviewed_delivery_quote(self):
        direct_client = APIClient()
        direct_client.force_authenticate(self.buyer)
        response = direct_client.post(reverse('shopping:checkout'), {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('expected_shipping_cost', response.data)
        self.assertFalse(Order.objects.exists())

    @override_settings(PICKUP_ONLY_CHECKOUT=True)
    def test_new_address_delivery_orders_are_rejected_before_payment(self):
        self.client.force_authenticate(self.buyer)
        payload = {
            'shipping_address': '123 Main Street', 'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100', 'shipping_country': 'Kenya',
        }
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, 400)
        self.assertIn('delivery_method', quote.data)
        checkout = self.client.post(reverse('shopping:checkout'), payload, format='json')
        self.assertEqual(checkout.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_changed_delivery_quote_rejects_order_before_payment(self):
        self.client.force_authenticate(self.buyer)
        payload = {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
        }
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        self.assertEqual(quote.status_code, status.HTTP_200_OK)
        self.product.weight_kg = Decimal('8.000')
        self.product.save(update_fields=['weight_kg'])
        response = self.client.post(reverse('shopping:checkout'), {
            **payload,
            'expected_item_subtotal': quote.data['item_subtotal'],
            'expected_shipping_cost': quote.data['shipping_cost'],
            'expected_total_amount': quote.data['total_amount'],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('quote', response.data)
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 2)

    @staticmethod
    def callback(payment, receipt):
        return {
            'Body': {'stkCallback': {
                'MerchantRequestID': payment.merchant_request_id,
                'CheckoutRequestID': payment.checkout_request_id,
                'ResultCode': 0,
                'ResultDesc': 'Processed successfully.',
                'CallbackMetadata': {'Item': [
                    {'Name': 'Amount', 'Value': int(payment.amount)},
                    {'Name': 'MpesaReceiptNumber', 'Value': receipt},
                    {'Name': 'PhoneNumber', 'Value': int(payment.phone_number)},
                ]},
            }},
        }

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        NOTIFICATION_EMAILS_ENABLED=True,
    )
    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_seller_purchase_email_waits_for_payment_and_duplicate_callback_is_silent(self, initiate_payment):
        from django.core import mail
        from notifications.models import Notification

        initiate_payment.return_value = {
            'MerchantRequestID': 'merchant-email',
            'CheckoutRequestID': 'checkout-email',
        }
        self.seller.profile.notification_preferences = {'email_seller_sales': True}
        self.seller.profile.save(update_fields=['notification_preferences'])
        self.buyer.profile.notification_preferences = {'email_order_updates': False}
        self.buyer.profile.save(update_fields=['notification_preferences'])
        self.client.force_authenticate(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            checkout = self.client.post(reverse('shopping:checkout'), {
                'shipping_address': '123 Main Street',
                'shipping_city': 'Nairobi',
                'shipping_postal_code': '00100',
                'shipping_country': 'Kenya',
                'payment_method': 'mpesa',
                'payment_option': 'pay_on_delivery',
            }, format='json')
        self.assertEqual(checkout.status_code, status.HTTP_201_CREATED, checkout.data)
        self.assertEqual(mail.outbox, [])
        order = Order.objects.get(pk=checkout.data['id'])
        initiated = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk), 'phone_number': '254712345678',
        }, format='json')
        self.assertEqual(initiated.status_code, status.HTTP_201_CREATED, initiated.data)
        payment = Payment.objects.get(pk=initiated.data['id'])
        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.process_mpesa_callback(self.callback(payment, 'EMAIL220'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.seller.email])
        self.assertEqual(Notification.objects.filter(user=self.seller, title='New order ready').count(), 1)
        self.assertIn('item price is due on delivery', mail.outbox[0].body)
        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.process_mpesa_callback(self.callback(payment, 'EMAIL220'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.filter(user=self.seller, title='New order ready').count(), 1)

    def test_delivery_fee_survives_quote_checkout_and_order_views(self):
        payload = {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
            'payment_method': 'mpesa',
            'payment_option': 'pay_now',
        }
        self.client.force_authenticate(self.buyer)
        quote = self.client.post(reverse('shopping:shipping-quote'), payload, format='json')
        checkout = self.client.post(reverse('shopping:checkout'), payload, format='json')
        self.assertEqual(quote.status_code, status.HTTP_200_OK, quote.data)
        self.assertEqual(checkout.status_code, status.HTTP_201_CREATED, checkout.data)
        order = Order.objects.get(pk=checkout.data['id'])
        expected = Decimal(quote.data['shipping_cost'])
        self.assertGreater(expected, Decimal('0'))
        self.assertEqual(order.shipping_cost, expected)
        self.assertEqual(Decimal(checkout.data['shipping_cost']), expected)
        self.assertEqual(order.total_amount, order.item_subtotal + expected)

        detail = self.client.get(reverse('shopping:order-detail', args=[order.pk]))
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        self.assertEqual(Decimal(detail.data['shipping_cost']), expected)

        self.client.force_authenticate(self.seller)
        seller_orders = self.client.get(reverse('shopping:seller-order-list'))
        self.assertEqual(seller_orders.status_code, status.HTTP_200_OK, seller_orders.data)
        records = seller_orders.data.get('results', []) if isinstance(seller_orders.data, dict) else seller_orders.data
        seller_order = next(item for item in records if str(item['id']) == str(order.pk))
        self.assertEqual(Decimal(seller_order['shipping_cost']), expected)
        self.assertEqual(Decimal(seller_order['total_amount']), order.item_subtotal)

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_delivery_fee_balance_and_seller_release_are_audited(self, initiate_payment):
        initiate_payment.side_effect = [
            {'MerchantRequestID': 'merchant-delivery', 'CheckoutRequestID': 'checkout-delivery'},
            {'MerchantRequestID': 'merchant-balance', 'CheckoutRequestID': 'checkout-balance'},
        ]
        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
            'payment_method': 'mpesa',
            'payment_option': 'pay_on_delivery',
        }, format='json')
        self.assertEqual(checkout.status_code, status.HTTP_201_CREATED, checkout.data)
        self.assertEqual(Decimal(checkout.data['shipping_cost']), Decimal('220.00'))
        self.assertEqual(Decimal(checkout.data['total_amount']), Decimal('1220.00'))
        order = Order.objects.get(pk=checkout.data['id'])

        initial = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
        }, format='json')
        self.assertEqual(initial.status_code, status.HTTP_201_CREATED, initial.data)
        delivery_payment = Payment.objects.get(pk=initial.data['id'])
        self.assertEqual(delivery_payment.purpose, PaymentPurpose.DELIVERY_FEE)
        self.assertEqual(delivery_payment.amount, Decimal('220.00'))
        PaymentService.process_mpesa_callback(self.callback(delivery_payment, 'DELIVERY220'))

        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PARTIAL)
        self.assertTrue(order.delivery_fee_paid)
        self.assertEqual(order.amount_paid, Decimal('220.00'))

        self.client.force_authenticate(self.seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped',
            'courier_name': 'Speedaf',
            'tracking_number': 'SPD-1001',
        }, format='json')
        self.assertEqual(shipped.status_code, status.HTTP_200_OK, shipped.data)

        self.client.force_authenticate(self.buyer)
        balance = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
            'purpose': 'delivery_balance',
        }, format='json')
        self.assertEqual(balance.status_code, status.HTTP_201_CREATED, balance.data)
        balance_payment = Payment.objects.get(pk=balance.data['id'])
        self.assertEqual(balance_payment.amount, Decimal('1000.00'))
        PaymentService.process_mpesa_callback(self.callback(balance_payment, 'BALANCE1000'))

        seller_wallet = Wallet.objects.get(user=self.seller, currency='KES')
        self.assertEqual(seller_wallet.balance, Decimal('900.00'))
        self.assertEqual(seller_wallet.held_balance, Decimal('900.00'))

        delivered = self.client.post(
            reverse('shopping:order-confirm-delivery', args=[order.pk]),
            format='json',
        )
        self.assertEqual(delivered.status_code, status.HTTP_200_OK, delivered.data)
        seller_wallet.refresh_from_db()
        self.assertEqual(seller_wallet.balance, Decimal('900.00'))
        self.assertEqual(seller_wallet.held_balance, Decimal('0.00'))
        self.assertEqual(order.fulfillments.get().settlement_status, SettlementStatus.RELEASED)
        self.assertTrue(LedgerEntry.objects.filter(
            order=order,
            entry_type=LedgerEntryType.COMMISSION_EARNED,
            amount=Decimal('100.00'),
        ).exists())
        self.assertTrue(LedgerEntry.objects.filter(
            order=order,
            entry_type=LedgerEntryType.SELLER_PROCEEDS_RELEASE,
            amount=Decimal('900.00'),
        ).exists())

    @patch('payments.gateways.mpesa.MpesaGateway.initiate_payment')
    def test_cancelled_seller_segment_is_removed_from_delivery_balance(self, initiate_payment):
        initiate_payment.side_effect = [
            {'MerchantRequestID': 'merchant-delivery', 'CheckoutRequestID': 'checkout-delivery'},
            {'MerchantRequestID': 'merchant-balance', 'CheckoutRequestID': 'checkout-balance'},
        ]
        User = get_user_model()
        second_seller = User.objects.create_user(
            email='pod-second-seller@example.com',
            password='StrongPassword123!',
            role=UserRole.SELLER,
            is_verified=True,
        )
        second_store = Store.objects.create(
            owner=second_seller,
            name='Second POD Store',
            slug='second-pod-store',
            city='Nairobi',
            country='Kenya',
        )
        second_product = Product.objects.create(
            store=second_store,
            name='Second POD Product',
            slug='second-pod-product',
            sku='POD-002',
            price=Decimal('500.00'),
            stock_quantity=2,
            weight_kg=Decimal('1.000'),
            length_cm=Decimal('10.00'),
            width_cm=Decimal('10.00'),
            height_cm=Decimal('10.00'),
            status='published',
        )
        CartItem.objects.create(cart=self.buyer.cart, product=second_product, quantity=1)

        self.client.force_authenticate(self.buyer)
        checkout = self.client.post(reverse('shopping:checkout'), {
            'shipping_address': '123 Main Street',
            'shipping_city': 'Nairobi',
            'shipping_postal_code': '00100',
            'shipping_country': 'Kenya',
            'payment_method': 'mpesa',
            'payment_option': 'pay_on_delivery',
        }, format='json')
        order = Order.objects.get(pk=checkout.data['id'])
        initial = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
        }, format='json')
        delivery_payment = Payment.objects.get(pk=initial.data['id'])
        PaymentService.process_mpesa_callback(self.callback(delivery_payment, 'MULTIPOD440'))

        self.client.force_authenticate(self.seller)
        cancelled = self.client.patch(
            reverse('shopping:seller-order-status', args=[order.pk]),
            {'status': 'cancelled'},
            format='json',
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK, cancelled.data)

        order.refresh_from_db()
        self.assertEqual(order.item_subtotal, Decimal('500.00'))
        self.assertEqual(order.shipping_cost, Decimal('220.00'))
        self.assertEqual(order.total_amount, Decimal('720.00'))
        self.assertEqual(order.amount_paid, Decimal('220.00'))
        self.assertEqual(order.amount_due, Decimal('500.00'))

        self.client.force_authenticate(second_seller)
        shipped = self.client.patch(reverse('shopping:seller-order-status', args=[order.pk]), {
            'status': 'shipped',
            'courier_name': 'Speedaf',
            'tracking_number': 'SPD-2002',
        }, format='json')
        self.assertEqual(shipped.status_code, status.HTTP_200_OK, shipped.data)

        self.client.force_authenticate(self.buyer)
        balance = self.client.post(reverse('payments:payment-initiate'), {
            'order_id': str(order.pk),
            'phone_number': '254712345678',
            'purpose': 'delivery_balance',
        }, format='json')
        self.assertEqual(balance.status_code, status.HTTP_201_CREATED, balance.data)
        self.assertEqual(Payment.objects.get(pk=balance.data['id']).amount, Decimal('500.00'))
