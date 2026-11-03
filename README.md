# Africlay-server
This is the server Side of the Africlay ecommerce platform

## Transactional email

Django sends verification codes, password reset codes, and account security notices
through its configured email backend. Marketplace order and booking emails are
optional; buyers can opt out and sellers must opt in from Profile > Settings.
The in-app notification remains available if an optional email fails.

To use Resend in production, verify an AfriClay sending domain in Resend, then set
these Render service variables:

- `RESEND_API_KEY`: send-only API key, stored as a secret
- `EMAIL_BACKEND`: `anymail.backends.resend.EmailBackend`
- `DEFAULT_FROM_EMAIL`: an address on the verified sending domain
- `NOTIFICATION_EMAILS_ENABLED`: `True` to enable optional order and booking emails

Leave optional notifications disabled until the sender is verified. Resend's
`onboarding@resend.dev` address is for testing and cannot deliver to marketplace
customers. Do not place the key in source control, frontend variables, or logs.
After switching backends, test registration and password reset with real inboxes,
then check Resend delivery status for a buyer and an opted-in seller.

### Marketplace email delivery

Optional order, booking, wallet, and seller updates create an `EmailDelivery` row in
the same database transaction as the in-app notification. The first send runs after
commit. Transient failures remain in the outbox and can be retried with
`python manage.py process_email_outbox --limit 100`. Run that command every five
minutes with a scheduler or worker to automate retries. It exits after processing
due rows. Confirm any additional hosting charge before scheduling it.

For delivery status, configure a Resend webhook at
`https://africlay-server-main-v4.onrender.com/api/notifications/email-events/resend/`
for `email.delivered`, `email.delivery_delayed`, `email.bounced`,
`email.complained`, `email.suppressed`, and `email.failed`. Set its signing secret
as `RESEND_WEBHOOK_SECRET` on the web service. The endpoint rejects callbacks
when that secret is missing or the signature is invalid. `PUBLIC_SITE_URL`
controls the destination for email buttons; `SUPPORT_EMAIL_ADDRESS` controls
reply-to.

The account's `/api/notifications/email-history/` route exposes only its own
recent optional email statuses. Verification and reset codes remain immediate
and are deliberately omitted from this history. Promotional email consent is
recorded separately and defaults to off; this release does not send broadcasts.

## Media storage

Media uses local `MEDIA_ROOT` for local development unless Supabase is configured.
Set all five `SUPABASE_*` storage values to enable Supabase Storage through its S3
endpoint. Production requires these values; media stays in a **private bucket** and
Django generates temporary signed URLs (one hour by default). Keep the generated S3
access key and secret only in the backend/Render environment; never put them in the
frontend. Use a bucket dedicated to Africlay media because the project stores both
public-facing product/store images and private KYC documents in it.

In Supabase, create a private Storage bucket, enable the S3 protocol, and generate an
S3 access key pair. Copy the S3 endpoint, region, access key ID, and secret from
Supabase Storage settings into the Render service variables:

- `SUPABASE_STORAGE_BUCKET`: bucket name
- `SUPABASE_S3_ENDPOINT_URL`: endpoint such as `https://PROJECT_REF.storage.supabase.co/storage/v1/s3`
- `SUPABASE_S3_REGION`: region shown in Supabase S3 settings
- `SUPABASE_S3_ACCESS_KEY_ID` and `SUPABASE_S3_SECRET_ACCESS_KEY`: generated S3 credentials
- `SUPABASE_SIGNED_URL_TTL`: optional signed URL lifetime in seconds (default `3600`)

The object prefixes remain `avatars/`, `stores/<store-id>/`, `kyc/<store-id>/`,
`products/<store-id>/`, and `services/<store-id>/`. Copy the existing production
uploads to the new bucket with their paths intact **before** deploying the new
storage configuration. For example, if `media/` contains the current files:

```sh
aws s3 sync media s3://YOUR_BUCKET_NAME \
  --endpoint-url "$SUPABASE_S3_ENDPOINT_URL" \
  --region "$SUPABASE_S3_REGION"
```

Configure the AWS CLI with the Supabase S3 access key and secret first. If production
uploads currently live in GCS, sync from that bucket instead of this local `media/`
folder. Verify the deployed connection after setting the Render variables:

```sh
python manage.py verify_supabase_storage
```

The check uploads a temporary object, reads it back, generates a signed HTTPS URL,
and removes the check object. Image URLs are signed, so the frontend must load the
current URL returned by the API rather than cache it indefinitely.

## Supabase database access

AfriClay uses Django's direct PostgreSQL connection for application data. The
frontend does not query the Supabase Data API, so Django-managed tables must not
be exposed to the `anon` or `authenticated` Postgres roles.

Migration `core.0001_secure_supabase_public_schema` enables RLS on every existing
table in `public`, removes table and sequence privileges from those two API roles,
and changes the migration role's default privileges so future Django tables are
deny-by-default. It intentionally does not use `FORCE ROW LEVEL SECURITY`; the
table owner used by Django therefore keeps normal direct access.

After deploying migrations, verify the live database with:

```sh
python manage.py audit_public_database_access --fail-on-findings
```

The production Docker startup runs this audit automatically after migrations,
so it also works on Render plans without interactive Shell access. Check the
deployment logs for the successful `Public database access is locked down`
message. A remaining finding stops that deploy before Gunicorn starts.

This database hardening is separate from Supabase Storage bucket policies. Product
media access and private KYC document access must continue to be managed through
their existing Storage configuration.

## BlessedTexts SMS

Set `BLESSEDTEXTS_API_KEY` and `BLESSEDTEXTS_SENDER_ID` on the backend service.
Set `SMS_NOTIFICATIONS_ENABLED=True` only after the sender and credit balance
have been confirmed. Do not put the key in the frontend, repository, or a
checked-in environment file. The authenticated superadmin endpoint
`POST /api/common/sms/send/` accepts one phone or up to 20 numbers and is
throttled. It sends a POST to BlessedTexts, never a GET containing the key.

Marketplace SMS preferences are separate from email and default off. An opted-in
member with a valid Kenyan mobile number can receive generic order, booking,
or wallet updates. `/api/notifications/sms-history/` shows only that member's
submission statuses. `accepted` means the provider accepted the request; the
supplied API does not document a delivery callback or idempotency key. Unknown
outcomes are not retried automatically; inspect the provider dashboard before
resubmitting. The SMS channel is not used for password or email verification.

## Persistent API smoke test

Run the core endpoint workflow against the configured database with:

```powershell
./.venv/Scripts/python.exe manage.py smoke_api
```

This creates uniquely named smoke records through the store, KYC, product,
and service endpoints so the results can be inspected in the persistent
database. Use a development database for this command.

## Seller payouts

Seller earnings are held until delivery is confirmed, then become available in
the seller wallet. A withdrawal request reserves the full eligible balance of
the selected shopping or earnings source before an asynchronous M-Pesa B2C
request is sent. A successful provider result debits the reserved amount; a
failed result releases it. Unknown results remain reserved for
super-administrator reconciliation so the same funds cannot be paid twice.

Configure these additional secrets on Render before enabling withdrawals:

- `MPESA_B2C_INITIATOR_NAME`: production API operator username
- `MPESA_B2C_SECURITY_CREDENTIAL`: operator password encrypted with the
  production M-Pesa certificate
- `MPESA_B2C_SHORTCODE`: optional override when B2C does not use `MPESA_SHORTCODE`
- `MPESA_B2C_COMMAND_ID`: normally `BusinessPayment`
- `MPESA_B2C_MIN_AMOUNT`: whole-KES minimum, default `10.00`
- `MPESA_B2C_MAX_AMOUNT`: whole-KES maximum, default `250000.00`

The collection passkey is not a B2C credential. Keep the B2C operator password
and encrypted security credential out of source control and frontend variables.
