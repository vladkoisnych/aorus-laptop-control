# Troubleshooting

Most of this was worked out on one AORUS 16X ASG, so the specifics are from a
real machine rather than from the datasheets.

## Start with the probe

```sh
sudo ./probe.sh          # writes aorus-probe-report.txt next to the script
```

It only reads. The report covers DMI strings, which WMI GUIDs your firmware
exposes, the hwmon tree, RAPL domains, `intel_pstate`, `nvidia-smi`
capabilities, whether the DSDT contains `WMBC` and `WMBD`, and what fan software
is already installed.

It describes the machine rather than you: your username and hostname are
replaced with placeholders before the file is written, and it does not collect
your running services, the processes using the GPU, or any serial numbers. Read
it before attaching it to an issue if you would rather not share any of it.

## The FANS section says unavailable

Either the `aorus-laptop` module is not loaded, which usually means Secure Boot,
or your firmware does not expose the Gigabyte WMI GUIDs at all. The probe report
distinguishes the two: look for the GUID list under WMI DEVICES.

If the GUIDs are missing, this driver cannot work on your machine and no amount
of installing will change that. The CPU and GPU halves still work.

## Secure Boot

With Secure Boot on, the kernel refuses any module it cannot verify. The
installer generates a machine owner key, points DKMS at it so future kernel
rebuilds get signed too, signs the module, and runs `mokutil --import`.

That needs one reboot. Before the OS starts you get a blue MOK management
screen: Enroll MOK, Continue, Yes, type the one-time password the installer
asked you to choose, Reboot. If you miss the prompt it times out and boots
normally; run `sudo mokutil --import /var/lib/shim-signed/mok.der` and reboot
again.

```sh
sudo /usr/local/share/aorusctl/secureboot.sh status
```

reports the Secure Boot state, whether the key exists and is enrolled, whether
the module is signed and by whom, and what `modprobe` actually says. `sign`,
`enroll`, `key` and `setup` are the other subcommands. All are safe to re-run.

Turning Secure Boot off in the BIOS also works, but avoid it on a dual-boot
machine: Windows treats the change as tampering and BitLocker will ask for your
recovery key at the next boot.

Signing does not weaken Secure Boot. The key is generated on your machine, never
leaves it, is readable only by root, and you approve it by hand at the firmware
prompt. It signs modules built on this machine and nothing else.

## The fans stop instead of speeding up at high duty

The EC's duty scale tops out at 229 (0xE5), not 255, and the kernel driver
passes the value through to the firmware without clamping. Writing 230 or more
lands outside the range and the fans stop rather than speed up.

100 percent maps to `fans.duty_max` in `/etc/aorusctl/config.toml`, which
defaults to 229. Lower it if your model still stops short of full speed, raise it
toward 255 if full speed feels weak on yours.

## Fan mode changes on its own

If the mode flips to `fixed` and the duty moves around, `aorusctld` is running
and driving the fans from its own curve. Picking a mode by hand stops the daemon,
so this should not happen any more; if it does, check `systemctl status
aorusctld`.

Duty moving on its own while the mode stays `normal` is just the firmware's own
curve reacting to temperature. That is expected.

## A write fails with EPERM even under sudo

`aorus-laptop` returns `-1` when the firmware's WMI method fails, and the kernel
reports `-1` as `-EPERM`, so an unimplemented method looks exactly like a
permissions error. `gpu boost` does this on the 16X ASG, which simply has no
`GPU_QBOOST` method. The tool says as much rather than telling you to use sudo.

## The GPU is stuck at its base TGP

Raising a laptop GPU's power limit with `nvidia-smi -pl` almost never works. The
limit belongs to the vBIOS and platform firmware, not the driver, so `-pl`
reports success and the enforced value never moves. A `power.limit` of N/A in
`aorusctl gpu show` is the tell.

What actually lifts a laptop GPU above base TGP is Dynamic Boost, and that is
`nvidia-powerd`. On Ubuntu the package ships with the driver but the service is
often left disabled:

```sh
systemctl status nvidia-powerd
sudo systemctl enable --now nvidia-powerd
```

`aorusctl gpu show` and `status` both report which of the three states it is in.
With it running, the enforced limit moves on its own: on the 16X ASG it went from
a fixed 95 W to 120 W within the hour.

Capping the GPU lower does work, through clocks rather than power:

```sh
sudo aorusctl gpu clocks 210,1800
sudo aorusctl gpu reset            # release the lock
```

The `silent`, `battery` and `cool` profiles use clock locks for this reason.

## Checking that Dynamic Boost works

Dynamic Boost moves power budget from the CPU to the GPU, so it only engages
under a load that is heavy on the GPU and light on the CPU. A light load proves
nothing, and a CPU-heavy one like a game engine or a compile is the worst case
for testing it.

```sh
sudo apt install -y vkmark          # Vulkan, so GPU-bound with almost no CPU load
sudo aorusctl mon                   # in a second terminal
vkmark
```

If it is working, GPU watts climbs past `power.default_limit` toward
`power.max_limit`. If it pins at the default under sustained GPU load, Dynamic
Boost is not engaging.

## A power limit takes and then reverts seconds later

Persistence mode was off, so the driver tore down GPU state when the last client
exited and the limit went with it. The tool turns persistence on before setting a
limit. That keeps the dGPU initialised and costs battery, and `aorusctl reset`
puts it back.

## PL1 will not go above some value

The MMIO RAPL domain often carries a firmware cap, and the lower of the MSR and
MMIO limits is the one that binds. On the 16X ASG the MMIO domain caps PL1 at
55 W while the MSR domain reads 200 W, so 55 W is the real ceiling and no amount
of writing will raise it.

`aorusctl cpu show` prints every domain, and `status` prints the effective PL1.

## CPU watts shows `--`

Since the RAPL side-channel mitigation, `energy_uj` is readable only by root.
Run with `sudo`.

Note also that the MMIO domain usually has no energy counter at all, only the MSR
one does, which is why the tool picks its source rather than taking the first
domain it finds.

## The dashboard shows stale values or an old error

The web server holds the Python in memory, so it keeps serving the old code after
you replace the binary:

```sh
sudo systemctl restart aorusctl-web.service
```

## The top bar extension shows nothing, or no GPU

It reads from `aorusctl-web.service` over loopback. Without that running it falls
back to sysfs, which covers temperatures and fan readings but not the GPU, and
the fan mode buttons are disabled. The popup says which mode it is in.

```sh
sudo systemctl enable --now aorusctl-web.service
gnome-extensions info aorusctl@vladkoisnych.github.io
journalctl -f -o cat /usr/bin/gnome-shell        # extension errors land here
```

If the address is not the default, set it in
`gnome-extensions prefs aorusctl@vladkoisnych.github.io`.

After editing extension files, GNOME has to reload them: log out and back in on
Wayland, or press Alt+F2 and type `r` on X11. Disabling and re-enabling the
extension is not enough.

Updating means re-running the installer, not copying one file. `./install.sh`
in `gnome-extension/` copies everything and recompiles the schema.

## Reverting everything

```sh
sudo aorusctl reset      # every setting back to what it was
sudo aorusctl fan auto   # fans back under firmware control, immediately
sudo ./uninstall.sh      # remove the tool, the units and the kernel module
```

`uninstall.sh` leaves `/etc/aorusctl` and the Secure Boot key alone on purpose.
Other DKMS modules on the machine may be signed with that key.
