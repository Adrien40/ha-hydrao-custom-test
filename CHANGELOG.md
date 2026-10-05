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
- **The Temperature sensor shows *unknown* instead of 0 °C** while *Shower Ended* awaits confirmation, so history and averages are no longer skewed.
- **The first measurement of a shower arrives sooner**: the device settings are now read after it, not before. This helps *Time to Comfort Temperature*.

### 🛡️ Hardening
- **A water temperature outside 0–100 °C is no longer used.** Some Hydrao revisions may encode the temperature differently, which could show hundreds of degrees and count all the water as comfortable. The *Temperature* sensor now shows *unknown*, the comfort / cold figures and the Comfort Mode Sync ignore that reading, volume, duration and flow keep working, and a single warning in the log gives the firmware, the hardware and the raw frame to report.
- A drop of the device's duration counter that is not a wrap-around is treated as a device reset and counts for nothing, instead of producing a huge wrong duration.
- Thresholds outside 0–255 are refused; an empty hardware value or incomplete stored settings no longer cause errors; a stale cached advertisement is no longer taken as fresh at startup.
- **The cumulative totals survive a crash**: they are saved in their own file within seconds of each change, not every 15 minutes. Totals from 1.0.0 are carried over automatically.
- **The Bluetooth connection is closed explicitly** after every cycle; a failure to close it is only logged.

### 🧰 Maintenance
- Every entity now shares a common base class (`HydraoEntity`): translated names, unique ID built from the Bluetooth address, device. **Entity IDs and unique IDs are unchanged.**
- Icons are defined in `icons.json` (same icons as before, the Bluetooth status keeps one icon per state).
- Durations are computed in seconds from integer counter differences, and displayed in minutes by Home Assistant.
- `PARALLEL_UPDATES` is set on every platform.
- Field descriptions (`data_description`) added to the setup and options forms, translated in all 19 languages.
- The whole integration passes `mypy --strict`, enforced by a dedicated *Typing* workflow.
- Test suite grown from 15 to over 500 tests, with 100 % coverage (lines and branches): simulated Bluetooth device, coordinator, config / options flows, entities, config entry lifecycle, translations.
- CI: pytest + coverage workflow (95 % minimum), *Typing* workflow, and a release workflow that publishes the notes from this changelog (`scripts/release_notes.py`). `.coveragerc` measures the integration only, with branches.
- The manifest declares the `platinum` quality scale (self-assessed in `quality_scale.yaml`, hassfest does not validate it for custom integrations), with tests keeping it consistent with the code.
- The **Bluetooth Signal** sensor updates at most once per second, and only when the value changes.
- `manifest.json` no longer lists `bleak` and `bleak-retry-connector` (provided by Home Assistant's Bluetooth integration); stricter `ruff` rules.
- Internal refactoring, no change in behaviour: sensors carry their own value function, form fields share a helper, constants are named.

### 📚 Documentation
- `README.md` / `README.fr.md`: minimum Home Assistant version, the new sensors, and new sections *How Data Is Updated*, *Use Cases*, *Automation Examples*, *Known Limitations* and *Removal*.
- A note on the per-shower and cumulative volume sensors, and status badges (CI, license, quality scale).

### 📋 Upgrade notes
- **Duration sensors keep their unit**: Home Assistant converts them automatically, so an existing *Shower Duration* still shows minutes.
- **Statistics of three sensors**: *Wasted Volume (Cold Water)*, *Comfort Shower Volume* and *Shower Volume* change state class. Home Assistant may offer to fix their long-term statistics in **Developer tools** > **Statistics**; accept it. For long-term statistics and the Water dashboard, use the cumulative sensors (*Total Cumulative Shower Volume*, *Total Cumulative Wasted Volume*, *Total Cumulative Comfort Shower Volume*) rather than the per-shower ones.
- The **Bluetooth Signal** sensor is only disabled on new installations: an existing one stays enabled.
- **Cumulative totals are carried over** at the first start after the update. Keep the two cumulative sensors **enabled** for that start, or their total restarts from 0.
- **The Temperature sensor can be *unknown*** instead of 0 °C during a *Shower Ended* confirmation: allow for it in templates.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

## 1.0.0

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

### ✨ New features
- First stable release.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬
