-- 建 video 表：存储所有视频的信息
CREATE TABLE video (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    domain TEXT,                   -- 所属领域（如 科技、娱乐、教育...）
    local_path TEXT,               -- 下载后的本地存储路径
    object_key TEXT NOT NULL,      -- 上传到 MinIO 的对象键
    created_at TIMESTAMP DEFAULT NOW(),   -- 创建时间（可用于追踪)
    status TEXT NOT NULL DEFAULT 'pending' -- 视频状态（pending, downloaded, failed)
);

-- 建视频间关系表：记录“视频A 生成了视频B” 或 “视频A 和 B 有某种关系”
CREATE TABLE video_relation (
    id SERIAL PRIMARY KEY,
    video_a_id INTEGER NOT NULL,
    video_b_id INTEGER NOT NULL,
    FOREIGN KEY (video_a_id) REFERENCES video(id) ON DELETE CASCADE,
    FOREIGN KEY (video_b_id) REFERENCES video(id) ON DELETE CASCADE
);

-- 给 url 加唯一约束，防止重复插入同一个视频
ALTER TABLE video ADD CONSTRAINT unique_url UNIQUE (url);

-- 给 object_key 加唯一约束，避免重复上传
ALTER TABLE video ADD CONSTRAINT unique_object_key UNIQUE (object_key);

-- 建立索引，加快查询速度（例如查找某视频的所有后继）
CREATE INDEX idx_video_relation_a ON video_relation(video_a_id);
CREATE INDEX idx_video_relation_b ON video_relation(video_b_id);
