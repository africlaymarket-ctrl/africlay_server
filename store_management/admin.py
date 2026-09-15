from django.contrib import admin

from authapp.models import UserRole

from .models import Store, StoreKYC


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
	list_display = ('name', 'owner', 'status', 'created_at')
	list_filter = ('status', 'created_at')
	search_fields = ('name', 'slug', 'owner__email')
	readonly_fields = ('owner', 'created_at', 'updated_at')

	def has_module_permission(self, request):
		return self._is_reviewer(request)

	def has_view_permission(self, request, obj=None):
		return self._is_reviewer(request)

	def has_change_permission(self, request, obj=None):
		return self._is_reviewer(request)

	def has_delete_permission(self, request, obj=None):
		return False

	@staticmethod
	def _is_reviewer(request):
		return request.user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]


@admin.register(StoreKYC)
class StoreKYCAdmin(admin.ModelAdmin):
	list_display = ('store', 'status', 'reviewed_by', 'submitted_at', 'reviewed_at')
	list_filter = ('status', 'submitted_at', 'reviewed_at')
	search_fields = ('store__name', 'business_name', 'business_registration_number', 'tax_identification_number')
	readonly_fields = (
		'store',
		'business_name',
		'business_registration_number',
		'tax_identification_number',
		'status',
		'submitted_at',
		'reviewed_at',
		'reviewed_by',
		'rejection_reason',
		'created_at',
		'updated_at',
	)

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def has_module_permission(self, request):
		return self._is_reviewer(request)

	def has_view_permission(self, request, obj=None):
		return self._is_reviewer(request)

	def has_change_permission(self, request, obj=None):
		return False

	@staticmethod
	def _is_reviewer(request):
		return request.user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]
