from datetime import datetime, timezone

from fastnet2ip.core.data_store import live_data


def print_live_data(fb):
    print("\033c", end="")
    now = datetime.now(timezone.utc)
    hdr = f"{'Channel':<35} {'ID':<10} {'Value':<20} {'Layout':<12} {'Age(s)':<10}"
    print(hdr)
    print("-" * len(hdr))
    for name, data in sorted(live_data.items()):
        ts = data.get("timestamp")
        val = data.get("value")
        display = str(val) if val is not None else data.get("display_text", "")
        age = f"{(now - ts).total_seconds():.1f}" if ts else ""
        print(
            f"{str(name):<35} {str(data.get('channel_id', '')):<10} "
            f"{display:<20} "
            f"{str(data.get('layout', '')):<12} "
            f"{age:<10}"
        )
    print(f"Buffer: {fb.get_buffer_size()}\n")
