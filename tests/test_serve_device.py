"""Dan's ruling 48: tools/serve_decider.py's --device flag. The flag reaches the model, cuda on a machine without it
fails at startup with a clear message and no fallback, and the default is the published CPU path, unchanged.
torch and the server are stand-ins: the courier's venv has neither."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("serve_decider_under_test", REPO / "tools" / "serve_decider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls, threads = {}, {}
    cuda = types.SimpleNamespace(is_available=lambda: calls.get("has_cuda", False),
                                 get_device_name=lambda i: "NVIDIA GeForce RTX 5050 Laptop GPU",
                                 memory_allocated=lambda: 4 * 1024 ** 3, memory_reserved=lambda: 5 * 1024 ** 3,
                                 max_memory_reserved=lambda: 6 * 1024 ** 3)
    torch = types.SimpleNamespace(__version__="2.14.1+fake", cuda=cuda,
                                  set_num_threads=lambda n: threads.update(intra=n),
                                  set_num_interop_threads=lambda n: threads.update(inter=n),
                                  get_num_threads=lambda: threads["intra"], get_num_interop_threads=lambda: 1)

    class App:
        def middleware(self, kind):
            calls["middleware"] = kind
            return lambda fn: fn

    server = types.ModuleType("strands_decider.server")
    server.serve = lambda checkpoint, **kw: calls.update(serve=dict(checkpoint=checkpoint, **kw))
    server.create_app = lambda checkpoint, **kw: calls.update(create_app=dict(checkpoint=checkpoint, **kw)) or App()
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.run = lambda app, **kw: calls.update(uvicorn=kw)
    for name, value in (("torch", torch), ("strands_decider", types.ModuleType("strands_decider")),
                        ("strands_decider.server", server), ("uvicorn", uvicorn)):
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(module, "lower_priority", lambda: None)
    return module, calls


def test_the_cuda_flag_reaches_the_model_and_health_names_the_card(launcher, capsys):
    module, calls = launcher
    calls["has_cuda"] = True
    assert module.main(["--device", "cuda"]) == 0
    assert calls["create_app"]["device"] == "cuda" and "serve" not in calls
    assert calls["uvicorn"] == {"host": "127.0.0.1", "port": 8000, "workers": 1}
    assert calls["middleware"] == "http"  # /health gains the card and its memory
    assert "device cuda, NVIDIA GeForce RTX 5050 Laptop GPU, 4.0 GB allocated" in capsys.readouterr().out


def test_cuda_on_a_machine_without_it_fails_at_startup_and_never_falls_back(launcher, capsys):
    module, calls = launcher
    assert module.main(["--device", "cuda"]) == 2
    assert "serve" not in calls and "create_app" not in calls and "uvicorn" not in calls
    err = capsys.readouterr().err
    assert "--device cuda" in err and "no CUDA device" in err and "--device cpu" in err


def test_the_default_is_the_published_cpu_path_unchanged(launcher):
    module, calls = launcher
    assert module.main([]) == 0
    assert calls["serve"] == {"checkpoint": "StrandsAgents/strands-decider-2B-hobson-v21", "host": "127.0.0.1",
                              "port": 8000, "device": "cpu", "use_prefix_cache": True, "model_name": None}
    assert "create_app" not in calls and "middleware" not in calls
    with pytest.raises(SystemExit):
        module.main(["--device", "mps"])  # cpu or cuda, nothing else
