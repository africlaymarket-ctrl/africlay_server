from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.test import TestCase
from django.utils import timezone

from authapp.models import UserRole
from authapp.utils import generate_access_token
from core.test_utils import approve_store_kyc, create_store, create_verified_user, authenticate_client
from store_management.models import Store, StoreKYC, StoreKYCStatus
from .models import Category, Product, ProductImage, Tag


User = get_user_model()


class ProductApiTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.seller = create_verified_user(UserRole.SELLER, 'product-seller')
		self.other_seller = create_verified_user(UserRole.SELLER, 'product-other-seller')
		self.store = create_store(self.seller, 'Nairobi Clay Studio')
		self.other_store = create_store(self.other_seller, 'Mombasa Ceramics')
		self.default_category = Category.objects.create(name='General Goods', slug='general-goods')
		self.product_data = {
			'category': str(self.default_category.id),
			'name': 'Hand-thrown Mug',
			'slug': 'hand-thrown-mug',
			'description': 'A locally made ceramic mug.',
			'sku': 'MUG-001',
			'price': '1800.00',
			'currency': 'KES',
			'stock_quantity': 12,
			'weight_kg': '0.500',
			'length_cm': '15.00',
			'width_cm': '12.00',
			'height_cm': '10.00',
		}

	def authenticate_as(self, user):
		authenticate_client(self.client, user)

	def approve_store_kyc(self, store=None):
		approve_store_kyc(store or self.store)

	def test_verified_seller_can_create_product_for_owned_store(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			self.product_data,
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['store'], str(self.store.id))
		self.assertEqual(response.data['price'], '1800.00')
		self.assertEqual(response.data['price_unit'], '')

	def test_sale_unit_round_trips_through_seller_and_public_product_api(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		created = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published', 'price_unit': 'piece'}, format='json',
		)
		self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
		self.assertEqual(created.data['price_unit'], 'piece')
		updated = self.client.patch(
			reverse('product_management:product-manage-detail', args=[created.data['id']]),
			{'price_unit': 'per bunch'}, format='json',
		)
		self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
		self.assertEqual(updated.data['price_unit'], 'bunch')
		self.client.credentials()
		catalogue = self.client.get(reverse('product_management:product-list'))
		self.assertEqual(catalogue.data['results'][0]['price_unit'], 'bunch')

	def test_seller_can_publish_named_price_choices(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published', 'price_unit': 'piece', 'length_cm': None, 'width_cm': None, 'height_cm': None, 'price_options': [
				{'label': 'Small', 'price': '80.00', 'stock_quantity': 3, 'weight_kg': '1.000'},
				{'label': 'Large', 'price': '150.00', 'stock_quantity': 2, 'weight_kg': '2.000'},
			]}, format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
		self.assertEqual(response.data['price'], '80.00')
		self.assertEqual(response.data['stock_quantity'], 5)
		self.assertEqual(len(response.data['price_options']), 2)
		self.client.credentials()
		catalogue = self.client.get(reverse('product_management:product-list'))
		self.assertEqual(len(catalogue.data['results'][0]['price_options']), 2)
		self.authenticate_as(self.seller)
		changed_base = self.client.patch(
			reverse('product_management:product-manage-detail', args=[response.data['id']]),
			{'price': '1.00', 'stock_quantity': 999}, format='json',
		)
		self.assertEqual(changed_base.status_code, status.HTTP_200_OK, changed_base.data)
		self.assertEqual(changed_base.data['price'], '80.00')
		self.assertEqual(changed_base.data['stock_quantity'], 5)

	def test_seller_cannot_create_product_for_another_store(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'store': str(self.other_store.id)},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data['store'], str(self.store.id))

	def test_public_catalogue_hides_drafts(self):
		self.authenticate_as(self.seller)
		create_response = self.client.post(
			reverse('product_management:product-manage-list'),
			self.product_data,
			format='json',
		)
		self.client.credentials()

		response = self.client.get(reverse('product_management:product-list'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['results'], [])
		self.assertEqual(create_response.data['status'], 'draft')

	def test_public_catalogue_returns_published_product(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		create_response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'},
			format='json',
		)
		self.client.credentials()

		response = self.client.get(reverse('product_management:product-list'))

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['slug'], create_response.data['slug'])

	def test_legacy_archived_product_can_be_republished_without_dimensions(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		created = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'archived', 'length_cm': None,
			 'width_cm': None, 'height_cm': None}, format='json',
		)
		self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
		url = reverse('product_management:product-manage-detail', args=[created.data['id']])
		partial = self.client.patch(url, {
			'status': 'published', 'length_cm': '15.00',
		}, format='json')
		self.assertEqual(partial.status_code, status.HTTP_400_BAD_REQUEST, partial.data)
		self.assertIn('shipping', partial.data)
		self.assertEqual(Product.objects.get(pk=created.data['id']).status, 'archived')
		restored = self.client.patch(url, {'status': 'published'}, format='json')
		self.assertEqual(restored.status_code, status.HTTP_200_OK, restored.data)
		self.assertEqual(restored.data['status'], 'published')
		self.assertIsNone(restored.data['length_cm'])
		self.assertIsNone(restored.data['width_cm'])
		self.assertIsNone(restored.data['height_cm'])
		self.client.credentials()
		catalogue = self.client.get(reverse('product_management:product-list'))
		self.assertEqual(catalogue.data['count'], 1)

	def test_public_catalogue_filters_by_category_and_tag_slug(self):
		self.approve_store_kyc()
		category = Category.objects.create(name='Ceramics', slug='ceramics')
		tag = Tag.objects.create(name='Studio Made', slug='studio-made')
		other_tag = Tag.objects.create(name='Decor', slug='decor')
		self.authenticate_as(self.seller)
		matching = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published', 'category': str(category.id), 'tags': [str(tag.id)]},
			format='json',
		)
		other = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'name': 'Decor Bowl', 'slug': 'decor-bowl', 'sku': 'BOWL-001', 'status': 'published', 'category': str(category.id), 'tags': [str(other_tag.id)]},
			format='json',
		)
		self.assertEqual(matching.status_code, status.HTTP_201_CREATED)
		self.assertEqual(other.status_code, status.HTTP_201_CREATED)

		self.client.credentials()
		response = self.client.get(reverse('product_management:product-list'), {'category': 'ceramics', 'tag': 'studio-made'})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual([item['slug'] for item in response.data['results']], ['hand-thrown-mug'])

	def test_main_and_subcategory_include_untagged_descendants(self):
		self.approve_store_kyc()
		root = Category.objects.create(name='Home', slug='home')
		child = Category.objects.create(name='Kitchen', slug='kitchen', parent=root)
		leaf = Category.objects.create(name='Mugs', slug='mugs', parent=child)
		other = Category.objects.create(name='Garden', slug='garden')
		self.authenticate_as(self.seller)
		for name, slug, sku, category in [
			('Clay mug', 'clay-mug', 'CLAY-1', leaf),
			('Kitchen tray', 'kitchen-tray', 'TRAY-1', child),
			('Plant pot', 'plant-pot', 'POT-1', other),
		]:
			response = self.client.post(reverse('product_management:product-manage-list'), {
				**self.product_data, 'name': name, 'slug': slug, 'sku': sku,
				'category': str(category.id), 'tags': [], 'status': 'published',
			}, format='json')
			self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.client.credentials()
		url = reverse('product_management:product-list')
		for slug, expected in [('home', {'clay-mug', 'kitchen-tray'}), ('kitchen', {'clay-mug', 'kitchen-tray'}), ('mugs', {'clay-mug'}), ('missing', set())]:
			response = self.client.get(url, {'category': slug})
			self.assertEqual(response.status_code, status.HTTP_200_OK)
			self.assertEqual({item['slug'] for item in response.data['results']}, expected)

	def test_search_finds_untagged_products_by_name_description_and_category(self):
		self.approve_store_kyc()
		category = Category.objects.create(name='Handmade Ceramics', slug='handmade-ceramics')
		self.authenticate_as(self.seller)
		response = self.client.post(reverse('product_management:product-manage-list'), {
			**self.product_data, 'name': 'Tea cup', 'slug': 'tea-cup', 'sku': 'CUP-1',
			'description': 'Stoneware for hot drinks', 'category': str(category.id),
			'tags': [], 'status': 'published',
		}, format='json')
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.client.credentials()
		url = reverse('product_management:product-list')
		for term in ('tea cup', 'stoneware', 'ceramics'):
			result = self.client.get(url, {'search': term})
			self.assertEqual(result.data['count'], 1)
			self.assertEqual(result.data['results'][0]['slug'], 'tea-cup')

	def test_publishing_requires_category_even_when_tags_are_present(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		response = self.client.post(reverse('product_management:product-manage-list'), {
			**self.product_data, 'category': None, 'status': 'published',
		}, format='json')
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('category', response.data)

	def test_public_catalogue_can_retrieve_product_by_slug(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		second = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'},
			format='json',
		)
		self.client.credentials()

		response = self.client.get(
			reverse('product_management:product-detail', kwargs={'slug': self.product_data['slug']}),
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['sku'], self.product_data['sku'])

	def test_product_rejects_negative_price_and_stock(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'price': '-1.00', 'stock_quantity': -2},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_seller_cannot_publish_product_before_kyc_approval(self):
		self.authenticate_as(self.seller)
		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'}, format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('KYC', str(response.data))

	def test_admin_can_create_category_and_tag_then_seller_assigns_them(self):
		admin = User.objects.create_superuser(email='admin@example.com', password='AdminPassword123!')
		self.authenticate_as(admin)
		category_response = self.client.post(
			reverse('product_management:category-list'),
			{'name': 'Ceramics', 'slug': 'ceramics'}, format='json',
		)
		tag_response = self.client.post(
			reverse('product_management:tag-list'),
			{'name': 'Artisan Finish', 'slug': 'artisan-finish', 'categories': [category_response.data['id']]}, format='json',
		)
		self.assertEqual(category_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(tag_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual([str(category_id) for category_id in tag_response.data['categories']], [category_response.data['id']])

		self.authenticate_as(self.seller)
		response = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'category': category_response.data['id'], 'tags': [tag_response.data['id']]},
			format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(str(response.data['category']), category_response.data['id'])
		self.assertEqual([str(tag_id) for tag_id in response.data['tags']], [tag_response.data['id']])

	def test_verified_seller_can_create_main_and_child_category_path(self):
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:seller-category-path-create'),
			{'parent_name': 'Farm and Agriculture', 'child_name': 'Irrigation Equipment'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertTrue(response.data['created'])
		self.assertIsNone(response.data['parent']['parent'])
		self.assertEqual(str(response.data['child']['parent']), response.data['parent']['id'])
		self.assertTrue(Category.objects.filter(name='Irrigation Equipment', parent__name='Farm and Agriculture').exists())

	def test_seller_category_path_reuses_existing_categories(self):
		parent = Category.objects.create(name='Agriculture', slug='agriculture')
		child = Category.objects.create(name='Farm Tools', slug='farm-tools', parent=parent)
		category_count = Category.objects.count()
		self.authenticate_as(self.seller)

		response = self.client.post(
			reverse('product_management:seller-category-path-create'),
			{'parent': str(parent.id), 'child_name': 'farm tools'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertFalse(response.data['created'])
		self.assertEqual(response.data['child']['id'], str(child.id))
		self.assertEqual(Category.objects.count(), category_count)

	def test_buyer_cannot_create_category_path(self):
		buyer = create_verified_user(UserRole.BUYER, 'category-buyer')
		self.authenticate_as(buyer)

		response = self.client.post(
			reverse('product_management:seller-category-path-create'),
			{'parent_name': 'Home', 'child_name': 'Lighting'},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_seller_can_upload_product_image_through_endpoint(self):
		self.authenticate_as(self.seller)
		product_response = self.client.post(
			reverse('product_management:product-manage-list'), self.product_data, format='json',
		)
		image = SimpleUploadedFile('mug.gif', b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;', content_type='image/gif')
		response = self.client.post(
			reverse('product_management:product-image-list', kwargs={'pk': product_response.data['id']}),
			{'image': image, 'alt_text': 'Hand-thrown mug', 'is_primary': True},
			format='multipart',
		)
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertIn('products/', response.data['image'])
		self.assertIn('/thumb/', response.data['thumbnail'])
		self.assertTrue(response.data['image'].endswith('.webp'))

	def test_seller_can_change_cover_and_delete_uploaded_photos(self):
		self.authenticate_as(self.seller)
		product = self.client.post(reverse('product_management:product-manage-list'), self.product_data, format='json')
		list_url = reverse('product_management:product-image-list', kwargs={'pk': product.data['id']})
		def upload(name):
			photo = SimpleUploadedFile(name, b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;', content_type='image/gif')
			return self.client.post(list_url, {'image': photo}, format='multipart')
		first, second = upload('first.gif'), upload('second.gif')
		self.assertEqual(first.status_code, 201)
		self.assertEqual(second.status_code, 201)
		first_image = ProductImage.objects.get(pk=first.data['id'])
		second_image = ProductImage.objects.get(pk=second.data['id'])
		self.assertTrue(first_image.is_primary)
		self.assertFalse(second_image.is_primary)
		second_url = reverse('product_management:product-image-detail', kwargs={
			'pk': product.data['id'], 'image_id': second_image.pk,
		})
		changed = self.client.patch(second_url, {'is_primary': True}, format='json')
		self.assertEqual(changed.status_code, 200, changed.data)
		first_image.refresh_from_db()
		second_image.refresh_from_db()
		self.assertFalse(first_image.is_primary)
		self.assertTrue(second_image.is_primary)
		self.authenticate_as(self.other_seller)
		self.assertEqual(self.client.delete(second_url).status_code, 404)
		self.assertTrue(ProductImage.objects.filter(pk=second_image.pk).exists())
		self.authenticate_as(self.seller)
		self.assertEqual(self.client.patch(second_url, {'image': 'raw-file'}, format='json').status_code, 400)
		with patch.object(second_image.image.storage, 'delete', side_effect=OSError('storage unavailable')):
			failed = self.client.delete(second_url)
		self.assertEqual(failed.status_code, 400)
		self.assertNotIn('storage unavailable', str(failed.data))
		self.assertTrue(ProductImage.objects.filter(pk=second_image.pk).exists())
		self.assertEqual(self.client.delete(second_url).status_code, 204)
		self.assertFalse(ProductImage.objects.filter(pk=second_image.pk).exists())
		first_image.refresh_from_db()
		self.assertTrue(first_image.is_primary)
		self.assertFalse(second_image.image.storage.exists(second_image.image.name))
		self.assertFalse(second_image.thumbnail.storage.exists(second_image.thumbnail.name))
		self.assertEqual(upload('replacement.gif').status_code, 201)

	def test_product_image_count_is_enforced_by_backend(self):
		self.authenticate_as(self.seller)
		product = self.client.post(reverse('product_management:product-manage-list'), self.product_data, format='json')
		url = reverse('product_management:product-image-list', kwargs={'pk': product.data['id']})
		for index in range(8):
			photo = SimpleUploadedFile(f'{index}.gif', b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;', content_type='image/gif')
			self.assertEqual(self.client.post(url, {'image': photo}, format='multipart').status_code, 201)
		extra = SimpleUploadedFile('extra.gif', b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;', content_type='image/gif')
		response = self.client.post(url, {'image': extra}, format='multipart')
		self.assertEqual(response.status_code, 400)
		self.assertEqual(ProductImage.objects.filter(product_id=product.data['id']).count(), 8)

	def test_public_catalogue_honours_limit_and_returns_only_cover_image(self):
		self.approve_store_kyc()
		self.authenticate_as(self.seller)
		first = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'status': 'published'},
			format='json',
		)
		second = self.client.post(
			reverse('product_management:product-manage-list'),
			{**self.product_data, 'name': 'Second mug', 'slug': 'second-mug', 'sku': 'MUG-002', 'status': 'published'},
			format='json',
		)
		for index in range(2):
			image = SimpleUploadedFile(
				f'mug-{index}.gif',
				b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;',
				content_type='image/gif',
			)
			self.client.post(
				reverse('product_management:product-image-list', kwargs={'pk': first.data['id']}),
				{'image': image, 'is_primary': index == 0, 'display_order': index},
				format='multipart',
			)
		second_image = SimpleUploadedFile(
			'second.gif',
			b'GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02L\x01\x00;',
			content_type='image/gif',
		)
		self.client.post(
			reverse('product_management:product-image-list', kwargs={'pk': second.data['id']}),
			{'image': second_image, 'is_primary': True},
			format='multipart',
		)
		self.client.credentials()

		response = self.client.get(reverse('product_management:product-list'), {'limit': 1})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 2)
		self.assertEqual(len(response.data['results']), 1)
		self.assertEqual(len(response.data['results'][0]['images']), 1)
		self.assertIn('/thumb/', response.data['results'][0]['images'][0]['image'])
		all_products = self.client.get(reverse('product_management:product-list'), {'limit': 2})
		first_product = next(item for item in all_products.data['results'] if item['slug'] == 'hand-thrown-mug')
		self.assertEqual(len(first_product['images']), 1)
