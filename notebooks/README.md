Locally: from the repository root, `pip install -e ".[notebook]"`, then `jupyter lab`, and open `notebooks/gatekeeper_triage.ipynb`.
On SageMaker: clone the repository in SageMaker Studio, open `notebooks/gatekeeper_triage.ipynb`, and run it from the top.
It needs no keys and makes no Bedrock call; without a Strands Decider server it replays the answers Decider gave in the bench.
A rendered copy, with the outputs of a live run on 21 Oct 2026: [gatekeeper_triage.html](gatekeeper_triage.html) (`python tools/export_notebook.py` makes it again).
