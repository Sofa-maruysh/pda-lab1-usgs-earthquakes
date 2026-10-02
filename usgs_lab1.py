"""Лабораторная работа №1: USGS GeoJSON -> SQLite -> аналитика."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    case,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
PAGE_SIZE = 20_000  # максимальный limit в документации USGS
metadata = MetaData()
earthquakes = Table(
    "earthquakes",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("occurred_at", String, nullable=False),  # UTC, ISO 8601
    Column("updated_ms", Integer, nullable=False),
    Column("magnitude", Float, nullable=False),
    Column("depth_km", Float, nullable=True),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("place", String, nullable=True),
    Column("network", String, nullable=True),
    Column("status", String, nullable=True),
    Column("tsunami", Integer, nullable=False),
    Column("significance", Integer, nullable=True),
    Column("url", String, nullable=True),
    CheckConstraint("tsunami IN (0, 1)", name="ck_tsunami"),
    Index("ix_earthquakes_occurred_at", "occurred_at"),
)


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def normalize(feature: dict) -> dict:
    """Преобразовать GeoJSON Feature в строку нашей таблицы."""
    properties = feature["properties"]
    coordinates = feature["geometry"]["coordinates"]  # longitude, latitude, depth
    if feature.get("id") is None or properties.get("mag") is None or len(coordinates) < 2:
        raise ValueError("В ответе USGS отсутствуют обязательные поля события")
    return {
        "event_id": str(feature["id"]),
        "occurred_at": utc_iso(int(properties["time"])),
        "updated_ms": int(properties["updated"]),
        "magnitude": float(properties["mag"]),
        "depth_km": float(coordinates[2]) if len(coordinates) > 2 and coordinates[2] is not None else None,
        "latitude": float(coordinates[1]),
        "longitude": float(coordinates[0]),
        "place": properties.get("place"),
        "network": properties.get("net"),
        "status": properties.get("status"),
        "tsunami": int(properties.get("tsunami") or 0),
        "significance": properties.get("sig"),
        "url": properties.get("url"),
    }


def request_json(url: str) -> dict:
    """GET с ограниченным числом повторов при временной ошибке сервера."""
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "pda-lab1-usgs-earthquakes/1.0 (educational project)"})
            with urlopen(request, timeout=60) as response:
                if response.status == 204:
                    return {"features": [], "metadata": {"count": 0}}
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (TimeoutError, URLError):
            if attempt == 3:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("USGS API недоступен")


def fetch_events(start: date, end: date, min_magnitude: float, page_size: int = PAGE_SIZE):
    """Получить все страницы за полуоткрытый период [start, end)."""
    if start >= end:
        raise ValueError("Дата начала должна быть раньше даты окончания")
    if not 1 <= page_size <= PAGE_SIZE:
        raise ValueError("Размер страницы должен быть от 1 до 20000")
    offset = 1
    # USGS считает endtime включительно. Полночь end исключаем одной миллисекундой.
    end_inclusive = datetime.fromisoformat(end.isoformat()).replace(tzinfo=timezone.utc).timestamp() * 1000 - 1
    end_iso = utc_iso(int(end_inclusive))
    while True:
        params = {
            "format": "geojson",
            "eventtype": "earthquake",
            "starttime": start.isoformat(),
            "endtime": end_iso,
            "minmagnitude": min_magnitude,
            "orderby": "time-asc",
            "limit": page_size,
            "offset": offset,
        }
        payload = request_json(f"{API_URL}?{urlencode(params)}")
        features = payload.get("features", [])
        for feature in features:
            yield normalize(feature)
        if len(features) < page_size:
            break
        offset += len(features)


def sync(db_path: Path, start: date, end: date, min_magnitude: float = 2.5, page_size: int = PAGE_SIZE) -> dict:
    """Загрузить события порциями. Повторный запуск обновляет исправленные записи."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    metadata.create_all(engine)
    statement = sqlite_insert(earthquakes)
    update_columns = {
        column.name: getattr(statement.excluded, column.name)
        for column in earthquakes.columns
        if column.name != "event_id"
    }
    upsert = statement.on_conflict_do_update(
        index_elements=[earthquakes.c.event_id],
        set_=update_columns,
        where=statement.excluded.updated_ms > earthquakes.c.updated_ms,
    )
    fetched = written = 0
    batch = []
    for row in fetch_events(start, end, min_magnitude, page_size):
        batch.append(row)
        fetched += 1
        if len(batch) >= 1000:
            with engine.begin() as connection:
                written += connection.execute(upsert, batch).rowcount
            batch.clear()
    if batch:
        with engine.begin() as connection:
            written += connection.execute(upsert, batch).rowcount
    with engine.connect() as connection:
        total = connection.scalar(select(func.count()).select_from(earthquakes))
    engine.dispose()
    return {"fetched": fetched, "inserted_or_updated": written, "total_in_database": total}


def magnitude_band_sql():
    return case(
        (earthquakes.c.magnitude < 3, "M < 3"),
        (earthquakes.c.magnitude < 4, "3 ≤ M < 4"),
        (earthquakes.c.magnitude < 5, "4 ≤ M < 5"),
        (earthquakes.c.magnitude < 6, "5 ≤ M < 6"),
        else_="M ≥ 6",
    )


def depth_band_sql():
    return case(
        (earthquakes.c.depth_km < 70, "мелкие (<70 км)"),
        (earthquakes.c.depth_km < 300, "промежуточные (70–300 км)"),
        else_="глубокие (≥300 км)",
    )


def sql_aggregations(connection) -> dict[str, pd.DataFrame]:
    e = earthquakes.c
    day = func.substr(e.occurred_at, 1, 10).label("day_utc")
    mag_band = magnitude_band_sql().label("magnitude_band")
    depth_band = depth_band_sql().label("depth_band")
    hour = func.substr(e.occurred_at, 12, 2).label("hour_utc")
    specs = {
        "01_daily": select(day, func.count().label("events"), func.avg(e.magnitude).label("avg_magnitude"), func.max(e.magnitude).label("max_magnitude")).group_by(day).order_by(day),
        "02_magnitude": select(mag_band, func.count().label("events"), func.avg(e.depth_km).label("avg_depth_km")).group_by(mag_band).order_by(mag_band),
        "03_depth": select(depth_band, func.count().label("events"), func.avg(e.magnitude).label("avg_magnitude")).where(e.depth_km.is_not(None)).group_by(depth_band).order_by(depth_band),
        "04_network": select(e.network, func.count().label("events"), func.avg(e.magnitude).label("avg_magnitude"), func.max(e.magnitude).label("max_magnitude")).group_by(e.network).order_by(func.count().desc(), e.network),
        "05_hour": select(hour, func.count().label("events"), func.avg(e.magnitude).label("avg_magnitude")).group_by(hour).order_by(hour),
        "06_tsunami": select(e.tsunami, func.count().label("events"), func.avg(e.magnitude).label("avg_magnitude"), func.avg(e.significance).label("avg_significance")).group_by(e.tsunami).order_by(e.tsunami),
    }
    return {name: pd.read_sql_query(query, connection) for name, query in specs.items()}


def pandas_aggregations(connection) -> dict[str, pd.DataFrame]:
    df = pd.read_sql_query(select(earthquakes), connection)
    if df.empty:
        return {}
    df["day_utc"] = df["occurred_at"].str[:10]
    df["hour_utc"] = df["occurred_at"].str[11:13]
    df["magnitude_band"] = pd.cut(
        df["magnitude"], [-float("inf"), 3, 4, 5, 6, float("inf")],
        labels=["M < 3", "3 ≤ M < 4", "4 ≤ M < 5", "5 ≤ M < 6", "M ≥ 6"], right=False,
    )
    df["depth_band"] = pd.cut(
        df["depth_km"], [-float("inf"), 70, 300, float("inf")],
        labels=["мелкие (<70 км)", "промежуточные (70–300 км)", "глубокие (≥300 км)"], right=False,
    )
    return {
        "01_daily": df.groupby("day_utc", observed=True).agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"), max_magnitude=("magnitude", "max")).reset_index().sort_values("day_utc"),
        "02_magnitude": df.groupby("magnitude_band", observed=True).agg(events=("event_id", "size"), avg_depth_km=("depth_km", "mean")).reset_index().sort_values("magnitude_band", key=lambda s: s.astype(str)),
        "03_depth": df.dropna(subset=["depth_km"]).groupby("depth_band", observed=True).agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean")).reset_index().sort_values("depth_band", key=lambda s: s.astype(str)),
        "04_network": df.groupby("network", dropna=False).agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"), max_magnitude=("magnitude", "max")).reset_index().sort_values(["events", "network"], ascending=[False, True]),
        "05_hour": df.groupby("hour_utc").agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean")).reset_index().sort_values("hour_utc"),
        "06_tsunami": df.groupby("tsunami").agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"), avg_significance=("significance", "mean")).reset_index().sort_values("tsunami"),
    }


def analyze(db_path: Path, output_dir: Path) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"База не найдена: {db_path}. Сначала запустите sync.")
    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    with engine.connect() as connection:
        sql_results = sql_aggregations(connection)
        pandas_results = pandas_aggregations(connection)
    engine.dispose()
    if not pandas_results:
        raise ValueError("База пуста. Выберите период с землетрясениями.")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in sql_results.items():
        frame.to_csv(output_dir / f"{name}_sql.csv", index=False, encoding="utf-8-sig")
        pandas_results[name].to_csv(output_dir / f"{name}_pandas.csv", index=False, encoding="utf-8-sig")
    return {name: len(frame) for name, frame in sql_results.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    sync_parser = subcommands.add_parser("sync", help="загрузить и обновить события")
    sync_parser.add_argument("--start", type=date.fromisoformat, required=True, help="начало периода, UTC, YYYY-MM-DD")
    sync_parser.add_argument("--end", type=date.fromisoformat, required=True, help="конец периода (не включается), UTC")
    sync_parser.add_argument("--min-magnitude", type=float, default=2.5)
    sync_parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    sync_parser.add_argument("--db", type=Path, default=Path("data/earthquakes.sqlite"))
    analyze_parser = subcommands.add_parser("analyze", help="шесть агрегаций в pandas и SQLAlchemy")
    analyze_parser.add_argument("--db", type=Path, default=Path("data/earthquakes.sqlite"))
    analyze_parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    try:
        result = (
            sync(args.db, args.start, args.end, args.min_magnitude, args.page_size)
            if args.command == "sync" else analyze(args.db, args.output)
        )
    except (ValueError, FileNotFoundError, HTTPError, URLError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
