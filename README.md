# Africlay-server
This is the server Side of the Africlay ecommerce platform

## Media storage

Uploads use local `MEDIA_ROOT` by default. Set `GS_BUCKET_NAME` (and, for local
development, `GS_CREDENTIALS_FILE`) to use private Google Cloud Storage. Object prefixes
are `avatars/`, `stores/<store-id>/`, `kyc/<store-id>/`, `products/<store-id>/`,
and `services/<store-id>/`.

GCS objects use signed URLs. Set `GS_PROJECT_ID` when the bucket is not in the
default project, and use workload identity or `GS_CREDENTIALS_FILE` for credentials.

After configuring credentials, verify the bucket connection with:

```powershell
./.venv/Scripts/python.exe manage.py verify_gcs
```
