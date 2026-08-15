"""T-021 — measure the weakness DR-001 declared, rather than asserting it away.

DR-001 chose Python knowing cold start is its weakest point against C-4's
1000ms budget, and committed to falsifying that with a measurement. If this
fails, the decision record is superseded, not explained.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET_MS = 1000  # C-4


def _run_once(tmp_path):
    env = dict(os.environ)
    env["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    env["PROMEDIA_CREDENTIAL_STORE"] = str(tmp_path / "creds.json")
    env["PYTHONPATH"] = str(REPO)
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "promedia", "ops", "--json"],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=60,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert proc.returncode == 0, proc.stderr
    return elapsed_ms


def test_cli_cold_start_within_budget(tmp_path, capsys):
    """AC-1: recorded as a number, compared against the budget."""
    _run_once(tmp_path)  # warm the filesystem cache; we measure steady cold start
    samples = sorted(_run_once(tmp_path) for _ in range(5))
    p50 = samples[len(samples) // 2]
    p95 = samples[-1]

    with capsys.disabled():
        print(
            f"\n[T-021] CLI cold start: p50={p50:.0f}ms p95={p95:.0f}ms "
            f"budget={BUDGET_MS}ms (C-4)  samples={[f'{s:.0f}' for s in samples]}"
        )

    assert p95 < BUDGET_MS, (
        f"cold start p95 {p95:.0f}ms exceeds C-4 budget {BUDGET_MS}ms; "
        "DR-001 must be superseded rather than the budget relaxed"
    )


def test_web_framework_not_imported_by_cli(tmp_path):
    """The mechanism that keeps cold start inside budget.

    If FastAPI ends up on the CLI import path, this fails before the timing
    test does — and points at the cause rather than the symptom.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    env["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    probe = (
        "import sys; import promedia.cli; "
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.split('.')[0] in {'fastapi','uvicorn','starlette','jinja2'})))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", "import json;" + probe],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    leaked = json.loads(proc.stdout)
    assert leaked == [], f"web framework imported on the CLI path: {leaked}"


def test_pyyaml_not_imported_by_cli(tmp_path):
    """PyYAML costs ~104ms and only load_ruleset needs it.

    Measured: deferring it moved cold-start p95 from 932ms to 697ms against the
    1000ms budget — from 7% headroom to 30%. This test stops it drifting back
    onto the import path.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    env["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    proc = subprocess.run(
        [sys.executable, "-c", "import sys, promedia.cli; print('yaml' in sys.modules)"],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", "PyYAML is back on the CLI import path"
