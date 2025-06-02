# utils, download video from youtube

import subprocess
import csv
import logging
import os

def fetch_youtube_videos(channel_url, output_csv=None, number = 10):
    """
    使用 yt-dlp 抓取指定频道下所有视频标题和 URL。
    :param channel_url: YouTube 频道地址，例如 https://www.youtube.com/@回形针PaperClip/videos
    :param output_csv: 可选，输出文件路径（保存为 CSV）
    :return: List[Dict]，包含 title 和 url
    """
    logging.info(f"🔍 正在抓取：{channel_url}")
    
    # 构造 yt-dlp 命令
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist_end", str(number),
        "--print", "%(title)s|||%(webpage_url)s|||%(id)s",
        channel_url
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split("\n")
        data = []
        for line in lines:
            if "|||" not in line:
                continue
            title, url, id = line.strip().split("|||", 2)
            data.append({"title": title.strip(), "url": url.strip(), "id": id.strip()})
        
        logging.info(f"✅ 抓取成功，共 {len(data)} 条视频")

        if output_csv:
            with open(output_csv, "w", newline='', encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=["title", "url", "id"])
                writer.writeheader()
                writer.writerows(data)
            logging.info(f"📁 已保存到 {output_csv}")
        
        return data

    except subprocess.CalledProcessError as e:
        logging.error("❌ 抓取失败：", e.stderr)
        return []

def download_video(url: str, local_video_path: str):
    cmd = [
        "yt-dlp",
        "--output", local_video_path,
        url
    ]
    try:
        subprocess.run(cmd, check=True)
        logging.info(f"Downloaded video from YouTube: {url}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to download video from YouTube: {e}")



# 示例调用
if __name__ == "__main__":
    # 你可以传入 @名 或完整频道 ID 链接
    channel = "https://www.youtube.com/@laogao/videos"
    fetch_youtube_videos(channel, output_csv="videos.csv")
