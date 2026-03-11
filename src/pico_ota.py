# pico_ota.py
# Pico W firmware OTA update via GitHub Releases.
#
# Flow: fetch latest firmware manifest from keglevelmonitor/keglevelpico releases,
# compare with Pico's /api/version, download zip, verify SHA256, extract, push
# each file to Pico via POST /api/update.

import hashlib
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

try:
    import urllib.request as _urllib
    import urllib.error as _urllib_error
except ImportError:
    _urllib = None
    _urllib_error = None

RELEASES_URL = "https://api.github.com/repos/keglevelmonitor/keglevelpico/releases"
REQUEST_TIMEOUT_S = 30


def _parse_version(ver_str: str) -> tuple:
    """Parse '1.0.0' -> (1, 0, 0) for comparison."""
    try:
        parts = [int(x) for x in re.sub(r"[^0-9.]", "", ver_str).split(".")[:3]]
        return tuple(parts + [0, 0, 0][: 3 - len(parts)])
    except (ValueError, IndexError):
        return (0, 0, 0)


def _version_lt(a: str, b: str) -> bool:
    """True if a < b (a needs update to reach b)."""
    return _parse_version(a) < _parse_version(b)


def fetch_latest_firmware_info():
    """
    Fetch latest firmware release info from GitHub.
    Returns (manifest_dict, download_url) or (None, None) on failure.
    """
    if _urllib is None:
        return None, None
    req = _urllib.Request(RELEASES_URL, headers={"Accept": "application/vnd.github.v3+json"})
    try:
        with _urllib.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            releases = json.loads(r.read().decode())
    except Exception:
        return None, None

    # Find latest release with tag firmware-X.Y.Z (highest version)
    best_manifest, best_zip_url, best_ver = None, None, (0, 0, 0)
    for rel in releases:
        tag = rel.get("tag_name", "")
        if not tag.startswith("firmware-"):
            continue
        ver_str = tag.replace("firmware-", "", 1)
        ver_tup = _parse_version(ver_str)
        assets = rel.get("assets", [])
        manifest_asset = next((a for a in assets if a["name"].startswith("manifest-") and a["name"].endswith(".json")), None)
        zip_asset = next((a for a in assets if a["name"].endswith(".zip")), None)
        if not manifest_asset or not zip_asset:
            continue
        manifest_url = manifest_asset.get("browser_download_url")
        zip_url = zip_asset.get("browser_download_url")
        if not manifest_url or not zip_url:
            continue
        try:
            mreq = _urllib.Request(manifest_url, headers={"Accept": "application/octet-stream"})
            with _urllib.urlopen(mreq, timeout=REQUEST_TIMEOUT_S) as mr:
                manifest = json.loads(mr.read().decode())
        except Exception:
            continue
        if "version" in manifest and "firmware_sha256" in manifest and ver_tup > best_ver:
            best_manifest, best_zip_url, best_ver = manifest, zip_url, ver_tup
    if best_manifest and best_zip_url:
        return best_manifest, best_zip_url
    return None, None


def download_and_verify_zip(zip_url: str, expected_sha256: str) -> bytes:
    """Download zip and verify SHA256. Returns zip bytes or raises on failure."""
    req = _urllib.Request(zip_url, headers={"Accept": "application/octet-stream"})
    with _urllib.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got.lower() != expected_sha256.lower():
        raise ValueError(f"SHA256 mismatch: expected {expected_sha256[:16]}..., got {got[:16]}...")
    return data


def install_firmware_to_pico(base_url: str, manifest: dict, zip_bytes: bytes, log_cb=None) -> bool:
    """
    Extract zip, push each file to Pico via POST /api/update.
    Pushes main.py last with reboot=true.
    base_url: e.g. http://keglevel-pico.local
    Returns True on success.
    """
    def log(msg):
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    update_url = f"{base_url.rstrip('/')}/api/update"
    if _urllib is None:
        log("[Pico OTA] urllib not available.")
        return False

    # Verify zip
    got_sha = hashlib.sha256(zip_bytes).hexdigest()
    if got_sha.lower() != manifest.get("firmware_sha256", "").lower():
        log("[Pico OTA] Zip SHA256 mismatch.")
        return False

    with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
        names = [n for n in zf.namelist() if n.endswith(".py") and "license" not in n.lower()]
        # Order: server.py first (adds path support for lib/), then lib/*, then rest, main last
        ordered = []
        if "server.py" in names:
            names.remove("server.py")
            ordered.append("server.py")
        ordered.extend([n for n in names if n.startswith("lib/")])
        for n in ordered:
            if n in names:
                names.remove(n)
        ordered.extend([n for n in names if n != "main.py"])
        if "main.py" in names:
            ordered.append("main.py")
        files = [(n, zf.read(n).decode("utf-8")) for n in ordered]

    for i, (arcname, content) in enumerate(files):
        reboot = i == len(files) - 1
        payload = json.dumps({"filename": arcname, "content": content, "reboot": reboot}).encode()
        req = _urllib.Request(
            update_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _urllib.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
                resp = json.loads(r.read().decode())
            if resp.get("status") != "ok":
                log(f"[Pico OTA] Failed to write {arcname}: {resp}")
                return False
            log(f"[Pico OTA] Wrote {arcname}")
        except (_urllib_error.HTTPError, _urllib_error.URLError, OSError) as e:
            err_detail = ""
            if isinstance(e, _urllib_error.HTTPError) and hasattr(e, "read"):
                try:
                    err_detail = e.read().decode()[:200]
                except Exception:
                    pass
            log(f"[Pico OTA] Error pushing {arcname}: {e}" + (f" | {err_detail}" if err_detail else ""))
            return False

    log("[Pico OTA] Update complete. Pico is rebooting.")
    return True


def fetch_pico_version(base_url: str) -> str:
    """GET /api/version from Pico. Returns version string or empty on failure."""
    if _urllib is None:
        return ""
    url = f"{base_url.rstrip('/')}/api/version"
    try:
        req = _urllib.Request(url)
        with _urllib.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            return data.get("version", "")
    except Exception:
        return ""


def check_pico_firmware_update(pico_version: str) -> tuple:
    """
    Check if a Pico firmware update is available.
    Returns (update_available: bool, latest_version: str or None, error_msg: str or None).
    """
    manifest, _ = fetch_latest_firmware_info()
    if not manifest:
        return False, None, "Could not fetch firmware info from GitHub."
    latest = manifest.get("version", "")
    if not latest:
        return False, None, "Invalid manifest."
    if _version_lt(pico_version, latest):
        return True, latest, None
    return False, latest, None
