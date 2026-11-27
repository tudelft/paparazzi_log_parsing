# Flight logbook

## Setup and further help

For a local trial, follow the [quick start](../README.md#quick-start).
For server installation, configuration, backups, account administration, detailed feature rules,
tests and API/storage architecture, use the [technical guide](TECHNICAL.md).

Upload flight logs and browse flights, crew, projects, aircraft and locations in one logbook.
All pages require sign-in; the records you can see depend on your access. Where TU Delft sign-in is set up, the
sign-in page shows only that; **Sign in with a local account** opens the username/password form (`/login?local=1`).
After signing in you land on **Home**; the logbook stays at `/`. The menu has Home, Logbook, Agenda, Training and
Safety (when enabled) and **Directory ▾** (People, Aircraft, Projects, Locations, Documents, then Logs and Statistics). **⬇ Download logs** at the top right shows how to fetch logs from Python or MATLAB, **⬆ Upload** opens the upload dialog; your
name opens My account, Night mode and Sign out. Existing `/logbook` links still work.

## Everyday use

**Home** (first in the menu, and where signing in lands) shows what needs your attention (expired
trainings, qualifications that expire soon, logs without a date, approvals for admins), your flying
as PIC (untick **Only mine** for all flying in your projects), the coming bookings, the weather at a flying site,
the latest flights and the latest photos and videos of your projects, and, when you add them with **Customize**, your
qualifications, trainings, aircraft and recent documents. **Customize** chooses and orders the cards for your
account; the **Only mine** choices are saved too (notices and Needs your attention stay at the top). Choose the weather location
under **My account**; the Logbook stays at `/`.

On **Logs** (Directory ▾), use **Filename** to find logs by part of their filename across all pages.
It works together with the status filter.

Logbook, Logs and entity flight/log tables remember their page and view options in the
URL. Open a flight and use browser **Back** to return to the same view and scroll position.
Use the arrows or page numbers to move through large lists. The first and last page numbers
stay visible; type into the highlighted current-page number to jump directly to another page.

After changing firmware lookup repositories, administrators can use **Re-parse unknown dates**
on **Logs** to retry completed logs with unknown dates, even with the current parser version.
Manual dates and plausible estimated dates are left unchanged.

Estimated dates in the logbook have a dotted underline. Hover over or keyboard-focus the
date for an explanation; the full timing evidence remains available in the flight details.

Merged SD/GCS flights normally use SD. When GCS has a substantially better, nearly complete GPS
track, the logbook uses GCS consistently for the track, distance and flight-page link.
The flight page explains the choice under **Log sources and upload details**, where either
original recording can still be opened or downloaded.

The **Checklist** tab shows all recorded checklist answers (item, answer, time), including crew and flight goal.
For Paparazzi logs with embedded checklist definitions, full descriptions replace abbreviations;
hover over or focus a description to see its help text (dotted underline) and the original message.
Ticked checkboxes show as ✓, unticked as ✗; optional items left empty as –. Older logs need re-parsing
to gain descriptions and previously omitted crew entries.

**Pre-flight** (flight page, under Operation) says whether the pre-flight inspection is recorded: the checklist in the
log (one checklist covers every flight of that log), confirmed by hand, or ticked on the paper record. Indoor flights
and simulations do not need it. Flights and paper records from before it was recorded count as done by their PIC.
Without a checklist, the PIC or anyone else who may edit the flight clicks
**Confirm…**; Edit flight takes a confirmation back. The **last-minute risk assessment (LMRA)**, the risks of the
location, airspace or weather that day and how you mitigate them, comes from the checklist item `special` (Special
conditions), or is typed with the confirmation or on the paper record. The flight page always shows it for outdoor
flights: *Nothing special* when the inspection is recorded without one, *Not recorded* without an inspection. Several flights at once: tick them and use **Change…**. The
Logbook has a **Pre-flight** column (hidden by default, under **Columns**), and **Export CSV** includes it.

| Page | What to do |
|---|---|
| **Logbook** | Filter flights (also by a paper record's reference, e.g. F00090), review flight time, expand a row or open its flight page (Back returns to the logbook as you left it; ← / → there go to the previous/next flight of the project). Click aircraft, crew, project or location names for their detail pages. |
| **Download logs** | The button at the top right: install and example code for the Python package and the MATLAB tools, to download logs by flight number as `.mat` exports or original files. |
| **Upload** | The button at the top right (or drop files on any page): send logs, ZIP archives or folders and follow your recent uploads while they are parsed. Photos, videos and documents dropped with them are matched to your flights by when they were made (photo/video time, a time in the name, else the file date): a review shows the proposed flight per file, which you can change, before anything is uploaded. **Logs** (Directory ▾) lists every log you can see, with parsing progress and errors. Paparazzi logs need matching `.data` and `.log` files. PX4 `.ulg`, ArduPilot `.bin`/`.log`, `.tlog` and Betaflight/INDIFlight `.bfl`/`.bbl` files can also be uploaded. A folder named after one fleet aircraft still links when the files sit in subfolders; already uploaded logs with that layout are linked at each start. ArduPilot logs are linked by their flight controller's unique ID, not by their default name (`ArduPilot-1` is the same on every drone whose system ID was never changed): link the board once under Aircraft → Shared names and every log from it follows ([details](TECHNICAL.md#default-autopilot-names)). |
| **Statistics** | Under Directory: totals for aircraft, projects and crew within your access; admins also see the disk space used. |
| **Aircraft / People / Projects / Locations** | Browse records and open their histories, totals and flights. **My aircraft** on Aircraft shows the aircraft you are the responsible person of; a person's page lists them under **Responsible for**. When enabled, admins can **View as** a person from their page to see and use the site as them; see [View as another user](TECHNICAL.md#view-as-another-user). |
| **Training** | When enabled, complete questionnaires until you pass. A pass writes the linked qualification immediately. |
| **Agenda** | When enabled, book shared items such as an indoor arena; see [Agenda](#agenda). |
| **My account** | Click your name at the top right to manage your password, API keys, private qualification submissions, which emails you get and, when enabled, your profile. |

- Dates and times are **UTC**. Indoor and uncertain/non-flight records are hidden by default;
  enable their filters when needed.
- The page footer shows the Web UI release (when tagged), commits since that release and running
  revision, linking to that exact commit on GitHub.
  **(modified)** indicates local code changes; this is separate from the telemetry parser version.
- **Tags** are your own labels for logbook entries, such as "bench test" or "sim-study-3": one list for all projects.
  Tag entries with **Tags…** in the selection bar (for all ticked entries), in **Edit flight** or on a paper record;
  typing a new name makes a new tag. Tags can also be given when uploading: in the **Upload** dialog (for the logs you
  upload next), with `--tag` in `upload_logs.py`, and in Python and MATLAB; logs already on the server are skipped
  and keep their tags. The Logbook has a **Tags** column (hidden by default, under **Columns**), and the
  search finds tags too. Admins manage the list under **Logbook → Manage ▾ → Tags…**: rename, merge or delete a tag,
  and mark one **hidden by default**, so its entries stay out of the logbook and totals until a filter asks for it
  (for simulation studies, for instance). Logs follow their tags too: on **Logs** (Directory ▾) and as Logbook rows
  without flights, a log has the tags given at upload and those of its flights, and Flight filters' tags hide it;
  tick logs on **Logs** and use **Tags…** to tag them and their flights (also a log that produced no flight).
- Use **Flight filters** in the top menu to select a period (default: last three years), custom
  UTC dates, **Tags** (✓ only entries with a tag, ⊘ leave them out; click a tag to switch), **Hide indoor / no-GPS flights**, **Hide non-flights**, **Hide logs without flights** and **Hide unknown
  dates**, and under **More filters** the operator and category. Non-flights are ground runs, uncertain flights and simulations. Logs without flights are parsed logs that
  produced no flight; the Logbook shows them as rows marked **no flight**, with their log number (outlined, with ⧉ to
  copy it, e.g. for `ml.fetch(log=n)`), grouped by day with **Group by day**. Click one to open it below its row: file,
  type, time, aircraft, project and uploader, with **Open in Logs**, **Download**, **Set date…** and **Re-parse**.
  By default everything is shown; ticked options stay saved in this browser. Unknown-date records are
  shown alongside the selected period unless **Hide unknown dates** is ticked, independently of
  **Hide non-flights**; other visibility filters still apply.
  Corrected and **estimated dates** follow the selected period normally, even with **Hide unknown dates**
  ticked. Estimated dates are labelled: the GPS time of day is known but the week is guessed from the
  firmware commit. Confirm or correct the date with **Edit flight** on the flight page. Choose **Apply filters**;
  the selection stays the same when moving between pages in this browser. **Reset** restores
  the defaults after applying. The filters cover Logbook, Statistics, Locations and individual
  person, aircraft, project and location overviews, including their totals, maps, charts and
  flight lists. Detail-page log lists show contributing logs; zero-flight logs appear only
  without **Hide logs without flights** and within the selected period.
- On a flight's **Path & charts** tab, use **Show sectors** to hide/show flight-plan sector
  boundaries independently of the flight-plan waypoint markers. Waypoint names appear above
  their dots with a subtle text halo; **Show flight plan** hides/shows both the dots and names. Waypoints whose names
  start with `_` are hidden by default; enable **Show hidden waypoints** to reveal them.
  OptiTrack flights show the **OptiTrack / INS path** by default; switch to **GPS path** next to the title
  (see "INS path" in the [technical guide](TECHNICAL.md)). Logs without GPS but with a local position (motion
  capture, simulation) are drawn from a location: the project's default (Edit project) or **Track origin** in Edit
  flight ([details](TECHNICAL.md#track-origin)).
- Logs has a **Status** filter: **Parser errors**, **Unfinished logs** (queued, parsing or
  error), or **All failures** (also failed/rejected upload receipts). Filters cover all pages.
- Statistics uses the shared flight filters. **Total log data (GB)** sums original telemetry sizes for logs contributing to
  filtered flights, counting each SD/GCS source once and excluding companion files.
  **Flying days** counts distinct UTC dates; **Average flight** divides total time by the recorded flight count,
  including paper records. **Monthly activity** switches between hours and flights; hover over a bar for both.
  Unknown dates are excluded from flying days and monthly activity, with an explanatory count on the chart.
  Aircraft, project and crew tables scroll independently, with column headings kept visible.
- Detected simulations, uncertain flights (marked (?)) and ground records are non-flights: tick **Hide
  non-flights** to leave them out of lists and totals. **Classification** in **Edit flight** on the flight page
  (Automatic, Flight or Not a flight) corrects the estimate when it is wrong. Flight details show the evidence; reparse outdated Paparazzi
  uploads to assess older recordings.
- A missing telemetry endpoint location uses the other endpoint's GPS position for display.
  Flights with both endpoint locations unknown are included in indoor/no-GPS filtering, even if a partial
  GPS path exists; the flight page labels this inference, which is not proof of indoor operation.
  Endpoint locations use only good GPS fixes (no unconverged fix after power-up). A flight is indoor when it took off
  or landed in an indoor place, or used motion capture (OptiTrack, ArduPilot/PX4 external navigation).
- A paper record that clearly matches a logged flight (same aircraft, takeoff within 5 min) is linked to it
  and counts once, as the log; unclear cases show as *possible duplicate* to link or dismiss, and an expanded
  entry shows where the paper record differs from the log.
- A log without a calendar date (1980 clock) gets a **suggested date** in its details on Logs when a dated ground
  station log matches the same flight, its file or folder name holds a date, or the same aircraft's neighbouring
  logs in its folder agree on one. Nothing is set until you choose **Use this date** (or **Use for all in this
  folder**); admins can review and settle all suggestions at once. The project then follows from the date;
  until then an undated log only gets its aircraft's project when the aircraft has just one, open-ended project.
- Uploading a file that someone else already uploaded keeps one copy and makes you a **co-uploader**: you can then
  see that log and its flights, and the Logs page links to it.
- **Review** (Logbook, **Manage ▾**) walks through possible duplicates, paper records where only another aircraft
  flew, and conflicts, with the paper record next to the candidate logs; every decision can be undone there.
- Tick the entries of one flight and press **Same flight** in the selection bar to join them (also when the automatic
  SD/GCS merge missed it), or, in an expanded entry, choose **⋯ → Split … off this entry** to split a recording off.
- Each flight shows the **operator** (operator registration) and the **category** (the rules it was flown under:
  national rules before 2021, then OPEN, SPECIFIC under an operational authorisation, LUC or indoor). By default
  a flight is OPEN under our own operator. Set a preference on the **project** (Edit project) or the **location**
  (Edit location), or choose them for a slot in **Plan flight**; an exception for one flight goes in **Edit flight**.
  Inferred values are grey; hover for where they come from. The order is in the
  [technical guide](TECHNICAL.md#operator-per-flight). Choose **Own operations only** in Flight filters for operator
  reporting; pilot experience counts all operators.
- Directory → Projects has the **Categories** tab (who may choose each category, and the authorisations with their
  validity) and the **Operators** tab (partners, and the operator external crew fly for). Admins click a row to change
  it, including **Who may choose**: the qualifications needed to choose it in Plan flight or Edit flight (both you and
  the PIC need one; nothing listed: everyone). **(check)** marks SPECIFIC without an authorisation, or flown outside
  its validity; Flight filters can show one category or those to check.
- **Columns** in the Logbook hides or shows table columns. Your choice is kept in this browser;
  **Export CSV** always contains every column.
- **No.** is the flight number, the number in `/flight/<number>` and on the flight page, which Python's `ml.fetch` and
  MATLAB's `pprz_fetch` use (**Download logs**). **⧉** next to a number (Logbook, the flights of a log under Logs, the
  flight page) copies it; under Logs, **No.** is the log number with its own **⧉**, for `pprz_fetch('log', n)` and
  `ml.fetch(log=n)` (the whole log, also without flights); with flights ticked, **Copy numbers** in the selection bar copies their numbers
  (`1, 3, 4`) to paste into a Python or MATLAB array. Search for `#1234` to find a flight by number; with SD
  and GCS recordings of one flight, hover over the number for both.
- On the flight page, **Edit flight** (shown when you may change the flight) opens one dialog with everything you may
  change: aircraft, goal, date, crew, project, operator, category, classification and comment. Clicking such a value
  opens it at that field. **⋯** next to it re-parses or deletes the log.
- SD and GCS recordings of the same flight are merged. **Group by day** (on by default) shows one row per
  aircraft, crew and takeoff place per day, like a paper daily log; such a row is tinted, shows its number of flights
  (for example **4×**) with a button that copies their flight numbers, and opens with ▸ to show its flights.
  It does not change stored flights. Tick it and use **Change…** to change the project or crew of all its flights.
- Hover over an aircraft name in the logbook for its TUD/serial number; it is also on the aircraft detail page.
- There is no Edit switch: what you may change is offered where it applies (**Edit flight**, **Edit aircraft**,
  **Edit account**, …, rare actions under **⋯**, imports under **Manage ▾**). Longer explanations are under **Help**.
- Flight pages include the path, charts, weather, settings, messages, the pre-flight checklist and software version.
  The Overview shows a small path map.
  In Path & charts, hover over a chart for exact values; use **+ Add** and **+ Add chart** to choose what is plotted.
  Expanded Logbook rows show comments and buttons beside the map (stacked on small screens).
  Comments are read-only there; edit them on the flight page.
  Distance from takeoff and maximum range are shown only on the flight page, not in overview tables or statistics.
  If the aircraft did not send a version, the Paparazzi version from the GCS `.log` header is shown instead.
- **Open in Flight Plotter ▾** (Betaflight, INDIFlight and blackbox logs, on the flight page and in an expanded
  Logbook row): opens the log in the INDIFlight Flight Plotter, or Blackbox Viewer from the menu, in a new tab. See
  [Open in Flight Plotter](TECHNICAL.md#open-in-flight-plotter) for how the log is handed over.
- **MATLAB ▾** (top right of a flight page, in an expanded Logbook row, and in a log's details under Logs, also for a
  log without flights) exports a Paparazzi SD or GCS log:
  **This flight** or **All flights** (10 s before takeoff to 10 s after landing, that aircraft, time 0 at takeoff)
  or the **Whole log**, as `s.aircraft(i).flights(k).data.<MSG>` with values in their converted unit (e.g.
  degrees). It includes settings by name, the checklist, crew, configuration, waypoints and named actuators
  (`ACTUATORS.FRONT_MOT`). The file is prepared in the background and kept for two weeks; load it with
  `s = load('file.mat').s`.
- To export several flights at once, tick them in the Logbook (on any page) and use **MATLAB ▾** in the selection
  bar: a `.zip` with one `.mat` per log (each flight's SD log,
  else GCS; at most 200 logs).

## Add or correct information

Administrators can use **People → Edit account** to grant or remove another person's admin rights,
and the **API keys** tab to inspect, narrow or revoke their API keys (not retrieve secret values). You cannot remove your own admin rights.

- **Locations** lists the places on the left (search by name or country, filter by **Country**) and shows the map
  on the right. Click a location in the list or on the map to select it: the map zooms to it and its details
  appear in a card on the map (with a link to its location page). The Flights and Time headers sort the list; the
  map opens on where most of the flying happens. Nearby places are grouped into numbered markers when
  zoomed out; click a group to zoom in.
- On **Locations**, admins have **Add location**: choose polygon, rectangle or circle in the bar on the map, draw it
  and name it. A selected location has **Edit location** (name, country, indoors, operator and category in one
  dialog; click a value to open it there) and **⋯** → *Edit boundary…* (*Edit boundary and flight area…* at a location
  bookable for outdoor operations: a map over the whole window with the corners, or a circle's centre and radius, beside
  it to drag, draw, type or paste; Save keeps the name and linked records) and *Delete location…*. **Only unnamed dots** (admins) hides named endpoint dots: select a coordinate or
  use **Previous / Next** to visit each remaining dot, then Add location around it; covered positions leave the
  review list after saving.

- The **Logbook** looks the same for everyone; what you may change is offered where it applies. Tick one or more
  rows for the **selection bar** at the bottom: copy numbers, MATLAB, and, when you may change all ticked entries,
  **Change…** (project, crew, pre-flight inspection) and **Same flight**. Click **date unknown** to set a missing date;
  change an existing date or the classification on the flight page. Admins can also **Hide**
  entries from the logbook and totals without deleting files; **Manage ▾ → Hidden records only** shows them to
  **Restore**. Paper records must keep a PIC and project.
  Flight records are kept for 3 years (`LOGDB_RETENTION_YEARS`): a log with a real flight, or a paper record, younger
  than that cannot be deleted; hide it instead. Only an admin can still delete one, with a reason; **Logs → Manage ▾ →
  Deleted records** lists those deletions. Ground runs, simulations and logs without flights can always be deleted.
  Use **Add paper record…** (Logbook, **Manage ▾**) to enter historical flights without logs. Admins use **Import CSV**;
  importing a corrected CSV again updates records matched by reference (e.g. flight number F00001)
  or by aircraft, date and takeoff time, after a preview of what each row will do.
  A new paper record needs **Pre-flight inspection completed** ticked, unless it was flown indoors (an indoor
  location or the Indoor category); in a CSV, the optional `preflight` column (`yes`) records it, and `lmra`
  the LMRA.
  Paper records add flight counts and time, not telemetry; check for overlap with digital logs.
  Overviews show their recorded location in both the takeoff and landing location columns;
  this does not add a landing time or GPS coordinates.
  CSV crew names follow log matching: use unique display names or explicit aliases, not login usernames
  unless added as aliases. Unknown or ambiguous names produce a row error without importing any records.
  The original CSV is attached to all imported records: anyone who can read one can download the
  whole CSV, including rows from other projects. Failed imports retain no attachment.
- **People** is a table (name, role, department, flights, hours and last flight as PIC; current licences, sign-in
  and last sign-in for admins; Columns adds supervisors, expected end, number,
  GitHub and last confirmed; **Filters** for role, department, profile status and (admins) qualification, external
  people and people without flights; Manage ▾ → Download profiles (CSV); pagination like Aircraft). A row opens the pilot page; for admins it opens the person beside the
  list with the tabs Details (account), Profile, Qualifications, API keys and Aliases. The Edit button follows the
  tab: **Edit account**, **Edit profile** or **Edit aliases** (chips: ✕ removes, Add adds); a click on a value opens it there; ⋯ has password, **Move TU Delft sign-in…** (a TU
  Delft login matched to the wrong person), **Merge into…** (one person with two accounts) and **Remove user…**.
  A new user needs an email address (the username is optional and then taken from it); an address can be
  changed later but not removed.
  The normal overview shows aliases too. Logs match explicit aliases or unique display names, not usernames.
  Add a username as an alias only if it reliably identifies that person in logs. Duplicate display names
  stay unresolved unless an explicit alias identifies the person. Renaming a username does not add an alias.
- Admins manage **Aircraft** using Details, Project history and Aliases. Add/import buttons open dialogs;
  Close or Escape dismisses them. **Current user** suggests registered users as you type (free text is allowed for
  people without an account). **Manager** is always a registered person and is required for a new aircraft; it
  starts as you.
- Project members who may add logs can **Request aircraft** for their project (Aircraft). A project manager or an
  admin approves or rejects it in the list of requested aircraft; the serial is assigned on approval and the requester becomes its manager.
  Until then it is listed as **Pending** and reserves nothing.
  Project managers also edit the status, current user, comments and manager of their project's aircraft, and the
  linked manager of an aircraft can do the same.
  On Aircraft, click a row to open the aircraft beside the list; **Edit aircraft** changes what you may change, ⋯ has
  the rare actions. Admins link unrecognised log names under the **Unlinked names** tab (tick names, then **Link to
  aircraft…**) and shared names under **Shared names**. Admins link unrecognised crew names under People → **Unlinked crew names** (tick names, then
  **Link to person…** or **Add as external…**); People shows tabs (People · Unlinked crew names · Approval queue ·
  Qualification types · Profile options), so one list is on screen at a time; the tab is kept in the URL.
  Logs with an autopilot board ID (newer firmware) also match the aircraft that board was seen on.
  Servers can hide unlinked aircraft flights from Logbook, Statistics, person details and Locations until linked;
  admins can still find the names under Unlinked names on Aircraft (see the [settings](TECHNICAL.md#settings)).
  Mark generic airframe names as **ambiguous** (Aircraft, **Manage ▾**) instead of linking every recording
  to one aircraft. A uniquely matching GCS recording can identify its SD source; use the
  shared-name panel for explicit per-log assignments when needed.
- Crew names in logs do not create user profiles. Admins can link multiple names to one person using
  aliases, or use Add user if a new profile is needed. Previously generated profiles are kept.
- Admins manage project access on **Projects** and named takeoff areas on **Locations**. **Projects** is a table
  (project, members, logs, operator, category); a row opens a project you may change beside it (Details, Members),
  other rows open the project page. **Edit project** changes the name, operator, category, track origin and members in one dialog
  (access per member: See, upload, edit, delete or Manager); ⋯ deletes the project. Admins can make a member
  **Manager** of a project; a manager changes its members and track origin with Edit project, but only admins appoint managers
  ([details](TECHNICAL.md#accounts-and-tu-delft-login)).
  **Manage ▾ → Import CSV…** on Projects takes `project,person,role`; blank roles mean view/upload.
  Reimports replace all members of included projects. For initial setup, import qualifications first,
  then projects, then aircraft. Aircraft project changes require a `project_effective_date`;
  manual and legacy flight assignments are preserved. Both import dialogs offer templates.
- Locations has **Manage ▾ → Import CSV…** (admins). It accepts `logbooks/places.csv` from SVN as is
  (`place,lat,lon,radius_m,indoor`, optionally `country`; the other columns are ignored) and shows a preview before importing.

## Agenda

**Agenda** (optional) is a week agenda of bookable items, one tab per item plus **All** (no tabs with a single
item). Click a booking to open it, or drag in an empty part of a day (or use **New booking**) to book that time; the
strip to the right of the bookings stays free for that, and dragging across a booking you may not change books too.
Drag a booking you may change to move it: it opens with the new times, and **Save** moves it. A red line marks the
current time on today; switching tabs keeps the same time at the top. Every item has a colour, shown as a dot on its
tab and on its bookings, so **All** shows only the description (hover for the item).
Choose **Day**, **3 days**, **Work week** (the default; on a phone **Day**) or **Week** next to the dates; ‹ › move by
a week or by the days shown, and **Today** (shown when you are elsewhere) jumps back. A work week tells you when the
weekend has bookings, with a link to the whole week. The whole day can be scrolled; a view opens at 08:00. Hover over
the dates for the time zone. **⋯** holds **Subscribe**, which gives private links to show the agenda in your calendar app,
and Help. A booking has a description, start and end, and the item's
options, for example "No others" or "Demo". Bookings of one item may overlap unless one of them ticks an option that
blocks overlaps (such as "No others"); later ones then show **2nd**, **3rd**, … by when they were booked. An item
can be set the other way round (**Edit item → Overlapping bookings**): its bookings overlap only when both tick the
same option, such as "Others allowed". An item can need a current
qualification, for example an arena safety briefing, to book or even to see its agenda. An item you may not see has
no tab; a link to it says which qualification is needed and links to the training that grants it. You can change or delete your own bookings; administrators can
book for someone else (**Booked for**) and change any. They also see items that are closed for bookings (the tabs
stay with a single item), add items with **⋯** → *Add bookable item…*, change the open item with **Edit item**
at the end of the tabs (**⋯** → *Delete item…*) and drag the tabs to order them.

An item can need approval (**Bookings need approval**, with optional approvers besides the administrators). Your
booking then shows as **Pending** (striped) and holds its time until an administrator or approver opens it and
clicks **Approve** or **Reject** (with an optional reason); you get a mail either way. Moving it asks again.
Administrators and approvers book such an item without approval; administrators also find the requests in the
People **Approval queue** (**Bookings**).

**Outdoor operations** (when the server has it on) is a fixed item for planning outdoor flights; in its Edit item, admins
choose who may plan (qualifications) and who sees it (everyone, or only who may plan). **Plan flight** books
a slot at a bookable location (admins tick *Bookable for outdoor operations* in Locations) for your team: projects (by default the one
of your latest flight), PIC (you by default), optional crew and aircraft (several of each), the goal, and the operator and category its flights
are flown under (by default the first project's, else the location's, else OPEN under our own operator; only what you
and the PIC are qualified for). A map shows the flight area with its ground risk buffer (1:1:
as wide as the maximum height; the location's area and height unless you draw your own or fly lower) and warns when
the buffer leaves the location; admins set each location's area and height in Locations. The buffer is red, and bolder when it crosses the boundary. Drawing an area opens a map over the whole window, with the corners as
latitude, longitude lines that you can also type or paste. It also shows the weather forecast, red/green
checks of the qualifications and the aircraft for that day, and the NOTAM check; these are saved with your team. Other
teams **Join** a slot instead of booking their own (one booking, a shared field), also a second team of the same PIC
for another project or aircraft; the slot goes when its last team
leaves. Flights uploaded later at that location and time, by a team's PIC, crew or aircraft, appear in the slot (link or
unlink others there by hand), and the flight page shows its flight plan, or *Not planned* at a bookable location. A
location can be booked elsewhere by mail (for example Unmanned Valley): Plan flight then shows the deadline, the request is
mailed to that mailbox (also when teams join or leave, the time changes or the slot is cancelled), and the slot shows
*Requested* until an admin enters the booking number (*Booked*); a reminder follows when it is still not booked close to the
deadline. Who
may plan, and whether it needs approval, depends on your qualifications (**Edit item**). Rules:
[technical guide](TECHNICAL.md#outdoor-operations).

## Documents

**Documents** (optional) holds shared documents, for example the ones to show when you are asked for them in the field
(insurance, operator registration, operations approval, manuals), one line each in tabs per group; **Search** finds them
by title, text or file name. The date on a line is its expiry (orange within 60 days) or its release date. Administrators write a text at the
top (click it, or **Manage ▾** → *Edit text at the top…*) and add documents with **Add document**; **Manage ▾** also adds
a group. **✎** next to a document opens **Edit document** (details, group, limits, a new file; **Delete…** at the
end); a group's heading has **Edit group** and **⋯** → *Delete group…* (only when it is empty). A
document can be only for administrators, for people with one of some current qualifications (for example A1/A3, A2 or STS) or for some profile roles, and can
have validity dates: outside them only administrators see it, so an old certificate never shows. The tab
**My qualifications** lists your own approved qualifications that are valid today, with their documents; add or renew
them in **My account** (see below). **Manage ▾** → *Import ZIP…* adds or replaces many documents at once from a
`manifest.csv` and the files: preview, then import; importing the same ZIP again skips unchanged documents. Rules:
[technical guide](TECHNICAL.md#documents).

## Safety

**Safety** (optional, at `/safety`) is where you report a danger, incident or accident: the red **Report incident**
button opens the registration form. Type, date and time and description are required; pilot in command, aircraft,
location, conditions, flight phase and a sketch or photo are optional. The **?** next to Type explains danger, incident
and accident. The report goes by email to the administrators (**Safety
reports** under My account → Email, separate from the other notices). Tick **Leave my name out of the notification
email** to keep your name out of that email; it is still stored with the report. Administrators change the text above
the button with **Edit text** (or a click on the text); **⋯** → *Use the default text…* goes back to the general default
text, which is shown until they set one. When an administrator has uploaded the emergency flowchart (**⋯** →
*Upload emergency flowchart…*: one HTML page, kept behind the login), the blue **Emergency: who to call** button opens
it: who to call after an incident, per location and kind of incident, with tap-to-call buttons. Rules:
[technical guide](TECHNICAL.md#safety).

## Email

When the server sends email, you get reminders 30 and 7 days before a qualification or your expected end date
expires, and mails about your bookings. Choose per kind under **My account**, tab **Email**: immediately,
in one daily summary, or off. Admins also choose how they get notices about approvals and failed logs (unless these
go to a shared mailbox) and can follow everyone's expiry reminders; **Statistics** lists the mails sent. Rules:
[technical guide](TECHNICAL.md#email-notifications).

## Qualifications and additional files

**Training** (optional): when the server turns it on, a Training tab lists questionnaires. When a training has
reading material, it opens first; confirm that you have read and understood it to start the test. Answer one question
at a time (Next / Previous), then submit; answers (and, when the training is set up so, the questions) come in a new
random order each attempt. After passing, **Practice again** lets you answer the questions again without changing your pass. Retry until you pass; only the passing answers and the attempt count
are kept. A pass can grant the linked qualification immediately, or wait in the People approval queue when the
training has **A pass needs approval** on; admins see **N passes to approve** above the trainings and approve or reject
them (several at once, with Undo) right in that queue; its **Decided** view lists what was approved, rejected or withdrawn. After that qualification expires, a queued pass is rejected, or an
administrator resets the pass, you can take it again. Admins create questionnaires with **Add training** (then **Edit training** and the
**Questions** tab: single choice, multiple choice, ranking, optional image) or **Manage ▾ → Import** a JSON/ZIP file kept
outside the repository, and can ask who
delivered the briefing from a short list of people. A training can also mail an access request (for example to a secretary, for room access)
once a pass is granted, optionally only when the person answers **No** to a question such as "Do you already have
access to the flight lab?"; admins can get a copy under **My account → Email**. When the training offers it,
**Request extension…** asks for access until a later expected end date (with an optional note): sent at once, after
an admin approves it, or after you pass the test again, depending on the training; it also changes the end date in
your profile, and the end date reminder links to it. Rules: [technical guide](TECHNICAL.md#access-extensions).

**Profile** (optional): when the server turns it on, you fill in your role, department, supervisors, expected end
date and student/employee number after signing in, and again once that end date has passed. Your role limits the
end date (for example at most 2 years ahead for a Master student; admins set this per role) and whether it may be
indefinite. Change it under
**My account**. Only you, admins and your supervisors see it (supervisors without your number): on your person
page, and on People (the profile columns and filters, and the **Supervision** tab), which show supervisors the people
they supervise.

**Welcome message and notices:** when admins have written a welcome message or notices for you, they are at the top
of your Home page until you dismiss them (some notices cannot be closed and stay until their end date). Admins write
them on Home, **Manage ▾** → *Welcome message and notices*, like a
training's access mail: tags such as `{name}`, a little markup and parts per role ([details](TECHNICAL.md#welcome-message)).

**Qualifications:** submit your own evidence under **My account**; admins review it on **People**.
Each type determines whether evidence, expiry and a licence number are required (the number and validity
dates are read from the evidence when you choose the file; empty fields are filled, typed values are kept). Evidence is one PDF, JPEG or PNG,
up to **20 MiB**. Submissions need admin approval, and the previous approval stays active during renewal.
Expired approvals are marked; approval alone does not establish operational eligibility.
Evidence and submission history are private to you and admins; audit history is admin-only.
Pilot pages show approved summaries, not evidence.
Use **Valid from** and **Valid until** to record the licence period, including past qualifications.
When one certificate covers several qualifications (e.g. A1/A3, A2 and STS under one number), add them under
**Also covers** in the same dialog: each becomes its own record with the same file, number and dates, approved one by one.
Admins can make either date optional per type; both date switches start checked for new types.
Admins can correct the dates of any submission with **Edit dates** (the person's Qualifications tab), for example to end a licence that has expired.
An Internal switch marks in-house qualifications. Drag types to set their order, then use one Save.
**Manage ▾ → Import qualifications (ZIP)…** on People loads a `manifest.csv` plus evidence PDFs: preview, then import
and approve in one step. Older licence periods are under **Show older** in each person's qualifications.
Names not yet on People are added as new users (shown as **New user** in the preview).
Dates are entered manually, and existing records keep an unknown start date. Historical flight
eligibility is not checked. The read-only People list shows only approved, non-expired qualifications,
without an approval-status label; management still shows pending, expired and previous submissions.

**Additional files:** drop files on the file list, or use **Upload files** in a flight's Files tab, a paper record's Additional files section or a
log's **Additional files** on the Logs page to attach photos, video or other recordings (default limit **10 GiB** per file).
Clicking a photo, video or sound file opens it on the page (← / → for the next one in the list); a PDF or text file opens in a new tab. The small arrow beside it downloads it.
Other files download from the name. Word files (`.docx`) open as PDF when LibreOffice is installed on the server.
Qualification evidence works the same way.
Uploading a file that is already stored only links it; a file with the name of an existing one replaces it
on that flight/log, and the old file stays under **Show earlier versions**.
Files can have a category (for example *Preparation & permits* for NOTAMs and landowner permission), chosen at upload
or later by clicking the file's row (its name opens the file); the list groups them by category and has a search box and category filter.
In the logbook, opening an entry shows its files by category below the comment, so you can open them from there.
The logbook's filter box also finds file names, and **Files ▾** next to it shows only flights with files (of a category,
for example *With Media*).
On a flight, **Upload files** → **Upload to** chooses *This flight only*, *All flights of this
log* (default), all flights of the aircraft that day, or all flights that day (for example a NOTAM or weather briefing).
**Anyone able to read any linked flight/log can download the file. Linking across projects shares
its contents. Never put private qualification evidence here.**

Historical weather is fetched only when you request it. That sends rounded takeoff coordinates and
the UTC date to Open-Meteo; its free service is non-commercial only.

## Upload a folder with a script

Create an API key on **My account** (tick the `logs:upload` and `read` scopes) and download [upload_logs.py](upload_logs.py). It needs Python 3
and skips Paparazzi pairs the server already has:

```sh
export LOGDB_SERVER=https://logs.example.org
export LOGDB_API_KEY=your-api-key
python3 upload_logs.py ~/paparazzi/var/logs -n      # preview
python3 upload_logs.py ~/paparazzi/var/logs         # upload
python3 upload_logs.py ~/sim/study3 --tag sim-study-3   # and tag the flights (repeat --tag for more)
```

Keep the key private. For a large initial import, see [controlled bulk uploads](TECHNICAL.md#uploading-with-a-script).
To download and upload logs from your own Python code or MATLAB (examples under **Download logs**), use the [mavlab-logbook-client](../python/mavlab-logbook-client/README.md) package
(`pip install mavlab-logbook-client`) or the [MATLAB tools](../matlab/README.md); they sign in through the browser.

A key can also add paper records: `POST /api/historical` (full project access; send `"preflight": true` unless
flown indoors) and, with an admin's key,
the CSV import `POST /api/historical/import`. A key can also upload additional files to records it may edit
(`POST /api/attachments`, which returns the file id) and link a file to more records
(`POST /api/attachments/<id>/links`). Changing or deleting records, unlinking files, and managing keys or the
account, still need a browser session.
