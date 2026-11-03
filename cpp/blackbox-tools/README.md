# Blackbox decoder

C sources for `blackbox_decode`, used by the web UI to turn Betaflight and
INDIFlight `.bfl`/`.bbl` logs into CSV.

They come from the INDIFlight log tools (which extend Nicholas Sherlock's
blackbox-tools with INDIFlight fields). Stock Betaflight logs use the same
container, so one decoder covers both. Built by the top-level `cpp/CMakeLists.txt`.
