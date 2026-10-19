import re


def extract_region(place):
    """Извлекает географическую подпись из place; неопределимые случаи — NULL."""
    if not place or not place.strip():
        return None
    place = place.strip()
    if "," in place:
        return place.rsplit(",", 1)[1].strip() or None
    if re.match(r"^\d+(?:\.\d+)?\s*km\b", place, flags=re.IGNORECASE):
        return None
    if re.search(r"\bearthquake\b", place, flags=re.IGNORECASE):
        return None
    return place
