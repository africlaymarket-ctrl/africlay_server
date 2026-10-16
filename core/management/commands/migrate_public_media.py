from django.core.files.storage import default_storage, storages
from django.core.management.base import BaseCommand, CommandError

from core.images import marketplace_image_variants, optimized_webp
from core.models import (
    product_image_upload_path, product_thumbnail_upload_path,
    service_image_upload_path, service_thumbnail_upload_path,
    store_banner_upload_path, store_logo_upload_path,
)
from product_management.models import ProductImage
from service_management.models import ServiceImage
from store_management.models import Store


class Command(BaseCommand):
    help = 'Copy legacy marketplace images to public storage and generate optimized WebP variants.'

    def add_arguments(self, parser):
        parser.add_argument('--delete-source', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--ignore-errors', action='store_true')

    def handle(self, *args, **options):
        self.public_storage = storages['public_media']
        self.delete_source = options['delete_source']
        self.dry_run = options['dry_run']
        migrated = 0
        skipped = 0
        failures = []

        jobs = [
            ('product', item, self._migrate_product_image)
            for item in ProductImage.objects.select_related('product').iterator()
        ]
        jobs.extend(
            ('service', item, self._migrate_service_image)
            for item in ServiceImage.objects.select_related('service').iterator()
        )
        jobs.extend(
            ('store', store, self._migrate_store)
            for store in Store.objects.iterator()
        )

        for kind, instance, migrate in jobs:
            try:
                changed = migrate(instance)
                migrated += int(changed)
                skipped += int(not changed)
            except Exception as error:
                failures.append(f'{kind} {instance.pk}: {error}')
                self.stderr.write(self.style.WARNING(failures[-1]))

        summary = f'Public media migration complete: migrated={migrated}, unchanged={skipped}, failed={len(failures)}.'
        self.stdout.write(self.style.SUCCESS(summary) if not failures else self.style.WARNING(summary))
        if failures and not options['ignore_errors']:
            raise CommandError('Some media could not be migrated. Re-run after resolving the reported files.')

    def _read(self, field):
        name = getattr(field, 'name', '')
        if not name:
            return None
        storage = self.public_storage if name.startswith('public/') else default_storage
        with storage.open(name, 'rb') as source:
            return source.read()

    def _save(self, name, content):
        if self.dry_run:
            return name
        return self.public_storage.save(name, content)

    def _delete_legacy(self, name):
        if self.dry_run or not self.delete_source or not name or name.startswith('public/'):
            return
        default_storage.delete(name)

    def _migrate_product_image(self, instance):
        old_name = instance.image.name
        if old_name.startswith('public/') and instance.thumbnail and instance.thumbnail.name:
            return False
        source = self._read(instance.image)
        if source is None:
            return False
        detail, thumbnail = marketplace_image_variants(source)
        detail_name = old_name if old_name.startswith('public/') else self._save(
            product_image_upload_path(instance, detail.name), detail,
        )
        thumbnail_name = self._save(product_thumbnail_upload_path(instance, thumbnail.name), thumbnail)
        if not self.dry_run:
            ProductImage.objects.filter(pk=instance.pk).update(image=detail_name, thumbnail=thumbnail_name)
            self._delete_legacy(old_name)
        return True

    def _migrate_service_image(self, instance):
        old_name = instance.image.name
        if old_name.startswith('public/') and instance.thumbnail and instance.thumbnail.name:
            return False
        source = self._read(instance.image)
        if source is None:
            return False
        detail, thumbnail = marketplace_image_variants(source)
        detail_name = old_name if old_name.startswith('public/') else self._save(
            service_image_upload_path(instance, detail.name), detail,
        )
        thumbnail_name = self._save(service_thumbnail_upload_path(instance, thumbnail.name), thumbnail)
        if not self.dry_run:
            ServiceImage.objects.filter(pk=instance.pk).update(image=detail_name, thumbnail=thumbnail_name)
            self._delete_legacy(old_name)
        return True

    def _migrate_store(self, store):
        updates = {}
        legacy_names = []
        changed = False
        for field_name, upload_path, max_dimension, quality in (
            ('logo', store_logo_upload_path, 640, 80),
            ('banner', store_banner_upload_path, 1600, 82),
        ):
            field = getattr(store, field_name)
            old_name = getattr(field, 'name', '')
            if not old_name or old_name.startswith('public/'):
                continue
            source = self._read(field)
            optimized = optimized_webp(source, max_dimension=max_dimension, quality=quality)
            updates[field_name] = self._save(upload_path(store, optimized.name), optimized)
            legacy_names.append(old_name)
            changed = True
        if updates and not self.dry_run:
            Store.objects.filter(pk=store.pk).update(**updates)
            for old_name in legacy_names:
                self._delete_legacy(old_name)
        return changed
