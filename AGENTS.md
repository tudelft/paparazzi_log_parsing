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
