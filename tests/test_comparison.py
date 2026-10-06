"""Scope and freshness regressions against captured operator and LEA data."""
import datetime as dt
import gzip
import json
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import compare
import price_engine


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / "tests/fixtures"
        raw = gzip.decompress((fixtures / "lea-prices-20261006.json.gz").read_bytes()).decode()
        with patch("fetch_lea_portal.discover_credentials", return_value=("", "")), \
             patch("fetch_lea_portal.get", return_value=raw):
            cls.stations = price_engine.merge([price_engine.from_portal()])
        cls.saurida = json.loads((fixtures / "saurida-20261006.json").read_text(encoding="utf-8"))
        cls.circlek = json.loads((fixtures / "circlek-20261006.json").read_text(encoding="utf-8"))
        cls.now = price_engine._parse_ts(cls.saurida["fetched"])

    def test_real_per_station_prices_are_compared_only_at_matching_stations(self):
        pairs = price_engine.match_saurida_rows(self.saurida["prices"], self.stations)
        self.assertEqual(len(pairs), 31)
        self.assertEqual(len({id(s) for _, s in pairs}), len(pairs))
        items = compare.compare_source(self.saurida, self.stations, now=self.now)
        self.assertEqual(len(items), 2)
        for item in items:
            station = next(s for s in self.stations if "|".join(s[k] for k in
                           ("network", "address", "municipality")) == item["station_key"])
            self.assertEqual(item["lea_min"], station[item["fuel"]])
            self.assertEqual(item["scope"], "per_station")
            self.assertEqual(item["networks"], [station["network"]])

    def test_old_failed_and_unstamped_operator_checks_cannot_warn_as_live(self):
        self.assertEqual(compare.compare_source(self.saurida, self.stations,
                         now=self.now + dt.timedelta(hours=49)), [])
        for field, value in (("stale_kept", True), ("fetched", None)):
            failed = {**self.saurida, field: value}
            self.assertEqual(compare.compare_source(failed, self.stations, now=self.now), [])

    def test_network_lowest_remains_a_network_comparison(self):
        for item in compare.compare_source(self.circlek, self.stations, now=self.now):
            self.assertEqual(item["scope"], "network_lowest")
            self.assertNotIn("station_key", item)
            values = [s[item["fuel"]] for s in self.stations
                      if s["network"] in item["networks"] and s.get(item["fuel"]) is not None]
            self.assertEqual(item["lea_min"], min(values))

    def test_network_quotes_also_require_a_fresh_successful_fetch(self):
        fetched = price_engine._parse_ts(self.circlek["fetched"])
        self.assertEqual(compare.compare_source(self.circlek, self.stations,
                         now=fetched + dt.timedelta(hours=49)), [])
        with patch.object(compare, "lea_networks") as matching:
            for field, value in (("stale_kept", True), ("fetched", None)):
                failed = {**self.circlek, field: value}
                self.assertEqual(compare.compare_source(failed, self.stations, now=self.now), [])
            matching.assert_not_called()

    def test_app_rejects_old_dates_and_does_not_spread_a_station_flag_to_its_chain(self):
        items = compare.compare_source(self.saurida, self.stations, now=self.now)
        result = subprocess.run(["node", str(ROOT / "tests/test_comparison_ui.js")],
                 input=json.dumps({"items": items, "stations": self.stations, "date": "2026-10-06"}),
                 text=True, encoding="utf-8", capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
