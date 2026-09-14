# Africlay-server
This is the server Side of the Africlay ecommerce platform

## Media storage

Uploads use local `MEDIA_ROOT` by default. Set `GCS_BUCKET_NAME` (and, for local
development, `GCS_CREDENTIALS_PATH`) to use Google Cloud Storage. Object prefixes
are `avatars/`, `stores/<store-id>/`, `kyc/<store-id>/`, `products/<store-id>/`,
and `services/<store-id>/`.

After configuring credentials, verify the bucket connection with:

```powershell
./.venv/Scripts/python.exe manage.py verify_gcs
```
