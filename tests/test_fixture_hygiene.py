"""Saved source evidence must not retain page scripts or common credentials.

This is a bounded regression check, not a general-purpose secret scanner.
Failure messages deliberately report only the path and credential type.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIRS = (ROOT / 'tests/fixtures', ROOT / 'tools/audit')
PATTERNS = {
    'Google API key': rb'AIza[0-9A-Za-z_-]{35}',
    'AWS access key': rb'(?:AKIA|ASIA)[0-9A-Z]{16}',
    'GitHub token': rb'gh[pousr]_[A-Za-z0-9]{36,255}',
    'GitHub fine-grained token': rb'github_pat_[A-Za-z0-9_]{50,255}',
    'Slack token': rb'xox[baprs]-[0-9A-Za-z-]{20,}',
    'Stripe secret key': rb'sk_(?:live|test)_[0-9A-Za-z]{20,}',
    'OpenAI API key': rb'sk-(?:proj-|svcacct-)?[0-9A-Za-z_-]{40,}',
    'private key': rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
}


class FixtureHygieneTests(unittest.TestCase):
    def test_saved_evidence_has_no_common_credential_patterns_or_page_scripts(self):
        files = sorted(path for directory in EVIDENCE_DIRS for path in directory.rglob('*') if path.is_file())
        self.assertTrue(files, 'No source evidence found')
        for path in files:
            data = gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()
            name = path.relative_to(ROOT).as_posix()
            for kind, pattern in PATTERNS.items():
                with self.subTest(path=name, credential_type=kind):
                    self.assertFalse(bool(re.search(pattern, data)), f'{name}: remove {kind} from saved evidence')
            if path.suffix == '.html':
                with self.subTest(path=name, content='embedded code'):
                    self.assertFalse(bool(re.search(rb'<script\b|\bon[a-z]+\s*=|(?:href|src)\s*=\s*[\x22\x27]\s*javascript:', data, re.I)),
                                      f'{name}: retain source markup without executable page code')

    def test_sanitized_html_matches_recorded_provenance_hashes(self):
        metadata = json.loads((ROOT / 'tests/fixtures/html_sources_20261006.metadata.json').read_text(encoding='utf-8'))
        for fixture in metadata['fixtures']:
            path = ROOT / fixture['path']
            data = path.read_text(encoding='utf-8').encode('utf-8')
            with self.subTest(path=fixture['path']):
                self.assertEqual(hashlib.sha256(data).hexdigest(), fixture['sanitized_sha256'])
                self.assertEqual(len(data), fixture['sanitized_bytes'])
                self.assertTrue(fixture['source_url'].startswith('https://'))


if __name__ == '__main__':
    unittest.main()
