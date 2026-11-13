# Paparazzi log parsing

Flight-log parsing tools and a web-based flight logbook.

## Quick start

For a local trial, build the [C++ parser](cpp/) in **Release** mode (a Debug build is many times slower) and
run the web app from `webui/` with your Python environment. Data goes to `webui/data/`.

```sh
cd webui
cmake -S ../cpp -B ../cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build ../cpp/build
pip install -r requirements.txt
python3 app.py create-user alice --admin --email alice@example.org
python3 app.py                                  # http://127.0.0.1:8000
```

## Guides

- **[User guide](webui/README.md)** - uploading logs, browsing flights and managing records.
- **[Technical guide](webui/TECHNICAL.md)** - deployment, configuration, backups, permissions and development.
- **[Coding assistant guidance](AGENTS.md)** - repository structure and change/test conventions.

The [C++ parser](cpp/) powers the [web app](webui/).
The [MATLAB tools](matlab/README.md) load, plot and analyse flights exported by the logbook (or converted
locally with `webui/matlab_export.py`). The Python package [mavlab-logbook-client](python/mavlab-logbook-client/README.md) downloads and uploads logs
of the logbook from your own code; the repository also contains [Python](python/) tools.
