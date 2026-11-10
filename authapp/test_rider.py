import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from authapp.models import AdminAuditLog, RiderApplication, RiderApplicationStatus, StaffRole
from notifications.models import Notification
from shopping.models import PickupPoint


class RiderApplicationTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('rider-admin@example.com', 'StrongPassword123!')
        self.rider = get_user_model().objects.create_user(
            email='new-rider@example.com', password='StrongPassword123!',
            phone_number='254712345678', is_verified=True,
        )
        PickupPoint.objects.create(name='Nairobi rider test point', city='Nairobi', address='Test address')
        PickupPoint.objects.create(name='Eldoret rider test point', city='Eldoret', address='Test address')

    @staticmethod
    def file(name):
        return SimpleUploadedFile(name, b'test private image bytes', content_type='image/jpeg')

    def test_rider_application_review_and_private_document_access(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.client.force_authenticate(self.rider)
            create = self.client.post(reverse('authapp:rider-application-mine'), {
                'city': 'Nairobi', 'vehicle_type': 'Motorcycle', 'vehicle_registration': 'KMX 100A',
                'licence_number': 'DL-100', 'sms_alerts': 'true',
                'identity_document': self.file('id.jpg'), 'licence_document': self.file('licence.jpg'),
            }, format='multipart')
            self.assertEqual(create.status_code, 201, create.data)
            app_id = create.data['id']
            self.assertIsNone(create.data['identity_document_url'])
            self.assertEqual(self.client.post(reverse('authapp:rider-application-mine'), {}, format='multipart').status_code, 400)
            doc_url = reverse('authapp:rider-application-document', args=[app_id, 'identity'])
            self.assertEqual(self.client.get(doc_url).status_code, 403)
            self.client.force_authenticate(self.admin)
            pending = self.client.get(reverse('authapp:rider-application-queue'))
            self.assertEqual(len(pending.data['results']), 1)
            document_response = self.client.get(doc_url)
            self.assertEqual(document_response.status_code, 200)
            self.assertEqual(b''.join(document_response.streaming_content), b'test private image bytes')
            document_response.close()
            reviewed = self.client.post(reverse('authapp:rider-application-review', args=[app_id]),
                {'decision': 'approved', 'reason': 'Identity and driving licence checked.'}, format='json')
            self.assertEqual(reviewed.status_code, 200, reviewed.data)
            self.assertTrue(self.rider.has_staff_role(StaffRole.LOGISTICS))
            self.assertTrue(Notification.objects.filter(user=self.rider, title='Rider application reviewed').exists())
            self.assertTrue(AdminAuditLog.objects.filter(action='rider_application_approved').exists())
            self.assertEqual(self.client.post(reverse('authapp:rider-application-review', args=[app_id]),
                {'decision': 'approved', 'reason': 'Again'}, format='json').status_code, 400)

    def test_rejected_application_can_be_resubmitted_without_old_docs_becoming_public(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            app = RiderApplication.objects.create(user=self.rider, city='Nairobi', vehicle_type='Motorcycle',
                vehicle_registration='KMX 100A', licence_number='DL-100',
                identity_document='kyc/riders/old-id.jpg', licence_document='kyc/riders/old-licence.jpg',
                status=RiderApplicationStatus.REJECTED, review_reason='Document unclear')
            self.client.force_authenticate(self.rider)
            result = self.client.post(reverse('authapp:rider-application-mine'), {
                'city': 'Eldoret', 'vehicle_type': 'Motorcycle', 'vehicle_registration': 'KMX 101A',
                'licence_number': 'DL-101', 'identity_document': self.file('new-id.jpg'),
                'licence_document': self.file('new-licence.jpg'), 'sms_alerts': 'false',
            }, format='multipart')
            self.assertEqual(result.status_code, 201, result.data)
            app.refresh_from_db()
            self.assertEqual(app.status, RiderApplicationStatus.PENDING)
            self.assertEqual(app.city, 'Eldoret')
            self.assertIsNone(result.data['identity_document_url'])
