"""API key scopes (issue 235): what a key may do, on top of what its user may do. Scopes only narrow.

A key has a space-separated list of scopes in api_keys.scopes:
  read              every GET (and HEAD), plus the read-only POSTs in READ_POSTS (MATLAB export requests)
  logs:upload       log upload and reservations, logs/check, attachments (upload, share), paper records (single, CSV)
  logs:edit         project, date, crew and comment of logs and flights, date suggestions, conflicts, reparse
  documents:upload  add a document and replace its file
  documents:manage  edit and delete documents, groups and the text at the top of Documents
  admin             the other administration routes (/api/admin/..., and the few admin routes elsewhere)
A request that changes something is allowed only when its endpoint is in the map below for one of the key's scopes
(or in WRITE_ANY and the key has any scope beyond read);
an endpoint that is not in the map is refused to every scoped key (fail closed), so a new route is never reachable
by key by accident. Routes that need a browser session keep refusing every key.
A key from before scopes (scopes NULL) behaves as before: read_only=1 as `read`, read_only=0 as all of its user's
rights, except managing documents (browser session only since issue 235). It can be narrowed to scopes.
"""
SCOPES = ("read", "logs:upload", "logs:edit", "documents:upload", "documents:manage", "admin")
ADMIN_SCOPES = ("documents:upload", "documents:manage", "admin")  # useless without administrator rights
LOGIN_SCOPES = ("read", "logs:upload", "logs:edit")  # keys from a browser login (Python package, MATLAB)
READ_POSTS = {"request_matlab_export", "request_flight_matlab_export", "request_flights_matlab_export"}
# a key with any scope beyond read may revoke its user's browser-login keys (the Python and MATLAB sign-out)
WRITE_ANY = {"revoke_all_keys"}

ENDPOINTS = {
    "logs:upload": {"upload", "reserve_bulk_upload", "check_logs", "upload_attachment", "share_attachment",
                    "create_historical", "import_historical"},
    "logs:edit": {"set_log_project", "set_log_date", "accept_log_date_suggestion", "set_flight_project",
                  "set_flights_project", "set_flight_crew", "set_flights_crew", "set_flight_comment", "reparse",
                  "accept_conflict"},
    "documents:upload": {"add_document", "replace_document_file"},
    "documents:manage": {"edit_document", "delete_document", "add_group", "edit_group", "delete_group",
                         "save_intro"},
    # besides every endpoint under /api/admin/ that is not about documents (see scope_for):
    "admin": {"reparse_outdated", "reparse_unknown_dates", "save_qualification_type", "edit_qualification_dates",
              "delete_qualification", "admin_update_drone", "admin_set_member"},
}
DOCUMENT_PREFIXES = ("/api/admin/documents", "/api/admin/document-groups")


def scope_for(endpoint, rule):
    """The scope a request that changes something needs, or None when no scoped key may make it."""
    for scope, endpoints in ENDPOINTS.items():
        if endpoint in endpoints:
            return scope
    if rule.startswith("/api/admin/") and not rule.startswith(DOCUMENT_PREFIXES):
        return "admin"
    return None


def allows(scopes, method, endpoint, rule):
    if method in ("GET", "HEAD") or endpoint in READ_POSTS:
        return "read" in scopes
    if endpoint in WRITE_ANY:
        return any(scope != "read" for scope in scopes)
    needed = scope_for(endpoint, rule)
    return needed is not None and needed in scopes


def parse(raw, is_admin, label="scopes"):
    """A list of scopes from a request body, in SCOPES order; raises ValueError with a message."""
    if not isinstance(raw, list) or not raw or not all(isinstance(s, str) for s in raw):
        raise ValueError(f"{label} must be a non-empty list of: {', '.join(SCOPES)}")
    unknown = [s for s in raw if s not in SCOPES]
    if unknown:
        raise ValueError(f"unknown scope {unknown[0]!r}; choose from: {', '.join(SCOPES)}")
    if not is_admin and any(s in ADMIN_SCOPES for s in raw):
        raise ValueError(f"only administrators can give a key {', '.join(ADMIN_SCOPES)}")
    return [s for s in SCOPES if s in raw]


def of_key(scopes, read_only):
    """The scopes of a stored key as a list, or None for an old full key (all of its user's rights)."""
    if scopes is not None:
        return scopes.split()
    return ["read"] if read_only else None


def narrower(new, current):
    """Whether new only takes rights away from current (None: an old full key)."""
    return current is None or set(new) <= set(current)
