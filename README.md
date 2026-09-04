# aorus-dashboard

Fan control, power limits and live dashboards for Gigabyte AORUS and AERO
laptops on Linux.

I wrote this for my own AORUS 16X because nothing else worked on it. Gigabyte
has no Linux build of Control Center, NBFC has no config for the recent chassis,
`nbfc-linux` wants embedded controller register offsets nobody has published for
them, and the TUXEDO drivers only bind to Clevo and TUXEDO boards. It has since
been tidied up enough to be worth publishing, but it started as a personal fix
and the hardware coverage still reflects that.

![The aorusctl browser dashboard](docs/dashboard.png)

```
aorusctl status                    # snapshot
sudo aorusctl mon                  # live terminal dashboard
sudo aorusctl web                  # browser dashboard on http://127.0.0.1:8787
sudo aorusctl profile apply cool
sudo aorusctl reset                # undo everything
```

## What it does

Fans: the firmware's own modes (normal, silent, gaming, custom, auto, fixed),
fixed duty, and its 15-point curve, with RPM and duty readout for both fans.

CPU: sustained and burst package power limits (RAPL PL1 and PL2), a ceiling on
the `intel_pstate` performance window, turbo, governor and energy performance
preference.

GPU: NVIDIA power limit, hard clock lock, and the EC-side Dynamic Boost toggle.

Plus a curses dashboard for the terminal, a self-contained browser dashboard
with history graphs and working controls, named profiles in one TOML file, and
the battery charge limit and cycle count.

## How it talks to the hardware

Three separate paths, none of them writing the embedded controller directly.

Fans, EC temperatures, charge limit and GPU boost go through the `aorus-laptop`
WMI kernel driver, which calls the same `WMBC` and `WMBD` ACPI methods Control
Center uses on Windows. The firmware validates every value it gets.

CPU power and frequency ceilings use mainline sysfs: `intel-rapl` powercap,
`intel_pstate` and `cpufreq`.

GPU power and clock ceilings go through `nvidia-smi -pl` and `-lgc`, which clamp
to whatever the vBIOS allows. On laptops running `nvidia-powerd` the power limit
belongs to that daemon, so the EC's Dynamic Boost toggle is the lever that
actually works. Every GPU write is read back and a change that did not stick is
reported as a failure rather than assumed.

Nothing here writes `/dev/port`, sets `ec_sys write_support=1`, or pokes MSRs.
That is how people brick the EC on these machines, and why the `p37-ec-*`
scripts you find online are model specific and dangerous on the wrong model.

The kernel module is not part of this repository. `install.sh` downloads
[tangalbert919/gigabyte-laptop-wmi](https://github.com/tangalbert919/gigabyte-laptop-wmi)
at a pinned commit, checks its SHA-256, and builds it through DKMS so it
survives kernel upgrades.

## Hardware

Tested on an AORUS 16X ASG (i7-14650HX, RTX 4070 Laptop) running Ubuntu 24.04,
kernel 6.8. That is the only machine this has run on.

The fan half should work on anything the upstream driver supports: all AERO
models, all AORUS models, Gigabyte Gaming 2025 and newer, and some P-series.
Sabre and pre-2025 Gigabyte Gaming models are rebadged Clevo boards and will not
work.

The CPU and GPU halves are generic. They work on any Intel machine with
`intel-rapl` and any NVIDIA GPU with `nvidia-smi`, Gigabyte or not.

If your model is not listed, run `sudo ./probe.sh` first. It only reads, and the
report says which interfaces your firmware exposes. Reports from untested models
are welcome as issues.

## Install

```sh
git clone https://github.com/vladkoisnych/aorus-dashboard
cd aorus-dashboard
sudo ./install.sh --check     # see what it would do, changes nothing
sudo ./install.sh
```

If the module fails to build or fails to bind, the installer says so and carries
on. The CPU and GPU halves work with no module at all.

| Flag | Effect |
|---|---|
| `--check` | dry run, changes nothing |
| `--no-driver` | install the tool only, no kernel module |
| `--driver-dir DIR` | build from a local checkout instead of downloading, for offline installs |
| `--driver-ref REF` | build a different upstream commit, branch or tag, skipping checksum verification |

To remove everything and put the hardware back to firmware defaults:

```sh
sudo ./uninstall.sh
```

### Secure Boot

With Secure Boot on, the kernel refuses any module it cannot verify, so this one
has to be signed with a key you enroll yourself. The installer generates a
machine owner key, points DKMS at it so future kernel rebuilds get signed too,
signs the module, and runs `mokutil --import` at the end.

That needs a reboot. Before the OS starts you get a blue MOK management screen:
Enroll MOK, Continue, Yes, type the one-time password the installer asked you to
choose, Reboot.

If something looks wrong afterwards, this reports the Secure Boot state, whether
the key exists and is enrolled, whether the module is signed and by whom, and
what `modprobe` says:

```sh
sudo /usr/local/share/aorusctl/secureboot.sh status
```

`sign`, `enroll` and `setup` are the other subcommands. All of them are safe to
re-run.

Turning Secure Boot off in the BIOS also works, but avoid it on a dual-boot
machine: Windows treats the change as tampering and BitLocker will ask for your
recovery key at the next boot.

Signing does not weaken Secure Boot. The key is generated on your machine, never
leaves it, is readable only by root, and you approve it by hand at the firmware
prompt. It signs modules built on this machine and nothing else.

## Use

```sh
aorusctl status                    # no root needed
aorusctl status --json             # for scripts
sudo aorusctl mon                  # live terminal dashboard
sudo aorusctl web                  # browser dashboard, loopback only

sudo aorusctl fan mode gaming      # normal silent gaming custom auto fixed
sudo aorusctl fan speed 60         # pin the fans at 60 percent
sudo aorusctl fan curve 45:15,55:25,65:40,72:55,80:70,86:85,92:100
sudo aorusctl fan auto             # hand the fans back to the firmware
aorusctl fan show

sudo aorusctl cpu pl1 45           # sustained package watts
sudo aorusctl cpu pl2 110          # burst package watts
sudo aorusctl cpu maxpct 70        # cap the intel_pstate performance window
sudo aorusctl cpu turbo off
sudo aorusctl cpu epp power
aorusctl cpu show

sudo aorusctl gpu limit 100        # watts, clamped to what the vBIOS allows
sudo aorusctl gpu boost 2          # EC Dynamic Boost: 0 off, 1 on, 2 max
sudo aorusctl gpu clocks 210,1800  # hard SM clock lock, MHz
sudo aorusctl gpu reset
aorusctl gpu show

sudo aorusctl profile apply silent
aorusctl profile list
sudo aorusctl reset
```

Add `--dry-run` to anything to see the exact sysfs writes without performing
them.

In `aorusctl mon`: `q` quit, `n` normal, `s` silent, `g` gaming, `1` to `9` pin
the fans at 10 to 90 percent.

### Profiles

`/etc/aorusctl/config.toml` ships with `silent`, `balanced`, `gaming`, `battery`
and `cool`. A profile is a bundle of the settings above. Anything the hardware
refuses is reported as skipped and the rest still applies.

```toml
[profiles.cool]
fan_mode        = "custom"
fan_curve       = [[40,15],[50,25],[60,40],[68,55],[75,70],[82,85],[88,100]]
cpu_pl1         = 45
cpu_pl2         = 80
turbo           = true
epp             = "balance_performance"
gpu_power_limit = 100
```

To apply one at every boot, see [Running at boot](#running-at-boot).

### The fan daemon

The firmware curve is usually the better choice, and `aorusctl fan curve` writes
straight into it, so nothing has to keep running.

`aorusctld` is for when you want a curve driven by sensors the EC cannot see, or
hysteresis the firmware does not offer. It samples the hottest of CPU package,
GPU and EC temperatures, drives the fans in fixed mode, and restores the previous
fan mode when it stops: on clean exit, on SIGTERM, and through `ExecStopPost` if
it gets killed outright. At or above `guard_temp` the fans go to 100 percent
whatever the curve says.

```sh
sudo systemctl enable --now aorusctld.service
journalctl -u aorusctld -f
```

### Running at boot

`install.sh` puts three systemd units in place and leaves all of them disabled.

| Unit | What it does |
|---|---|
| `aorusctl-web.service` | keeps the browser dashboard up on http://127.0.0.1:8787 |
| `aorusctld.service` | runs the software fan curve and thermal guard |
| `aorusctl-profile.service` | applies one profile at every boot |

`--now` starts a unit immediately as well as enabling it for future boots:

```sh
sudo systemctl enable --now aorusctl-web.service
sudo systemctl enable --now aorusctld.service
```

And stops it immediately as well as disabling it:

```sh
sudo systemctl disable --now aorusctl-web.service
sudo systemctl disable --now aorusctld.service
```

To see what a unit is doing, and follow its log:

```sh
systemctl status aorusctl-web.service
journalctl -u aorusctld -f
```

The profile unit needs one extra step, since you pick which profile first. It
ships pointing at `balanced`:

```sh
sudo sed -i 's/apply balanced/apply cool/' /etc/systemd/system/aorusctl-profile.service
sudo systemctl daemon-reload
sudo systemctl enable --now aorusctl-profile.service
```

Disabling that one runs `aorusctl reset` through `ExecStop`, so the hardware goes
back to firmware defaults as it stops:

```sh
sudo systemctl disable --now aorusctl-profile.service
```

`aorusctld` restores the previous fan mode whenever it stops, killed or not, so
disabling it will never leave the fans pinned.

`uninstall.sh` disables and removes all three units, so you do not need to do
this by hand first.

## Safety

Every write is clamped to the range the kernel or firmware reports. Where the
firmware publishes no maximum, the write is attempted and the kernel's rejection
is reported rather than guessed at.

The first value seen for anything changed is recorded in
`/var/lib/aorusctl/state.json`, and `aorusctl reset` puts all of it back.

The web dashboard binds to `127.0.0.1` and the API refuses control requests from
any other address. Do not change `--bind` unless you mean to.

Thermal throttling in the CPU and GPU is a hardware feature that nothing here
touches. A bad fan curve costs you performance and cannot damage anything.

On a dual boot, the firmware fan curve, charge limit and GPU boost live in EC
registers that Control Center writes too, so whichever OS booted last wins. Fan
mode and power limits do not survive a power cycle either way.

## Troubleshooting

```sh
sudo ./probe.sh    # writes aorus-probe-report.txt, reads only
```

The report covers DMI strings, which WMI GUIDs are exposed, the hwmon tree, RAPL
domains, `intel_pstate`, `nvidia-smi` capabilities, whether the DSDT contains
`WMBC` and `WMBD`, and what fan software is already running. Attach it to an
issue.

Common problems:

- FANS section says unavailable. Either the module is not loaded, which usually
  means Secure Boot, or your firmware does not expose the Gigabyte WMI GUIDs.
  The probe report distinguishes the two.
- CPU watts shows `--`. Since the RAPL side-channel mitigation, `energy_uj` is
  readable only by root, so run with `sudo`.
- `gpu limit` reverts to the default a moment after you set it. Two causes,
  and `aorusctl gpu show` names which one you have. Persistence mode off is the
  common one: the driver tears down GPU state whenever the last client exits and
  the limit goes with it, so the tool now turns persistence on before setting a
  limit. That keeps the dGPU initialised and costs battery, and `aorusctl reset`
  puts it back. The other is `nvidia-powerd`, which owns the power limit on
  laptops with Dynamic Boost and rewrites it every second or so. Steer the budget
  from the EC with `aorusctl gpu boost 0|1|2` instead, or stop `nvidia-powerd` to
  take manual control and give up Dynamic Boost. Some vBIOSes also ignore the
  limit outright, in which case `aorusctl gpu clocks` caps the GPU by clock,
  which they generally do honour.
- PL1 will not go above some value. The MMIO RAPL domain often carries a
  firmware cap, and the lower of the MSR and MMIO limits wins. `aorusctl cpu
  show` prints both, `status` prints the effective one.

## Not covered

- RGB keyboard. A different interface entirely, `ite-8291` and OpenRGB
  territory.
- Writable `pwmX` hwmon nodes. The upstream driver exposes them read-only, so
  fixed mode plus `fan speed` is how you set duty for now.
- A separate GPU fan curve. The EC drives both fans from one curve on these
  chassis.
- Undervolting. Locked on 13th and 14th gen HX by Intel microcode. PL1, PL2 and
  `maxpct` are the levers you have instead.

## Credits

The kernel driver is Albert Tang's
[gigabyte-laptop-wmi](https://github.com/tangalbert919/gigabyte-laptop-wmi),
GPL-2.0-or-later. Reverse engineering Gigabyte's WMI interface was the hard part
and it is his work. This repository installs it and builds tooling on top.

Earlier attempts at the same problem, all writing the EC directly:
[jertel/p37-ec](https://github.com/jertel/p37-ec),
[tangalbert919/p37-ec-aero-15](https://github.com/tangalbert919/p37-ec-aero-15),
[s-h-a-d-o-w/alfc](https://github.com/s-h-a-d-o-w/alfc).

## License

MIT, see [LICENSE](LICENSE).

The kernel driver is licensed separately under GPL-2.0-or-later by its authors.
It is downloaded from upstream at install time and is not redistributed here.
