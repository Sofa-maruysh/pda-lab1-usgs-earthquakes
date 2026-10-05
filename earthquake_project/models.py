from pathlib import Path

from sqlalchemy import URL, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "earthquakes.sqlite"


class Base(DeclarativeBase):
    pass


class Earthquake(Base):
    __tablename__ = "earthquakes"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    occurred_at: Mapped[str] = mapped_column(String)
    updated_ms: Mapped[int] = mapped_column(Integer)
    magnitude: Mapped[float] = mapped_column(Float)
    depth_km: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    place: Mapped[str | None] = mapped_column(String)
    network: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    tsunami: Mapped[int | None] = mapped_column(Integer)
    significance: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String)


def create_database(db_path=DB_PATH):
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path)))
    Base.metadata.create_all(engine)
    return engine


def save_earthquakes(rows, engine):
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    with Session(engine) as session:
        for row in rows:
            existing = session.get(Earthquake, row["event_id"])
            if existing is None:
                session.add(Earthquake(**row))
                counts["inserted"] += 1
            elif row["updated_ms"] > existing.updated_ms:
                for field, value in row.items():
                    setattr(existing, field, value)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
        session.commit()
    return counts
