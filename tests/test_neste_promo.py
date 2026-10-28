"""Regression cases based on captured official HTML and published promo data."""
import datetime as dt
import json
import re
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
        cls.rimi_html = (FIXTURES / "neste_offers_20261028.html").read_text(encoding="utf-8")
        cls.previous = json.loads((FIXTURES / "neste_previous_promo_20261006.json").read_text(encoding="utf-8"))

    def test_real_official_empty_index_is_confirmed_without_price(self):
        self.assertEqual(promo.announcement(self.html), {"status": "no_announcement"})

    def test_real_rimi_voucher_index_is_not_a_wednesday_fuel_discount(self):
        self.assertEqual(promo.announcement(self.rimi_html), {"status": "no_announcement"})

    def test_changed_rimi_card_cannot_be_treated_as_reviewed(self):
        for old, new in (
            ('rimi-gimtadienis', 'new-offer'),
            ('RIMI_reward_special_offer_page.jfif', 'new-offer.png'),
            ('Pilkis NESTE ir gauk RIMI dovanų!', 'Naujas pasiūlymas'),
        ):
            with self.subTest(changed=old), self.assertRaises(RuntimeError):
                promo.announcement(self.rimi_html.replace(old, new))

    def test_unknown_content_beside_rimi_card_is_still_rejected(self):
        for addition in (
            '<p>Additional offer content</p>',
            '<img src="https://images.ctfassets.net/new-offer.png">',
            '<a href="/unknown-offer">Daugiau</a>',
        ):
            with self.subTest(addition=addition), self.assertRaises(RuntimeError):
                promo.announcement(self.rimi_html.replace('</main>', addition + '</main>'))

    def test_unknown_responsive_card_images_cannot_be_treated_as_reviewed(self):
        image = re.search(r'<img\b[^>]*RIMI_reward_special_offer_page\.jfif[^>]*>', self.rimi_html).group()
        for replacement in (
            re.sub(r'srcSet="[^"]+"', 'srcSet="https://images.ctfassets.net/new-offer.png 400w"', image),
            re.sub(r'srcSet="[^"]+"', "srcSet='https://images.ctfassets.net/new-offer.png 400w'", image),
            re.sub(r'srcSet="[^"]+"', 'srcSet = "https://images.ctfassets.net/new-offer.png 400w"', image),
            re.sub(r'srcSet="[^"]+"', 'srcset=https://images.ctfassets.net/new-offer.png', image),
            re.sub(r'srcSet="[^"]+"', 'srcset', image),
            '<picture><source srcset="https://images.ctfassets.net/new-offer.png 400w">' + image + '</picture>',
            image + '<img srcset="https://images.ctfassets.net/new-offer.png 400w">',
            re.sub(r'\bsrc="[^"]+"', '', image),
        ):
            with self.subTest(replacement=replacement[:80]), self.assertRaises(RuntimeError):
                promo.announcement(self.rimi_html.replace(image, replacement, 1))

    def test_unknown_responsive_images_outside_reviewed_card_are_rejected(self):
        for html in (self.html, self.rimi_html):
            for addition in ('<img srcset="https://images.ctfassets.net/new-offer.png 400w">',
                             '<picture><source srcset="https://images.ctfassets.net/new-offer.png 400w"></picture>'):
                with self.subTest(addition=addition), self.assertRaises(RuntimeError):
                    promo.announcement(html.replace('</main>', addition + '</main>'))

    def test_dated_wednesday_announcement_still_takes_precedence(self):
        # Official announcement wording recorded in the fetcher's July audit.
        html = self.rimi_html.replace('</main>', '<p>Tik šį trečiadienį, liepos 8 d. - 7 ct nuolaida</p></main>')
        self.assertEqual(promo.announcement(html, dt.date(2026, 7, 8)),
                         {'status': 'announced', 'valid_date': '2026-07-08', 'cents': 7.0})

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
        now = dt.datetime(2026, 10, 28, 13, tzinfo=gate.VILNIUS)
        with patch.object(gate, 'load', return_value=payload), patch.object(gate, 'FAILURES', []), patch.object(gate, 'WARNINGS', []):
            gate.check_neste(now)
            return list(gate.FAILURES)

    def test_same_day_verified_no_announcement_is_not_a_missing_promo(self):
        for html in (self.html, self.rimi_html):
            payload = dict(promo.announcement(html), source_url=promo.URL, generated='2026-10-28T09:30:00Z')
            self.assertEqual(self.check_wednesday(payload), [])
            for changes in ({'generated': '2026-10-27T09:30:00Z'}, {'generated_stale_kept': True}, {'source_url': self.previous['source_url']}, {'cents': self.previous['cents']}):
                with self.subTest(changes=changes):
                    self.assertTrue(self.check_wednesday(dict(payload, **changes)))

    def test_success_after_stale_keep_replaces_failure_state_without_price(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'neste.json'
            out.write_text(json.dumps(dict(self.previous, generated_stale_kept=True)), encoding='utf-8')
            with patch.object(promo, 'OUT', str(out)), patch.object(promo, 'fetch', return_value=self.rimi_html):
                self.assertEqual(promo.main(), 0)
            refreshed = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(refreshed['status'], 'no_announcement')
            self.assertNotEqual(refreshed['generated'], self.previous['generated'])
            self.assertNotIn('generated_stale_kept', refreshed)
            self.assertNotIn('cents', refreshed)
            self.assertNotIn('valid_date', refreshed)

    def test_real_old_promo_still_fails_wednesday_and_rot_checks(self):
        failures = self.check_wednesday(self.previous)
        self.assertTrue(any('Wednesday' in message for message in failures))
        self.assertTrue(any('>96h' in message for message in failures))


if __name__ == '__main__':
    unittest.main()
