# mavlab-logs

Download and upload drone flight logs of a [MAVLab flight log database](https://github.com/tudelft/paparazzi_log_parsing)
from Python, without a browser download or drag and drop. Works with every log the server stores (Paparazzi
`.data`/`.log`, ArduPilot `.bin`/`.log`/`.tlog`, PX4 `.ulg`, Betaflight `.BFL`, …). Pure Python 3.10+, no dependencies.
The [MATLAB tools](https://github.com/tudelft/paparazzi_log_parsing/tree/master/matlab) fetch the same flights as
MATLAB exports and share the login.

```sh
pip install -i https://test.pypi.org/simple/ mavlab-logs
```

```python
import mavlab_logs as ml

ml.connect("logs.example.org")                     # or ml.connect("logs.example.org", key="logdb_…")
paths = ml.fetch([1234, 1240])                     # flight numbers, as in /flight/1234: one path per log
paths[0]                     # e.g. ~/.cache/mavlab-logs/logs/<sha256>/24_10_30__16_27_37_SD.data (.log next to it)
paths = ml.fetch([1234, 1240], scope="flight")     # one path per number instead
receipts = ml.upload(["flight.data", "LOG00030.BFL"], wait=True)
receipts[0]["flight_ids"]                          # the flights the server found, for ml.fetch
```

`connect(server, key)` does the authentication. The server is required (`https://` is added to a bare host name);
the key defaults to `LOGDB_API_KEY` (a key from your account page, as for `upload_logs.py`; not used when
`LOGDB_SERVER` names another server), else the key of the last browser login to that server. Values taken from the environment are announced and named
in errors. Without a key, `connect` opens the browser: sign in, check that the page shows the same code as the
terminal and press **Allow**. That key acts as you for a week and is stored for the next `connect`.
`connect(..., login=False)` raises `AuthError` instead (for unattended scripts); `insecure=True` accepts a
self-signed certificate. `connect` returns a `Client` with the same methods, for using several servers at once.

`ml.revoke_all()` revokes your browser-login keys on every computer (Python and MATLAB) and forgets the stored one;
`ml.revoke_all(include_manual=True)` also revokes the keys made on the account page.

**Fetching** downloads the original log files that hold the flights, always whole: they are binary and are not cut
into flights (use MATLAB's `pprz_fetch` for exports of single flights). `scope` is that of `pprz_fetch`:
`"selected"` (default), `"all"` and `"log"` return one path per log, `"flight"` one path per number. Logs are
cached and reused; one never changes (it is named by its SHA-256, which is checked after each download).
`force=True` downloads again; without a connection or when the server fails (HTTP 5xx) the cached copies are
used, with a warning.

**Uploads** pair a `.data` with the `.log` next to it (whichever of the two you pass). A log already on the server
is skipped. `name="Hex 1/LOG00030.BFL"` sets the stored name of a single file, which can link it to a fleet
aircraft. With `wait=True` each call returns once the server has parsed the log, else as soon as it accepted it.

## Command line

```sh
mavlab-logs --server logs.example.org status                       # signs in if needed; user, key and cache
mavlab-logs --server logs.example.org fetch 1234 1240               # prints the local paths; --scope flight
mavlab-logs --server logs.example.org upload flight.data LOG00030.BFL --wait
mavlab-logs --server logs.example.org revoke-all                    # --include-manual for account-page keys too
mavlab-logs --server logs.example.org --key logdb_… fetch 1234      # as connect(server, key)
```

## Where files are kept

| | Linux | macOS | Windows |
|---|---|---|---|
| Login (`config.json`) | `~/.config/mavlab-logs` | `~/Library/Application Support/mavlab-logs` | `%APPDATA%\mavlab-logs` |
| Cache | `~/.cache/mavlab-logs` | `~/Library/Caches/mavlab-logs` | `%LOCALAPPDATA%\mavlab-logs\Cache` |

`config.json` holds API keys and is readable by you only; `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` are respected on
Linux. `MAVLAB_LOGS_CACHE` (or `ml.set_cache_dir(folder)`, also seen by MATLAB) and `MAVLAB_LOGS_CONFIG` choose
other folders. The cache holds `logs/<sha256>/` (original files) and `flights/<server>/` (which log each flight
is in, for offline use) from Python, and `mat/<server>/` (exports) from MATLAB; delete it at any time to free space.
