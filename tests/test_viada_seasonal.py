"""Seasonal absence must have two successful, independent official checks."""
import datetime as dt
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import fetch_viada_promos as promo
import verify_sources as gate


class ViadaSeasonalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parent / 'fixtures/viada_listing_20261006.html').read_text(encoding='utf-8')

    def test_actual_listing_is_recognized_and_has_no_wednesday_offer(self):
        with patch.object(promo, '_http_get', return_value=(200, self.html)):
            slugs, verified = promo.discover_listing_slugs()
        self.assertTrue(verified)
        self.assertIn('nuolaidu-savaitgaliai', slugs)
        self.assertNotIn('super-treciadieniai', slugs)

    def test_cached_listing_cannot_prove_offer_absence(self):
        with patch.object(promo, '_http_get', side_effect=OSError('direct unavailable')), patch.object(promo, 'http_text', return_value=(200, self.html)):
            slugs, verified = promo.discover_listing_slugs()
        self.assertTrue(slugs)
        self.assertFalse(verified)

    def test_unrecognized_listing_identity_is_not_a_successful_check(self):
        with patch.object(promo, '_http_get', return_value=(200, self.html.replace('Akcijos | VIADA LT', 'Unavailable'))):
            _, verified = promo.discover_listing_slugs()
        self.assertFalse(verified)

    def test_direct_404_confirms_removal_without_fallback(self):
        error = urllib.error.HTTPError(promo.BASE + 'super-treciadieniai/', 404, 'Not Found', {}, None)
        with patch.object(promo, '_http_get', side_effect=error), patch.object(promo, 'http_text') as fallback:
            self.assertEqual(promo.read_wednesday(), (None, True))
        fallback.assert_not_called()

    def test_fallback_404_does_not_confirm_direct_removal(self):
        error = urllib.error.HTTPError(promo.BASE + 'super-treciadieniai/', 404, 'Not Found', {}, None)
        with patch.object(promo, '_http_get', side_effect=OSError('direct unavailable')), patch.object(promo, 'http_text', side_effect=error):
            self.assertEqual(promo.read_wednesday(), (None, False))

    def check_wednesday(self, payload):
        with patch.object(gate, 'load', return_value=payload), patch.object(gate, 'FAILURES', []), patch.object(gate, 'WARNINGS', []):
            gate.check_viada(dt.datetime(2026, 10, 7, 13, tzinfo=gate.VILNIUS))
            return list(gate.FAILURES)

    def test_same_day_proven_absence_passes_and_failed_or_old_checks_fail(self):
        payload = {'generated': '2026-10-07T09:30:00Z', 'promos': [], 'wednesday_check': {
            'status': 'no_announcement', 'checked': '2026-10-07T09:30:00Z',
            'listing_url': promo.LISTING, 'source_url': promo.BASE + 'super-treciadieniai/'}}
        self.assertEqual(self.check_wednesday(payload), [])
        for changes in ({'generated_stale_kept': True}, {'generated': '2026-10-06T09:30:00Z'}, {'wednesday_check': dict(payload['wednesday_check'], status='unavailable')}):
            with self.subTest(changes=changes):
                self.assertTrue(self.check_wednesday(dict(payload, **changes)))


if __name__ == '__main__':
    unittest.main()
