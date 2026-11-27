"""API key scopes (issue 235): what a key may do, on top of what its user may do. Scopes only narrow.

A key has a space-separated list of scopes in api_keys.scopes:
  read              every GET (and HEAD), plus the read-only POSTs in READ_POSTS (MATLAB export requests, matching files to flights)
  logs:upload       log upload and reservations, logs/check, attachments (upload, share), paper records (single, CSV)
  logs:edit         project, date, crew, comment, pre-flight inspection, tags and track origin of logs and flights,
                    date suggestions, conflicts, reparse
  documents:upload  add a document and replace its file
  documents:manage  edit and delete documents, groups and the text at the top of Documents
  admin             the administration routes listed in ENDPOINTS["admin"] (places, drone zones, operators, authorisations,
                    projects, aircraft, tags, who may choose a category, deleting accounts, reparse); not the account routes that can lead to a sign-in
                    (new account, setup link, email, password, admin rights): those need a browser session, for every key
A request that changes something is allowed only when its endpoint is in the map below for one of the key's scopes
(or in WRITE_ANY and the key has any scope beyond read);
an endpoint that is not in the map is refused to every scoped key (fail closed), so a new route is never reachable
by key by accident. Routes that need a browser session keep refusing every key.
A key from before scopes (scopes NULL) behaves as before: read_only=1 as `read`, read_only=0 as all of its user's
rights, except managing documents (browser session only since issue 235). It can be narrowed to scopes (see old_full).
"""
SCOPES = ("read", "logs:upload", "logs:edit", "documents:upload", "documents:manage", "admin")
ADMIN_SCOPES = ("documents:upload", "documents:manage", "admin")  # useless without administrator rights
LOGIN_SCOPES = ("read", "logs:upload", "logs:edit")  # keys from a browser login (Python package, MATLAB)
READ_POSTS = {"request_matlab_export", "request_flight_matlab_export", "request_flights_matlab_export",
              "match_attachments"}
# a key with any scope beyond read may revoke its user's browser-login keys (the Python and MATLAB sign-out)
WRITE_ANY = {"revoke_all_keys"}

ENDPOINTS = {
    "logs:upload": {"upload", "reserve_bulk_upload", "check_logs", "upload_attachment", "share_attachment",
                    "create_historical", "import_historical"},
    "logs:edit": {"set_log_project", "set_log_date", "accept_log_date_suggestion", "set_flight_project",
                  "set_flights_project", "set_flight_crew", "set_flights_crew", "set_flight_comment",
                  "set_flight_preflight", "set_flight_tags", "set_flight_track_origin", "reparse", "accept_conflict"},
    "documents:upload": {"add_document", "replace_document_file"},
    "documents:manage": {"edit_document", "delete_document", "add_group", "edit_group", "delete_group",
                         "save_intro"},
    # listed one by one, so a new administration route is refused to scoped keys until it is added here
    "admin": {"admin_add_place", "admin_update_place", "admin_delete_place", "admin_import_places",
              "admin_import_uas_zones", "admin_remove_uas_zones",
              "admin_save_operator", "admin_delete_operator", "admin_save_authorisation", "admin_delete_authorisation",
              "admin_add_project", "admin_update_project", "admin_delete_project", "admin_import_projects",
              "admin_set_member", "admin_add_drone", "admin_update_drone", "admin_delete_drone",
              "admin_set_drone_projects", "admin_import_drones", "admin_add_drone_alias", "admin_delete_drone_alias",
              "admin_link_drone_board", "admin_drone_board", "admin_add_alias", "admin_delete_alias",
              "admin_accept_date_suggestions", "admin_delete_user", "reparse_outdated", "reparse_unknown_dates",
              "admin_tag", "admin_merge_tag", "admin_category_rule"},
}


def scope_for(endpoint):
    """The scope a request that changes something needs, or None when no scoped key may make it."""
    for scope, endpoints in ENDPOINTS.items():
        if endpoint in endpoints:
            return scope
    return None


def allows(scopes, method, endpoint):
    if method in ("GET", "HEAD") or endpoint in READ_POSTS:
        return "read" in scopes
    if endpoint in WRITE_ANY:
        return any(scope != "read" for scope in scopes)
    needed = scope_for(endpoint)
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


def old_full(is_admin):
    """What an old full key (scopes NULL) may do, as scopes: everything its user may, except managing documents."""
    return [s for s in SCOPES if s not in ("documents:upload", "documents:manage") and (is_admin or s not in ADMIN_SCOPES)]


def narrower(new, current, is_admin):
    """Whether new only takes rights away from current (None: an old full key of a user with is_admin)."""
    return set(new) <= set(old_full(is_admin) if current is None else current)
