"""Our SP metadata stays valid until our certificate ends, not two days (python3-saml's default)."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import saml


class SamlMetadataTests(unittest.TestCase):
    def test_valid_until_is_the_certificate_end(self):
        with tempfile.TemporaryDirectory() as folder:
            saml.ensure_sp_certs(Path(folder))  # a fresh 10-year certificate, as on the server
            cert = Path(folder) / "saml" / "sp-cert.pem"
            end = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", str(cert)],
                                 check=True, capture_output=True, text=True).stdout
            year = end.split()[-2]
            request = SimpleNamespace(url_root="https://logs.example.org/", is_secure=True)
            with patch.object(saml, "_load_idp", return_value={"idp": {}}), \
                    patch.dict("os.environ", {"LOGDB_PUBLIC_URL": "https://logs.example.org"}):
                settings = saml.settings_for(request, Path(folder))
            valid = settings["security"]["metadataValidUntil"]
            self.assertRegex(valid, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(valid[:4], year)
            from onelogin.saml2.settings import OneLogin_Saml2_Settings
            xml = OneLogin_Saml2_Settings(settings, sp_validation_only=True).get_sp_metadata()
            xml = xml.decode() if isinstance(xml, bytes) else xml
            self.assertEqual(re.search(r'validUntil="([^"]+)"', xml).group(1), valid)


if __name__ == "__main__":
    unittest.main()
