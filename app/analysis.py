import math
import statistics
from collections import defaultdict

METRICS = ["latency_ms", "packet_loss_pct", "dns_delay_ms", "download_mbps", "upload_mbps", "jitter_ms", "loaded_jitter_ms"]
LOWER_IS_BETTER = {"latency_ms", "packet_loss_pct", "dns_delay_ms", "jitter_ms", "loaded_jitter_ms"}

def summarize(values):
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "stddev": None, "ci95_low": None, "ci95_high": None}
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    # Normal approximation is explicitly labelled; small pilot samples are uncertain.
    margin = 1.96 * sd / math.sqrt(len(vals)) if len(vals) > 1 else None
    return {"n": len(vals), "mean": round(mean, 3), "median": round(statistics.median(vals), 3),
            "stddev": round(sd, 3), "ci95_low": round(mean-margin, 3) if margin is not None else None,
            "ci95_high": round(mean+margin, 3) if margin is not None else None}

def grouped_comparison(rows):
    groups = defaultdict(list)
    for row in rows:
        # Include confounders in grouping rather than attributing differences to medium alone.
        key = (row["access_medium"], row["city"], row.get("site_label", ""), row["isp"], row["wifi_type"])
        groups[key].append(row)
    output = []
    for (medium, city, site_label, isp, wifi), records in sorted(groups.items()):
        item = {"access_medium": medium, "city": city, "site_label": site_label, "isp": isp, "wifi_type": wifi,
                "volunteer_count": len({r["volunteer_id"] for r in records}), "measurement_count": len(records)}
        item["metrics"] = {m: summarize([r.get(m) for r in records]) for m in METRICS}
        output.append(item)
    return output
