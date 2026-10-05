# Hydrao Custom - Changelog

## 1.1.0

💧💧💧💧💧💧💧💧💧💧

This release makes the integration more reliable and more accurate: durations computed from the device's own counter, cumulative totals kept in a dedicated file, two new diagnostic sensors, and a test suite of over 500 tests.

### 🚨 Breaking changes
- **Home Assistant 2026.5.0 or newer** (`hacs.json`), as the integration uses a Bluetooth helper first shipped in that release (Python 3.14).

### ✨ New features
- **Cold Water Shower Duration** diagnostic sensor: time spent below the comfort temperature during the current shower.
- **Time to Comfort Temperature** diagnostic sensor: time elapsed before the comfort temperature was first reached (unavailable if the water was already warm at connection).
- Downloadable **diagnostics** (address, name and serial number redacted): raw Bluetooth frames, firmware and coordinator state, to make support easier.
- Field descriptions (`data_description`) in the setup and options forms, in all 19 languages.

### 🔄 Changes
- Durations are now computed in seconds from the device counter (1/50 s) and displayed in minutes; values restored from before the update are converted automatically.
- Comfort time is computed by interpolating the moment the temperature crosses the threshold, instead of counting the whole interval on one side.
- The **Bluetooth Signal** sensor is disabled by default (enable it to check the range) and limited to one update per second.
- Per-shower volume sensors switch to `total_increasing` and the flow rate gets its device class. For long-term statistics, use the **Total Cumulative** sensors.

### 🐛 Bug fixes
- The **cumulative totals** (wasted volume and comfort volume) are now saved in their own `.storage` file: they no longer depend on the state of the entities. Automatic migration from 1.0.0; the file is deleted with the device.
- An **impossible temperature** (outside 0-100 °C) is ignored (*Unknown* + a single warning in the log) instead of being counted as hot water.
- The **duration counter wrap-around** (uint16) is handled correctly; any other decrease is treated as a device reset, with no bogus duration.
- A cached Bluetooth advertisement is no longer mistaken for a recent signal (its own timestamp is used).
- The soaping time is clamped to 10-600 s before the BLE write, and volume thresholds must fit in one byte.
- The Bluetooth connection is now closed cleanly after each session.

### 🧰 Maintenance
- New `HydraoEntity` base class, icons moved to `icons.json`, `PARALLEL_UPDATES` declared, named constants, full typing (`mypy --strict`).
- The `bleak` / `bleak-retry-connector` requirements are no longer declared in the manifest (provided by Home Assistant's Bluetooth integration).
- The manifest declares the `platinum` quality scale (`quality_scale.yaml`), with tests keeping it consistent.
- Test suite of **over 500 tests**: config/options flow, coordinator, entities, diagnostics, translations, totals storage, documentation.
- CI: *Tests*, *Typing* (mypy) and *Release* workflows, ruff configuration (`ruff.toml`) and `.coveragerc`.

### 📚 Documentation
- `README.md` / `README.fr.md`: badges, minimum Home Assistant version, new *How Data Is Updated*, *Use Cases*, *Automation Examples*, *Known Limitations* and *Removal* sections, new sensors and temperature troubleshooting.

💧💧💧💧💧💧💧💧💧💧

## 1.0.0

💧💧💧💧💧💧💧💧💧💧

### ✨ New features
- First stable release.

💧💧💧💧💧💧💧💧💧💧
