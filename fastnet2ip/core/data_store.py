from datetime import datetime, timezone

# Single-threaded access only: written and read from the main run loop.
live_data: dict = {}


def update_live_data(channel_name, channel_id, value, display_text, layout):
    live_data[channel_name] = {
        "channel_id":   channel_id,
        "value":        value,
        "display_text": display_text,
        "layout":       layout,
        "timestamp":    datetime.now(timezone.utc),
    }


def get_live_data(name, as_string=False):
    entry = live_data.get(name)
    if not entry:
        return None
    val = entry.get("value")
    if as_string:
        return str(val) if val is not None else entry.get("display_text")
    return val


def get_live_display(name):
    entry = live_data.get(name)
    return entry.get("display_text") if entry else None


def get_live_layout(name):
    entry = live_data.get(name)
    return entry.get("layout") if entry else None
