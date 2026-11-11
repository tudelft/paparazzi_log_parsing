# MATLAB tools

Tools to load, plot and analyse Paparazzi flights exported by the flight logbook (MATLAB format 5).

## Get a .mat file

- **Straight into MATLAB from the logbook server**, by flight number (the number in `/flight/<number>`). Create an
  API key once under **My account** (read-only is enough):

  ```matlab
  pprz_server('https://logs.example.org', 'API-KEY')   % once; kept in MATLAB's preferences
  T = pprz_logbook();                                   % table of the flights you may see (flight, date, aircraft, …)
  logs = pprz_fetch([1234 1240]);                       % or pprz_view([1234 1240]), or From logbook… in pprz_view
  logs = pprz_fetch([1234 1240], 'scope', 'log');       % the whole logs instead
  f = pprz_flight(logs, 'number', 1240);                % one flight by its logbook number
  ```

  `pprz_fetch` makes **one file per log**: numbers from the same log end up in one export with those flights
  (`logs(i).aircraft(a).flights(k)`). `'scope'` chooses what to download: `'selected'` (default) the given
  flights, `'all'` every flight of those logs, `'log'` the whole log of each (also between flights), `'flight'`
  one file per number. Copy numbers in the logbook's Select mode gives the list to paste.

  The exports are made on the server from each log and kept in a cache folder (`pprz_server('cache',
  folder)`; default in `prefdir`). A cached file is reused until the export would change (new log version,
  parser or logbook corrections such as the crew); without a connection the last cached copy is used.
- **From the logbook:** **MATLAB ▾** on a flight page or an expanded Logbook row (this flight, all flights or
  the whole log), or tick flights and use **MATLAB ▾** next to "N selected" for a `.zip` with one `.mat` per log.
- **Offline, from a local `.data` with its `.log` next to it** (plain or `.gz`), also in the field without
  internet. Prepare the computer once, with internet (Windows, Linux and macOS on Apple silicon):

  ```matlab
  pprz_setup('install', true)   % finds Python 3 (installs h5py/numpy with pip) and downloads the released parser
  s = pprz_load('24_10_30__16_27_37_SD.data');   % or its .log, a .data.gz, or a folder of logs; pprz_view too
  ```

  The whole log is converted to `24_10_30__16_27_37_SD_whole_log.mat` next to the `.data` (in the cache folder of
  `pprz_setup` when that folder is read-only, e.g. an SD card) and reused while it is newer than the `.data`
  and the parser. `pprz_flights` lists the detected flights in it. Python 3 must be installed first
  (python.org; on Windows tick "Add to PATH"); `pprz_setup('python', path)` picks another one.
  A self-built parser (`cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build
  --config Release`; on Windows with `-DBUILD_BLACKBOX=OFF` and Visual Studio's C++ tools) is used before a
  downloaded one; `pprz_setup('parser', path)` sets one explicitly. The converter also runs without MATLAB:

  ```sh
  python3 webui/matlab_export.py 24_10_30__16_27_37_SD.data              # whole log -> ..._whole_log.mat
  python3 webui/matlab_export.py 24_10_30__16_27_37_SD.data --flights    # detected flights -> ..._flights.mat
  python3 webui/matlab_export.py logs/*.data --flights -o out/           # many logs
  ```

  A local conversion has the crew, checklist, settings and configuration from the log itself, not the
  corrections made later in the logbook.

## Quick start

`pprz_example.m` is a section-by-section example session (load, pick a flight, ready-made and own plots,
helpers, analysis); run it one section at a time.

```matlab
addpath(genpath('matlab'))                      % from the repository root; +pprz is found automatically
pprz_view                                       % pick files, a flight and plots in a window
logs = pprz_load('selection.zip');              % a .mat, a folder or a bulk .zip
pprz_flights(logs)                              % table of the flights
f = pprz_flight(logs, 3);                       % row 3 of that table
pprz_plot(f, 'overview', 'euler', 'actuators', 'modes', 'ap')
pprz_plot()                                     % list every plot
plot(f.data.ROTORCRAFT_FP.t, f.data.ROTORCRAFT_FP.up)
```

| Function | Use |
|---|---|
| `pprz_load` | Load `.mat` files, folders or bulk `.zip` files into a struct array of exports; `.data`/`.log` files are converted first (offline, after `pprz_setup`). |
| `pprz_setup` | Prepare offline conversion once: Python with h5py/numpy and the parser (self-built or downloaded from the latest `parser-v*` release). |
| `pprz_server` | Set (or show) the logbook server, API key and cache folder for `pprz_fetch` and `pprz_logbook`. |
| `pprz_fetch(ids)` | Load flights from the logbook server by flight number, one file per log, cached locally; `'scope'` `selected`/`all`/`log`/`flight`; `'force', true` downloads again. |
| `pprz_logbook` | Table of the logbook flights the key may see, to pick numbers for `pprz_fetch` (`'all', true` adds uncertain and ground records). |
| `pprz_flights` | Table of all flights (row numbers for `pprz_flight`), with the logbook `number`; flight 0 is a whole-log export. |
| `pprz_flight` | One flight ready to plot (row of `pprz_flights`, or `'number', n`): `f.data`, `f.time`, `f.summary`, `f.logbook`, `f.aircraft`, `f.messages`, `f.title`. A flight in a whole-log export is cut from the log. |
| `pprz_plot` | Make plots by name, or every plot whose messages were logged; options `modes` (`ap`/`rc`), `range` [t0 t1], `docked`, `events` (markers: `true` or kinds such as `{'mode', 'message'}`). |
| `pprz_view` | One window: pick a flight, the time window (a range slider applied live to every plot), shading and event markers (modes, settings, checklist, messages), then tick plots (search; unlogged ones greyed out) and press Plot. Plots open as tabs (Undock, Save PNG, Close). Click or drag in any time plot to move a linked cursor on all of them; below the plots are the values at the cursor and the events near it (click one to jump there). Analysis buttons: 3-D replay (follows the cursor), System ID and Effectiveness; *Compare flights…* overlays the ticked plots of several flights. |
| `pprz_compare(flights, names...)` | Overlay the same plots (by name) or signals (`'MSG.field'`) of several flights, one colour per flight. Align at takeoff (default), `'align', {'setting', name}` (first change), `{'mode', 'NAV'}` (first switch) or given times; `'range'` [t0 t1]. Also *Compare…* in `pprz_view`. |
| `pprz_replay(f, model)` | Replay a flight in 3-D with the airframe model (`3d_animation/3d_models`: Nederdrone5, RotatingWingV3, Cyclone2, picked from the aircraft name or airframe file, else a generic quadrotor): attitude, control surfaces and motor throttle from `ACTUATORS`, the track from above and height, airspeed, battery and mode. Play/pause, slider and arrow keys; also the *3-D replay* button in `pprz_view`. No toolboxes needed. |
| `pprz_sysid(f)` | Find identification experiments and measure them: `DOUBLET`/`CHIRP` runs of the sys_id module (input delay and effectiveness on the body rate; chirps also gain, phase and coherence), setting changes in flight (step metrics of `'signal', 'MSG.field'`) and RC stick steps (delay, rise time, overshoot and settling of the roll/pitch angle or yaw rate). Prints a table and plots each experiment. No toolboxes needed. |
| `pprz_effectiveness(f)` | Control effectiveness of every actuator: least squares Δω̇ = G·Δu on low-passed body rates and ESC rpm (or `ACTUATORS`); options `range`, `cutoff`, `delay`, `input`. Plots the fit per axis and G. |
| `pprz_plot(f, 'vibration')` | Accelerometer and gyro spectrograms over time with the 1× and 2× rotor frequency (mean rpm of the running ESCs), and the spectrum against rotor speed (Campbell diagram); without ESC rpm against the mean actuator command. Uses the `_SCALED` IMU messages when only those were logged. Also in `pprz_view` (IMU). |
| `pprz.Cursor(figs)` | The linked cursor for your own figures: `c.move(t)`, `c.values()`, `c.listen(@(t) ...)`; arrow keys step it. |
| `pprz.crop(d, t0, t1)` | Rows of every message between t0 and t1. |
| `pprz.actuators(d)` | `ACTUATORS` as an N×k matrix with names, whether exported by name or as `values`. |
| `pprz.enum(f, msg, field, v)` | Names of enum values, e.g. autopilot modes. |
| `pprz.modes(f, fig, 'ap')` | Shade autopilot (`ap`) or RC (`rc`) mode regions in a figure. |
| `pprz.events(f)` | What happened in a flight: mode changes, setting changes, checklist items and `INFO_MSG`, with times from takeoff. |
| `pprz.markers(f, figs)` | Draw those events as labelled lines on every time plot; click a line for its full text. Labels are laid out again after zooming; `pprz.markers(f, figs, {})` removes them. |
| `pprz.rates(d)` | Body rates p, q, r in rad/s from `IMU_GYRO`, `IMU_GYRO_SCALED` or `BODY_RATES_ACCEL`. |
| `pprz.lowpass(x, fc, fs)`, `pprz.step_info(t, y, t0, t1)`, `pprz.freq_response(u, y, fs, n)` | Zero-phase Butterworth low-pass, step response metrics and Welch frequency response, as used by `pprz_sysid`. |
| `pprz.spectrogram(t, x, n)` | Power spectral density over time (Hann windows, no toolbox). |
| `pprz.has(d, msg, ...)` | True when the messages were logged. |
| `pprz.track(f, by)` | The flown path (lat/lon and local east/north) with a value per point to colour by, and the flight plan waypoints. |

Plots live in `plotters/` as `plot_xxx(f.data)` and are listed in `pprz_plots.m` (name, group, title and the
messages they need); add a plot by writing the function and one line there. `pprz.tiles(n, title)` makes the
stacked, linked time axes and `pprz.series(ax, t, Y, names)` plots named columns, so every line has a name in
the cursor table. `pprz_plot()` prints the list: overview, maps coloured by height, ground speed, autopilot mode or battery (satellite background with the
flight plan waypoints, navigation circles/segments and moved waypoints; `plot_map(f, by, basemap)` for other
backgrounds), status, setting changes, navigation (flight plan block and stage), attitude, guidance and its
reference, wind, navigation sensors and GPS satellites, IMU and AHRS bias, actuators, commands, control
allocation (WLS), power, effectiveness, rotating wing, RC, bus errors and datalink.

Analysis scripts in `tools/` (altitude tuning, airspeed sensor scale, takeoff distance, servo model,
rotating-wing flight envelope) work on `ac_data = f.data`. The C2C and Dronetag tools read their own file
formats. `3d_animation/` holds the airframe models of `pprz_replay`.

## File format (format 5)

Load a file directly with `s = load('x.mat').s`. Times are log time unless noted:

```
s
├─ info                exporter_version, created_utc, scope (flight/flights/log), margin_s
├─ log                 name, sha256, type, time0_utc, time0_posix, warnings, unparsed (t, lines)
├─ messages.<MSG>      name, class, id, fields, types, units, raw_units, coef, enums.<field>
└─ aircraft(i)         only aircraft with an exported flight in a flight export
   ├─ id, name         AC_ID and the name in the log
   ├─ version          number, desc, sha1, build_time_utc, mcu_uid ('' when not sent)
   ├─ crew             pic, pac, gcs, goal (as written in the log, no account details)
   ├─ simulation       assessed, detected, evidence
   ├─ settings
   │  ├─ list          table: index, var, shortname, unit, parent
   │  ├─ values.<name> t, value       (every DL_VALUE report over the whole log)
   │  └─ changes       table: t, index, name, from, to, in_flight
   ├─ checklist
   │  ├─ items         table: t, item, answer, message
   │  └─ answers.<key> last answer per logged key
   ├─ config
   │  ├─ xml           airframe, flight_plan, settings, radio, telemetry
   │  ├─ flight_plan   name, lat0, lon0, blocks (cur_block + 1), stages (table: block, stage, name),
   │  │                waypoints.<NAME> (index, name, lat, lon, alt)
   │  └─ actuators     servo names of the airframe
   ├─ data, unparsed   whole-log export only
   └─ flights(k)
      ├─ index         the parser's flight number of that aircraft
      ├─ time          takeoff_utc, landing_utc, takeoff_posix, offset_s (log time of takeoff), window_s
      │                (from takeoff), start_s / end_s (log time, to slice a whole-log export)
      ├─ summary       duration, distance, range, positions, battery, landed, verdict, reasons
      ├─ logbook       the flight as the logbook shows it (empty fields when exported without the logbook):
      │                number (flight number, pprz_fetch), comment, goal, pic, pac, gcs (linked people),
      │                project, operator, category, authorisation, takeoff_place, landing_place, indoor,
      │                recordings (flight_id, log_id, log_type, log_name, this: e.g. the GCS log of an SD flight),
      │                other_setting_changes (t, index, name, from, to, log) and other_messages (t, text, log):
      │                those of the other recordings, not already in this log, on this log's time (s); in a
      │                flight export only from 10 s before takeoff to 10 s after landing, in a whole log all
      └─ data, unparsed  flight exports only; t = 0 at takeoff
```

`data.<MSG>` holds the time `t` [s] and one column per field. A field with `alt_unit_coef` is stored converted
(e.g. deg, double) under its own name; the logged value is `value / s.messages.<MSG>.coef(k)` in `raw_units`.
Other types: `uint*` uint32, `int*` int32, float double, arrays N×k double padded with NaN, text cellstr. Enum
fields stay numeric (`s.messages.<MSG>.enums.<field>{v + 1}` names value v). `ACTUATORS` stores each actuator as its
own column (`ACTUATORS.FRONT_MOT`) when the airframe lists as many servos, else `values` (N×k). MATLAB keywords
get a trailing `_`. Rows that do not match the protocol are in `unparsed` and counted in `s.log.warnings`.

Example: `s = load('x.mat').s; a = s.aircraft(1); f = a.flights(3); act = f.data.ACTUATORS;`
`plot(act.t, act.FRONT_MOT); a.settings.values.guidance_h_gains_p.value; a.config.flight_plan.waypoints.HOME.lat`.
