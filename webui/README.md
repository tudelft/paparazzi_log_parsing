# Flight logbook

## Setup and further help

For a local trial, run from `webui/` using your Python environment after building the
[C++ parser](../cpp/) in **Release** mode:

```sh
cmake -S ../cpp -B ../cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build ../cpp/build
pip install -r requirements.txt
python3 app.py create-user alice --admin
python3 app.py                                  # http://127.0.0.1:8000
```

For server installation, configuration, backups, account administration, detailed feature rules,
tests and API/storage architecture, use the [technical guide](TECHNICAL.md).

Upload flight logs and browse flights, crew, projects, aircraft and locations in one logbook.
All pages require sign-in; the records you can see depend on your access.
The home page (`/`) opens the logbook. Uploads has its own page at `/uploads`;
existing `/logbook` links still work.

## Everyday use

| Page | What to do |
|---|---|
| **Logbook** | Filter flights, review flight time, expand a row or open its flight page. Click aircraft, crew, project or location names for their detail pages. |
| **Uploads** | Drop logs, ZIP archives or folders; check parsing progress and errors. Paparazzi logs need matching `.data` and `.log` files. PX4 `.ulg`, ArduPilot `.bin`/`.log` and `.tlog` files can also be uploaded. |
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
  If the aircraft did not send a version, the Paparazzi version from the GCS `.log` header is shown instead.

## Add or correct information

- In **Logbook**, Edit mode exposes crew/project corrections where permitted.
  Admins can bulk-edit paper and telemetry records together, or **Hide selected** to remove entries
  from the logbook and totals without deleting files. Use **Hidden records only** and **Restore selected**
  to undo this. Paper records must keep a PIC and project.
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
Use **Valid from** and **Valid until** to record the licence period, including past qualifications.
Admins can make either date optional per type; both date switches start checked for new types.
An Internal switch marks in-house qualifications. Drag types to set their order, then use one Save.
In Edit mode, **Import ZIP** on People loads a `manifest.csv` plus evidence PDFs: preview, then import
and approve in one step. Older licence periods go to the **Archive** tab of each person's qualifications.
Names not yet on People are added as new users (shown as **New user** in the preview).
Dates are entered manually, and existing records keep an unknown start date. Historical flight
eligibility is not checked. The read-only People list shows only approved, non-expired qualifications,
without an approval-status label; management still shows pending, expired and previous submissions.

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

A key can also add paper records: `POST /api/historical` (full project access) and, with an admin's key,
the CSV import `POST /api/historical/import`. Changing or deleting records, and managing keys or the
account, still need a browser session.
