"""Unit tests for Google Sheets exporter, retry/backoff, readback verification, freshness filtering, and CSV fallback."""
import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.core.store import Store
from src.export.flatten import COLUMNS
from src.export.sheets import (
    TAB_CONFIG,
    export_csv_fallback,
    export_pipeline_data,
    prepare_tab_data,
    retry_call,
    write_tab_to_sheets,
)


class MockWorksheet:
    def __init__(self, title: str, rows: int = 100, cols: int = 20):
        self.title = title
        self.rows_dim = rows
        self.cols_dim = cols
        self.cells: List[List[Any]] = []
        self.frozen_rows = 0
        self.frozen_cols = 0
        self.update_calls = []

    def clear(self):
        self.cells = []

    def resize(self, rows: int, cols: int):
        self.rows_dim = rows
        self.cols_dim = cols

    def freeze(self, rows: Optional[int] = None, cols: Optional[int] = None):
        if rows is not None:
            self.frozen_rows = rows
        if cols is not None:
            self.frozen_cols = cols

    def update(self, values: List[List[Any]], range_name: Optional[str] = None, value_input_option: str = "RAW"):
        self.update_calls.append((range_name, len(values), value_input_option))
        # Parse start row from range_name (e.g. "A1:G10")
        start_row = 1
        if range_name and range_name.startswith("A"):
            num_part = ""
            for ch in range_name[1:]:
                if ch.isdigit():
                    num_part += ch
                else:
                    break
            if num_part:
                start_row = int(num_part)

        while len(self.cells) < start_row - 1:
            self.cells.append([])

        for idx, row in enumerate(values):
            target_idx = (start_row - 1) + idx
            if target_idx < len(self.cells):
                self.cells[target_idx] = list(row)
            else:
                self.cells.append(list(row))

    def get_all_values(self) -> List[List[Any]]:
        return [list(r) for r in self.cells]


class MockSpreadsheet:
    def __init__(self, sheet_id: str = "mock-sheet-123"):
        self.id = sheet_id
        self.url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        self.worksheets_map: Dict[str, MockWorksheet] = {}
        self.shared_calls = []

    def worksheet(self, title: str) -> MockWorksheet:
        if title in self.worksheets_map:
            return self.worksheets_map[title]
        raise Exception(f"Worksheet {title} not found")

    def add_worksheet(self, title: str, rows: int = 100, cols: int = 20) -> MockWorksheet:
        ws = MockWorksheet(title, rows, cols)
        self.worksheets_map[title] = ws
        return ws

    def share(self, email_address: str, perm_type: str, role: str, notify: bool = False, with_link: bool = False):
        self.shared_calls.append((email_address, perm_type, role, with_link))


class MockGSpreadClient:
    def __init__(self, spreadsheet: MockSpreadsheet):
        self.spreadsheet = spreadsheet

    def open_by_key(self, key: str) -> MockSpreadsheet:
        if key == self.spreadsheet.id:
            return self.spreadsheet
        raise Exception(f"Spreadsheet key {key} not found")


class ExportUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_export.db")
        self.store = Store(self.db_path)
        self.now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_freshness_filtering_jobs_and_news(self):
        # 1 fresh job (1 hour ago), 1 stale job (30 hours ago)
        fresh_time = (self.now - timedelta(hours=1)).isoformat()
        stale_time = (self.now - timedelta(hours=30)).isoformat()

        self.store.insert_record("JOB", "https://site.com/job1", {
            "schemaVersion": "1.0", "recordType": "JOB",
            "source": {"name": "Test", "url": "https://site.com/job1"},
            "content": {"company": "Anthropic", "date": fresh_time, "is_remote": True, "role_family": "Engineering"},
            "collectedAt": fresh_time,
        })
        self.store.insert_record("JOB", "https://site.com/job2", {
            "schemaVersion": "1.0", "recordType": "JOB",
            "source": {"name": "Test", "url": "https://site.com/job2"},
            "content": {"company": "OpenAI", "date": stale_time, "is_remote": True, "role_family": "Engineering"},
            "collectedAt": stale_time,
        })

        header, rows, dropped = prepare_tab_data(self.store, "Jobs", "JOB", now=self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(dropped, 1)
        self.assertEqual(header, COLUMNS["JOB"])

    def test_entity_mapping_log_ordering_non_identity_first(self):
        # Add non-identity mapping (raw != canon) and identity mapping (raw == canon)
        self.store.log_mapping("STARTUP", "OpenAI", "OpenAI", "exact", 100.0, "https://openai.com")
        self.store.log_mapping("STARTUP", "OpenAI, Inc.", "OpenAI", "alias", 100.0, "https://yc.com/openai")

        header, rows, dropped = prepare_tab_data(self.store, "Entity Mapping Log", "ENTITY_MAPPING_LOG", now=self.now)
        self.assertEqual(len(rows), 2)
        # First row MUST be the non-identity merge
        self.assertEqual(rows[0][0], "OpenAI, Inc.")
        self.assertEqual(rows[0][1], "OpenAI")
        # Second row is identity
        self.assertEqual(rows[1][0], "OpenAI")
        self.assertEqual(rows[1][1], "OpenAI")

    def test_csv_fallback_export(self):
        self.store.insert_record("STARTUP", "https://yc.com/test", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/test"},
            "content": {"entityName": "TestCo", "data": {"employeeCount": 5}},
            "collectedAt": "2026-11-22T00:00:00Z",
        })
        export_dir = os.path.join(self.tmp.name, "export")
        res = export_csv_fallback(self.store, export_dir=export_dir, now=self.now)

        self.assertEqual(len(res["tabs"]), 6)
        startups_csv = res["tabs"]["Startups"]["path"]
        self.assertTrue(os.path.exists(startups_csv))

        with open(startups_csv, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
            self.assertEqual(reader[0], COLUMNS["STARTUP"])
            self.assertEqual(len(reader), 2)  # 1 header + 1 data row
            self.assertEqual(reader[1][4], "TestCo")

    def test_retry_and_backoff(self):
        attempts = 0
        sleeps = []

        def flaky_call():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise Exception("Quota exceeded (429)")
            return "SUCCESS"

        result = retry_call(flaky_call, max_attempts=5, sleep_fn=lambda s: sleeps.append(s))
        self.assertEqual(result, "SUCCESS")
        self.assertEqual(attempts, 3)
        self.assertEqual(len(sleeps), 2)

    def test_readback_verification_assertion_on_mismatch(self):
        mock_sheet = MockSpreadsheet("sheet-fail")
        ws = mock_sheet.add_worksheet("TestTab")

        # Intentionally sabotage worksheet.get_all_values to return mismatched rows
        ws.get_all_values = lambda: [["wrong", "header"], ["row1"]]

        header = ["col1", "col2"]
        data_rows = [["val1", "val2"]]

        with self.assertRaises(AssertionError) as ctx:
            write_tab_to_sheets(mock_sheet, "TestTab", header, data_rows, sleep_fn=lambda s: None)
        self.assertIn("Header mismatch", str(ctx.exception))

    def test_full_export_pipeline_with_mock_sheets(self):
        # Insert sample rows across records
        self.store.insert_record("STARTUP", "https://yc.com/startup", {
            "schemaVersion": "1.0", "recordType": "STARTUP",
            "source": {"name": "YC", "url": "https://yc.com/startup"},
            "content": {"entityName": "Anthropic", "data": {"employeeCount": 500}},
            "collectedAt": self.now.isoformat(),
        })
        self.store.insert_record("PRODUCT", "https://anthropic.com/claude", {
            "schemaVersion": "1.0", "recordType": "PRODUCT",
            "source": {"name": "Anthropic", "url": "https://anthropic.com"},
            "content": {"startupName": "Anthropic", "pricingModel": "PAID"},
            "collectedAt": self.now.isoformat(),
        })

        mock_sheet = MockSpreadsheet("mock-sheet-success")
        mock_client = MockGSpreadClient(mock_sheet)

        res = export_pipeline_data(
            self.store,
            sheet_id="mock-sheet-success",
            client=mock_client,
            export_dir=os.path.join(self.tmp.name, "export_full"),
            now=self.now,
            sleep_fn=lambda s: None,
        )

        self.assertTrue(res["executed_sheets"])
        self.assertEqual(res["sheet_url"], "https://docs.google.com/spreadsheets/d/mock-sheet-success")
        self.assertEqual(res["sheets_tab_counts"]["Startups"], 1)
        self.assertEqual(res["sheets_tab_counts"]["Products"], 1)

        # Verify frozen header row and permissions on mock sheet
        startups_ws = mock_sheet.worksheets_map["Startups"]
        self.assertEqual(startups_ws.frozen_rows, 1)
        self.assertTrue(len(mock_sheet.shared_calls) > 0)


if __name__ == "__main__":
    unittest.main()
