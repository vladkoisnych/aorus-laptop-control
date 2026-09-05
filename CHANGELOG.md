# Changelog

Notable changes per release. Versions follow [semver](https://semver.org):
while the major version is 0, the command line and `config.toml` may still
change between minor releases.

## [0.1.0] - 2026-09-05

First release. Everything below has run daily on one AORUS 16X ASG
(i7-14650HX, RTX 4070 Laptop) on Ubuntu 24.04, kernel 6.8. No other machine has
reported in yet.

### Fans

- Firmware fan modes: normal, silent, gaming, custom, auto, fixed.
- Fixed duty, and the firmware's own 15-point curve.
- RPM and duty readout for both fans, plus the EC's own temperature sensors.
- `aorusctld`, an optional software curve with hysteresis and a thermal guard
  that forces full speed at or above `guard_temp`.
- A fan mode you pick by hand always wins: setting one stops the daemon.

### CPU

- Sustained and burst package power limits through `intel-rapl`, reporting the
  effective limit when the MSR and MMIO domains disagree.
- Ceiling on the `intel_pstate` performance window, turbo, governor and energy
  performance preference.

### GPU

- Power limit and hard clock lock through `nvidia-smi`, read back after every
  write so a change that did not stick is reported as a failure.
- Detects whether `nvidia-powerd` is running, since Dynamic Boost is what
  actually lifts a laptop GPU above its base TGP.

### Interfaces

- `aorusctl status`, `mon` for a terminal dashboard, `web` for a browser
  dashboard on loopback.
- Named profiles in one TOML file: silent, balanced, gaming, battery, cool.
- A GNOME Shell extension for the top bar, reading from the web service so it
  needs no privileges of its own.

### Safety

- Every write is clamped to what the kernel or firmware reports.
- The original value of anything changed is recorded, and `aorusctl reset`
  puts all of it back.
- Nothing writes the embedded controller directly. Fan control goes through the
  `aorus-laptop` WMI kernel driver, which `install.sh` fetches from upstream at
  a pinned commit and verifies by SHA-256.

### Known limitations

- Tested on one machine. The fan half depends on your firmware exposing the
  Gigabyte WMI GUIDs; `./probe.sh` says whether yours does.
- Raising a laptop GPU's power limit rarely works, because the vBIOS owns it.
  Lowering it, and locking clocks, do work.
- `gpu boost` needs a `GPU_QBOOST` WMI method that the 16X ASG does not
  implement. It fails cleanly where it is missing.
- Undervolting is locked on 13th and 14th gen HX by Intel microcode.
