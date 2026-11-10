"""Run the supplied backfill scenario at the brief's 20,000-item size."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "starter-kit"))
from harness_lib.__main__ import print_result
from harness_lib.procs import ProcessControl
from harness_lib.scenarios import Context, backfill, run_scenario
from harness_lib.service import ServiceClient, SidecarClient
from memkit.sidecar import DEFAULT_CONFIG

context = Context(service=ServiceClient("http://localhost:8080"),
                  sidecar=SidecarClient("http://localhost:7070"),
                  procs=ProcessControl(".", None, None), policy="permanent", seed=73,
                  sidecar_defaults=dict(DEFAULT_CONFIG), log=print)
result = run_scenario(context, "backfill-20000", lambda run: backfill(run, backfill_items=20000))
# Reference lineage mismatches can contain thousands of item IDs. Keep failures
# readable without changing the pass/fail result or the checks being performed.
result.problems = [problem[:500] for problem in result.problems]
print_result(result)
sys.exit(0 if result.passed else 1)
