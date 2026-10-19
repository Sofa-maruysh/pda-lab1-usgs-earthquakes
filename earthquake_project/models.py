from pathlib import Path

from sqlalchemy import URL, Float, Integer, String, bindparam, create_engine, inspect, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from earthquake_project.regions import extract_region

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "earthquakes.sqlite"
BATCH_SIZE = 500


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
    region: Mapped[str | None] = mapped_column(String)
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
    table = Earthquake.__table__
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("earthquakes")}
        if "region" not in columns:
            connection.exec_driver_sql("ALTER TABLE earthquakes ADD COLUMN region VARCHAR")
        # Пополняется новая колонка; существующие строки и прочие поля сохраняются.
        missing = connection.execute(
            select(table.c.event_id, table.c.place).where(table.c.region.is_(None))
        ).all()
        regions = [
            {"key_event_id": event_id, "value_region": region}
            for event_id, place in missing
            if (region := extract_region(place)) is not None
        ]
        statement = table.update().where(table.c.event_id == bindparam("key_event_id")).values(
            region=bindparam("value_region")
        )
        for start in range(0, len(regions), BATCH_SIZE):
            connection.execute(statement, regions[start:start + BATCH_SIZE])
    return engine


def save_earthquakes(rows, engine):
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    unique_rows = {}
    for row in rows:
        row = dict(row)
        row.setdefault("region", extract_region(row.get("place")))
        previous = unique_rows.get(row["event_id"])
        if previous is None or row["updated_ms"] > previous["updated_ms"]:
            unique_rows[row["event_id"]] = row
    rows = list(unique_rows.values())
    table = Earthquake.__table__
    insert = sqlite_insert(table)
    upsert = insert.on_conflict_do_update(
        index_elements=[table.c.event_id],
        set_={column.name: insert.excluded[column.name]
              for column in table.columns if column.name != "event_id"},
        where=insert.excluded.updated_ms > table.c.updated_ms,
    )
    with engine.begin() as connection:
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            # Один SELECT на пакет вместо отдельного поиска каждого события.
            existing = dict(connection.execute(
                select(table.c.event_id, table.c.updated_ms)
                .where(table.c.event_id.in_([row["event_id"] for row in batch]))
            ).all())
            changed = []
            for row in batch:
                old_updated = existing.get(row["event_id"])
                if old_updated is None:
                    counts["inserted"] += 1
                    changed.append(row)
                elif row["updated_ms"] > old_updated:
                    counts["updated"] += 1
                    changed.append(row)
                else:
                    counts["unchanged"] += 1
            if changed:
                connection.execute(upsert, changed)
    return counts
