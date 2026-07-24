from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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
