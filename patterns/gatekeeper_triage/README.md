# Gatekeeper triage

**Triage before action.** Before an agent acts on something a person said (writes a task, sends
a message, files a ticket), ask a System One model typed questions about it, and let code, not
the model, decide whether the action may proceed. Gate the source text first, where there is one.

1. **A tripwire the model cannot override.** A check in code, before any model call: if a listed
   term (a client under NDA, a project name) or one of its listed spellings appears in the text,
   or a word there sounds like the term (Soundex, also on two neighbouring words written
   together, so "Vargrime" and "var grime" catch "Vargrheim"), hold it. No model ever sees it.
   This standard-library Soundex is coarser than the courier's Double Metaphone (the `Metaphone`
   package on PyPI), which anyone wanting the stronger match can swap in.
2. **Gate the source text.** `gate_source_text(text) -> Verdict` asks
   [Strands Decider](https://github.com/strands-labs/strands-decider) one question, `sensitive`,
   about the transcript, note or email itself: split on speaker turns into windows of about 300
   words (Decider reads 4,096 tokens), one read per window, and hold if any window is sensitive or
   unsure. Decider runs on the same machine and keeps nothing, which is why it, and only it, may
   read the source.
3. **One read about the action.** One `POST /v1/systemone` request over the action carries six
   typed questions:

   | question | type | answers |
   |---|---|---|
   | `project` | choice | which area it belongs to |
   | `someone_waiting` | noul | is a specific person (named, or by relationship or role) expecting a reply or a deliverable, date or not? |
   | `hard_deadline` | noul | is there a stated date, time or event after which it is late? |
   | `worth_researching_soon` | noul | worth following up in the next few weeks, with nobody waiting? |
   | `real_commitment` | noul | a concrete commitment, or a pleasantry |
   | `sensitive` | noul | should a person review it first? |

4. **A confidence floor per question.** Decider's answers are calibrated probabilities. (A floor
   protects a question only as far as the model's sureness tracks being right: in Ratatoskr Demo's
   bench it held for the project, commitment and sensitivity questions and not for the deadline
   question; see its RESULTS.md, "Sure and wrong".) Below its
   floor an answer is not used: the project becomes "unsorted". A noul has no confidence field;
   its confidence is `max(p, 1 − p)`.
5. **Priority by a rule in code**, not by the model, walked in order: `someone_waiting` yes →
   high; else `hard_deadline` yes with the deadline within 30 days → high (a later one counts as
   no); else `worth_researching_soon` yes → medium; else low. If the step that would decide
   rests on an unsure answer, the project's default applies. The result records which step set
   it, or which question was unsure (`default:waiting`, `default:deadline`, `default:soon`). The
   client reads no dates: pass `deadline_near=False` to `triage()` when the deadline is more than
   30 days out (Ratatoskr Demo's `ratatoskr_demo/due.py` works it out from the due string and
   the recording's date).
6. **Hold on unsure.** `sensitive` = yes holds. `sensitive` below its floor (at 0.60, p between
   0.40 and 0.60) also holds. If Decider cannot be asked, the action holds too (fail closed),
   unless `[gatekeeper] on_unavailable = "proceed"`.

## When to use it

When an agent turns free text into an action that is hard to take back or visible to others, and
you want a cheap, local, explainable check in front of it: every judgment comes with a number you
can log and a floor you can tune. Decider runs on the CPU at no per-call cost, so the check can run
on every action instead of a sample.

It is not a replacement for authorization or for a person's review: it decides which actions go
to that review.

## The files

| file | what it is |
|---|---|
| `questions.toml` | the five questions, their floors, the project defaults, the tripwire terms, the source window |
| `decider_client.py` | a standard-library client for `POST /v1/systemone`: `gate_source_text()` returning a `Verdict`, and `triage()` returning a typed `Triage` |
| `strands_hook.py` | a Strands Agents `BeforeToolCallEvent` hook: gates each pending tool call's arguments and sets `cancel_tool` on a hold; its demo gates the note before the agent runs |
| `example.py` | twenty lines: one task, one read, five answers |

```bash
pip install strands-decider==0.1.0
strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --port 8000 --device cpu
python example.py
```

The hook, with an agent whose model is `openai.gpt-oss-20b` on Amazon Bedrock through the Strands
OpenAI-compatible provider (`pip install "strands-agents[openai]==1.61.1"`, and a Bedrock API key in
`BEDROCK_API_KEY`):

```python
from strands import Agent
from decider_client import gate_source_text
from strands_hook import GatekeeperHook, bedrock_model, add_task, describe

note = "Met Priya at the meetup. I promised to email her my thermal preprint by Friday."
verdict = gate_source_text(note)  # 1. the note is gated before any model reads it
if verdict.verdict == "hold":
    print(describe(verdict))  # held: the agent never runs
else:
    hook = GatekeeperHook(tools={"add_task"})  # 2. then each add_task call's arguments
    agent = Agent(model=bedrock_model(api_key), tools=[add_task], hooks=[hook])
    agent(note)
    for tool, decision in hook.decisions:
        print(tool, describe(decision))
```

`python strands_hook.py "<note>"` runs the same order.

## Adapting it

- **Rename the projects** and rewrite their descriptions for your domain. Keep one property per
  question, describe situations rather than numbers, and keep the options distinct from one
  another, or the probability spreads across them and falls under the floor.
- **Tune the floors** on your own traffic. Decider's calibration was fitted on short classification
  tasks; its authors advise measuring before trusting a threshold.
- **Give the gate the source.** Call `gate_source_text()` before the agent runs, whenever the
  action comes from a text you have, and stop on a hold. The hook's `source_text` is only a
  backstop: by the time a tool call reaches the hook, the model has read the text.
- **Choose the failure mode.** `on_unavailable = "hold"` fails closed. Ratatoskr Demo itself writes
  nothing while Decider is down: the recording waits, and after three cycles without Decider one
  notice gives the count waiting.

## Privacy: content sensitivity checked and protected

Privacy is a feature of this design, and its refusals are how it is built: the tripwire (1), the
source-text gate (2), and the holds on a sensitive or unsure answer and when Decider cannot be
asked (6).

### Where the gate looks, and why

The gate judges the text it is given. An extraction step, or an agent writing its own tool
arguments, can drop the very detail that made the source sensitive: "give Mara's Tuesday talk"
without "she starts medical leave". On 17 Oct 2026, in Ratatoskr Demo's live hook check, the
Strands hook judging tool arguments alone let exactly that note through. So the pattern gates on
the **source text where it exists**, and falls back to gating on the tool's arguments only when
there is no source text. With the source gate, the same note is held before the agent runs.

## Measured

See [`bench/RESULTS.md`](../../bench/RESULTS.md) for the measured comparison with
`openai.gpt-oss-20b` on Bedrock and with TypeSafe's Jev, and for the source gate on every fixture
recording.
