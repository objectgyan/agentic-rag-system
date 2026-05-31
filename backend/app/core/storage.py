"""MinIO/S3 object storage client."""

from io import BytesIO

from minio import Minio

from app.core.config import settings

minio_client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    secure=settings.minio_use_ssl,
)


def ensure_bucket():
    if not minio_client.bucket_exists(settings.minio_bucket):
        minio_client.make_bucket(settings.minio_bucket)


def upload_file(object_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    ensure_bucket()
    minio_client.put_object(
        settings.minio_bucket,
        object_name,
        BytesIO(data),
        len(data),
        content_type=content_type,
    )
    return f"{settings.minio_bucket}/{object_name}"


def download_file(object_name: str) -> bytes:
    response = minio_client.get_object(settings.minio_bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_file(object_name: str):
    minio_client.remove_object(settings.minio_bucket, object_name)


def get_presigned_url(object_name: str, expires_hours: int = 1) -> str:
    from datetime import timedelta
    return minio_client.presigned_get_object(
        settings.minio_bucket, object_name, expires=timedelta(hours=expires_hours)
    )
