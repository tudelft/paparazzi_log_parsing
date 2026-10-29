# MATLAB tools

Tools to load, plot and analyse Paparazzi flights exported by the flight logbook (MATLAB format 5).

## Get a .mat file

- **From the logbook:** **MATLAB ▾** on a flight page or an expanded Logbook row (this flight, all flights or
  the whole log), or tick flights and use **MATLAB ▾** next to "N selected" for a `.zip` with one `.mat` per log.
- **Offline, from a local `.data` with its `.log` next to it** (plain or `.gz`), with the same result:

  ```sh
  cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build cpp/build   # once
  pip install h5py numpy                                                              # once
  python3 webui/matlab_export.py 24_10_30__16_27_37_SD.data              # whole log -> ..._whole_log.mat
  python3 webui/matlab_export.py 24_10_30__16_27_37_SD.data --flights    # detected flights -> ..._flights.mat
  python3 webui/matlab_export.py logs/*.data --flights -o out/           # many logs
  ```

  A local conversion has the crew, checklist, settings and configuration from the log itself, not the
  corrections made later in the logbook.

## Quick start

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
| `pprz_load` | Load `.mat` files, folders or bulk `.zip` files into a struct array of exports. |
| `pprz_flights` | Table of all flights (row numbers for `pprz_flight`); flight 0 is a whole-log export. |
| `pprz_flight` | One flight ready to plot: `f.data`, `f.time`, `f.summary`, `f.aircraft`, `f.messages`, `f.title`. A flight in a whole-log export is cut from the log. |
| `pprz_plot` | Make plots by name, or every plot whose messages were logged; options `modes` (`ap`/`rc`), `range` [t0 t1], `docked`. |
| `pprz_view` | Window to pick a flight, tick plots (unlogged ones are marked), shade modes, choose a time range and save the figures. |
| `pprz.crop(d, t0, t1)` | Rows of every message between t0 and t1. |
| `pprz.actuators(d)` | `ACTUATORS` as an N×k matrix with names, whether exported by name or as `values`. |
| `pprz.enum(f, msg, field, v)` | Names of enum values, e.g. autopilot modes. |
| `pprz.modes(f, fig, 'ap')` | Shade autopilot (`ap`) or RC (`rc`) mode regions in a figure. |
| `pprz.has(d, msg, ...)` | True when the messages were logged. |
| `pprz.track(f, by)` | The flown path (lat/lon and local east/north) with a value per point to colour by, and the flight plan waypoints. |

Plots live in `plotters/` as `plot_xxx(f.data)` and are listed in `pprz_plots.m` (name, group, title and the
messages they need); add a plot by writing the function and one line there. `pprz_plot()` prints the list:
overview, track/map, maps coloured by height, ground speed, autopilot mode or battery (satellite background with the
flight plan waypoints; `plot_map(f, by, basemap)` for other backgrounds), status, setting changes, attitude, guidance,
navigation sensors, IMU, actuators and power, effectiveness, rotating wing, RC and errors.

Analysis tools in `tools/` (effectiveness estimation, altitude tuning, airspeed sensor scale, takeoff distance,
servo model, rotating-wing flight envelope, 3D visualisation) and `3d_animation/` work on `ac_data = f.data`.
The C2C and Dronetag tools read their own file formats. `math/` holds quaternion helpers.

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
