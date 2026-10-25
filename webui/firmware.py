"""Cached GitHub firmware evidence and explicitly uncertain GPS week estimates."""
import hashlib
import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import db


WEEK = 604800
GPS_EPOCH = 315964800
LEAPS = (362793600, 394329600, 425865600, 489024000, 567993600, 631152000,
         662688000, 709948800, 741484800, 773020800, 820454400, 867715200,
         915148800, 1136073600, 1230768000, 1341100800, 1435708800, 1483228800)
logger = logging.getLogger(__name__)
_lock = threading.Lock()
UPSTREAM = "paparazzi/paparazzi"


class LookupError(Exception):
    def __init__(self, resolution, message, retry_after=300):
        super().__init__(message)
        self.resolution = resolution
        self.retry_after = retry_after


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


def repositories(value):
    result = []
    for name in re.split(r"[\s,]+", value.strip()):
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", name) or name.split("/")[1] in (".", ".."):
            raise ValueError("GitHub repositories must be owner/repository names, separated by commas or whitespace.")
        if name.lower() != UPSTREAM and name.lower() not in [r.lower() for r in result]:
            result.append(name)
    return result


def request_json(path, token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PaparazziLogParsing-firmware",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # Do not forward credentials through redirects or follow URLs supplied by API responses.
        with build_opener(NoRedirects()).open(Request("https://api.github.com" + path, headers=headers), timeout=5) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise LookupError("invalid_response", "GitHub returned an oversized response.")
        return json.loads(raw)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (403, 429):
            delay = 300
            reset = exc.headers.get("X-RateLimit-Reset", "")
            retry = exc.headers.get("Retry-After", "")
            if reset.isdigit():
                delay = max(delay, int(reset) - int(time.time()) + 1)
            if retry.isdigit():
                delay = max(delay, int(retry))
            raise LookupError("github_access_or_rate_limit",
                              "GitHub denied access or rate-limited firmware lookups; check token access and retry later.",
                              min(delay, 86400)) from exc
        if exc.code == 422:
            raise LookupError("revision_ambiguous", "GitHub could not uniquely resolve the revision.") from exc
        raise LookupError("github_error", f"GitHub firmware lookup returned HTTP {exc.code}; retry later.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LookupError("github_unavailable", "Could not reach GitHub for firmware metadata; retry later.") from exc
    except (ValueError, UnicodeError) as exc:
        raise LookupError("invalid_response", "GitHub returned invalid firmware metadata.") from exc


def commit_metadata(data, revision, repository):
    try:
        full = data["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", full) or not full.startswith(revision.lower()):
            raise ValueError("Hash mismatch")
        committed = epoch(data["commit"]["committer"]["date"])
        authored = epoch(data["commit"]["author"]["date"])
        return {"resolution": "resolved", "commit": full, "repository": repository,
                "committed_at": iso(committed), "authored_at": iso(authored)}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError) as exc:
        raise LookupError("invalid_metadata", "GitHub returned incomplete or inconsistent commit metadata.") from exc


def lookup(revision, extra, token):
    def direct(repository):
        data = request_json(f"/repos/{repository}/commits/{revision}", token)
        return commit_metadata(data, revision, repository) if data is not None else None

    result = direct(UPSTREAM)
    if result:
        return result
    search = request_json("/search/commits?" + urlencode({"q": f"hash:{revision} is:public", "per_page": 100}), token)
    if (not isinstance(search, dict) or not isinstance(search.get("items"), list)
            or type(search.get("total_count")) is not int or type(search.get("incomplete_results")) is not bool):
        raise LookupError("invalid_response", "GitHub returned an invalid commit search response.")
    items = search["items"]
    if search.get("incomplete_results") or search.get("total_count", 0) > len(items):
        raise LookupError("search_incomplete", "GitHub commit search was incomplete; retry firmware resolution later.")
    if any(not isinstance(item, dict) or not isinstance(item.get("sha"), str) for item in items):
        raise LookupError("invalid_response", "GitHub search returned invalid commit entries.")
    hashes = {item["sha"] for item in items}
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value)
           or not value.startswith(revision.lower()) for value in hashes):
        raise LookupError("invalid_response", "GitHub search returned an inconsistent commit hash.")
    if len(hashes) > 1:
        raise LookupError("revision_ambiguous", "The abbreviated firmware hash matches multiple full commits.")
    for item in items:
        repo_data = item.get("repository")
        name = repo_data.get("full_name", "") if isinstance(repo_data, dict) else ""
        try:
            names = repositories(name)
        except (ValueError, AttributeError) as exc:
            raise LookupError("invalid_response", "GitHub search returned an invalid repository name.") from exc
        if len(names) != 1:
            raise LookupError("invalid_response", "GitHub search returned an invalid repository name.")
        name = names[0]
        # Global search can contain unrelated repositories; accept only the Paparazzi fork network.
        repo = request_json(f"/repos/{name}", token)
        source = repo.get("source") if isinstance(repo, dict) else None
        if isinstance(source, dict) and source.get("full_name") == UPSTREAM:
            result = direct(name)
            if result:
                return result
    for name in extra:
        result = direct(name)
        if result:
            return result
    return {"resolution": "revision_unresolved",
            "lookup_warning": "Firmware revision was not found. Private repositories may be inaccessible; check configured repositories/token and retry."}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Firmware timestamps must have a timezone")
    return instant.timestamp()


def resolve(con, revision, extra_repositories="", token=None, enabled=True):
    """Persist immutable successes and short-lived failures without storing credentials."""
    if not revision or not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
        return {"resolution": "revision_missing"}
    if not enabled:
        return {"resolution": "lookup_disabled"}
    try:
        extra = repositories(extra_repositories)
    except ValueError as exc:
        logger.warning("Firmware lookup configuration: %s", exc)
        return {"resolution": "configuration_error", "lookup_warning": str(exc)}
    scope = hashlib.sha256(json.dumps([extra, token or ""]).encode()).hexdigest()
    key = scope + "/" + revision.lower()
    backoff = scope + "/backoff"

    def cached(cache_key):
        row = con.execute("SELECT payload,expires_at FROM firmware_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return json.loads(row[0]) if row and (row[1] is None or row[1] > time.time()) else None

    def save(cache_key, result, expires):
        def write():
            with db.write_lock:
                con.execute("INSERT OR REPLACE INTO firmware_cache(cache_key,payload,expires_at) VALUES (?,?,?)",
                            (cache_key, json.dumps(result), expires))
                con.commit()
        db.retry_locked(write, con)

    with _lock:
        result = cached(key) or cached(backoff)
        if result is not None:
            return result
        ttl = 3600
        try:
            result = lookup(revision.lower(), extra, token)
        except LookupError as exc:
            logger.warning("Firmware revision %s: %s", revision, exc)
            result = {"resolution": exc.resolution, "lookup_warning": str(exc)}
            ttl = exc.retry_after
            if exc.resolution in ("github_access_or_rate_limit", "github_unavailable", "github_error"):
                save(backoff, result, time.time() + ttl)
        if result["resolution"] == "revision_unresolved":
            logger.warning("Firmware revision %s: %s", revision, result["lookup_warning"])
        save(key, result, None if result["resolution"] == "resolved" else time.time() + ttl)
        return result


def gps_to_utc(gps):
    offset = 0
    for leap in LEAPS:
        if gps >= leap + offset + 1:
            offset += 1
    return gps - offset


def set_absolute_times(obj, start):
    if isinstance(obj, dict):
        if "utc" in obj and obj.get("time_s") is not None:
            obj["utc"] = iso(start + obj["time_s"])
        for value in obj.values():
            set_absolute_times(value, start)
    elif isinstance(obj, list):
        for value in obj:
            set_absolute_times(value, start)


def enrich(doc, con, extra_repositories="", token=None, enabled=True):
    """Enrich parser diagnostics before paths and database rows are persisted."""
    log = doc.get("log", {})
    timing = log.get("timing")
    if not timing or "firmware" not in timing:
        return
    versions = timing["firmware"]
    for version in versions:
        version.update(resolve(con, version.get("revision"), extra_repositories, token, enabled))
    commits = [epoch(v["committed_at"]) for v in versions if v["resolution"] == "resolved"]
    reasons = timing.setdefault("reasons", [])
    if not commits:
        return
    latest = max(commits)
    start = log.get("start_utc")
    if start and epoch(start) < latest - 86400:
        reasons.append("recorded_date_predates_firmware")
        timing["firmware_warning"] = "Recorded date predates the firmware commit; commit dates are supporting evidence only."
        if timing.get("status") != "unknown":
            timing["status"] = "conflicting"
    if timing.get("status") != "unknown":
        return
    evidence = timing.get("gps_evidence", [])
    usable = [e for e in evidence if e.get("week_zero_samples", 0) >= 3
              and e.get("week_zero_span_s", 0) >= 30
              and e.get("week_zero_offset_spread_s", math.inf) <= 5
              and e.get("weeks") == [{"value": 0, "samples": e.get("samples")}]]
    unresolved_aircraft = {v["ac_id"] for v in versions if v["resolution"] != "resolved"}
    resolved_aircraft = {v["ac_id"] for v in versions if v["resolution"] == "resolved"} - unresolved_aircraft
    if not usable or len(usable) != len(evidence) or any(e["ac_id"] not in resolved_aircraft for e in usable):
        reasons.append("firmware_week_estimate_insufficient_evidence")
        return
    offsets = [e["week_zero_start_tow_s"] for e in usable]
    aligned = [offsets[0] + ((v - offsets[0] + WEEK / 2) % WEEK - WEEK / 2) for v in offsets]
    if max(aligned) - min(aligned) > 5:
        reasons.append("firmware_week_estimate_aircraft_conflict")
        return
    tow = sum(aligned) / len(aligned)
    commit_gps = latest + sum(latest >= leap for leap in LEAPS)
    gps_start = GPS_EPOCH + math.floor((commit_gps - GPS_EPOCH - tow) / WEEK) * WEEK + tow
    candidate = gps_to_utc(gps_start)
    if candidate < latest:
        candidate = gps_to_utc(gps_start + WEEK)
    if candidate < 946684800 or candidate > datetime.now(timezone.utc).timestamp() + 86400:
        reasons.append("firmware_week_estimate_date_implausible")
        return
    timing["parsed_source"] = timing["source"]
    timing["source"] = "firmware_gps_estimated"
    timing["status"] = "estimated"
    timing["estimated"] = True
    timing["estimate"] = {
        "basis": "first_gps_week_compatible_start_on_or_after_latest_firmware_commit",
        "firmware_committed_at": iso(latest), "week_uncertain": True,
        "alternative_interval_days": 7,
        "warning": "Estimated week: assumes flight within seven days after the firmware commit. Later weeks are possible; dirty firmware may contain unrecorded changes.",
    }
    timing["effective_start_utc"] = iso(candidate)
    timing["correction_s"] = candidate - epoch(start) if start else None
    reasons.append("firmware_gps_week_estimated")
    log["start_utc"] = iso(candidate)
    for key, relative in (("first_data_utc", "first_data_time_s"), ("end_utc", "end_time_s")):
        log[key] = iso(candidate + log[relative])
    for aircraft in doc.get("aircraft", []):
        span = aircraft.get("data_span", {})
        for key, relative in (("first_utc", "first_time_s"), ("last_utc", "last_time_s")):
            if relative in span:
                span[key] = iso(candidate + span[relative])
        set_absolute_times(aircraft, candidate)
