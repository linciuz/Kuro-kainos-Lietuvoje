"""Report discovery regression using the current official iframe HTML."""
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_lea_powerbi


class LeaRegistryTests(unittest.TestCase):
    def test_report_key_is_found_on_the_official_monitoring_page(self):
        html = (ROOT / "tools/audit/lea_official_20261006/dk-irankis.html").read_bytes()
        with patch.object(fetch_lea_powerbi, "_get", return_value=html) as get:
            self.assertEqual(fetch_lea_powerbi.resource_key(), "60850ad8-c1ee-47ef-8a08-339eaee7bff4")
            get.assert_called_once_with("https://www.ena.lt/dk-irankis/")


if __name__ == "__main__":
    unittest.main()
