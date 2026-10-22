# Photo scene

**Decide what a picture is before anything acts on it.** A photograph of a note or a whiteboard can carry a promise;
a photograph of a menu or a poster carries information worth keeping and nothing owed. Ask a vision model one typed
question about the picture, and let code, not the model, decide what follows: a commitment goes on to the extraction
step, reference material never reaches it, and when the model is unsure, nothing runs until the owner answers.

1. **One read.** `SceneClient().decide(picture, text)` sends one `POST /v1/systemone` to a
   [Strands Decider](https://github.com/strands-labs/strands-decider) vision server (strands-decider 0.2.0, served
   with `--vision`) on the same machine: the picture, its text (what an OCR on the laptop read from it, or `""`) as
   the state, and the scene question, "What does this picture represent?", with two options, `commitment` and
   `reference`. It returns the answer, its confidence, and each option's probability.
2. **A confidence floor.** At or above the floor the answer acts; under it the answer is `unsure`. The floor, 0.70,
   is Ratatoskr Demo's floor for a choice question in its routing read, used here as a starting point: it was not
   tuned on pictures.
3. **The owner handoff.** `handoff(scene)` turns the answer into a decision: `commitment` → extract, `reference` →
   keep, `unsure` → ask the owner, with the two choices. `apply_owner_answer(decision, answer)` takes the owner's
   recorded answer, which then acts as an answer above the floor would, recorded as the owner's; the first answer
   stands.
4. **The wiring rule.** `route(decision, extract, keep)`: reference never calls the extractor, commitment does, and
   unsure calls nothing until the owner answers.

## The three outcomes

| answer | what runs | in Ratatoskr Demo |
|---|---|---|
| `commitment` | the extractor | the picture's text goes on to the extraction on Amazon Bedrock, the routing read and Todoist |
| `reference` | not the extractor | the text is kept as a note on the laptop; nothing goes to a cloud service or to Todoist |
| `unsure` | nothing, until the owner answers | the owner is asked on Telegram, with two buttons, Commitment and Reference |

## What the checkpoint can and cannot do

Strands Decider was not trained on images: a vision server answers with the vision tower inside its base model.
Ratatoskr Demo measured it on rendered pictures, and its photo reports give the numbers:

- **Reference is recognized reliably.** In the final photo run, all 6 pictures labelled reference were answered
  reference, above the floor.
- **Commitments are mostly unsure.** Of the 9 pictures labelled commitment, 1 was answered commitment above the
  floor and 8 were unsure; none was answered reference. Expect the owner to be asked often: that is the handoff
  doing its work, not a fault.
- **The direction of a promise is not readable from a picture.** On mirrored pairs (the same wording, the opposite
  direction), one pair got identical probabilities for both directions (the second addendum). That is why the
  question is two-way, and who owes whom is left to the extraction and the owner.
- **Rendered pictures only.** Phone photographs are not measured yet.

The floor is a starting point: measure on your own pictures before trusting it.

## The files

| file | what it is |
|---|---|
| `questions.toml` | the scene question, its two options and their criteria, and the floor |
| `scene.py` | a standard-library client (Pillow to read the picture): `SceneClient.decide()` returning a `Scene` |
| `handoff.py` | `handoff()`, `apply_owner_answer()`, and `route()`, the wiring rule |
| `example.py` | two bundled pictures, one reference and one commitment, end to end, with no keys and no cloud |
| `pictures/` | those two pictures: fictional, rendered, from Ratatoskr Demo's evaluation set |

The vision server runs from an environment of its own (its vision extra pins a newer transformers):

```bash
pip install "strands-decider[vision]==0.2.0" Pillow
```
```bash
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --vision --port 8001 --device cpu
```
```bash
python example.py
```

The example prints, for each picture, the answer, its confidence and the floor, the action, and the cloud calls:
`0` for reference, "would call" for commitment, and `0` until the owner answers for unsure.

## In front of an extraction tool

With an agent whose model is `openai.gpt-oss-20b` on Amazon Bedrock through the Strands OpenAI-compatible provider
(`pip install "strands-agents[openai]==1.61.1"`, and a Bedrock API key in `BEDROCK_API_KEY`), decide the scene
before the agent runs, as the first recipe gates the source text, and let only a commitment reach it:

```python
from strands import Agent
from handoff import apply_owner_answer, handoff, route
from scene import SceneClient
from strands_hook import GatekeeperHook, add_task, bedrock_model  # from patterns/gatekeeper_triage/

decision = handoff(SceneClient().decide("whiteboard.jpg", ocr_text))  # 1. the scene, before the agent runs
hook = GatekeeperHook(tools={"add_task"})  # 2. then each add_task call's arguments, as in the first recipe
agent = Agent(model=bedrock_model(api_key), tools=[add_task], hooks=[hook])
route(decision, extract=lambda: agent(ocr_text), keep=lambda: keep_note(ocr_text))
# unsure: nothing ran. Ask the owner; then route(apply_owner_answer(decision, their_answer), ...)
```

The first recipe's hook is a Strands Agents `BeforeToolCallEvent` hook that gates each pending tool call's arguments
and sets `cancel_tool` on a hold; on the same agent it still gates each extracted task.

## Adapting it

- **Rename the options** and rewrite their criteria for your domain, and change `ACTIONS` in `handoff.py` to match.
  Keep each option one kind of thing, and keep them distinct, or the confidence spreads across them.
- **Tune the floor** on your own pictures.
- **Fail closed.** `decide()` raises `SceneUnavailable` when the server cannot be asked or its reply is unusable: run
  nothing on a picture whose scene is not known.
- **Privacy comes first.** The scene question does not decide whether a picture may leave the machine. In Ratatoskr
  Demo an image gate on the picture and the first recipe's gate on its text run before it, and the vision server
  runs on the same machine.

## Measured

See [`bench/IMAGE_EVAL_2026-10-22_final.md`](../../bench/IMAGE_EVAL_2026-10-22_final.md), the final photo report.
