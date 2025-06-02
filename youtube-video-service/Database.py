import os
import psycopg2
from datetime import datetime

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    dbname=os.environ.get("DB_NAME", "video"),
    user=os.enciron.get("DB_USER", "user"),
    password=os.environ.get("DB_PASSWORD", "password")
)

cursor = conn.cursor()

def insert_video_record(name, url, domain, local_path, object_key, status = 'pending'):
    sql = """
        INSERT INTO videos (name, url, domain, local_path, object_key, created_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    cursor.execute(sql, (name, url, domain, local_path, object_key, datetime.now(), status))
    conn.commit()

def get_video_by_id(id):
    cursor.execute("SELECT * FROM videos WHERE id = %s;", (id,))
    return cursor.fetchone()

def get_video_by_object_key(object_key):
    cursor.execute("SELECT * FROM videos WHERE object_key = %s;", (object_key,))
    return cursor.fetchone()

def get_video_by_url(url):
    cursor.execute("SELECT * FROM videos WHERE url = %s;", (url,))
    return cursor.fetchone()

def get_all_videos():
    cursor.execute("SELECT * FROM video ORDER BY created_at DESC;")
    return cursor.fetchall()

def update_video_name(video_id, new_name):
    cursor.execute("UPDATE video SET name = %s WHERE id = %s;", (new_name, video_id))
    conn.commit()

def delete_video(video_id):
    cursor.execute("DELETE FROM video WHERE id = %s;", (video_id,))
    conn.commit()
