"""Test rig for aorusctl.

Builds a throwaway sysfs tree, points the module's path constants at it, and
swaps subprocess execution for a recorder, so tests can assert on both the
values written to the kernel and the exact commands that would have run.

Standard library only, to match the tool itself. Run from the repository root:

    python3 -m unittest discover -s tests -t .
"""

import importlib.machinery
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "aorusctl"

_counter = 0


def load_module():
    """A fresh copy of the tool, so module globals never leak between tests."""
    global _counter
    _counter += 1
    name = f"aorusctl_under_test_{_counter}"
    loader = importlib.machinery.SourceFileLoader(name, str(TOOL))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


class RunRecorder:
    """Stands in for aorusctl.run(). Records argv, replays canned output."""

    def __init__(self):
        self.calls = []
        self._replies = []

    def reply(self, contains, stdout="", returncode=0, stderr=""):
        self._replies.append((contains, SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr)))
        return self

    def __call__(self, cmd, check=False, timeout=15):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        for contains, resp in self._replies:
            if contains in joined:
                return resp
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # -- assertions used by the tests

    def argv(self, program):
        """Every recorded call to `program`."""
        return [c for c in self.calls if Path(c[0]).name == program]

    def ran(self, *fragments):
        """True when some call contains all of the given argv fragments."""
        for c in self.calls:
            joined = " ".join(c)
            if all(f in joined for f in fragments):
                return True
        return False


class Rig:
    """A fake machine. Construct, use `.ac` as the module, call `.close()`."""

    def __init__(self, *, fans=True, rapl=True, pstate=True, coretemp=True,
                 nvidia=False, config=None, root=True, dual_rapl=True):
        self.root = Path(tempfile.mkdtemp(prefix="aorusctl-test-"))
        self.run = RunRecorder()
        self._build(fans=fans, rapl=rapl, pstate=pstate, coretemp=coretemp,
                    dual_rapl=dual_rapl)
        self.ac = load_module()
        self._patch(nvidia=nvidia, config=config, root=root)

    # -- building the tree

    def w(self, rel, value):
        p = self.root / str(rel).lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{value}\n")
        return p

    def read(self, rel):
        return (self.root / str(rel).lstrip("/")).read_text().strip()

    def exists(self, rel):
        return (self.root / str(rel).lstrip("/")).exists()

    def _build(self, *, fans, rapl, pstate, coretemp, dual_rapl):
        self.w("sys/class/dmi/id/product_name", "AORUS 16X ASG")
        self.w("sys/class/dmi/id/sys_vendor", "GIGABYTE")

        if fans:
            a = "sys/devices/platform/aorus_laptop"
            for k, v in {"fan_mode": 0, "fan_custom_speed": 114, "fan_pwm": 96,
                         "fan_curve_index": 0, "fan_curve_data": "55 127",
                         "charge_mode": 0, "charge_limit": 100, "gpu_boost": 1,
                         "battery_cycle": 53}.items():
                self.w(f"{a}/{k}", v)
            h = f"{a}/hwmon/hwmon5"
            for k, v in {"name": "aorus_laptop", "fan1_input": 3180,
                         "fan2_input": 3075, "pwm1": 96, "pwm2": 0,
                         "temp1_input": 61000, "temp2_input": 46000,
                         "temp3_input": 61000}.items():
                self.w(f"{h}/{k}", v)

        if rapl:
            # Mirrors the real machine: the MMIO domain caps PL1 at 55 W while
            # the MSR domain reads 200 W, and neither publishes a short_term max.
            domains = [("intel-rapl:0", 200), ("intel-rapl-mmio:0", 55)]
            if not dual_rapl:
                domains = domains[:1]
            for dom, pl1 in domains:
                d = f"sys/class/powercap/{dom}"
                self.w(f"{d}/name", "package-0")
                self.w(f"{d}/enabled", 1)
                self.w(f"{d}/energy_uj", 123456789)
                self.w(f"{d}/max_energy_range_uj", 262143328850)
                self.w(f"{d}/constraint_0_name", "long_term")
                self.w(f"{d}/constraint_0_power_limit_uw", pl1 * 1000000)
                self.w(f"{d}/constraint_0_max_power_uw", 55000000)
                self.w(f"{d}/constraint_0_time_window_us", 27983872)
                self.w(f"{d}/constraint_1_name", "short_term")
                self.w(f"{d}/constraint_1_power_limit_uw", 157000000)
                self.w(f"{d}/constraint_1_max_power_uw", 0)
                self.w(f"{d}/constraint_1_time_window_us", 2440)

        if pstate:
            p = "sys/devices/system/cpu/intel_pstate"
            for k, v in {"status": "active", "max_perf_pct": 100,
                         "min_perf_pct": 17, "no_turbo": 0}.items():
                self.w(f"{p}/{k}", v)
            for i in range(4):
                c = f"sys/devices/system/cpu/cpu{i}/cpufreq"
                self.w(f"{c}/scaling_cur_freq", 2800000 + i * 1000)
                self.w(f"{c}/scaling_governor", "powersave")
                self.w(f"{c}/scaling_available_governors", "performance powersave")
                self.w(f"{c}/energy_performance_preference", "balance_performance")
                self.w(f"{c}/energy_performance_available_preferences",
                       "default performance balance_performance balance_power power")

        if coretemp:
            c = "sys/class/hwmon/hwmon3"
            self.w(f"{c}/name", "coretemp")
            self.w(f"{c}/temp1_label", "Package id 0")
            self.w(f"{c}/temp1_input", 64000)
            for i, t in enumerate([62000, 65000], start=2):
                self.w(f"{c}/temp{i}_label", f"Core {i - 2}")
                self.w(f"{c}/temp{i}_input", t)

        self.w("sys/firmware/acpi/platform_profile", "balanced")
        self.w("sys/firmware/acpi/platform_profile_choices",
               "low-power balanced performance")

    # -- wiring the module to the tree

    def _patch(self, *, nvidia, config, root):
        ac = self.ac
        R = lambda p: self.root / str(p).lstrip("/")

        ac.AORUS = R("sys/devices/platform/aorus_laptop")
        ac.POWERCAP = R("sys/class/powercap")
        ac.PSTATE = R("sys/devices/system/cpu/intel_pstate")
        ac.CPUBASE = R("sys/devices/system/cpu")
        ac.HWMON = R("sys/class/hwmon")
        ac.PLATFORM_PROFILE = R("sys/firmware/acpi/platform_profile")
        ac.STATE_DIR = self.root / "var/lib/aorusctl"
        ac.STATE_FILE = ac.STATE_DIR / "state.json"
        ac.YIELD_FLAG = self.root / "run/aorusctl-daemon-yielded"
        ac.CONFIG_FILE = Path(config) if config else REPO / "config.toml"
        ac.STATE = ac.State()

        # A handful of reads use literal paths; send those into the tree too.
        real_rd = ac.rd

        def rd(path, default=None, strip=True):
            s = str(path)
            if s.startswith("/sys") or s.startswith("/proc"):
                v = real_rd(R(s), None, strip)
                if v is not None:
                    return v
                return default
            return real_rd(path, default, strip)

        def rdint(path, default=None):
            v = rd(path)
            if v is None:
                return default
            try:
                return int(str(v).split()[0])
            except (ValueError, IndexError):
                return default

        ac.rd, ac.rdint = rd, rdint

        # Record every sysfs write so tests can assert that a rejected input
        # touched nothing at all.
        real_wr = ac.wr
        self.writes = []

        def wr(path, value):
            self.writes.append((Path(path).name, str(value)))
            return real_wr(path, value)

        ac.wr = wr
        ac.run = self.run

        # Real sleeps only make the suite slow; the read-back delay after a GPU
        # write has nothing to wait for here.
        import time as _time
        self.slept = []

        def _sleep(secs):
            self.slept.append(secs)

        ac.time = SimpleNamespace(sleep=_sleep, monotonic=_time.monotonic,
                                  time=_time.time)
        ac.is_root = lambda: root
        if root:
            ac.need_root = lambda: None
        ac.daemon_active = lambda: False

        # Always stub this. Leaving it to the host means the suite passes on a
        # machine without an NVIDIA GPU and fails on one with it, which is
        # exactly backwards for a tool aimed at gaming laptops.
        import shutil as _shutil
        real_which = _shutil.which

        def which(name, *a, **k):
            if name == "nvidia-smi":
                return "/usr/bin/nvidia-smi" if nvidia else None
            return real_which(name, *a, **k)

        ac.shutil = SimpleNamespace(which=which)
        ac.FANS = ac.CPU = ac.GPU = None

    # -- convenience

    @property
    def fans(self):
        return self.ac.hw()[0]

    @property
    def cpu(self):
        return self.ac.hw()[1]

    @property
    def gpu(self):
        return self.ac.hw()[2]

    def cli(self, *argv):
        """Run the CLI, returning (exit code, stdout+stderr)."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = self.ac.main(list(argv))
        return code, buf.getvalue()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class RigTest(unittest.TestCase):
    """Base class that builds a rig per test and tears it down after."""

    RIG_KWARGS = {}

    def setUp(self):
        self.rig = Rig(**self.RIG_KWARGS)
        self.ac = self.rig.ac
        self.addCleanup(self.rig.close)
