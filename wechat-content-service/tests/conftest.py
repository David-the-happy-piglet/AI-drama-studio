import atexit
import os
import shutil
import tempfile
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix="wechat-cg-tests-"))
atexit.register(shutil.rmtree, TEST_DIR, True)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'test.db'}"
os.environ["GENERATED_ASSETS_DIR"] = str(TEST_DIR / "assets")
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["IMAGE_PROVIDER"] = "placeholder"
os.environ["WECHAT_ENABLED"] = "false"

