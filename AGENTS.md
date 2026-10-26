# Coding assistant guidance

## Start here

- [User guide](webui/README.md): everyday workflows and quick start.
- [Technical guide](webui/TECHNICAL.md): setup, configuration, storage, permissions and detailed feature rules.
- Keep the user guide compact; put implementation and operational detail in the technical guide.

## Repository map

- `cpp/`: fast Paparazzi parser; use Release builds for meaningful performance measurements.
- `webui/app.py`: Flask routes, ingestion/parsing coordination and shared logbook read model.
- `webui/db.py`: SQLite schema and migrations; `config.py`: `LOGDB_*` configuration.
- `webui/historical.py`: paper records; `mav_parse.py`: other telemetry formats.
- `webui/qualifications.py`, `attachments.py`, `weather.py`, `storage.py`, `saml.py`: feature-specific helpers.
- `webui/static/`: plain HTML/JavaScript pages; shared navigation in `theme.js`, styling in `theme.css`.
- `webui/tests/`: unittest regression tests. Reuse existing fixtures; do not start real workers to test routes.

## Change conventions

- Preserve existing access checks, browser-session versus API-key restrictions and project scope.
  Private qualification evidence is separate from shared flight/log attachments.
- Preserve the distinction between uploaded-log `aircraft` rows and fleet `drones` identities.
  Paper records contribute counts/time without fabricated telemetry or automatic merging with digital logs.
- Keep SQLite on local disk; log/evidence storage may be on a share. See the technical guide before changing storage.
- Follow shared Edit-mode, Help, day/night and entity-detail navigation patterns when changing UI.
  Check both read-only and editing behavior and the relevant user/admin visibility.
- Update directly related documentation; avoid copying detailed rules into multiple guides.
- Do not touch real databases, logs, evidence, credentials or running services for development checks.
  Use isolated temporary fixtures; never add local data or environments to commits.

## Validation

- Use only simple, minimal validation relevant to the change. **Do not run the full test suite unless requested.**
- Documentation-only edits need no application tests; check local links and `git diff --check`.
- For Python changes, use the selected project environment and run a relevant test class/method.
  Example, from `webui/`: `python -m unittest tests.test_people.PeopleDirectoryTests -q`.
- App test fixtures use temporary storage/in-memory SQLite and load routes without starting ingestion workers.
- For UI-only changes, prefer editor diagnostics and a small mock-data browser check if needed.
  Do not install new tooling just to validate a small change.

### Browser checks (Playwright / integrated browser)

- Reuse a shared browser page when appropriate. Start an isolated mock server only when needed,
  verify its URL responds before opening it, and use fixtures matching the fields the page actually
  reads (including nested objects). A fixture-caused JavaScript error is not an application bug.
- Read the current page snapshot before interacting. Prefer accessible roles/names or stable IDs;
  check locator uniqueness before acting. Pagination numbers and Next/Last can share `data-page`
  values, so that attribute alone is not necessarily a unique selector.
- Keep each check short and focused: perform one transition, wait for its observable result, then
  inspect it. Avoid long interaction chains that hide which step failed. Wait for rendered rows,
  control values or URLs rather than fixed sleeps or network-idle on polling pages.
- Use normal clicks and keyboard input first. If an action stalls, inspect visibility, enabled
  state, overlays and locator matches before retrying. DOM-dispatched events are suitable for
  isolating handler behavior, but do not prove a real click or keyboard interaction works.
- If a browser call returns a deferred result ID, resume that call before starting another action
  on the same page. Do not leave competing checks running or repeat a timed-out chain unchanged.
- Check `document.visibilityState` when timing is surprising: integrated browser tabs can remain
  hidden even after `bringToFront()`, and `requestAnimationFrame` can be suspended. Do not diagnose
  broken restoration solely from a hidden-tab animation timeout; test the actual saved/restored
  state and distinguish tool limitations from user-visible behavior.
- For navigation checks, record page/filter/expansion state and scroll before leaving, then compare
  after Back/Forward and after rendering completes. Check both same-document history and returning
  from another page; browser back-forward caching can take a different path.
- After editing mock-server code, restart that specific server and verify it responds. Stop only
  the server started for the check when finished; leave loaded previews open when requested.

## Commits and releases

- When the user asks to commit/push, also create and push an annotated Web UI release tag
  `webui-vMAJOR.MINOR.PATCH` on the resulting tested commit, unless they explicitly opt out.
- Inspect existing remote tags and all changes since the previous release: bump PATCH for fixes,
  MINOR for new backward-compatible features, and MAJOR for breaking changes.
- Keep Web UI tags separate from parser versions. Never move or overwrite an existing release tag.
  Push the branch and new tag atomically where supported, and verify both remote refs.
