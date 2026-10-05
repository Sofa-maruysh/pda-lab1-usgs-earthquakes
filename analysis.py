import pandas as pd
from sqlalchemy import URL, case, create_engine, func, select

from earthquake_project.models import DB_PATH, PROJECT_DIR, Earthquake

RESULTS_DIR = PROJECT_DIR / "results"
QUERY_TITLES = {
    "daily": "Число событий и магнитуда по дням",
    "magnitude": "Распределение по диапазонам магнитуды",
    "depth": "Распределение по глубине",
    "network": "Статистика по сетям каталога",
    "hour": "Распределение по часам UTC",
    "tsunami": "Сравнение по флагу tsunami",
}


def pandas_queries(engine):
    with engine.connect() as connection:
        events = pd.read_sql_query(select(Earthquake.__table__), connection)

    events["day_utc"] = events["occurred_at"].str[:10]
    events["hour_utc"] = events["occurred_at"].str[11:13]
    events["magnitude_band"] = pd.cut(
        events["magnitude"], [-float("inf"), 3, 4, 5, 6, float("inf")],
        labels=["M < 3", "3 ≤ M < 4", "4 ≤ M < 5", "5 ≤ M < 6", "M ≥ 6"],
        right=False,
    )
    events["depth_band"] = pd.cut(
        events["depth_km"], [-float("inf"), 70, 300, float("inf")],
        labels=["мелкие (<70 км)", "промежуточные (70–300 км)", "глубокие (≥300 км)"],
        right=False,
    )

    # Число событий и магнитуда по дням
    pd_daily = (
        events.groupby("day_utc", observed=True)
        .agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"),
             max_magnitude=("magnitude", "max"))
        .reset_index()
        .sort_values("day_utc")
    )

    # Распределение по диапазонам магнитуды
    pd_magnitude = (
        events.groupby("magnitude_band", observed=True)
        .agg(events=("event_id", "size"), avg_depth_km=("depth_km", "mean"))
        .reset_index()
    )

    # Распределение по глубине
    pd_depth = (
        events.dropna(subset=["depth_km"])
        .groupby("depth_band", observed=True)
        .agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"))
        .reset_index()
    )

    # Статистика по сетям каталога
    pd_network = (
        events.groupby("network", dropna=False)
        .agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"),
             max_magnitude=("magnitude", "max"))
        .reset_index()
        .sort_values(["events", "network"], ascending=[False, True])
    )

    # Распределение по часам UTC
    pd_hour = (
        events.groupby("hour_utc")
        .agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"))
        .reset_index()
        .sort_values("hour_utc")
    )

    # Сравнение по флагу tsunami
    pd_tsunami = (
        events.groupby("tsunami")
        .agg(events=("event_id", "size"), avg_magnitude=("magnitude", "mean"),
             avg_significance=("significance", "mean"))
        .reset_index()
        .sort_values("tsunami")
    )

    return {
        "daily": pd_daily,
        "magnitude": pd_magnitude,
        "depth": pd_depth,
        "network": pd_network,
        "hour": pd_hour,
        "tsunami": pd_tsunami,
    }


def sqlalchemy_queries(engine):
    e = Earthquake.__table__.c
    day_sql = func.substr(e.occurred_at, 1, 10).label("day_utc")
    hour_sql = func.substr(e.occurred_at, 12, 2).label("hour_utc")
    magnitude_band_sql = case(
        (e.magnitude < 3, "M < 3"),
        (e.magnitude < 4, "3 ≤ M < 4"),
        (e.magnitude < 5, "4 ≤ M < 5"),
        (e.magnitude < 6, "5 ≤ M < 6"),
        else_="M ≥ 6",
    ).label("magnitude_band")
    depth_band_sql = case(
        (e.depth_km < 70, "мелкие (<70 км)"),
        (e.depth_km < 300, "промежуточные (70–300 км)"),
        else_="глубокие (≥300 км)",
    ).label("depth_band")

    # Число событий и магнитуда по дням
    query = (
        select(day_sql, func.count().label("events"),
               func.avg(e.magnitude).label("avg_magnitude"),
               func.max(e.magnitude).label("max_magnitude"))
        .group_by(day_sql)
        .order_by(day_sql)
    )
    with engine.connect() as connection:
        sql_daily = pd.read_sql_query(query, connection)

    # Распределение по диапазонам магнитуды
    query = (
        select(magnitude_band_sql, func.count().label("events"),
               func.avg(e.depth_km).label("avg_depth_km"))
        .group_by(magnitude_band_sql)
        .order_by(magnitude_band_sql)
    )
    with engine.connect() as connection:
        sql_magnitude = pd.read_sql_query(query, connection)

    # Распределение по глубине
    query = (
        select(depth_band_sql, func.count().label("events"),
               func.avg(e.magnitude).label("avg_magnitude"))
        .where(e.depth_km.is_not(None))
        .group_by(depth_band_sql)
        .order_by(depth_band_sql)
    )
    with engine.connect() as connection:
        sql_depth = pd.read_sql_query(query, connection)

    # Статистика по сетям каталога
    query = (
        select(e.network, func.count().label("events"),
               func.avg(e.magnitude).label("avg_magnitude"),
               func.max(e.magnitude).label("max_magnitude"))
        .group_by(e.network)
        .order_by(func.count().desc(), e.network)
    )
    with engine.connect() as connection:
        sql_network = pd.read_sql_query(query, connection)

    # Распределение по часам UTC
    query = (
        select(hour_sql, func.count().label("events"),
               func.avg(e.magnitude).label("avg_magnitude"))
        .group_by(hour_sql)
        .order_by(hour_sql)
    )
    with engine.connect() as connection:
        sql_hour = pd.read_sql_query(query, connection)

    # Сравнение по флагу tsunami
    query = (
        select(e.tsunami, func.count().label("events"),
               func.avg(e.magnitude).label("avg_magnitude"),
               func.avg(e.significance).label("avg_significance"))
        .group_by(e.tsunami)
        .order_by(e.tsunami)
    )
    with engine.connect() as connection:
        sql_tsunami = pd.read_sql_query(query, connection)

    return {
        "daily": sql_daily,
        "magnitude": sql_magnitude,
        "depth": sql_depth,
        "network": sql_network,
        "hour": sql_hour,
        "tsunami": sql_tsunami,
    }


def compare_results(pandas_results, sql_results):
    for name in QUERY_TITLES:
        frames = []
        for frame in (pandas_results[name], sql_results[name]):
            frame = frame.copy()
            key = frame.columns[0]
            frame[key] = frame[key].astype(object).where(frame[key].notna(), "<NULL>").astype(str)
            frames.append(frame.sort_values(key).reset_index(drop=True))
        pd.testing.assert_frame_equal(
            frames[0], frames[1], check_dtype=False, atol=1e-8, rtol=1e-8,
        )


def main():
    if not DB_PATH.exists():
        raise SystemExit("База данных не найдена. Сначала выполните сбор данных.")
    engine = create_engine(URL.create("sqlite", database=str(DB_PATH)))
    try:
        with engine.connect() as connection:
            count = connection.scalar(select(func.count()).select_from(Earthquake))
        if count == 0:
            raise SystemExit("В базе нет событий для анализа.")
        pandas_results = pandas_queries(engine)
        sql_results = sqlalchemy_queries(engine)
        compare_results(pandas_results, sql_results)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Событий в базе: {count}")
        for name, title in QUERY_TITLES.items():
            print(f"\n{title}")
            for method, results in (("pandas", pandas_results), ("sqlalchemy", sql_results)):
                print(f"\n{method}:")
                print(results[name].round(3).to_string(index=False))
                results[name].to_csv(
                    RESULTS_DIR / f"{name}_{method}.csv", index=False, encoding="utf-8-sig",
                )
        print("\nРезультаты всех шести запросов pandas и SQLAlchemy совпадают.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
