from minio import Minio
import logging
import os
from datetime import timedelta
client = Minio(
    ednpoint="minio:9000",
    access_key=os.environ.get("MINIO_ROOT_USER"),
    secret_key=os.environ.get("MINIO_ROOT_PASSWORD"),
    SECURE=False
)

bucket_name = "videos"
logging.basicConfig(level=logging.INFO)

if not client.bucket_exxists(bucket_name):
    client.make_bucket(bucket_name)

def upload_video_to_minio(local_video_path: str, object_key: str):

    # 上传视频到minio
    client.fput_object(bucket_name, object_key, local_video_path, content_type = "video/mp4")
    logging.info(f"Uploaded video to minio: {object_key}")


def generate_temporary_url(onject_key: str)->str:
    url = client.presigned_get_object(bucket_name, object_key, expires=timedelta(hours=1))
    return url


def download_video_from_minio(object_key:str, local_video_path:str):
    client.fget_object(bucket_name, object_key, local_video_path)
    logging.info(f"downloaded video {object_key} to {local_video_path}")



