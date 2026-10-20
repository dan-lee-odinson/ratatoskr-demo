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

"""Does Strands Decider's server contact the Hugging Face Hub when it starts? (Dan's ruling 26, claim 9.)

    .venv-decider/Scripts/python tools/hf_offline_check.py REPO_ID [REPO_ID ...]

Strands Decider loads its checkpoint with huggingface_hub.snapshot_download and its base model with
transformers' from_pretrained, both by repository id. This asks the Hub library for each repository the
same way, with every network connection refused, and prints one line per repository: whether it loaded
from the local cache, and which hosts the library tried to reach. Nothing is downloaded and nothing is
sent: the refusal happens before any connection opens. Run it with HF_HUB_OFFLINE=1 in the environment
to see that no host is tried at all.
"""

from __future__ import annotations

import socket
import sys

attempts: list[str] = []


def _refuse_lookup(host, *args, **kwargs):
    attempts.append(str(host))
    raise socket.gaierror("hf_offline_check: network refused")


def _refuse_connect(self, address):
    attempts.append(str(address[0] if isinstance(address, tuple) else address))
    raise OSError("hf_offline_check: network refused")


socket.getaddrinfo = _refuse_lookup
socket.socket.connect = _refuse_connect

from huggingface_hub import snapshot_download  # noqa: E402  after the refusal; reads HF_HUB_OFFLINE now


def main(repos: list[str]) -> int:
    for repo in repos:
        before = len(attempts)
        try:
            snapshot_download(repo)
            result = "loaded from the cache"
        except Exception as exc:  # report it, do not hide it: the caller checks the line
            result = f"not loaded ({type(exc).__name__})"
        hosts = sorted(set(attempts[before:]))
        print(f"{repo}: {result}; contacted: {', '.join(hosts) if hosts else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
