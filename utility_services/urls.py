from django.urls import path
from .views import SendSMSView

app_name = 'utility_services'

urlpatterns = [
    path('sms/send/', SendSMSView.as_view(), name='send-sms'),
]
