import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sqlalchemy import event, select

from analysis import compare_results, pandas_queries, sqlalchemy_queries
from earthquake_project.models import BATCH_SIZE, Earthquake, create_database, save_earthquakes
from earthquake_project.regions import extract_region


def make_row(number, **changes):
    row = {
        "event_id": str(number), "occurred_at": "2025-01-01T02:00:00.000+00:00",
        "updated_ms": 100, "magnitude": 4.5, "depth_km": 10.0,
        "latitude": 0.0, "longitude": 0.0, "place": "88 km NE of Hirara, Japan",
        "network": "us", "status": "reviewed", "tsunami": 0,
        "significance": 100, "url": None,
    }
    return {**row, **changes}


class LabTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "earthquakes.sqlite"
        self.engine = create_database(self.path)

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_regions_without_guessing(self):
        for place, region in [
            ("88 km NE of Hirara, Japan", "Japan"),
            ("6 km E of Willits, CA", "CA"),
            ("Mid-Indian Ridge", "Mid-Indian Ridge"),
            ("South Sandwich Islands region", "South Sandwich Islands region"),
            ("9 km NW of Test Village", None),
            ("2025 Southern Tibetan Plateau Earthquake", None),
            ("Somewhere, ", None), ("", None), (None, None),
        ]:
            with self.subTest(place=place):
                self.assertEqual(extract_region(place), region)

    def test_batch_reads_and_versioned_writes(self):
        rows = [make_row(i) for i in range(2 * BATCH_SIZE + 1)]
        self.assertEqual(save_earthquakes(rows, self.engine)["inserted"], len(rows))
        queries = []

        def record_select(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(self.engine, "before_cursor_execute", record_select)
        try:
            counts = save_earthquakes(rows, self.engine)
        finally:
            event.remove(self.engine, "before_cursor_execute", record_select)
        self.assertEqual(counts, {"inserted": 0, "updated": 0, "unchanged": len(rows)})
        self.assertEqual(len(queries), 3)
        self.assertTrue(all(" IN (" in query for query in queries))
        newer = make_row(0, updated_ms=200, magnitude=6.0)
        self.assertEqual(save_earthquakes([newer], self.engine)["updated"], 1)
        self.assertEqual(save_earthquakes([rows[0]], self.engine)["unchanged"], 1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(
                select(Earthquake.magnitude).where(Earthquake.event_id == "0")
            ), 6.0)

    def test_duplicate_ids_in_one_batch_keep_latest(self):
        counts = save_earthquakes([
            make_row(0, updated_ms=200, magnitude=6.0), make_row(0),
        ], self.engine)
        self.assertEqual(counts, {"inserted": 1, "updated": 0, "unchanged": 0})
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(select(Earthquake.magnitude)), 6.0)

    def test_legacy_database_migration_preserves_rows(self):
        legacy = Path(self.directory.name) / "legacy.sqlite"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("""CREATE TABLE earthquakes (
                event_id VARCHAR PRIMARY KEY, occurred_at VARCHAR NOT NULL,
                updated_ms INTEGER NOT NULL, magnitude FLOAT NOT NULL, depth_km FLOAT,
                latitude FLOAT, longitude FLOAT, place VARCHAR, network VARCHAR,
                status VARCHAR, tsunami INTEGER, significance INTEGER, url VARCHAR
            )""")
            row = make_row(0)
            connection.execute(
                f"INSERT INTO earthquakes ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                list(row.values()),
            )
            before = connection.execute("SELECT * FROM earthquakes").fetchall()
        for _ in range(2):
            engine = create_database(legacy)
            engine.dispose()
        with closing(sqlite3.connect(legacy)) as connection:
            self.assertEqual(connection.execute(
                f"SELECT {', '.join(row)} FROM earthquakes"
            ).fetchall(), before)
            self.assertEqual(connection.execute("SELECT region FROM earthquakes").fetchone(), ("Japan",))

    def test_depth_boundaries_and_hour_count_order(self):
        depths = [-1, 69.9, 70, 299.9, 300, 600, None]
        hours = [2, 2, 2, 1, 1, 0, 3]
        rows = [make_row(i, depth_km=depth, network=None if i == 0 else "us",
                         occurred_at=f"2025-01-01T{hour:02}:00:00.000+00:00")
                for i, (depth, hour) in enumerate(zip(depths, hours))]
        save_earthquakes(rows, self.engine)
        pandas_results, sql_results = pandas_queries(self.engine), sqlalchemy_queries(self.engine)
        compare_results(pandas_results, sql_results)
        self.assertEqual(pandas_results["depth"]["events"].tolist(), [2, 2, 2])
        self.assertEqual(pandas_results["hour"]["hour_utc"].tolist(), ["02", "01", "00", "03"])
        self.assertEqual(sql_results["hour"]["hour_utc"].tolist(), ["02", "01", "00", "03"])


if __name__ == "__main__":
    unittest.main()
