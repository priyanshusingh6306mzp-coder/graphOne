import json
from datetime import datetime, timezone
from src.core.store import Store
from src.dates import parse_any, is_fresh
from src.export.flatten import COLUMNS, to_row

store = Store("data/pipeline.db")
now = datetime.now(timezone.utc)

print("=== CHECKING RECORD PAYLOADS & FRESHNESS ===")
for rt in ["STARTUP", "PRODUCT", "RESEARCH_PAPER", "JOB", "NEWS"]:
    payloads = store.payloads(rt)
    if rt in ("JOB", "NEWS"):
        fresh = []
        dropped = 0
        for p in payloads:
            dt_str = p.get("content", {}).get("date")
            dt = parse_any(dt_str, now=now)
            if is_fresh(dt, now=now, hours=24):
                fresh.append(p)
            else:
                dropped += 1
        print(f"{rt}: total {len(payloads)}, fresh {len(fresh)}, dropped {dropped}")
    else:
        print(f"{rt}: total {len(payloads)}")

mapping_rows = store.mapping_rows()
non_id = [r for r in mapping_rows if r.get("raw_name") != r.get("canonical_name")]
ident = [r for r in mapping_rows if r.get("raw_name") == r.get("canonical_name")]
print(f"mapping_log: total {len(mapping_rows)}, non-identity {len(non_id)}, identity {len(ident)}")

store.close()
