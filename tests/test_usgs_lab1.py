import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pandas as pd
from sqlalchemy import create_engine, select

import usgs_lab1 as lab


def event(event_id, day, magnitude, depth, network, tsunami=0, updated=1):
    return {
        "event_id": event_id,
        "occurred_at": f"2025-01-{day:02d}T12:00:00.000+00:00",
        "updated_ms": updated,
        "magnitude": magnitude,
        "depth_km": depth,
        "latitude": 10.0,
        "longitude": 20.0,
        "place": "Test region",
        "network": network,
        "status": "reviewed",
        "tsunami": tsunami,
        "significance": 400,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/test",
    }


class LabTests(unittest.TestCase):
    def test_pagination_and_geojson_coordinates(self):
        calls = []

        def fake_request(url):
            calls.append(parse_qs(urlparse(url).query))
            feature = {
                "id": f"id-{len(calls)}",
                "properties": {"time": 1735732800000, "updated": 1735732801000, "mag": 4.5, "net": "us"},
                "geometry": {"coordinates": [120.5, -5.5, 32.0]},
            }
            return {"features": [feature, feature] if len(calls) == 1 else [feature]}

        with patch.object(lab, "request_json", side_effect=fake_request):
            rows = list(lab.fetch_events(date(2025, 1, 1), date(2025, 1, 3), 2.5, page_size=2))
        self.assertEqual(len(rows), 3)
        self.assertEqual([call["offset"][0] for call in calls], ["1", "3"])
        self.assertEqual((rows[0]["longitude"], rows[0]["latitude"], rows[0]["depth_km"]), (120.5, -5.5, 32.0))
        self.assertEqual(calls[0]["endtime"][0], "2025-01-02T23:59:59.999+00:00")

    def test_incremental_upsert_and_six_aggregation_pairs(self):
        first = [
            event("a", 1, 2.7, 10, "ak"),
            event("b", 1, 3.6, 90, "us"),
            event("c", 2, 4.8, 350, "us", tsunami=1),
            event("d", 2, 5.4, 20, "ci"),
            event("e", 3, 6.2, None, "us"),
        ]
        second = [event("b", 1, 3.9, 90, "us", updated=2), event("f", 3, 4.1, 40, "ak")]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.sqlite"
            with patch.object(lab, "fetch_events", side_effect=[iter(first), iter(first), iter(second)]):
                initial = lab.sync(db, date(2025, 1, 1), date(2025, 1, 4))
                replay = lab.sync(db, date(2025, 1, 1), date(2025, 1, 4))
                increment = lab.sync(db, date(2025, 1, 1), date(2025, 1, 4))
            self.assertEqual(initial, {"fetched": 5, "inserted_or_updated": 5, "total_in_database": 5})
            self.assertEqual(replay, {"fetched": 5, "inserted_or_updated": 0, "total_in_database": 5})
            self.assertEqual(increment, {"fetched": 2, "inserted_or_updated": 2, "total_in_database": 6})

            engine = create_engine(f"sqlite:///{db.as_posix()}")
            with engine.connect() as connection:
                changed = connection.execute(select(lab.earthquakes.c.magnitude).where(lab.earthquakes.c.event_id == "b")).scalar_one()
                sql = lab.sql_aggregations(connection)
                pandas = lab.pandas_aggregations(connection)
            self.assertEqual(changed, 3.9)
            self.assertEqual(len(sql), 6)
            self.assertEqual(set(sql), set(pandas))
            for name in sql:
                left = sql[name].sort_values(sql[name].columns[0]).reset_index(drop=True)
                right = pandas[name].sort_values(pandas[name].columns[0], key=lambda s: s.astype(str)).reset_index(drop=True)
                self.assertEqual(list(left.columns), list(right.columns), name)
                self.assertEqual(left.iloc[:, 0].astype(str).tolist(), right.iloc[:, 0].astype(str).tolist(), name)
                for column in left.columns[1:]:
                    pd.testing.assert_series_equal(
                        pd.to_numeric(left[column]).reset_index(drop=True),
                        pd.to_numeric(right[column]).reset_index(drop=True),
                        check_dtype=False, check_names=False, atol=1e-9,
                    )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
