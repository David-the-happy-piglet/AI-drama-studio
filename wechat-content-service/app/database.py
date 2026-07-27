from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_runtime_schema() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    required_by_table = {
        "generation_runs": {
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
        },
        "assets": {
            "source_kind": "VARCHAR(40) DEFAULT 'generated'",
            "source_page_url": "TEXT",
            "source_author": "TEXT",
            "license_name": "VARCHAR(120)",
            "license_url": "TEXT",
        },
        "articles": {
            "target_length": "INTEGER DEFAULT 1200",
        },
        "automation_plans": {
            "target_length": "INTEGER",
        },
    }
    with engine.begin() as connection:
        for table_name, required in required_by_table.items():
            if table_name not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for name, sql_type in required.items():
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}"))


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
