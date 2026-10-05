# Hydrao Custom - Changelog

## 1.1.0

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

This release is about accuracy and reliability: shower durations and the cold / comfort split are now correct, two new diagnostic sensors show how long the water stayed cold, and the integration is fully tested and strictly typed.

### ✨ New features
- **Cold Water Shower Duration** sensor: time spent below the comfort temperature during the current shower. Diagnostic, disabled by default.
- **Time to Comfort Temperature** sensor: how long the water took to reach the comfort temperature. It stays *unknown* when the water was already warm at connection, because the cold phase cannot be measured then. Diagnostic, disabled by default.
- **Diagnostics download** (*Download diagnostics* on the device page), with the Bluetooth address, device name, title and device ID redacted so the file can be attached to a public issue. It includes the last raw Bluetooth frames, which makes it possible to work out how a device revision encodes its values.
- The **Flow Rate** sensor now has the *volume flow rate* device class, so Home Assistant can show it in other units.
- The **Bluetooth Signal** (RSSI) sensor is now disabled by default: it is a support tool. Enable it from the entity settings to check the Bluetooth range.

### 🐛 Bug fixes
- **The minimum Home Assistant version is now declared correctly: 2026.5.0** (`hacs.json`, was 2025.1.0). The integration has always needed a Bluetooth function (`async_clear_advertisement_history`) that first shipped in Home Assistant 2026.5.0, so on an earlier version it could not load at all.
- **Shower durations are now correct beyond about 21 minutes.** The device counts time in 1/50 s steps on 16 bits, so its counter wraps around every 21.8 minutes; the wrap-around is now handled, and the durations no longer restart from zero.
- **The cold / comfort split is more accurate.** When the temperature crosses the comfort threshold between two readings, the volume and the time are now split at the crossing point, instead of being counted entirely on the side of the latest reading.
- **Maximum Soaping Time out of range** (outside 10–600 s) is brought back into range with a warning in the log, instead of being sent to the device as is.
- Durations saved before the update (in minutes) are converted when restored, so a restart right after updating no longer shows values 60 times too small.
- The wasted volume, comfort shower volume and raw shower volume sensors now use the `total_increasing` state class instead of `measurement`, which Home Assistant rejects for the `water` device class (a warning was logged at every startup). See the upgrade notes.
- **The Temperature sensor no longer reports 0 °C when the value is unknown** (while a press of the *Shower Ended* button is waiting for the device to confirm). It now shows *unknown*, so the history and its averages are no longer skewed by fake 0 °C readings.
- **The first measurement of a shower now arrives sooner.** When connecting, the integration used to read the device settings (thresholds, soaping time) before taking its first measurement, and read the thresholds a second time right after. The settings are now read after the first measurement, and only once: two Bluetooth reads fewer before it, one fewer overall. This helps the *Time to Comfort Temperature* sensor, which needs a first reading taken while the water is still cold.

### 🛡️ Hardening
- **A water temperature outside 0–100 °C is no longer used.** Some Hydrao revisions may encode the temperature differently, which could show hundreds of degrees and count all the water as comfortable. The *Temperature* sensor now shows *unknown*, the comfort / cold figures and the Comfort Mode Sync ignore that reading, volume, duration and flow keep working, and a single warning in the log gives the firmware, the hardware and the raw frame to report.
- A drop of the device's duration counter that is not a wrap-around is treated as a device reset and counts for nothing, instead of producing a huge wrong duration.
- Thresholds outside 0–255 are refused with a warning instead of being sent to the device; an empty hardware-revision value no longer raises an error; incomplete stored thresholds or colours no longer prevent the integration from loading; a cached, outdated Bluetooth advertisement is no longer mistaken for a fresh one at startup.
- **The cumulative totals are no longer lost in a crash.** The *Total Cumulative Wasted Volume* and *Total Cumulative Comfort Shower Volume* are now saved in a file of their own within a few seconds of each change, instead of relying on Home Assistant's entity-state snapshot (taken every 15 minutes), and no longer depend on the state of the sensors. The totals of version 1.0.0 are carried over automatically at the first start; deleting the device also deletes the file.
- **The Bluetooth connection is now closed explicitly** at the end of every cycle (also on an error or when Home Assistant cancels the task), as the connection helper's documentation prescribes, instead of entering an already-connected client a second time and relying on Home Assistant's Bluetooth layer to ignore it. A failure to close the link is only logged, so it can no longer turn the end of a shower into a *Connection Error*.

### 🧰 Maintenance
- Every entity now shares a common base class (`HydraoEntity`): translated names, unique ID built from the Bluetooth address, device. **Entity IDs and unique IDs are unchanged.**
- Icons are defined in `icons.json` (same icons as before, the Bluetooth status keeps one icon per state).
- Durations are computed in seconds from integer counter differences, and displayed in minutes by Home Assistant.
- `PARALLEL_UPDATES` is set on every platform.
- Field descriptions (`data_description`) added to the setup and options forms, translated in all 19 languages.
- The whole integration passes `mypy --strict`, enforced by a dedicated *Typing* workflow.
- Test suite grown from 15 to over 400 tests, with 100 % coverage (lines and branches): simulated Bluetooth device, coordinator, config / options flows, entities, config entry lifecycle, translations.
- CI: pytest + coverage workflow (95 % minimum), *Typing* workflow, and a release workflow that publishes the notes from this changelog (`scripts/release_notes.py`). `.coveragerc` measures the integration only, with branches.
- The manifest declares the `platinum` quality scale (self-assessed in `quality_scale.yaml`, hassfest does not validate it for custom integrations), with tests keeping it consistent with the code.
- The **Bluetooth Signal** (RSSI) sensor now updates its state at most once per second, and only when the value changes.
- `manifest.json` no longer lists `bleak` and `bleak-retry-connector` as requirements: they come with Home Assistant's Bluetooth integration, which this one depends on. A `ruff.toml` enables stricter lint rules (bug-finding families such as `B`, `ASYNC`, `PERF`, `UP`).
- Internal refactoring, no change in behaviour: each sensor now carries its own value function (`HydraoSensorEntityDescription`, as in Home Assistant's own integrations) instead of three separate lists of keys; the numeric fields of the forms share one helper; the raw-frame constants and the form's threshold limits have names; `is_valid_temp` is now `is_valid_comfort_threshold`, which says what it bounds.

### 📚 Documentation
- `README.md` / `README.fr.md`: minimum Home Assistant version, the new sensors, and new sections *How Data Is Updated*, *Use Cases*, *Automation Examples*, *Known Limitations* and *Removal*.
- A note on the per-shower and cumulative volume sensors, and status badges (CI, license, quality scale).

### 📋 Upgrade notes
- **Duration sensors keep their unit**: Home Assistant converts them automatically, so an existing *Shower Duration* still shows minutes.
- **Statistics of three sensors**: *Wasted Volume (Cold Water)*, *Comfort Shower Volume* and *Shower Volume* change state class. Home Assistant may offer to fix their long-term statistics in **Developer tools** > **Statistics**; accept it. For long-term statistics and the Water dashboard, use the cumulative sensors (*Total Cumulative Shower Volume*, *Total Cumulative Wasted Volume*, *Total Cumulative Comfort Shower Volume*) rather than the per-shower ones.
- The **Bluetooth Signal** sensor is only disabled on new installations: an existing one stays enabled.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

## 1.0.0

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

### ✨ New features
- First stable release.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬
