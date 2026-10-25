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

On **Uploads**, use **Filename** to find logs by part of their filename across all pages.
It works together with the status filter.

After changing firmware lookup repositories, administrators can use **Re-parse unknown dates**
on **Uploads** to retry completed logs with unknown dates, even with the current parser version.
Manual dates and plausible estimated dates are left unchanged.

Estimated dates in the logbook have a dotted underline. Hover over or keyboard-focus the
date for an explanation; the full timing evidence remains available in the flight details.

| Page | What to do |
|---|---|
| **Logbook** | Filter flights (also by a paper record's reference, e.g. F00090), review flight time, expand a row or open its flight page. Click aircraft, crew, project or location names for their detail pages. |
| **Uploads** | Drop logs, ZIP archives or folders; check parsing progress and errors. Paparazzi logs need matching `.data` and `.log` files. PX4 `.ulg`, ArduPilot `.bin`/`.log` and `.tlog` files can also be uploaded. |
| **Statistics** | View totals for aircraft, projects and crew within your access. |
| **Aircraft / People / Projects / Locations** | Browse records and open their histories, totals and flights. |
| **My account** | Click your name at the top right to manage your password, API keys and private qualification submissions. |

- Dates and times are **UTC**. Indoor and uncertain/non-flight records are hidden by default;
  enable their filters when needed.
- The page footer shows the Web UI release (when tagged), commits since that release and running
  revision, linking to that exact commit on GitHub.
  **(modified)** indicates local code changes; this is separate from the telemetry parser version.
- Use **Flight filters** in the top menu to select a period (default: last three years), custom
  UTC dates, **Show indoor / no-GPS flights**, **Show non-flights** and **Show unknown dates**.
  Unknown-date records are hidden by default. **Show unknown dates** includes them alongside the
  selected period, independently of **Show non-flights**; other visibility filters still apply.
  Corrected and **estimated dates** follow the selected period normally, even with **Show unknown dates**
  off. Estimated dates are labelled: the GPS time of day is known but the week is guessed from the
  firmware commit. Confirm or correct the date in Edit mode. Choose **Apply filters**;
  the selection stays the same when moving between pages in this browser. **Reset** restores
  the defaults after applying. The filters cover Logbook, Statistics, Locations and individual
  person, aircraft, project and location overviews, including their totals, maps, charts and
  flight lists. Detail-page log lists show contributing logs; zero-flight logs appear only
  with **Show non-flights** enabled and within the selected period.
- On a flight's **Path & charts** tab, use **Show sectors** to hide/show flight-plan sector
  boundaries independently of the flight-plan waypoint markers. Waypoint names appear above
  their dots with a subtle text halo; **Show flight plan** hides/shows both the dots and names. Waypoints whose names
  start with `_` are hidden by default; enable **Show hidden waypoints** to reveal them.
- Uploads has a **Status** filter: **Parser errors**, **Unfinished logs** (queued, parsing or
  error), or **All failures** (also failed/rejected upload receipts). Filters cover all pages.
- Statistics uses the shared flight filters. **Total log data (GB)** sums original telemetry sizes for logs contributing to
  filtered flights, counting each SD/GCS source once and excluding companion files.
  **Flying days** counts distinct UTC dates; **Average flight** divides total time by the recorded flight count,
  including paper records. **Monthly activity** switches between hours and flights; hover over a bar for both.
  Unknown dates are excluded from flying days and monthly activity, with an explanatory count on the chart.
  Aircraft, project and crew tables scroll independently, with column headings kept visible.
- Detected simulations are labelled and excluded from overview totals by default. Enable **Show
  non-flights** to include simulations, uncertain flights and ground records. Flight details show the evidence; reparse outdated Paparazzi
  uploads to assess older recordings.
- A missing telemetry endpoint location uses the other endpoint's GPS position for display.
  Flights with both endpoint locations unknown are included in indoor/no-GPS filtering, even if a partial
  GPS path exists; the flight page labels this inference, which is not proof of indoor operation.
- A paper record that clearly matches a logged flight (same aircraft, takeoff within 5 min) is linked to it
  and counts once, as the log; unclear cases show as *possible duplicate* to link or dismiss, and an expanded
  entry shows where the paper record differs from the log.
- An onboard log without a calendar date (1980 clock) that matches a dated ground station log of the same flight gets
  a **suggested date** in its details on Uploads; accept it there (admins can accept all at once).
- Uploading a file that someone else already uploaded keeps one copy and makes you a **co-uploader**: you can then
  see that log and its flights, and the Uploads page links to it.
- In Edit mode, tick the entries of one flight and press **Same flight** to join them (also when the automatic
  SD/GCS merge missed it), or press **Not the same flight** next to a recording in an expanded entry to split it off.
- SD and GCS recordings of the same flight are merged. **Combine flights** additionally groups nearby
  flights into sessions; it does not change stored flights.
- Hover over an aircraft name in the logbook for its TUD/serial number; it is also on the aircraft detail page.
- Turn on **Edit** to show permitted editing controls. It stays enabled across pages until turned off.
  Turn it off for the simpler browsing view. Longer explanations are under **Help**.
- Flight pages include the path, charts, weather, settings, messages, pre-flight checks and software version.
  Expanded Logbook rows show comments and buttons beside the map (stacked on small screens).
  Comments are read-only there; edit them on the flight page.
  Distance from takeoff and maximum range are shown only on the flight page, not in overview tables or statistics.
  If the aircraft did not send a version, the Paparazzi version from the GCS `.log` header is shown instead.

## Add or correct information

Administrators can use **People → Edit → Account** to grant or remove another person's admin rights
and inspect or revoke their API keys (not retrieve secret values). You cannot remove your own admin rights.

- The **Locations** map groups nearby places into numbered markers when zoomed out.
  Click a group to zoom in; hover for names or select a location to keep its label visible.
  Boundaries appear when zoomed in or editing. Use **Find location** to filter the table,
  then **Show** to select and zoom to a place.
- On **Locations**, admins can turn on **Edit**, choose **Edit shape** beside a place,
  then drag the map handles to move or resize its circle or polygon. Choose **Save shape**
  to apply the boundary or **Cancel** to discard changes. **Name / type** changes its name
  or indoor status separately.
  To replace the boundary entirely, use **Edit shape**, select a **New shape** (polygon,
  rectangle or circle), then **Redraw boundary** and draw it on the map. **Save shape**
  keeps the location's name and linked records; **Cancel** restores the original boundary.
  Admins in **Edit** mode can use **Only unnamed dots** to hide already named endpoint dots, then select a coordinate
  or use **Previous / Next** to visit each remaining dot on the map. In **Edit** mode,
  draw and name a boundary around it; covered positions leave the review list after saving.

- In **Logbook**, Edit mode exposes crew/project corrections where permitted.
  Admins can bulk-edit paper and telemetry records together, or **Hide selected** to remove entries
  from the logbook and totals without deleting files. Use **Hidden records only** and **Restore selected**
  to undo this. Paper records must keep a PIC and project.
  Use **Add paper record** to enter historical flights without logs. Admins can also import CSV;
  importing a corrected CSV again updates records matched by reference (e.g. flight number F00001)
  or by aircraft, date and takeoff time, after a preview of what each row will do.
  Paper records add flight counts and time, not telemetry; check for overlap with digital logs.
  Overviews show their recorded location in both the takeoff and landing location columns;
  this does not add a landing time or GPS coordinates.
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
  Servers can hide unlinked aircraft flights from Logbook, Statistics, person details and Locations until linked;
  admins can still find the names in Aircraft Edit mode (see the [settings](TECHNICAL.md#settings)).
  Mark generic airframe names as **ambiguous** in Edit mode instead of linking every recording
  to one aircraft. A uniquely matching GCS recording can identify its SD source; use the
  shared-name panel for explicit per-log assignments when needed.
- Crew names in logs do not create user profiles. Admins can link multiple names to one person using
  aliases, or use Add user if a new profile is needed. Previously generated profiles are kept.
- Admins manage project access on **Projects** and named takeoff areas on **Locations**.
  In Edit mode, Projects supports CSV import with `project,person,role`; blank roles mean view/upload.
  Reimports replace all members of included projects. For initial setup, import qualifications first,
  then projects, then aircraft. Aircraft project changes require a `project_effective_date`;
  manual and legacy flight assignments are preserved. Both import dialogs offer templates.
- Locations has **Import places from CSV** in Edit mode. It accepts `logbooks/places.csv` from SVN as is
  (`place,lat,lon,radius_m,indoor`; the other columns are ignored) and shows a preview before importing.

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
Clicking a file name downloads it; the eye icon next to a PDF, text file or image opens it in a new tab.
Word files (`.docx`) get the eye icon too when LibreOffice is installed on the server; they open as PDF.
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
the CSV import `POST /api/historical/import`. A key can also upload additional files to records it may edit
(`POST /api/attachments`, which returns the file id) and link a file to more records
(`POST /api/attachments/<id>/links`). Changing or deleting records, unlinking files, and managing keys or the
account, still need a browser session.
