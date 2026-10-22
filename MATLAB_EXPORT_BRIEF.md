# On-demand MATLAB export: implementation brief

Planning only; implementation has not started.

## Agreed requirements

- Generate a ready-to-load MATLAB v7.3/HDF5 `.mat` file on the server, without MATLAB installed.
- Initially support Paparazzi `.data` + companion `.log` files only.
- Export the entire selected original log, including all aircraft and messages, not just the viewed flight.
- Prefer a structure similar to `matlab/tools/parselog.m`, but exact compatibility is not required;
  existing MATLAB scripts/plotters may be adapted.
- Support multi-GB logs through queued background conversion with progress/status.
- Offer export on the flight page only, with separate labelled buttons for each source log (SD/GCS).
  Do not merge recordings. Clearly state that each download includes the whole source log.
- Allow requests from signed-in browser users who can download the original whole log.
  API-key export requests are out of scope. Recheck permissions for status/download access.
- Generate only on request. Reuse identical exports for 24 hours, then clean up automatically.
- Export decodable messages, preserve undecodable rows as raw data and include explicit warnings.
  Never silently discard undecodable data.
- Include aircraft identity, embedded message definitions/units, alternate-unit values, timestamps,
  autopilot version and export warnings.
- Do not add private qualifications or web-app account details. Original logs may contain identifying data.

## Implementation direction

1. Inspect the MATLAB parser and representative plotters; document the output schema.
2. Reuse suitable protocol-reading logic, but read original logs: stored flight summaries are incomplete.
3. Choose a genuine MATLAB-v7.3-compatible writer, not generic HDF5 alone.
   Verify bounded-memory writing for large datasets.
4. Add permission-checked job creation, status and download endpoints.
5. Deduplicate jobs by immutable source identity and exporter/schema version.
   Publish completed files atomically; never expose partial exports.
6. Bound concurrency, memory and temporary disk use. Handle failures, service restarts,
   source deletion and expired-file cleanup explicitly.
7. Add queued/running/ready/failed UI states and a short MATLAB loading/access example.

## Details to settle

- Nested structure, numeric types, array orientation, strings and variable-length arrays.
- Export storage location, disk budget and queue limits.
- Missing/unusable companion protocol handling; never silently substitute an unrelated protocol.
- Progress measurement and restart recovery.

## Minimal validation

Use synthetic fixtures covering multiple aircraft, scalar/array/string messages, alternate units,
malformed rows, permissions and cache expiry. Check representative large-log resource usage and
verify that MATLAB actually loads the output. No full test suite unless requested.
