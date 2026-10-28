from django.db import migrations


SECURE_PUBLIC_SCHEMA_SQL = r"""
DO $secure_public_schema$
DECLARE
    api_role text;
    target record;
BEGIN
    -- AfriClay's browser and mobile clients use the Django API, not PostgREST.
    -- Keep Django's direct database connection operational while removing the
    -- implicit Data API access granted to Supabase's public API roles.
    FOR target IN
        SELECT namespace.nspname AS schema_name, relation.relname AS object_name
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            target.schema_name,
            target.object_name
        );

        FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
                    target.schema_name,
                    target.object_name,
                    api_role
                );
            END IF;
        END LOOP;
    END LOOP;

    FOR target IN
        SELECT namespace.nspname AS schema_name, relation.relname AS object_name
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind = 'S'
    LOOP
        FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
                    target.schema_name,
                    target.object_name,
                    api_role
                );
            END IF;
        END LOOP;
    END LOOP;

    -- Existing Supabase projects historically exposed new public objects by
    -- default. Revoke those defaults for objects created by this migration
    -- role so future Django migrations are deny-by-default over PostgREST.
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                'REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
                'ON TABLES FROM %I',
                current_user,
                api_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                'REVOKE USAGE, SELECT, UPDATE ON SEQUENCES FROM %I',
                current_user,
                api_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                'REVOKE EXECUTE ON FUNCTIONS FROM %I',
                current_user,
                api_role
            );
        END IF;
    END LOOP;
END
$secure_public_schema$;
"""


def secure_public_schema(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SECURE_PUBLIC_SCHEMA_SQL)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ('admin', '0003_logentry_add_action_flag_choices'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('authapp', '0006_dashboardaccessrequest'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('messaging', '0001_initial'),
        ('notifications', '0001_initial'),
        ('payments', '0007_backfill_wallet_balance_pockets'),
        ('product_management', '0009_productimage_thumbnail_alter_productimage_image'),
        ('reviews', '0001_initial'),
        ('service_management', '0004_serviceimage_thumbnail_alter_serviceimage_image'),
        ('sessions', '0001_initial'),
        ('shopping', '0008_order_amount_paid_order_delivery_fee_paid_and_more'),
        ('store_management', '0006_alter_store_banner_alter_store_logo'),
    ]

    operations = [
        migrations.RunPython(
            secure_public_schema,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
