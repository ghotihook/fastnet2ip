from datetime import datetime, timezone

from fastnet2ip.core.data_store import live_data


def print_live_data(fb):
    print("\033c", end="")
    now = datetime.now(timezone.utc)
    hdr = f"{'Signal K path':<52} {'Value (SI)':<26} {'Age(s)':<8}"
    print(hdr)
    print("-" * len(hdr))
    for path, data in sorted(live_data.items()):
        ts = data.get("timestamp")
        age = f"{(now - ts).total_seconds():.1f}" if ts else ""
        print(f"{str(path):<52} {str(data.get('value')):<26} {age:<8}")
    print(f"Buffer: {fb.get_buffer_size()}\n")
