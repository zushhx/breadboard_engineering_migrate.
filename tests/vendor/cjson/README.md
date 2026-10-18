# Pinned host-test cJSON

The two source files come from the already pinned PlatformIO
`framework-espidf@3.50402.0` (ESP-IDF 5.4.2), `components/json/cJSON`, version 1.7.18.
The C source is an unmodified byte copy. One trailing space in a header comment at
original line 264 is removed so the fixture passes the repository whitespace check;
no API or code is changed. Both the original header hash and pinned fixture hashes
are in `manifest.json`; fixture hashes are checked before the network host test.
The MIT copyright and license notices remain at the top of both files.

Only the host network regression uses these files. The actual firmware still builds
against the SDK's own cJSON; no firmware dependency or SDK option has been changed.
This avoids depending on an unlisted `libcjson-dev` package on GitHub runners or a
different system cJSON version. The host suite needs `gcc` and `g++`, already used by
the existing native tests. It does not access radios, NVS, serial ports or TF cards.
