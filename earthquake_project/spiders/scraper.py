from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode

import scrapy

from earthquake_project.models import DB_PATH, create_database, save_earthquakes


class EarthquakesSpider(scrapy.Spider):
    name = "earthquakes"
    allowed_domains = ["earthquake.usgs.gov"]
    handle_httpstatus_list = [204]
    api_url = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    def __init__(self, start="2025-01-01", end="2025-01-15",
                 min_magnitude="2.5", page_size="1000", db_path=None, **kwargs):
        super().__init__(**kwargs)
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if start_date >= end_date:
            raise ValueError("Дата end должна быть позже start.")
        self.page_size = int(page_size)
        if not 1 <= self.page_size <= 20_000:
            raise ValueError("page_size должен быть от 1 до 20000.")
        # USGS включает endtime; вычитание миллисекунды задаёт период [start, end).
        end_time = datetime.combine(end_date, time.min, tzinfo=timezone.utc)
        end_time -= timedelta(milliseconds=1)
        self.params = {
            "format": "geojson",
            "eventtype": "earthquake",
            "starttime": start_date.isoformat(),
            "endtime": end_time.isoformat(timespec="milliseconds"),
            "minmagnitude": float(min_magnitude),
            "orderby": "time-asc",
            "limit": self.page_size,
        }
        self.engine = create_database(db_path or DB_PATH)

    def page_request(self, offset):
        params = {**self.params, "offset": offset}
        return scrapy.Request(
            f"{self.api_url}?{urlencode(params)}",
            callback=self.parse,
            cb_kwargs={"offset": offset},
        )

    async def start(self):
        yield self.page_request(1)

    def parse(self, response, offset=1):
        if response.status == 204:
            return
        features = response.json()["features"]
        rows = []
        for feature in features:
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if (not feature.get("id") or props.get("mag") is None
                    or props.get("time") is None or props.get("updated") is None
                    or len(coords) < 2):
                self.crawler.stats.inc_value("events/skipped")
                continue
            rows.append({
                "event_id": feature["id"],
                "occurred_at": datetime.fromtimestamp(
                    props["time"] / 1000, tz=timezone.utc
                ).isoformat(timespec="milliseconds"),
                "updated_ms": int(props["updated"]),
                "magnitude": float(props["mag"]),
                "depth_km": coords[2] if len(coords) > 2 else None,
                "latitude": coords[1],
                "longitude": coords[0],
                "place": props.get("place"),
                "network": props.get("net"),
                "status": props.get("status"),
                "tsunami": int(props.get("tsunami") or 0),
                "significance": props.get("sig"),
                "url": props.get("url"),
            })

        counts = save_earthquakes(rows, self.engine)
        self.crawler.stats.inc_value("events/received", len(features))
        for name, count in counts.items():
            self.crawler.stats.inc_value(f"events/{name}", count)
        self.logger.info(
            "Получено: %s; добавлено: %s; обновлено: %s; без изменений: %s",
            len(features), counts["inserted"], counts["updated"], counts["unchanged"],
        )
        if len(features) == self.page_size:
            yield self.page_request(offset + len(features))

    def closed(self, reason):
        self.engine.dispose()
