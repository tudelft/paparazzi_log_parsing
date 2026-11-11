# mavlab-logbook-client

Download and upload drone flight logs of a [MAVLab flight log database](https://github.com/tudelft/paparazzi_log_parsing)
from Python, without a browser download or drag and drop. Works with every log the server stores (Paparazzi
`.data`/`.log`, ArduPilot `.bin`/`.log`/`.tlog`, PX4 `.ulg`, Betaflight `.BFL`, …). Pure Python 3.10+, no dependencies.
The API follows the [MATLAB tools](https://github.com/tudelft/paparazzi_log_parsing/tree/master/matlab), which share
the login and the cache.

```sh
pip install -i https://test.pypi.org/simple/ mavlab-logbook-client
```

```python
import mavlab_logbook_client as ml

ml.connect("logs.example.org")                     # or ml.connect("logs.example.org", key="logdb_…")
rows = ml.logbook()                                # the flights you may see, newest first (as pprz_logbook)
numbers = [r["flight"] for r in rows if "Hex" in r["aircraft"]]
mats = ml.fetch([1234, 1240])                      # flight numbers, as in /flight/1234: MATLAB exports, one per log
mats = ml.fetch([1234, 1240], scope="flight")      # one .mat per number instead (as pprz_fetch)
logs = ml.fetch_raw([1234, 1240])                  # the original logs, untouched: one path per log
logs[0]                      # e.g. ~/.cache/mavlab-logbook-client/logs/<sha256>/24_10_30__16_27_37_SD.data (.log next to it)
receipts = ml.upload(["flight.data", "LOG00030.BFL"], wait=True)
receipts[0]["flight_ids"]                          # the flights the server found, for ml.fetch
```

`connect(server, key)` does the authentication. The server is required (`https://` is added to a bare host name;
`http://` is refused except for `localhost`, so keys never travel unencrypted);
the key defaults to `LOGDB_API_KEY` (a key from your account page, as for `upload_logs.py`; not used when
`LOGDB_SERVER` names another server), else the key of the last browser login to that server. Values taken from the environment are announced and named
in errors. Without a key, `connect` opens the browser: sign in, check that the page shows the same code as the
terminal and press **Allow**. That key acts as you for a week and is stored for the next `connect`.
`connect(..., login=False)` raises `AuthError` instead (for unattended scripts); `insecure=True` accepts a
self-signed certificate. `connect` returns a `Client` with the same methods, for using several servers at once.

`ml.revoke_all()` revokes your browser-login keys on every computer (Python and MATLAB) and forgets the stored one.
Keys made on the account page are revoked there.

**The logbook** (`ml.logbook()`, as MATLAB's `pprz_logbook`) is a list of dicts, one per flight, newest first:
`flight` (the number for `fetch`), `date` (takeoff, UTC `datetime`; `None` when unknown), `date_ok` (`False` when
the log's clock was not set and no date was entered), `aircraft`, `duration_min`, `pic`, `project`, `goal`, `verdict`,
`simulation` (`True` for a simulated flight) and `log`. It holds the flights the parser judged real; `all=True` adds uncertain and ground
records. Paper records are left out. `pandas.DataFrame(rows)` makes a table of it.

**Fetching** works as MATLAB's `pprz_fetch`: `fetch` downloads the MATLAB exports (`.mat`, v7.3 = HDF5, e.g. for
`h5py`; the format is in the [MATLAB README](https://github.com/tudelft/paparazzi_log_parsing/tree/master/matlab))
and exists for Paparazzi logs. `scope` chooses what each file holds: `"selected"` (default) the given flights of
each log, 10 s before takeoff to 10 s after landing; `"all"` every flight of those logs; `"log"` the whole log; these
return one file per log. `"flight"` returns one file per number. Exports are cached (shared with MATLAB) and
reused while they would be identical; a change of the log, parser or logbook data fetches them again.

`fetch_raw` downloads the original log files that hold the flights (`.data` with its `.log`, `.BFL`, `.ulg`, …),
whole and untouched, one path per log. One never changes (it is named by its SHA-256, which is checked after each
download). For both, `force=True` downloads again; without a connection or when the server fails (HTTP 5xx) the
cached copies are used, with a warning.

**Uploads** pair a `.data` with the `.log` next to it (whichever of the two you pass). A log already on the server
is skipped. `name="Hex 1/LOG00030.BFL"` sets the stored name of a single file, which can link it to a fleet
aircraft. With `wait=True` each call returns once the server has parsed the log, else as soon as it accepted it.

## Command line

```sh
mavlab-logbook-client --server logs.example.org status                       # signs in if needed; user, key and cache
mavlab-logbook-client --server logs.example.org logbook                      # tab-separated flights; --all
mavlab-logbook-client --server logs.example.org fetch 1234 1240               # prints the .mat paths; --scope flight
mavlab-logbook-client --server logs.example.org fetch-raw 1234 1240           # prints the paths of the original logs
mavlab-logbook-client --server logs.example.org upload flight.data LOG00030.BFL --wait
mavlab-logbook-client --server logs.example.org revoke-all                    # keys of browser logins, all computers
mavlab-logbook-client --server logs.example.org --key logdb_… fetch 1234      # as connect(server, key)
```

## Where files are kept

| | Linux | macOS | Windows |
|---|---|---|---|
| Login (`config.json`) | `~/.config/mavlab-logbook-client` | `~/Library/Application Support/mavlab-logbook-client` | `%APPDATA%\mavlab-logbook-client` |
| Cache | `~/.cache/mavlab-logbook-client` | `~/Library/Caches/mavlab-logbook-client` | `%LOCALAPPDATA%\mavlab-logbook-client\Cache` |

`config.json` holds API keys and is readable by you only; `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` are respected on
Linux. `MAVLAB_LOGBOOK_CACHE` (or `ml.set_cache_dir(folder)`, also seen by MATLAB) and `MAVLAB_LOGBOOK_CONFIG` choose
other folders. The cache holds `mat/<server>/` (exports, from `fetch` and MATLAB), `logs/<sha256>/` (original files) and
`flights/<server>/` (which log each flight is in, for offline use); delete it at any time to free space.
