Locally: from the repository root, `pip install -e ".[notebook]"`, then `jupyter lab`, and open `notebooks/gatekeeper_triage.ipynb`.
On SageMaker: clone the repository in SageMaker Studio, open `notebooks/gatekeeper_triage.ipynb`, and run it from the top.
It needs no keys and makes no Bedrock call; without a Strands Decider server it replays the answers Decider gave in the bench.
