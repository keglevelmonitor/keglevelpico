# KegLevel Pico Firmware Distribution

This repository hosts over-the-air (OTA) firmware manifests and release
assets for the KegLevel Brands Raspberry Pi Pico product family:

* **KegLevel Pico** -- keg volume + temperature + gas monitor
  ([source](https://github.com/keglevelmonitor/keglevelpicoonly))
* **GrowStation Brain** -- controlled-environment agriculture controller
  ([source](https://github.com/keglevelmonitor/growstationbrain))
* **ScaleBrain** -- brewing-scale controller
* **FermVault Pico** -- fermentation-chamber controller
  ([source](https://github.com/keglevelmonitor/fermvaultpico))

Each project's source code lives in its own (private) repository. This
repo holds only the public artifacts each running Pico fetches at
update time:

```
ota/
  manifest.json           <- KegLevel Pico
  bundle.json
  changelog.json
  growstation/            <- GrowStation Brain
  scalebrain/             <- ScaleBrain
  fermvault/              <- FermVault Pico
```

Release tags follow the pattern `<project>-<version>` (e.g.
`fermvault-0.1.31`, `growstation-0.1.35`).

Historical note: this repository was previously the home of a
now-retired Raspberry Pi Linux keg-monitor application. That code
still lives in git history but is no longer maintained.