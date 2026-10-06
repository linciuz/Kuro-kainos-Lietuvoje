"""Offline rendering regressions from the recorded real 2026-10-06 prices."""
import copy
import datetime as dt
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import mock_open, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_pages as pages
import build_opendata as opendata


class SeoPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "tests/fixtures/fuelis-stations-20261006.json.gz"
        raw = gzip.decompress(fixture.read_bytes())
        metadata = json.loads((fixture.with_name("fuelis-stations-20261006.metadata.json")).read_bytes())
        if hashlib.sha256(raw).hexdigest() != metadata["payload_sha256"]:
            raise AssertionError("Recorded real-data fixture provenance changed")
        cls.document = json.loads(raw)
        cls.rows = cls.document["stations"]
        cls.updated = cls.document["updated"]
        cls.by_muni = {}
        for row in cls.rows:
            cls.by_muni.setdefault(row["municipality"], []).append(row)
        cls.slugs = [(pages.slugify(m), m) for m in cls.by_muni]

    def section(self, markup, fuel):
        return re.search(rf'<section id="{fuel}">(.*?)</section>', markup, re.S).group(1)

    def test_four_real_lpg_winners_are_visible_and_first_in_their_own_table(self):
        cases = [
            ("Klaipėdos m. sav.", "Klaipėda, Tilžės g. 90, 91101", 0.779),
            ("Kėdainių r. sav.", "Kėdainiai, Basanavičiaus g. 91E, 57356", 0.790),
            ("Vilniaus m. sav.", "Vilnius, Savanorių pr. 121, 03150", 0.789),
            ("Šilalės r. sav.", "Gineikių k., 75445", 0.750),
        ]
        for muni, address, price in cases:
            with self.subTest(municipality=muni):
                rows = self.by_muni[muni]
                actual = next(r for r in rows if r["address"] == address)
                self.assertEqual(actual["lpg"], price)
                self.assertEqual(price, min(r["lpg"] for r in rows if r.get("lpg") is not None))
                markup = pages.build_muni_page(muni, rows, self.updated)[1]
                section = self.section(markup, "lpg")
                first = re.search(r"<tbody>(<tr>.*?</tr>)", section, re.S).group(1)
                self.assertIn(address, html.unescape(first))
                self.assertIn(f"€{price:.3f}", first)
                self.assertLessEqual(len(re.findall("<tr>", section)) - 1, 3)

    def test_national_comparison_contains_real_counts_minima_sources_and_all_regions(self):
        markup = pages.build_directory(self.slugs, self.updated, self.rows)
        body = markup.split("<body>", 1)[1]
        priced = [r for r in self.rows if any(isinstance(r.get(f), (int, float))
                                            for f in ("petrol95", "diesel", "lpg"))]
        self.assertIn(f"{len(priced)} degalinės su kainomis", body)
        for fuel in ("petrol95", "diesel", "lpg"):
            values = [r[fuel] for r in self.rows if isinstance(r.get(fuel), (int, float))]
            section = self.section(body, fuel)
            self.assertIn(f"€{min(values):.3f}", section)
            self.assertIn(f"fuel={fuel}", section)
            self.assertRegex(body, rf"<td class='n'>{len(values)}</td>")
        for slug, _ in self.slugs:
            self.assertIn(f'href="https://fuelis.lt/kainos/{slug}.html"', body)
        self.assertIn("Kuro kainos Lietuvoje", body)
        self.assertIn("kuro kainų paieška", body)
        self.assertIn("Saurida", body)
        self.assertIn(self.updated, body)
        self.assertIn("€/l", body)

    def test_regional_sources_and_aware_record_times_are_visible(self):
        for muni in ("Kalvarijos sav.", "Prienų r. sav.", "Šakių r. sav."):
            markup = pages.build_muni_page(muni, self.by_muni[muni], self.updated)[1]
            self.assertIn("papildyti naujesnėmis patikrintomis", markup)
            self.assertIn(opendata.DATA_SOURCE.split(" ir ")[0], markup)
        for row in self.rows:
            if row.get("price_src") == "saurida":
                shown = pages.row_source_html(row)
                self.assertIn("saurida.lt/kuro-kainos-degalinese/", shown)
                self.assertIn(row["price_updated"], shown)
                self.assertIn("Nuskaityta", shown)
                self.assertIn("LT</time>", shown)

    def test_city_district_scope_no_false_locality_and_missing_lpg(self):
        for muni, expected in (("Kauno m. sav.", "Kuro kainos Kaune"),
                               ("Šilalės r. sav.", "Kuro kainos Šilalės rajone")):
            markup = pages.build_muni_page(muni, self.by_muni[muni], self.updated,
                                          [("Kauno m. sav.", min(r["diesel"] for r in self.by_muni["Kauno m. sav."]
                                                                if r.get("diesel") is not None), None)])[1]
            self.assertIn(f"<h1>{expected}</h1>", markup)
            self.assertIn(muni, markup)
            self.assertIn("tiesia linija", markup)
            self.assertIn("nėra kelionės keliu", markup)
            self.assertNotIn('"addressLocality"', markup)
            self.assertIn('"addressRegion"', markup)
        neringa = pages.build_muni_page("Neringos sav.", self.by_muni["Neringos sav."], self.updated)[1]
        self.assertIn("1 degalinė su kainomis", neringa)
        self.assertIn("Nepateikta kainų šioms kuro rūšims: Dujos (LPG)", neringa)
        self.assertNotIn('<section id="lpg">', neringa)
        self.assertNotIn("Palyginkite benzino 95, dyzelino ir LPG", neringa)

    def test_map_link_uses_encoded_municipality_and_explicit_view(self):
        from urllib.parse import parse_qs, urlparse
        muni = "Šilalės r. sav."
        markup = pages.build_muni_page(muni, self.by_muni[muni], self.updated)[1]
        href = re.search(r'<a class="cta" href="([^"]+)">Atidaryti žemėlapyje', markup).group(1)
        self.assertIn("&amp;", href)
        params = parse_qs(urlparse(html.unescape(href)).query)
        self.assertEqual(params["view"], ["map"])
        self.assertEqual(params["muni"], [muni])

    def test_rendering_and_public_payload_preserve_recorded_prices_and_raw_identity(self):
        before = copy.deepcopy(self.document)
        for muni, rows in self.by_muni.items():
            pages.build_muni_page(muni, rows, self.updated)
        pages.build_directory(self.slugs, self.updated, self.rows)
        public = opendata.public_rows(self.rows)
        by_key = {tuple(r[k] for k in ("network", "address", "municipality")): r for r in self.rows}
        for row in public:
            original = by_key[tuple(row[k] for k in ("network", "address", "municipality"))]
            for field in ("petrol95", "diesel", "lpg", "price_updated", "price_src"):
                self.assertEqual(row[field], original.get(field))
        self.assertEqual(self.document, before)

    def test_homepage_marker_is_compact_dated_national_and_preserves_markers(self):
        opener = mock_open(read_data=(ROOT / "index.html").read_text(encoding="utf-8"))
        count = sum(any(isinstance(r.get(f), (int, float)) for f in ("petrol95", "diesel", "lpg"))
                    for r in self.rows)
        with patch("builtins.open", opener):
            pages.refresh_index_html(self.document["summary"], self.updated, count)
        markup = opener().write.call_args[0][0]
        block = re.search(r"<!-- FUELIS:STATIC-PRICES -->(.*?)<!-- /FUELIS:STATIC-PRICES -->",
                          markup, re.S).group(1)
        self.assertIn('id="crawl-prices" lang="lt"', block)
        self.assertIn(f"Lietuvos kainų suvestinė · {self.updated}", block)
        self.assertIn("operatorių kainos", block)
        self.assertIn("€/l", block)
        self.assertEqual(markup.count("<!-- FUELIS:STATIC-PRICES -->"), 1)
        self.assertIn("Kuro kainų paieška Lietuvoje", markup)

    def test_docs_sample_is_actual_cheapest_record_and_provenance_is_mixed(self):
        rows = opendata.public_rows(self.rows)
        with patch.object(opendata, "_w") as writer:
            opendata.build_docs_page(self.updated, rows)
        markup = writer.call_args[0][1]
        samples = []
        for block in re.findall(r"<pre><code>(.*?)</code></pre>", markup, re.S):
            try:
                samples.append(json.loads(html.unescape(block)))
            except json.JSONDecodeError:
                pass
        sample = next(s for s in samples if isinstance(s, dict) and "price" in s)
        minimum = min(r["diesel"] for r in self.rows if isinstance(r.get("diesel"), (int, float)))
        self.assertEqual(sample["price"], minimum)
        self.assertTrue(any(r["network"] == sample["network"] and r["address"] == sample["address"]
                            and r.get("diesel") == sample["price"] for r in self.rows))
        self.assertNotIn("price: 1.94", markup)
        self.assertNotIn("per kelias minutes", markup)
        self.assertIn("operatorių", markup)
        self.assertIn("saurida: 3", markup)
        with patch.object(opendata, "_w"):
            doc = opendata.build_prices_json(self.document, self.updated, rows)
        self.assertEqual(doc["stations"], rows)
        self.assertIn("operatorių", doc["source"])
        self.assertIn("compilation free to use, including commercially", doc["terms"])
        self.assertIn("LEA or marked operator", doc["terms"])

    def test_rss_has_no_assumed_publication_clock_and_is_stable_between_runs(self):
        rows = opendata.public_rows(self.rows)
        history = json.loads((ROOT / "data/price_history.json").read_bytes())["history"]
        actual_time = dt.datetime.now(dt.timezone.utc)
        with patch.object(opendata, "_w") as writer:
            opendata.build_feed(self.updated, rows, history)
        first_xml = writer.call_args[0][1]
        feed = ET.fromstring(first_xml)
        channel = feed.find("channel")
        self.assertIsNone(channel.find("lastBuildDate"))
        self.assertTrue(channel.findall("item"))
        self.assertTrue(all(item.find("pubDate") is None for item in channel.findall("item")))
        # Vary wall clock without modifying any real station/source data.
        # XML changes bypass should_commit's import-timestamp suppression.
        with patch.object(opendata.dt, "datetime", wraps=dt.datetime) as clock:
            clock.now.return_value = actual_time + dt.timedelta(minutes=15)
            with patch.object(opendata, "_w") as writer:
                opendata.build_feed(self.updated, rows, history)
            second_xml = writer.call_args[0][1]
            clock.now.assert_not_called()
        self.assertEqual(first_xml, second_xml)


if __name__ == "__main__":
    unittest.main()
