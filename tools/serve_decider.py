# Copyright 2026 Dan Lee-Odinson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Serve Strands Decider on 127.0.0.1:8000, on the CPU, capped at 4 threads.

Run it with the Decider venv's Python (strands-decider 0.1.0 and torch live there,
not in the courier's venv):

    .venv-decider/Scripts/python tools/serve_decider.py            (Windows)
    .venv-decider/bin/python tools/serve_decider.py                (elsewhere)

It is `strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --port 8000
--device cpu` with the thread cap applied first. strands-decider 0.1.0 has no threads
flag and never calls torch.set_num_threads, so the cap is set here: the OpenMP/MKL
environment variables before torch is imported, then torch.set_num_threads(4) in the
same process the server runs in. The process also asks the OS for below-normal
priority, so it yields the CPU to anything else running on the machine.

With --max-commit-gb, a watcher thread checks the process's committed memory every 30 s and
exits the server (code 3, with a line saying why) once it passes the limit, so it is restarted
fresh rather than left to hang: on 17 Oct 2026 0.1.0 hung three times well into long runs of
reads, its commit growing far past its start-up size (BUILD_REPORT, "Decider hang (finding)").

It binds to 127.0.0.1 only and has no authentication: only this machine talks to it.
The first run downloads the base model (Qwen/Qwen3.5-2B-Base, about 4.5 GB) and the
v21 adapter (about 92 MB) into the Hugging Face cache.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

THREADS = 4
CHECKPOINT = "StrandsAgents/strands-decider-2B-hobson-v21"

for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = str(THREADS)
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def lower_priority() -> None:
    try:
        import psutil

        process = psutil.Process()
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except Exception as exc:  # best effort: the thread cap is the guarantee
        print(f"serve_decider: could not lower the priority ({type(exc).__name__})", file=sys.stderr)


def write_pid_file(path: str) -> None:
    """This process's id, for a clean stop by exact PID. The folder is created first: on a fresh clone
    `ledger/` does not exist until the courier has run (Dan's ruling 34)."""
    pid_file = Path(path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")


def over_limit(commit_bytes: int, limit_gb: float | None) -> bool:
    return limit_gb is not None and commit_bytes > limit_gb * 1024 ** 3


def watch_memory(limit_gb: float, every: float = 30.0) -> None:
    """Exit the whole process once its committed memory passes `limit_gb` (best effort: needs psutil)."""
    try:
        import psutil
    except ImportError:
        print("serve_decider: psutil missing, --max-commit-gb not enforced", file=sys.stderr, flush=True)
        return
    process = psutil.Process()

    def run() -> None:
        while True:
            time.sleep(every)
            info = process.memory_info()
            commit = getattr(info, "private", info.rss)
            if over_limit(commit, limit_gb):
                print(f"serve_decider: committed memory {commit / 1024 ** 3:.1f} GB passed --max-commit-gb "
                      f"{limit_gb:g}; exiting so it can be started fresh", flush=True)
                os._exit(3)

    threading.Thread(target=run, name="memory-guard", daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=CHECKPOINT)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--pid-file", default=None, help="write this process's id here (for a clean stop)")
    parser.add_argument("--no-prefix-cache", action="store_true", help="serve without Decider's prefix cache")
    parser.add_argument("--max-commit-gb", type=float, default=None,
                        help="exit (code 3) once this process's committed memory passes this many GB")
    args = parser.parse_args()

    import torch

    torch.set_num_threads(THREADS)
    torch.set_num_interop_threads(1)
    lower_priority()
    if args.max_commit_gb is not None:
        watch_memory(args.max_commit_gb)
    if args.pid_file:
        write_pid_file(args.pid_file)
    print(f"serve_decider: pid {os.getpid()}, torch {torch.__version__}, intra-op threads "
          f"{torch.get_num_threads()}, inter-op threads {torch.get_num_interop_threads()}, "
          f"OMP_NUM_THREADS={os.environ['OMP_NUM_THREADS']}", flush=True)

    from strands_decider.server import serve

    serve(args.checkpoint, host="127.0.0.1", port=args.port, device="cpu",
          use_prefix_cache=not args.no_prefix_cache, model_name=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
