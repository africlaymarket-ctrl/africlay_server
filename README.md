# Africlay-server
This is the server Side of the Africlay ecommerce platform

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
