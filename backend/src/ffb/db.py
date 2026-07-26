import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# DATABASE_URL points at hosted Postgres (Supabase) in deployed environments.
# Without it we fall back to the local SQLite file so local dev needs no env setup.
DATABASE_URL = os.getenv("DATABASE_URL")


def _normalize_url(raw: str) -> str:
    # Supabase hands out "postgres://", which SQLAlchemy 2.0 rejects, and the
    # bare "postgresql://" prefix resolves to psycopg2 rather than psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix) :]
    return raw


if DATABASE_URL:
    _url = _normalize_url(DATABASE_URL)
    # prepare_threshold is a psycopg argument: Supabase's transaction pooler
    # rejects prepared statements, which psycopg 3 starts issuing after a few
    # executions. Only pass it to Postgres - pointing DATABASE_URL at SQLite
    # (handy for a scratch run of the alerter) would otherwise fail to connect.
    _connect_args = {"prepare_threshold": None} if _url.startswith("postgresql") else {}
    engine = create_engine(
        _url,
        future=True,
        pool_pre_ping=True,
        connect_args=_connect_args,
    )
else:
    DATA_DIR = Path(__file__).resolve().parents[2] / "data"
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / "ffb.db"
    engine = create_engine(f"sqlite:///{DB_PATH}", future=True)

Session = sessionmaker(bind=engine, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from ffb import models  # noqa: F401  (register tables on Base.metadata)

    Base.metadata.create_all(engine)
