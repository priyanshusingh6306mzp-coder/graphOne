"""Google Sheets exporter with batch writing, retries, read-back verification, and CSV fallback."""
import csv
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

from ..core.retry import compute_backoff
from ..core.store import Store
from ..dates import is_fresh, parse_any
from .flatten import COLUMNS, to_row

logger = logging.getLogger(__name__)

TAB_CONFIG = [
    ("Startups", "STARTUP"),
    ("Products", "PRODUCT"),
    ("Research Papers", "RESEARCH_PAPER"),
    ("Jobs", "JOB"),
    ("News", "NEWS"),
    ("Entity Mapping Log", "ENTITY_MAPPING_LOG"),
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client(service_account_path: Optional[str] = None) -> Optional[gspread.Client]:
    """Authorize gspread client via service account credentials, or None if unavailable."""
    path = service_account_path or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not path or not os.path.exists(path):
        return None
    try:
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as exc:
        logger.warning(f"Failed to authorize gspread client: {exc}")
        return None


def retry_call(
    fn: Callable[[], Any],
    max_attempts: int = 5,
    base: float = 1.0,
    cap: float = 60.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    """Execute a callable with exponential backoff and jitter."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_attempts - 1:
                raise
            delay = compute_backoff(attempt, base=base, cap=cap)
            logger.warning(
                f"Sheets API call failed (attempt {attempt + 1}/{max_attempts}): {exc}. Retrying in {delay:.2f}s..."
            )
            sleep_fn(delay)


def prepare_tab_data(
    store: Store, tab_name: str, record_type: str, now: Optional[datetime] = None
) -> Tuple[List[str], List[List[Any]], int]:
    """Extract and flatten data rows for a given tab, applying ordering and freshness filters."""
    now = now or datetime.now(timezone.utc)
    header = list(COLUMNS[record_type])
    data_rows: List[List[Any]] = []
    dropped_count = 0

    if record_type == "ENTITY_MAPPING_LOG":
        mapping_rows = store.mapping_rows()
        # Order non-identity first (raw_name != canonical_name)
        non_identity = [r for r in mapping_rows if r.get("raw_name") != r.get("canonical_name")]
        identity = [r for r in mapping_rows if r.get("raw_name") == r.get("canonical_name")]
        sorted_rows = non_identity + identity
        for r in sorted_rows:
            data_rows.append(to_row(r, "ENTITY_MAPPING_LOG"))

    elif record_type in ("JOB", "NEWS"):
        payloads = store.payloads(record_type)
        for p in payloads:
            date_str = p.get("content", {}).get("date")
            dt = parse_any(date_str, now=now)
            if is_fresh(dt, now=now, hours=24):
                data_rows.append(to_row(p, record_type))
            else:
                dropped_count += 1
    else:
        payloads = store.payloads(record_type)
        for p in payloads:
            data_rows.append(to_row(p, record_type))

    return header, data_rows, dropped_count


def export_csv_fallback(
    store: Store,
    export_dir: str = "data/export",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Export all 6 tabs to CSV files as fallback or alongside Sheets."""
    os.makedirs(export_dir, exist_ok=True)
    results = {}
    dropped_stats = {"JOB": 0, "NEWS": 0}

    for tab_name, record_type in TAB_CONFIG:
        header, data_rows, dropped = prepare_tab_data(store, tab_name, record_type, now=now)
        if record_type in dropped_stats:
            dropped_stats[record_type] = dropped

        filename = f"{tab_name.replace(' ', '_').lower()}.csv"
        file_path = os.path.join(export_dir, filename)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(data_rows)

        results[tab_name] = {
            "path": file_path,
            "row_count": len(data_rows),
            "dropped": dropped,
        }

    return {"tabs": results, "dropped": dropped_stats}


def write_tab_to_sheets(
    spreadsheet: Any,
    tab_name: str,
    header: List[str],
    data_rows: List[List[Any]],
    chunk_size: int = 500,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Clear and batch-write rows into a worksheet with retry and read-back verification."""
    # 1. Get or create worksheet
    try:
        ws = retry_call(lambda: spreadsheet.worksheet(tab_name), sleep_fn=sleep_fn)
    except Exception:
        ws = retry_call(
            lambda: spreadsheet.add_worksheet(title=tab_name, rows=100, cols=max(len(header), 10)),
            sleep_fn=sleep_fn,
        )

    # 2. Clear previous contents
    retry_call(lambda: ws.clear(), sleep_fn=sleep_fn)

    # 3. Resize worksheet
    total_rows = max(len(data_rows) + 1, 10)
    total_cols = max(len(header), 10)
    retry_call(lambda: ws.resize(rows=total_rows, cols=total_cols), sleep_fn=sleep_fn)

    # 4. Freeze header row
    try:
        retry_call(lambda: ws.freeze(rows=1), sleep_fn=sleep_fn)
    except Exception as exc:
        logger.warning(f"Could not freeze header on '{tab_name}': {exc}")

    # 5. Batch write in chunks
    all_rows = [header] + data_rows
    for i in range(0, len(all_rows), chunk_size):
        chunk = all_rows[i : i + chunk_size]
        start_row = i + 1
        end_row = start_row + len(chunk) - 1
        end_cell = gspread.utils.rowcol_to_a1(end_row, len(header))
        range_label = f"A{start_row}:{end_cell}"

        retry_call(
            lambda c=chunk, r=range_label: ws.update(values=c, range_name=r, value_input_option="RAW"),
            sleep_fn=sleep_fn,
        )

    # 6. Read-back verification
    read_values = retry_call(lambda: ws.get_all_values(), sleep_fn=sleep_fn)
    read_header = read_values[0] if read_values else []
    read_data = read_values[1:] if len(read_values) > 1 else []

    assert read_header == header, (
        f"Header mismatch on tab '{tab_name}': read {read_header} != expected {header}"
    )
    assert len(read_data) == len(data_rows), (
        f"Row count mismatch on tab '{tab_name}': read {len(read_data)} != expected {len(data_rows)}"
    )

    return len(data_rows)


def export_pipeline_data(
    store: Store,
    sheet_id: Optional[str] = None,
    service_account_path: Optional[str] = None,
    client: Optional[Any] = None,
    export_dir: str = "data/export",
    chunk_size: int = 500,
    sleep_fn: Callable[[float], None] = time.sleep,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Execute export to Google Sheets (if credentials available) and always export CSV fallback."""
    now = now or datetime.now(timezone.utc)

    # 1. Always generate CSV fallback
    csv_results = export_csv_fallback(store, export_dir=export_dir, now=now)

    sid = sheet_id or os.getenv("SHEET_ID")
    gc = client or get_gspread_client(service_account_path)

    sheets_output: Dict[str, int] = {}
    sheet_url = None
    dropped_totals = csv_results["dropped"]

    if gc and sid:
        spreadsheet = retry_call(lambda: gc.open_by_key(sid), sleep_fn=sleep_fn)
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sid}"

        for tab_name, record_type in TAB_CONFIG:
            header, data_rows, dropped = prepare_tab_data(store, tab_name, record_type, now=now)
            count = write_tab_to_sheets(
                spreadsheet,
                tab_name,
                header,
                data_rows,
                chunk_size=chunk_size,
                sleep_fn=sleep_fn,
            )
            sheets_output[tab_name] = count

        # Try setting public view permission
        try:
            retry_call(
                lambda: spreadsheet.share("", perm_type="anyone", role="reader", with_link=True),
                sleep_fn=sleep_fn,
            )
        except Exception as exc:
            logger.warning(f"Could not set public view permission on spreadsheet: {exc}")

    return {
        "sheet_url": sheet_url,
        "sheets_tab_counts": sheets_output,
        "csv_tabs": csv_results["tabs"],
        "dropped_items": dropped_totals,
        "executed_sheets": bool(gc and sid),
    }
