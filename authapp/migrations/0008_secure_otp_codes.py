from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authapp', '0007_policyacceptance'),
    ]

    operations = [
        migrations.AlterField(
            model_name='otpverification',
            name='otp_code',
            field=models.CharField(max_length=128),
        ),
        migrations.AddField(
            model_name='otpverification',
            name='failed_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
