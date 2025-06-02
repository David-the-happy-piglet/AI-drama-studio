
from threading import Thread
from flask import Flask, request, jsonify
from Redis import pop_task, push_task
from Minio import upload_video_to_minio, generate_temporary_url
from Database import insert_video_record, get_video_by_id, update_video_name, get_video_by_url, update_video_status
from Download import download_video, fetch_video_info
import logging
import os
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/upload', methods = ['POST'])
def upload_video():
    data = request.json
    url = data.get('url')
    num = data.get('num')
    try:
        fetch_video_info(url, num)
        return jsonify({"message": "视频抓取成功"}), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500


TMP_PATH = os.environ.get("VIDEO_TEMP_DIR","/tmp/videos")
os.makedirs(TMP_PATH, exist_ok=True)
def download_worker():
    while True:
        url = pop_task(os.environ.get("REDIS_URL_QUEUE", "url_queue"))["url"]
        if url:
            try:
                row  =  get_video_by_url(url)
                id = row[0]
                local_path = os.path.join(TMP_PATH, f"{id}.mp4")

                download_video(url, local_path)
                update_video_status(row[0], 'downloaded')
                
                push_task(os.environ.get("REDIS_VIDEO_QUEUE", "video_queue"), {"local_path":local_path, "minio_key":f"youtube_video/{id}.mp4"})

            except Exception as e:
                push_task(os.environ.get("REDIS_VIDEO_QUEUE", "video_queue"), {"local_path":local_path, "minio_key":f"youtube_video/{id}.mp4"})
                logger.error(f"下载视频失败: {e}")
                
def upload_worker():
    while True:
        data = pop_task(os.environ.get("REDIS_VIDEO_QUEUE", "video_queue"), blocking=False) 
        if data:
            try:
                local_path = data["local_path"]
                minio_key = data["minio_key"]
                upload_video_to_minio(local_path, minio_key)
                update_video_status(data["id"], 'uploaded')
            except Exception as e:
                logger.error(f"上传视频失败: {e}")
                push_task(os.environ.get("REDIS_VIDEO_QUEUE", "video_queue"), data)


Thread(target=download_worker, daemon=True).start()
Thread(target=upload_worker, daemon=True).start()