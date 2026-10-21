# Flight logbook

Upload flight logs and browse flights, crew, projects, aircraft and locations in one logbook.
All pages require sign-in; the records you can see depend on your access.

## Everyday use

| Page | What to do |
|---|---|
| **Logbook** | Filter flights, review flight time, expand a row or open its flight page. Click aircraft, crew, project or location names for their detail pages. |
| **Uploads** | Drop logs, ZIP archives or folders; check parsing progress and errors. Paparazzi logs need matching `.data` and `.log` files. |
| **Statistics** | View totals for aircraft, projects and crew within your access. |
| **Aircraft / People / Projects / Locations** | Browse records and open their histories, totals and flights. |
| **My account** | Click your name at the top right to manage your password, API keys and private qualification submissions. |

- Dates and times are **UTC**. Indoor and uncertain/non-flight records are hidden by default;
  enable their filters when needed.
- SD and GCS recordings of the same flight are merged. **Combine flights** additionally groups nearby
  flights into sessions; it does not change stored flights.
- Hover over an aircraft name in the logbook for its TUD/serial number; it is also on the aircraft detail page.
- Turn on **Edit** to show permitted editing controls. It stays enabled across pages until turned off.
  Turn it off for the simpler browsing view. Longer explanations are under **Help**.
- Flight pages include the path, charts, weather, settings, messages, pre-flight checks and software version.

## Add or correct information

- In **Logbook**, Edit mode exposes crew/project corrections where permitted.
  Use **Add paper record** to enter historical flights without logs. Admins can also import CSV.
  Paper records add flight counts and time, not telemetry; check for overlap with digital logs.
  CSV crew names follow log matching: use unique display names or explicit aliases, not login usernames
  unless added as aliases. Unknown or ambiguous names produce a row error without importing any records.
  The original CSV is attached to all imported records: anyone who can read one can download the
  whole CSV, including rows from other projects. Failed imports retain no attachment.
- Admins manage **People** by selecting a person and opening Account, Aliases or Qualifications.
  Email is optional when adding a user and can be cleared later.
  Username, display name, email and default project share one **Save account** button.
  The normal overview shows aliases too. Logs match explicit aliases or unique display names, not usernames.
  Add a username as an alias only if it reliably identifies that person in logs. Duplicate display names
  stay unresolved unless an explicit alias identifies the person. Renaming a username does not add an alias.
- Admins manage **Aircraft** using Details, Project history and Aliases. Add/import buttons open dialogs;
  Close or Escape dismisses them. Link unrecognised log names to an existing aircraft/person in Edit mode.
- Crew names in logs do not create user profiles. Admins can link multiple names to one person using
  aliases, or use Add user if a new profile is needed. Previously generated profiles are kept.
- Admins manage project access on **Projects** and named takeoff areas on **Locations**.

## Qualifications and additional files

**Qualifications:** submit your own evidence under **My account**; admins review it on **People**.
Each type determines whether evidence and expiry are required. Evidence is one PDF, JPEG or PNG,
up to **20 MiB**. Submissions need admin approval, and the previous approval stays active during renewal.
Expired approvals are marked; approval alone does not establish operational eligibility.
Evidence and submission history are private to you and admins; audit history is admin-only.
Pilot pages show approved summaries, not evidence.

**Additional files:** use a flight's Files tab, a paper record's Additional files section or an
expanded upload to attach photos, video or other recordings (default limit **10 GiB** per file).
**Anyone able to read any linked flight/log can download the file. Linking across projects shares
its contents. Never put private qualification evidence here.**

Historical weather is fetched only when you request it. That sends rounded takeoff coordinates and
the UTC date to Open-Meteo; its free service is non-commercial only.

## Upload a folder with a script

Create an API key on **My account** and download [upload_logs.py](upload_logs.py). It needs Python 3
and skips Paparazzi pairs the server already has:

```sh
export LOGDB_SERVER=https://logs.example.org
export LOGDB_API_KEY=your-api-key
python3 upload_logs.py ~/paparazzi/var/logs -n      # preview
python3 upload_logs.py ~/paparazzi/var/logs         # upload
```

Keep the key private. For a large initial import, see [controlled bulk uploads](TECHNICAL.md#uploading-with-a-script).

## Setup and further help

For a local trial, run from `webui/` using your Python environment after building the
[C++ parser](../cpp/) in **Release** mode:

```sh
pip install -r requirements.txt
python3 app.py create-user alice --admin
python3 app.py                                  # http://127.0.0.1:8000
```

For server installation, configuration, backups, account administration, detailed feature rules,
tests and API/storage architecture, use the [technical guide](TECHNICAL.md).
