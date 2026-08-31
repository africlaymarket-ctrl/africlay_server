import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Store',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=150)),
                ('slug', models.SlugField(max_length=160, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('status', models.CharField(choices=[('active', 'Active'), ('suspended', 'Suspended')], default='active', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='store', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
                'indexes': [models.Index(fields=['status', 'name'], name='store_manag_status_3d8c9e_idx')],
            },
        ),
    ]
