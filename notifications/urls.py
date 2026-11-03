from django.urls import path
from .views import EmailDeliveryListView, NotificationDetailView, NotificationListView, NotificationMarkAllReadView, SMSDeliveryListView
from .webhooks import resend_email_events

app_name = 'notifications'

urlpatterns = [
    path('', NotificationListView.as_view(), name='notification-list'),
    path('email-history/', EmailDeliveryListView.as_view(), name='email-history'),
    path('sms-history/', SMSDeliveryListView.as_view(), name='sms-history'),
    path('email-events/resend/', resend_email_events, name='resend-email-events'),
    path('<uuid:pk>/', NotificationDetailView.as_view(), name='notification-detail'),
    path('mark-all-read/', NotificationMarkAllReadView.as_view(), name='notification-mark-all-read'),
]
