import os
from pathlib import Path
import subprocess
import sys


def test_phase6_contract_script_refuses_unapproved_paid_execution():
    environment = dict(os.environ)
    environment.pop("RUN_OPENAI_PHASE6_CONTRACT_TEST", None)
    environment.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "scripts/phase6_openai_contract.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing paid execution" in result.stderr
