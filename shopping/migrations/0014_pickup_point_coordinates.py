from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('shopping', '0013_assigned_logistics')]

    operations = [
        migrations.AddField(
            model_name='pickuppoint', name='latitude',
            field=models.DecimalField(blank=True, null=True, max_digits=9, decimal_places=6,
                                      validators=[MinValueValidator(-90), MaxValueValidator(90)]),
        ),
        migrations.AddField(
            model_name='pickuppoint', name='longitude',
            field=models.DecimalField(blank=True, null=True, max_digits=9, decimal_places=6,
                                      validators=[MinValueValidator(-180), MaxValueValidator(180)]),
        ),
    ]
