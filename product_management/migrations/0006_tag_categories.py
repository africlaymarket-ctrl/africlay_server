from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product_management', '0005_remove_product_variants'),
    ]

    operations = [
        migrations.AddField(
            model_name='tag',
            name='categories',
            field=models.ManyToManyField(
                blank=True,
                related_name='suggested_tags',
                to='product_management.category',
            ),
        ),
    ]
