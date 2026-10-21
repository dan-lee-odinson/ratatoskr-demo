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

"""Serve Strands Decider with --vision on 127.0.0.1:8001, on the CPU, capped at 4 threads (Dan's ruling 47).

The photo path's image gate. Run it with the vision venv's Python (strands-decider 0.2.0 with its vision extra:
`python -m pip install "strands-decider[vision]==0.2.0"` in `.venv-decider-vision`), beside the published server:

    .venv-decider-vision/Scripts/python tools/serve_vision.py --pid-file ledger/vision.pid --max-commit-gb 16

It is `strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --vision --port 8001 --device cpu`, set up
as tools/serve_decider.py sets up the published server, whose behaviour this does not change: the same thread
cap, below-normal priority, pid file and memory guard, taken from it. The vision tower is the one inside the
checkpoint's base model (Qwen3.5-2B-Base, already in the Hugging Face cache); no weights change, and the
checkpoint was not trained on images.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("serve_decider", Path(__file__).resolve().parent / "serve_decider.py")
serve_decider = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve_decider)  # sets the thread variables before torch is imported, as it does there

PORT = 8001


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=serve_decider.CHECKPOINT)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--pid-file", default=None, help="write this process's id here (for a clean stop)")
    parser.add_argument("--max-commit-gb", type=float, default=None,
                        help="exit (code 3) once this process's committed memory passes this many GB")
    args = parser.parse_args(argv)

    import torch

    torch.set_num_threads(serve_decider.THREADS)
    torch.set_num_interop_threads(1)
    serve_decider.lower_priority()
    if args.max_commit_gb is not None:
        serve_decider.watch_memory(args.max_commit_gb)
    if args.pid_file:
        serve_decider.write_pid_file(args.pid_file)
    print(f"serve_vision: pid {os.getpid()}, torch {torch.__version__}, intra-op threads {torch.get_num_threads()}, "
          f"OMP_NUM_THREADS={os.environ['OMP_NUM_THREADS']}, vision on", flush=True)

    from strands_decider.server import serve

    serve(args.checkpoint, host="127.0.0.1", port=args.port, device="cpu", use_prefix_cache=True, model_name=None,
          vision=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
