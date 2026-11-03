"""Explicit one-time local data reorganization; does not call any provider.

Run from backend: ../.venv/bin/python scripts/consolidate_black_mirror.py [--apply]
Default is a transactional dry run. Make a SQLite backup before --apply.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.database import Database
from app.services.series import consolidate_draft_series

TITLES = [
    "替你后悔",
    "静音人生",
    "代理出席",
    "最后一位真人客服",
    "十分钟后",
    "孝顺套餐",
    "记忆代驾",
    "已读",
    "情绪遗产",
    "请证明你不是本人",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = Database(Settings().database_url)
    database.create_all()
    with database.session_factory() as db:
        result = consolidate_draft_series(
            db,
            "BM26_SERIES",
            "黑镜风格短篇集",
            [
                {
                    "project_id": f"BM26_{i:02d}_PRJ",
                    "episode_id": f"BM26_EP_{i:02d}",
                    "script_id": f"BM26_{i:02d}_SCRIPT_V2_DIALOGUE",
                    "title": title,
                }
                for i, title in enumerate(TITLES, 1)
            ],
        )
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(
            json.dumps({"applied": args.apply, **result}, ensure_ascii=False, indent=2)
        )
    database.engine.dispose()


if __name__ == "__main__":
    main()
