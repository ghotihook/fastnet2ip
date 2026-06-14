from datetime import datetime, timezone

from fastnet2ip.core.data_store import live_data


def print_live_data(fb):
    print("\033c", end="")
    now = datetime.now(timezone.utc)
    hdr = f"{'Channel':<35} {'ID':<10} {'Value':<20} {'Display':<20} {'Layout':<12} {'Age(s)':<10}"
    print(hdr)
    print("-" * len(hdr))
    for name, data in sorted(live_data.items()):
        ts = data.get("timestamp")
        age = f"{(now - ts).total_seconds():.1f}" if ts else ""
        print(
            f"{str(name):<35} {str(data.get('channel_id', '')):<10} "
            f"{str(data.get('value')):<20} "
            f"{str(data.get('display_text')):<20} "
            f"{str(data.get('layout', '')):<12} "
            f"{age:<10}"
        )
    print(f"Buffer: {fb.get_buffer_size()}\n")
