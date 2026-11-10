from decimal import Decimal

from django.db import migrations


def set_confirmed_pins(apps, schema_editor):
    PickupPoint = apps.get_model('shopping', 'PickupPoint')
    points = (
        ('AfriClay Eldoret', Decimal('0.519008'), Decimal('35.269290')),
        ('AfriClay Nairobi', Decimal('-1.284070'), Decimal('36.826002')),
    )
    for name, latitude, longitude in points:
        # Preserve any coordinates that an administrator already corrected.
        PickupPoint.objects.using(schema_editor.connection.alias).filter(
            name=name, latitude__isnull=True, longitude__isnull=True,
        ).update(latitude=latitude, longitude=longitude)


class Migration(migrations.Migration):
    dependencies = [('shopping', '0014_pickup_point_coordinates')]
    operations = [migrations.RunPython(set_confirmed_pins, migrations.RunPython.noop)]
