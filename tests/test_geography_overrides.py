"""Admission and preservation checks using the recorded Pasvalys correction."""
import copy
import gzip
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import merge_chain_coords


def station_key(station):
    return "|".join(station.get(field) or "" for field in ("network", "address", "municipality"))


class GeographyOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.override = next(row for row in json.loads(
            (ROOT / "data/coord_overrides.json").read_text(encoding="utf-8"))["overrides"]
            if row.get("display_address"))
        baseline = json.loads(gzip.decompress(
            (ROOT / "tests/fixtures/fuelis-stations-20261006.json.gz").read_bytes()))
        cls.rows = [row for row in baseline["stations"] if row["network"] == "UAB Baltic Petroleum"]

    def apply(self, rows, override):
        with patch.object(merge_chain_coords.json, "load", return_value={"overrides": [override]}):
            return merge_chain_coords.apply_overrides(rows)

    def test_attributed_address_changes_only_the_exact_record_without_changing_identity_or_prices(self):
        rows = copy.deepcopy(self.rows)
        self.assertEqual(self.apply(rows, self.override), 1)
        for before, after in zip(self.rows, rows):
            self.assertEqual(station_key(before), station_key(after))
            for field in ("petrol95", "diesel", "lpg", "price_updated", "price_src", "municipality", "address"):
                self.assertEqual(before.get(field), after.get(field))
            if station_key(after) == self.override["station_key"]:
                self.assertEqual(after["display_address"], self.override["display_address"])
                self.assertEqual(after["display_address_source"], self.override["display_address_source"])
            else:
                self.assertEqual(before, after)

    def test_missing_identity_or_attribution_refuses_the_entire_address_override(self):
        for omitted in ("station_key", "display_address_source"):
            with self.subTest(omitted=omitted):
                override = copy.deepcopy(self.override)
                override.pop(omitted)
                rows = copy.deepcopy(self.rows)
                self.assertEqual(self.apply(rows, override), 0)
                self.assertEqual(rows, self.rows)

    def test_non_http_or_hostless_attribution_refuses_the_override(self):
        url = self.override["display_address_source"]
        for invalid in (url.replace("https://", "ftp://", 1), "https:", ""):
            with self.subTest(source=invalid):
                override = copy.deepcopy(self.override)
                override["display_address_source"] = invalid
                rows = copy.deepcopy(self.rows)
                self.assertEqual(self.apply(rows, override), 0)
                self.assertEqual(rows, self.rows)


if __name__ == "__main__":
    unittest.main()
