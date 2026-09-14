from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('product_management', '0004_alter_productimage_image_productvariant'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='ProductVariant'),
            ],
        ),
    ]