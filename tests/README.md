# Tests

Standard library only, same as the tool. From the repository root:

```sh
python3 -m unittest discover -s tests -t tests          # everything
python3 -m unittest discover -s tests -t tests -v       # with names
python3 -m unittest -t tests tests.test_curves          # one file
```

`harness.py` builds a throwaway sysfs tree that mirrors a real AORUS 16X ASG,
points the tool's path constants at it, records every write, and replaces
subprocess execution with a recorder so tests can assert on the exact commands
that would have run. Nothing touches real hardware, and the tests pass on any
machine.

Coverage so far:

| File | What it pins down |
|---|---|
| `test_state.py` | what gets recorded, and that `reset` puts all of it back |
| `test_validation.py` | clamping and range checks on every write path |
| `test_curves.py` | curve parsing, encoding, and that a rejected curve writes nothing |
| `test_profiles.py` | the shipped config, profile application, and how refusals are reported |
| `test_commands.py` | the argv handed to `nvidia-smi` and `systemctl`, and how replies are read |
| `test_daemon.py` | curve interpolation, hysteresis, the thermal guard, and handover |

Bugs these caught on the way in:

- `reset` forced the fans to normal on a machine it had never touched.
- `aorusctl fan curve garbage` ended in a Python traceback.
- `set_curve` re-read the firmware curve on every call, writing the index node
  thirty times instead of fifteen.
- Profiles silently ignored keys the tool did not understand, so a typo looked
  like it had applied.
