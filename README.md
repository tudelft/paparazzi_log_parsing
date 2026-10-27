# Paparazzi log parsing

## Quick start

For a local trial, run from `webui/` using your Python environment after building the
[C++ parser](cpp/) in **Release** mode:

```sh
cd webui
cmake -S ../cpp -B ../cpp/build -DCMAKE_BUILD_TYPE=Release && cmake --build ../cpp/build
pip install -r requirements.txt
python3 app.py create-user alice --admin
python3 app.py                                  # http://127.0.0.1:8000
```

Flight-log parsing tools and a web-based flight logbook.

- **[User guide and quick start](webui/README.md)** - uploading logs, browsing flights and managing records.
- **[Technical guide](webui/TECHNICAL.md)** - deployment, configuration, backups, permissions and development.
- **[Coding assistant guidance](AGENTS.md)** - repository structure and change/test conventions.

The [C++ parser](cpp/) powers the [web app](webui/).
The [MATLAB tools](matlab/README.md) load, plot and analyse flights exported by the logbook (or converted
locally with `webui/matlab_export.py`); the repository also contains [Python](python/) tools.
