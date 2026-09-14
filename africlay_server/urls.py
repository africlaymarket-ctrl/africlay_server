from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView


urlpatterns = [
    path('admin/', admin.site.urls),

    # Authentication & Accounts
    path('api/auth/', include('authapp.urls', namespace='authapp')),
    path('api/stores/', include('store_management.urls', namespace='store_management')),
    path('api/products/', include('product_management.urls', namespace='product_management')),
    path('api/services/', include('service_management.urls', namespace='service_management')),
    path('api/cart/', include('shopping.urls', namespace='shopping')),
    path('api/common/', include('utility_services.urls', namespace='utility_services')),

    # DRF Spectacular OpenAPI & API Docs
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
