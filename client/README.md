# mavlab-logs

Download and upload drone flight logs of a [MAVLab flight log database](https://github.com/tudelft/paparazzi_log_parsing)
from Python, without a browser download or drag and drop. Works with every log the server stores (Paparazzi
`.data`/`.log`, ArduPilot `.bin`/`.log`/`.tlog`, PX4 `.ulg`, Betaflight `.BFL`, …). Pure Python 3.9+, no dependencies.
The [MATLAB tools](https://github.com/tudelft/paparazzi_log_parsing/tree/master/matlab) offer the same and share the
login and the cache.

```sh
pip install mavlab-logs
```

```python
import mavlab_logs as ml

paths = ml.download([1234, 1240], server="https://logs.example.org")  # flight numbers, as in /flight/1234
paths[0]                     # e.g. ~/.cache/mavlab-logs/logs/<sha256>/24_10_30__16_27_37_SD.data (.log next to it)
mats = ml.download(1234, mat=True)                 # the flight's MATLAB export (.mat) instead
receipts = ml.upload(["flight.data", "LOG00030.BFL"], wait=True)
receipts[0]["flight_ids"]                          # the flights the server found, for ml.download
```

The first call opens the browser: sign in, check that the page shows the same code as the terminal and press
**Allow**. The key the script receives acts as you for a week; afterwards the next call asks again. The server is
remembered, so later calls need no `server=`. Instead of the browser login you can use an API key from your
account page: `ml.login(server, key="logdb_…")` stores it, or set `LOGDB_SERVER` and `LOGDB_API_KEY` (as for
`upload_logs.py`). `ml.logout()` revokes the stored key.

For several calls, keep one client: `c = ml.Client("https://logs.example.org")`, then `c.download(...)`,
`c.upload(...)`, `c.me()`. `Client(..., login=False)` raises `AuthError` instead of opening the browser (for
unattended scripts); `insecure=True` accepts a self-signed certificate.

**Downloads** are cached and reused. An original log never changes (it is named by its SHA-256, which is checked
after each download); an export is downloaded again when the server would export it differently (new parser or
logbook corrections). `force=True` downloads again; without a connection the cached copies are used.

**Uploads** pair a `.data` with the `.log` next to it (whichever of the two you pass). A log already on the server
is skipped. `name="Hex 1/LOG00030.BFL"` sets the stored name of a single file, which can link it to a fleet
aircraft. With `wait=True` each call returns once the server has parsed the log, else as soon as it accepted it.

## Command line

```sh
mavlab-logs --server https://logs.example.org login     # or: login --key logdb_…
mavlab-logs download 1234 1240                          # prints the local paths; --mat for the export
mavlab-logs upload flight.data LOG00030.BFL --wait
mavlab-logs status                                      # server, user, key expiry and cache folder
mavlab-logs logout
```

## Where files are kept

| | Linux | macOS | Windows |
|---|---|---|---|
| Login (`config.json`) | `~/.config/mavlab-logs` | `~/Library/Application Support/mavlab-logs` | `%APPDATA%\mavlab-logs` |
| Cache | `~/.cache/mavlab-logs` | `~/Library/Caches/mavlab-logs` | `%LOCALAPPDATA%\mavlab-logs\Cache` |

`config.json` holds API keys and is readable by you only; `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` are respected on
Linux. `MAVLAB_LOGS_CACHE` (or `ml.set_cache_dir(folder)`, also seen by MATLAB) and `MAVLAB_LOGS_CONFIG` choose
other folders. The cache holds `logs/<sha256>/` (original files), `mat/<server>/` (exports) and `flights/<server>/`
(which log each flight is in, for offline use); delete it at any time to free space.
