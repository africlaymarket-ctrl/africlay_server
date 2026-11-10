from django.db import migrations


def seed_points(apps, schema_editor):
    PickupPoint = apps.get_model('shopping', 'PickupPoint')
    points = (
        ('AfriClay Eldoret', 'Eldoret', 'Shree Visa Oswal Community Hall, Moi Street, Uasin Gishu, Shop C6, Ground Floor', '30100'),
        ('AfriClay Nairobi', 'Nairobi', 'Simara Mall, 3rd floor, Shop No. 41', ''),
    )
    for name, city, address, postal_code in points:
        PickupPoint.objects.using(schema_editor.connection.alias).get_or_create(
            name=name, defaults={'city': city, 'address': address, 'postal_code': postal_code, 'active': True},
        )


class Migration(migrations.Migration):
    dependencies = [('shopping', '0009_pickup_point_delivery')]
    operations = [migrations.RunPython(seed_points, migrations.RunPython.noop)]
