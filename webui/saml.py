"""TU Delft NetID SAML 2.0 login (SimpleSAMLphp identity provider).

IdP metadata URL (entity ID is the same URL):
  https://login.tudelft.nl/sso/saml2/idp/metadata.php

Register this app as a service provider with TU Delft ICT (the contact on that
metadata document is middleware-ictfm@tudelft.nl) using GET /saml/metadata.
"""
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser

# Official TU Delft SAML 2.0 IdP metadata (NetID / SimpleSAMLphp).
TUD_SAML_IDP_METADATA_URL = "https://login.tudelft.nl/sso/saml2/idp/metadata.php"
TUD_SAML_IDP_ENTITY_ID = "https://login.tudelft.nl/sso/saml2/idp/metadata.php"

_idp_cache = {"url": None, "data": None, "at": 0}
_CACHE_S = 3600


def metadata_url():
    return (os.environ.get("PPRZ_SAML_METADATA_URL") or TUD_SAML_IDP_METADATA_URL).strip()


def saml_enabled():
    raw = os.environ.get("PPRZ_SAML_ENABLED")
    if raw is not None:
        return raw not in ("0", "false", "no")
    return bool(metadata_url())


def _load_idp(url):
    now = time.time()
    if _idp_cache["data"] and _idp_cache["url"] == url and now - _idp_cache["at"] < _CACHE_S:
        return _idp_cache["data"]
    req = urllib.request.Request(url, headers={"User-Agent": "paparazzi-log-webui"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml = resp.read()
    parsed = OneLogin_Saml2_IdPMetadataParser.parse(xml.decode("utf-8", errors="replace"))
    _idp_cache.update(url=url, data=parsed, at=now)
    return parsed


def ensure_sp_certs(data_dir: Path):
    """Self-signed SP key used to sign AuthnRequests. Stored under the data dir, not in git."""
    folder = Path(data_dir) / "saml"
    key, cert = folder / "sp-private-key.pem", folder / "sp-cert.pem"
    if key.exists() and cert.exists():
        return key.read_text(), cert.read_text()
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "3650",
         "-nodes", "-keyout", str(key), "-out", str(cert),
         "-subj", "/CN=paparazzi-log-webui"],
        check=True, capture_output=True)
    os.chmod(key, 0o600)
    return key.read_text(), cert.read_text()


def _strip_pem(pem):
    return re.sub(r"-----[^-]+-----", "", pem).replace("\n", "").replace("\r", "").strip()


def sp_urls(base):
    root = base.rstrip("/")
    return {
        "entityId": os.environ.get("PPRZ_SAML_ENTITY_ID") or (root + "/saml/metadata"),
        "acs": root + "/saml/acs",
        "sls": root + "/saml/sls",
    }


def settings_for(request, data_dir: Path):
    urls = sp_urls(request.url_root)
    key, cert = ensure_sp_certs(data_dir)
    idp = _load_idp(metadata_url())
    https = request.is_secure or os.environ.get("PPRZ_COOKIE_SECURE") == "1"
    return {
        "strict": https,
        "debug": False,
        "sp": {
            "entityId": urls["entityId"],
            "assertionConsumerService": {
                "url": urls["acs"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": urls["sls"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
            "x509cert": _strip_pem(cert),
            "privateKey": _strip_pem(key),
        },
        "idp": idp.get("idp") or {},
        "security": {
            "authnRequestsSigned": True,
            "logoutRequestSigned": False,
            "wantAssertionsSigned": True,
            "wantNameId": True,
            "requestedAuthnContext": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            "allowRepeatAttributeName": True,
        },
    }


def flask_request_data(request):
    return {
        "https": "on" if request.is_secure else "off",
        "http_host": request.host,
        "script_name": request.path,
        "get_data": request.args.copy(),
        "post_data": request.form.copy(),
        "query_string": request.query_string.decode() if request.query_string else "",
    }


def auth_for(request, data_dir: Path):
    return OneLogin_Saml2_Auth(flask_request_data(request), old_settings=settings_for(request, data_dir))


def attr_first(attrs, *names):
    for name in names:
        vals = attrs.get(name)
        if not vals:
            continue
        if isinstance(vals, (list, tuple)):
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
        elif str(vals).strip():
            return str(vals).strip()
    return None


def identity_from_auth(auth):
    """Pick a stable subject, email, username and display name from the SAML assertion."""
    attrs = auth.get_attributes() or {}
    nameid = (auth.get_nameid() or "").strip()
    email = attr_first(attrs, "mail", "email", "urn:oid:0.9.2342.19200300.100.1.3",
                       "urn:oid:1.2.840.113549.1.9.1")
    uid = attr_first(attrs, "uid", "eduPersonPrincipalName", "urn:oid:0.9.2342.19200300.100.1.1",
                     "urn:mace:dir:attribute-def:uid")
    display = attr_first(attrs, "displayName", "cn", "urn:oid:2.16.840.1.113730.3.1.241",
                         "urn:mace:dir:attribute-def:displayName")
    subject = nameid or uid or email
    username = (uid or (email.split("@")[0] if email else "") or "user")
    username = re.sub(r"[^A-Za-z0-9._-]+", "", username)[:40] or "user"
    return {"subject": subject, "email": email, "username": username, "display_name": display}
