"""Offline station regressions using the captured, real LEA response."""

import copy
import gzip
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_lea_portal
import fetch_prices
import merge_chain_coords
import price_engine


class StationCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "tests/fixtures/lea-prices-20261006.json.gz"
        cls.raw = gzip.decompress(fixture.read_bytes())
        cls.payload = json.loads(cls.raw)
        cls.metadata = json.loads(fixture.with_name("lea-prices-20261006.metadata.json").read_text(encoding="utf-8"))
        cls.before = json.loads(gzip.decompress(fixture.with_name("fuelis-stations-20261006.json.gz").read_bytes()))
        cls.overrides = json.loads((ROOT / "data/coord_overrides.json").read_text(encoding="utf-8"))
        cls.target_override = next(o for o in cls.overrides["overrides"] if o.get("display_municipality"))
        cls.target_key = cls.target_override["station_key"]

    @staticmethod
    def key(station):
        return fetch_prices.station_key(station["network"], station["address"], station["municipality"])

    def source_result(self):
        # Intercept both credential discovery and transport: this test never
        # calls LEA or uses an authentication token.
        with patch.object(fetch_lea_portal, "discover_credentials", return_value=("", "")), \
             patch.object(fetch_lea_portal, "get", return_value=self.raw.decode("utf-8")):
            return price_engine.from_portal()

    def apply_overrides(self, stations, document=None):
        with patch.object(merge_chain_coords.json, "load", return_value=document or self.overrides):
            return merge_chain_coords.apply_overrides(stations)

    def test_fixture_identity_and_complete_active_station_coverage(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.metadata["payload_sha256"])
        rows = self.payload["data"]
        self.assertEqual(len(rows), self.metadata["raw_rows"])
        expected = {
            fetch_prices.station_key(r["company_name"].strip(), r["address"].strip(), r["municipality"].strip())
            for r in rows if r.get("is_active", True) and r.get("company_name")
        }
        stations = price_engine.merge([self.source_result()])
        self.assertEqual({self.key(s) for s in stations}, expected)
        self.assertEqual(len(stations), 765)

    def test_priceless_stations_retain_only_source_declared_supported_fuels(self):
        rows = self.payload["data"]
        supported = {}
        for row in rows:
            key = fetch_prices.station_key(row["company_name"].strip(), row["address"].strip(), row["municipality"].strip())
            fuel = price_engine.PORTAL_FUELS.get(row["fuel_type"])
            if fuel:
                supported.setdefault(key, set()).add(fuel)
        stations = price_engine.merge([self.source_result()])
        priceless = [s for s in stations if s.get("no_price")]
        self.assertEqual(len(priceless), 8)
        for station in stations:
            self.assertEqual(set(station["fuels"]), supported[self.key(station)])
            if station.get("no_price"):
                self.assertTrue(station["fuels"])
                self.assertTrue(all(station[f] is None for f in price_engine.FUELS))
            else:
                self.assertTrue(any(station[f] is not None for f in price_engine.FUELS))

    def test_exact_display_override_preserves_identity_coordinates_and_prices(self):
        # Include every actual EMSI station: an exact matcher must change one.
        stations = copy.deepcopy([s for s in self.before["stations"] if s["network"] == "UAB Emsi"])
        original = copy.deepcopy(stations)
        self.assertEqual(self.apply_overrides(stations), 1)
        for old, new in zip(original, stations):
            self.assertEqual(self.key(old), self.key(new))
            for field in ("lat", "lon", "approx", "coord_source", *price_engine.FUELS):
                self.assertEqual(old.get(field), new.get(field))
            if self.key(new) == self.target_key:
                self.assertEqual(new["display_municipality"], "Panevėžio m. sav.")
                self.assertEqual(new["municipality"], "Panevėžio r. sav.")
                self.assertEqual(new["display_municipality_source"], self.target_override["display_municipality_source"])
            else:
                self.assertNotIn("display_municipality", new)

    def test_display_override_requires_exact_identity_and_attribution(self):
        target = next(s for s in self.before["stations"] if self.key(s) == self.target_key)
        for omitted in ("station_key", "display_municipality_source"):
            override = copy.deepcopy(self.target_override)
            override.pop(omitted)
            stations = [copy.deepcopy(target)]
            self.assertEqual(self.apply_overrides(stations, {"overrides": [override]}), 0)
            self.assertEqual(stations, [target])

    def test_real_saurida_prices_clear_a_priceless_station_marker(self):
        fixture = ROOT / "tests/fixtures/saurida-prices-20261006.json"
        operator = json.loads(fixture.read_text(encoding="utf-8"))
        stations = price_engine.merge([self.source_result()])
        pairs = price_engine.match_saurida_rows(operator["prices"], stations)
        self.assertTrue(pairs, "Actual Saurida fixture must match source stations")
        published, station = next((p, s) for p, s in pairs if any(p.get(f) is not None for f in price_engine.FUELS))
        original_key = self.key(station)
        # Withhold LEA's prices to exercise the same priceless metadata state as
        # the eight observed rows. Every price subsequently added by the overlay
        # must come from the recorded operator response; none is invented.
        for fuel in price_engine.FUELS:
            station[fuel] = None
        station.pop("price_updated", None)
        station["no_price"] = True
        with patch.object(price_engine, "SAURIDA_PATH", str(fixture)), \
             patch.object(price_engine, "_now_utc", return_value=price_engine._parse_ts(operator["fetched"])):
            self.assertGreater(price_engine.saurida_overlay(stations), 0)
        self.assertEqual(self.key(station), original_key)
        self.assertNotIn("no_price", station)
        self.assertEqual(station["price_src"], "saurida")
        for fuel in price_engine.FUELS:
            if published.get(fuel) is not None:
                self.assertEqual(station[fuel], published[fuel])

    def test_browser_city_filter_and_priceless_visibility_with_real_station_data(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for the browser filtering regression")
        after = copy.deepcopy(self.before)
        source = price_engine.merge([self.source_result()])
        metadata = {self.key(s): s for s in source}
        for station in after["stations"]:
            if self.key(station) in metadata:
                station.update(metadata[self.key(station)])
        self.apply_overrides(after["stations"])
        completed = subprocess.run(
            [node, str(ROOT / "tests/test_station_ui.js")],
            input=json.dumps({"before": self.before, "after": after, "source_keys": list(metadata), "target_key": self.target_key}),
            text=True, encoding="utf-8", capture_output=True, cwd=ROOT, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        print(completed.stdout.strip())


if __name__ == "__main__":
    unittest.main()
