#!/usr/bin/env bash
# aorusctl hardware probe - READ ONLY. Writes nothing outside the report file.
# Usage: ./probe.sh [output-file]
# Run with sudo for the complete picture (some nodes are root-readable only).

OUT="${1:-$(dirname "$(readlink -f "$0")")/aorus-probe-report.txt}"

sec() { printf '\n\n===== %s =====\n' "$1"; }
run() { printf '\n--- $ %s\n' "$*"; "$@" 2>&1 | head -200; }
cat_if() { if [ -r "$1" ]; then printf '%-64s = %s\n' "$1" "$(tr -d '\n' < "$1" 2>/dev/null)"; else printf '%-64s : (unreadable/absent)\n' "$1"; fi; }

{
printf 'aorusctl probe report\n'
printf 'generated: %s\n' "$(date -Is)"
printf 'euid: %s\n' "$EUID"
printf '\nThis file describes the machine: model, firmware, sensors, kernel\n'
printf 'interfaces and GPU capabilities. Username and hostname are replaced\n'
printf 'with placeholders before it is written. It is meant to be attached to\n'
printf 'a hardware report; read it first if you would rather not share any of\n'
printf 'it.\n'

sec "IDENTITY"
run uname -a
run cat /etc/os-release
for f in sys_vendor product_name product_family product_version board_name board_vendor bios_version bios_date chassis_type; do
  cat_if "/sys/class/dmi/id/$f"
done

sec "SECURE BOOT / MODULE SIGNING"
run mokutil --sb-state
printf '\nefivars SecureBoot: '; od -An -t u1 /sys/firmware/efi/efivars/SecureBoot-* 2>/dev/null | tail -1
cat_if /sys/module/module/parameters/sig_enforce
run ls -1 /sys/firmware/efi

sec "WMI DEVICES"
run ls -1 /sys/bus/wmi/devices
printf '\n--- GUID map\n'
for d in /sys/bus/wmi/devices/*; do
  [ -e "$d" ] || continue
  printf '%s  guid=%s  obj=%s  inst=%s\n' "$(basename "$d")" \
    "$(cat "$d/modalias" 2>/dev/null)" "$(cat "$d/object_id" 2>/dev/null)" "$(cat "$d/instance_count" 2>/dev/null)"
done
printf '\n--- looking for Gigabyte WMBC/WMBD/event GUIDs\n'
for g in ABBC0F6F-8EA1-11D1-00A0-C90629100000 ABBC0F75-8EA1-11D1-00A0-C90629100000 ABBC0F72-8EA1-11D1-00A0-C90629100000; do
  low=$(echo "$g" | tr 'A-Z' 'a-z')
  if [ -e "/sys/bus/wmi/devices/$g" ] || [ -e "/sys/bus/wmi/devices/$low" ]; then echo "$g : PRESENT"; else echo "$g : absent"; fi
done

sec "AORUS-LAPTOP DRIVER"
run lsmod
printf '\n--- module present?\n'
modinfo aorus-laptop 2>&1 | head -20
run dkms status
run ls -la /sys/devices/platform/aorus_laptop
for n in fan_mode fan_custom_speed fan_pwm fan_curve_index fan_curve_data charge_mode charge_limit gpu_boost battery_cycle power_on_time light_sensor usb_charge_s3_toggle usb_charge_s4_toggle; do
  cat_if "/sys/devices/platform/aorus_laptop/$n"
done
run bash -c 'ls -la /sys/devices/platform/aorus_laptop/hwmon/*/'

sec "HWMON TREE"
for h in /sys/class/hwmon/hwmon*; do
  printf '\n%s  name=%s  driver=%s\n' "$h" "$(cat "$h/name" 2>/dev/null)" "$(basename "$(readlink -f "$h/device/driver" 2>/dev/null)" 2>/dev/null)"
  for f in "$h"/temp*_label "$h"/temp*_input "$h"/fan*_input "$h"/fan*_label "$h"/pwm* "$h"/power*_input "$h"/in*_input "$h"/curr*_input; do
    [ -f "$f" ] || continue
    printf '    %-40s = %s\n' "$(basename "$f")" "$(cat "$f" 2>/dev/null)"
  done
done
run sensors

sec "THERMAL ZONES / PLATFORM PROFILE"
for z in /sys/class/thermal/thermal_zone*; do
  [ -e "$z" ] || continue
  printf '%s  type=%s  temp=%s\n' "$z" "$(cat "$z/type" 2>/dev/null)" "$(cat "$z/temp" 2>/dev/null)"
done
cat_if /sys/firmware/acpi/platform_profile
cat_if /sys/firmware/acpi/platform_profile_choices

sec "CPU / INTEL_PSTATE"
run lscpu
for f in status max_perf_pct min_perf_pct no_turbo turbo_pct num_pstates hwp_dynamic_boost; do
  cat_if "/sys/devices/system/cpu/intel_pstate/$f"
done
printf '\n--- cpu0 cpufreq\n'
for f in scaling_driver scaling_governor scaling_available_governors scaling_min_freq scaling_max_freq cpuinfo_min_freq cpuinfo_max_freq energy_performance_preference energy_performance_available_preferences; do
  cat_if "/sys/devices/system/cpu/cpu0/cpufreq/$f"
done
printf '\n--- online cpus: %s\n' "$(cat /sys/devices/system/cpu/online)"

sec "RAPL / POWERCAP"
run ls -1 /sys/class/powercap
for d in /sys/class/powercap/*/; do
  [ -e "$d/name" ] || continue
  printf '\n%s name=%s enabled=%s\n' "$d" "$(cat "$d/name" 2>/dev/null)" "$(cat "$d/enabled" 2>/dev/null)"
  for c in "$d"constraint_*; do
    [ -f "$c" ] || continue
    printf '    %-46s = %s\n' "$(basename "$c")" "$(cat "$c" 2>/dev/null)"
  done
done
printf '\n--- MSR access\n'
lsmod | grep -q '^msr' && echo "msr module loaded" || echo "msr module NOT loaded"
command -v rdmsr >/dev/null && echo "msr-tools present" || echo "msr-tools absent"
[ -e /dev/cpu/0/msr ] && echo "/dev/cpu/0/msr exists" || echo "/dev/cpu/0/msr absent"

sec "NVIDIA GPU"
run nvidia-smi --query-gpu=name,driver_version,vbios_version,temperature.gpu,power.draw,persistence_mode --format=csv
run nvidia-smi -q -d POWER
run nvidia-smi -q -d SUPPORTED_CLOCKS
run nvidia-smi --query-gpu=name,driver_version,vbios_version,power.limit,power.default_limit,power.min_limit,power.max_limit,clocks.max.sm --format=csv
run bash -c 'ls -1 /proc/driver/nvidia/gpus/*/ 2>/dev/null'
run prime-select query

sec "EMBEDDED CONTROLLER"
run ls -la /sys/kernel/debug/ec
lsmod | grep -q ec_sys && echo "ec_sys loaded" || echo "ec_sys NOT loaded"
printf 'CONFIG_ACPI_EC_DEBUGFS: '; grep -h ACPI_EC_DEBUGFS /boot/config-"$(uname -r)" 2>/dev/null || echo unknown
run ls -la /proc/acpi/call
lsmod | grep -q acpi_call && echo "acpi_call loaded" || echo "acpi_call NOT loaded"

sec "ACPI TABLES (WMI method names)"
if command -v iasl >/dev/null && [ -r /sys/firmware/acpi/tables/DSDT ]; then
  TMPD=$(mktemp -d)
  cp /sys/firmware/acpi/tables/DSDT "$TMPD/dsdt.dat" 2>/dev/null
  ( cd "$TMPD" && iasl -d dsdt.dat >/dev/null 2>&1 )
  if [ -f "$TMPD/dsdt.dsl" ]; then
    echo "DSDT decompiled ok ($(wc -l < "$TMPD/dsdt.dsl") lines)"
    grep -n "WMBC\|WMBD\|_WDG\|AMW0\|Method (WM" "$TMPD/dsdt.dsl" | head -40
  else
    echo "iasl decompile failed"
  fi
  rm -rf "$TMPD"
else
  echo "iasl not installed or DSDT unreadable (install acpica-tools for this section)"
fi

sec "EXISTING TOOLING ON THIS MACHINE"
printf '\n--- leftover tooling in the home directory\n'
for d in nbfc tuxedo; do
  if [ -d "$HOME/$d" ]; then echo "home directory has $d/"; else echo "no $d/ in home directory"; fi
done
run bash -c 'systemctl list-unit-files --no-pager | grep -i "nbfc\|tuxedo\|fan\|thermald\|power-profiles\|tlp"'
run bash -c 'systemctl is-active nvidia-powerd aorusctld aorusctl-web 2>&1'

sec "END"
printf 'report complete\n'
} > "$OUT" 2>&1

# Scrub the two things that identify the person rather than the laptop.
HOST=$(hostname 2>/dev/null)
[ -n "${USER:-}" ] && sed -i "s/\b${USER}\b/USER/g" "$OUT" 2>/dev/null
[ -n "$HOST" ] && sed -i "s/\b${HOST}\b/HOSTNAME/g" "$OUT" 2>/dev/null
sed -i 's|/home/[^/ :]*|/home/USER|g' "$OUT" 2>/dev/null

echo "Report written to: $OUT"
echo "Size: $(wc -c < "$OUT") bytes"
