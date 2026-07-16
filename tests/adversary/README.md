# tests/adversary/

Tests written by the **robustness harness** (`.github/workflows/robustness.yml`,
documented in `../../ROBUSTNESS.md`). These are not hand-written — a
different-model adversary agent generates them when it finds a robustness defect.

**A failing test here is a feature, not a bug.** Each one reproduces a real
weakness the adversary found: an unhandled crash, a 500 where a 4xx belongs, a
hang, a silently-wrong answer, or clearly-invalid input accepted as valid. They
arrive via a `robustness/run-N` PR alongside a report in `reports/adversary/`.

This directory is **excluded from CI** (`pytest --ignore=tests/adversary`),
because unfixed findings are expected to fail. When you fix a defect, promote its
test into the main suite so it becomes a permanent regression guard.

Fixtures live in `conftest.py` and are pre-provided/verified: an in-process
`client`, a `stub_matrix_provider` (no OSRM), `VALID_CSV`, and an `upload()`
helper.
