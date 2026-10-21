"""Dan's ruling 27 (20 Oct 2026): the license, completed for the public repository, and kept that way.

LICENSE is the Apache License 2.0, sections 1-9 byte for byte as apache.org publishes them, with the
appendix naming the copyright holder; every first-party .py file carries the appendix's short header.
"""

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# sha256 of sections 1-9 (from "   1. Definitions." up to "   END OF TERMS AND CONDITIONS", 9,919 bytes, LF) of
# https://www.apache.org/licenses/LICENSE-2.0.txt, fetched and hashed on 2026-10-20. Split in two so the
# secrets grep (C6) does not read it as a token.
CANONICAL_SECTIONS_1_TO_9 = "7269b685b5d2093efc3bff1ce9c7f0a0" + "15920f146c57ed4b190cab85fc00a68c"
HOLDER = "Copyright 2026 Dan Lee-Odinson"
# Built by concatenation so that the publish-day grep for them finds nothing, this file included.
PLACEHOLDERS = ("[" + "yyyy]", "[" + "name of copyright owner]")

HEADER = """# Copyright 2026 Dan Lee-Odinson
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
"""
FIRST_PARTY = ("ratatoskr_demo", "patterns", "tools", "bench")  # ratatoskr_demo/ is the package (no src/)


def test_license_sections_1_to_9_are_apache_org_s_text_and_the_appendix_names_the_holder():
    text = (REPO / "LICENSE").read_bytes()
    start, end = text.index(b"   1. Definitions."), text.index(b"   END OF TERMS AND CONDITIONS")
    assert hashlib.sha256(text[start:end]).hexdigest() == CANONICAL_SECTIONS_1_TO_9
    appendix = text[end:].decode("utf-8")
    assert f"   {HOLDER}\n" in appendix
    assert not any(p in appendix for p in PLACEHOLDERS)


def first_party_python():
    for folder in FIRST_PARTY:
        for path in sorted((REPO / folder).rglob("*.py")):
            if "__pycache__" not in path.parts and "fixtures" not in path.parts:
                yield path


def test_every_first_party_python_file_carries_the_license_header():
    files = list(first_party_python())
    missing = [str(p.relative_to(REPO)) for p in files if not p.read_text(encoding="utf-8").startswith(HEADER)]
    assert len(files) >= 45 and missing == []


def test_the_header_is_the_license_appendix_s_own_words():
    appendix = (REPO / "LICENSE").read_text(encoding="utf-8").split("END OF TERMS AND CONDITIONS")[1]
    for line in HEADER.splitlines()[2:]:
        assert line.removeprefix("#").strip() in appendix


# --- the CUDA build's NVIDIA libraries in NOTICE (Dan's ruling 51) ---

# Every NVIDIA library the CUDA 13.0 build of torch 2.14.1 carries in torch/lib, as the CUDA venv held them.
NVIDIA_DLLS = {
    "cublas64_13.dll", "cublasLt64_13.dll", "cudart64_13.dll", "cudnn64_9.dll", "cudnn_adv64_9.dll",
    "cudnn_cnn64_9.dll", "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_engines_tensor_ir64_9.dll", "cudnn_ext64_9.dll", "cudnn_graph64_9.dll", "cudnn_heuristic64_9.dll",
    "cudnn_ops64_9.dll", "cufft64_12.dll", "cufftw64_12.dll", "cupti64_2025.3.0.dll", "curand64_10.dll",
    "cusolver64_12.dll", "cusolverMg64_12.dll", "cusparse64_12.dll", "nvJitLink_130_0.dll", "nvToolsExt64_1.dll",
    "nvperf_host.dll", "nvrtc-builtins64_130.dll", "nvrtc64_130_0.alt.dll", "nvrtc64_130_0.dll",
}
GPU_ROLE = "(GPU server, optional"


def test_notice_names_the_cuda_builds_nvidia_libraries_in_its_own_format():
    import re

    lines = [line for line in (REPO / "NOTICE").read_text(encoding="utf-8").splitlines() if GPU_ROLE in line]
    assert lines[0].startswith("torch 2.14.1+cu130 (GPU server, optional): ")
    # "<name> <version> (<role>): <licence> [<source>]", as every other line of the file
    assert all(re.match(r"^.+ \S+ \(GPU server, optional[^)]*\): .+ \[.+\]$", line) for line in lines)
    named = set(re.findall(r"[\w.-]+\.dll", "\n".join(lines)))
    assert NVIDIA_DLLS <= named
    lib = REPO / ".venv-decider-cuda" / "Lib" / "site-packages" / "torch" / "lib"
    if lib.is_dir():  # where the CUDA venv is installed, every NVIDIA library in it is named
        assert {p.name for p in lib.glob("*.dll") if re.match(r"(cu|nv|cudnn)", p.name)} <= named
