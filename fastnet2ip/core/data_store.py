from datetime import datetime, timezone

# Single-threaded access only: written and read from the main run loop.
# pyfastnet v3 emits {signalk_path: SI_value}, so entries key a Signal K path to its
# latest SI value plus a timestamp for freshness/age.
live_data: dict = {}


def update_live_data(path, value):
    live_data[path] = {"value": value, "timestamp": datetime.now(timezone.utc)}


def get_live_data(path):
    entry = live_data.get(path)
    return entry.get("value") if entry else None
