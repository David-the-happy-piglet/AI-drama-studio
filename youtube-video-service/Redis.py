import redis
import os
import logging
import json

# 配置日志输出
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 从环境变量加载配置
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

# Redis 客户端初始化（decode_responses=True 返回字符串）
try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=0,
        decode_responses=True
    )
    redis_client.ping()
    logger.info("Connected to Redis successfully.")
except redis.RedisError as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis_client = None


def push_task(queue_name:str,data:dict) -> bool:
    if redis_client is None:
        logger.error("Redis client not initialized.")
        return False
    try:
        redis_client.rpush(queue_name, json.dumps(data))
        logger.info(f"Pushed task: {dict}")
        return True
    except redis.RedisError as e:
        logger.error(f"Error pushing task to queue: {e}")
        return False


def pop_task(queue_name:str,blocking: bool = True, timeout: int = 0):
    if redis_client is None:
        logger.error("Redis client not initialized.")
        return None
    try:
        if blocking:
            result = redis_client.blpop(queue_name, timeout=timeout)
            if result:
                _, url = result
                logger.info(f"Popped task: {url}")
                return url
        else:
            url = redis_client.lpop(queue_name)
            if url:
                logger.info(f"Popped task: {url}")
                return json.loads(url)
        return None
    except redis.RedisError as e:
        logger.error(f"Error popping task from queue: {e}")
        return None
