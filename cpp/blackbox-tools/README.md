# Blackbox decoder

C sources for `blackbox_decode`, used by the web UI to turn Betaflight and
INDIFlight `.bfl`/`.bbl` logs into CSV.

Upstream: INDIFlight log tools at commit
[`9d93ad71b2f2dab02a1e8b5c60e8aaf0e4559528`](https://github.com/tudelft/indiflight/tree/9d93ad71b2f2dab02a1e8b5c60e8aaf0e4559528/support/LogAnalysis/indiflight_log_tools/indiflight_log_tools/blackbox-tools),
which extend [Nicholas Sherlock's blackbox-tools](https://github.com/betaflight/blackbox-tools)
with INDIFlight fields. Stock Betaflight logs use the same container, so one
decoder covers both.

Licensed under the GNU GPL v3; see `LICENSE`. Built by the top-level
`cpp/CMakeLists.txt`.
