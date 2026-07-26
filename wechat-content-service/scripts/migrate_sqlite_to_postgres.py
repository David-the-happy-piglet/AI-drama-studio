import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text

from app import models as _models  # noqa: F401 - register every mapped table
from app.database import Base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy all WeChat content data from SQLite to an empty PostgreSQL DB."
    )
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument(
        "--target-url",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL URL; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def migrate(sqlite_path: Path, target_url: str) -> dict[str, int]:
    source_path = sqlite_path.resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite backup not found: {source_path}")
    if not target_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("Target must be a PostgreSQL DATABASE_URL")

    source = create_engine(f"sqlite:///{source_path}")
    target = create_engine(target_url, pool_pre_ping=True)
    source_tables = set(inspect(source).get_table_names())
    Base.metadata.create_all(target)

    nonempty: list[str] = []
    with target.connect() as connection:
        for table in Base.metadata.sorted_tables:
            count = connection.scalar(select(func.count()).select_from(table))
            if count:
                nonempty.append(f"{table.name}={count}")
    if nonempty:
        details = ", ".join(nonempty)
        raise SystemExit(
            "Target database is not empty; migration stopped to avoid duplicates: "
            f"{details}"
        )

    copied: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in source_tables:
                continue
            rows = [
                dict(row._mapping)
                for row in source_connection.execute(select(table)).all()
            ]
            if rows:
                target_connection.execute(table.insert(), rows)
            copied[table.name] = len(rows)

        if target.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                if "id" not in table.c:
                    continue
                target_connection.execute(
                    text(
                        "SELECT setval("
                        "pg_get_serial_sequence(:table_name, 'id'), "
                        "COALESCE((SELECT MAX(id) FROM "
                        f'"{table.name}"'
                        "), 1), "
                        "EXISTS(SELECT 1 FROM "
                        f'"{table.name}"'
                        "))"
                    ),
                    {"table_name": table.name},
                )
    return copied


def main() -> None:
    args = parse_args()
    copied = migrate(args.sqlite_path, args.target_url)
    for table_name, count in copied.items():
        print(f"{table_name}: {count}")


if __name__ == "__main__":
    main()
