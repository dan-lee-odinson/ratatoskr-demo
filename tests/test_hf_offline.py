"""Dan's ruling 26, claim 9 (Terms of use, "What it talks to"): Strands Decider's server contacts the Hugging
Face Hub when it starts, and with HF_HUB_OFFLINE=1 it contacts nothing and loads from the local cache.

Run against this machine's .venv-decider and Hugging Face cache, through tools/hf_offline_check.py, which
refuses every connection (nothing is downloaded or sent). Skipped where either is missing, as in a clean
clone: the server's venv and its 4.4 GB of weights are not part of the repository.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DECIDER_PYTHON = REPO / ".venv-decider" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
HUB_CACHE = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")
REPOS = ("StrandsAgents/strands-decider-2B-hobson-v21", "Qwen/Qwen3.5-2B-Base")
CACHED = all((HUB_CACHE / ("models--" + repo.replace("/", "--"))).is_dir() for repo in REPOS)

pytestmark = pytest.mark.skipif(not (DECIDER_PYTHON.exists() and CACHED),
                                reason="needs .venv-decider and the Decider weights in the Hugging Face cache")


def check(offline: bool) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
    done = subprocess.run([str(DECIDER_PYTHON), str(REPO / "tools" / "hf_offline_check.py"), *REPOS],
                          env=env, capture_output=True, text=True, timeout=180, cwd=REPO)
    assert done.returncode == 0, done.stderr[-500:]
    return done.stdout.splitlines()


def test_the_decider_server_s_loader_contacts_the_hub_and_makes_no_contact_with_hf_hub_offline():
    online, offline = check(offline=False), check(offline=True)
    assert [line.split(":")[0] for line in online] == list(REPOS)
    assert all(line.endswith("loaded from the cache; contacted: huggingface.co") for line in online)
    assert all(line.endswith("loaded from the cache; contacted: none") for line in offline)
