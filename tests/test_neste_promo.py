"""Regression cases based on captured official HTML and published promo data."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_neste_promo as promo
import verify_sources as gate

FIXTURES = Path(__file__).parent / "fixtures"


class NestePromoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (FIXTURES / "neste_offers_20261006.html").read_text(encoding="utf-8")
        cls.previous = json.loads((FIXTURES / "neste_previous_promo_20261006.json").read_text(encoding="utf-8"))

    def test_real_official_empty_index_is_confirmed_without_price(self):
        self.assertEqual(promo.announcement(self.html), {"status": "no_announcement"})

    def test_unknown_body_content_cannot_be_blessed_as_empty(self):
        for addition in ('<p>Additional offer content</p>', '<img src="https://images.ctfassets.net/new-offer.png">'):
            with self.subTest(addition=addition), self.assertRaises(RuntimeError):
                promo.announcement(self.html.replace('</main>', addition + '</main>'))

    def test_wrong_or_missing_page_identity_is_rejected(self):
        for html in (self.html.replace('Specialūs pasiūlymai', 'Unavailable'), self.html.replace('ContentBody_body__', 'Unknown_body__')):
            with self.assertRaises(RuntimeError):
                promo.announcement(html)

    def test_fetch_failure_preserves_actual_previous_success_stamp(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'neste.json'
            out.write_text(json.dumps(self.previous), encoding='utf-8')
            with patch.object(promo, 'OUT', str(out)), patch.object(promo, 'fetch', side_effect=OSError('fetch unavailable')):
                self.assertEqual(promo.main(), 1)
            kept = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(kept['generated'], self.previous['generated'])
            self.assertEqual(kept['valid_date'], self.previous['valid_date'])
            self.assertTrue(kept['generated_stale_kept'])

    def test_first_fetch_failure_does_not_publish_fresh_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'neste.json'
            with patch.object(promo, 'OUT', str(out)), patch.object(promo, 'fetch', side_effect=OSError('fetch unavailable')):
                self.assertEqual(promo.main(), 1)
            self.assertFalse(out.exists())

    def check_wednesday(self, payload):
        now = dt.datetime(2026, 10, 7, 13, tzinfo=gate.VILNIUS)
        with patch.object(gate, 'load', return_value=payload), patch.object(gate, 'FAILURES', []), patch.object(gate, 'WARNINGS', []):
            gate.check_neste(now)
            return list(gate.FAILURES)

    def test_same_day_verified_no_announcement_is_not_a_missing_promo(self):
        payload = dict(promo.announcement(self.html), source_url=promo.URL, generated='2026-10-07T09:30:00Z')
        self.assertEqual(self.check_wednesday(payload), [])
        for changes in ({'generated': '2026-10-06T09:30:00Z'}, {'generated_stale_kept': True}, {'source_url': self.previous['source_url']}, {'cents': self.previous['cents']}):
            with self.subTest(changes=changes):
                self.assertTrue(self.check_wednesday(dict(payload, **changes)))

    def test_real_old_promo_still_fails_wednesday_and_rot_checks(self):
        failures = self.check_wednesday(self.previous)
        self.assertTrue(any('Wednesday' in message for message in failures))
        self.assertTrue(any('>96h' in message for message in failures))


if __name__ == '__main__':
    unittest.main()
