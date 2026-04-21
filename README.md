# ContentGenerator

Content-ingestion prototype centered on downloading online video, tracking metadata, and uploading artifacts to object storage.

## Highlights

- Worker-style pipeline for video download and upload queues
- Database-backed metadata tracking around URLs and generated objects
- Lightweight Flask API for starting ingestion requests

## Tech Stack

- Python, Flask, Redis, MinIO, SQL, Docker

## Repository Layout

- `youtube-video-service/main.py`
- `youtube-video-service/requirements.txt`
- `db/init/schema.sql`

## Getting Started

- Install the Python dependencies for `youtube-video-service` or containerize the service.
- Prepare the database schema and Redis / MinIO endpoints expected by the service.
- Start the Flask app and submit ingestion requests to the `/upload` endpoint.

## Current Status

- This README was refreshed from a code audit and is intentionally scoped to what is directly visible in the repository.
