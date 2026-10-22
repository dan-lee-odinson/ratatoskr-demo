# Contributing

Thanks for looking. This file says how the project is built, what a change needs before it is merged, and which changes are welcome.

## How decisions are made

One person directs this project and dispositions every design question; the code is written to those decisions, by Claude Code and by contributors. For anything beyond a bug fix, open an issue first and say what you want to change and why. You will get a yes, a no, or a question, and a yes before you write the code saves both of us a rewrite.

## Setting up

Two virtual environments, one for the Decider server and one for the courier, because the server's dependency tree is large and the courier's is small.

1. Python 3.12 or newer.
2. Courier: `python -m venv .venv`, activate it, `pip install -e ".[dev]"`.
3. Decider server: `python -m venv .venv-decider`, activate it, `pip install strands-decider==0.1.0` as the README's "Run it" section shows, then, from the repository root, start the server with `python tools/serve_decider.py --pid-file ledger/decider.pid --max-commit-gb 16`.
4. Copy `.env.example` to `.env` and fill in only the keys for the adapters you enable. `.env` is git-ignored and must stay that way.

The README's "What you need" table says which keys each adapter needs. With a Telegram bot token and its chat id, a Bedrock key, a Todoist token, a local Decider, and `[devices] inputs = ["text"]` in `config.toml`, `/demo <sentence>` runs the whole pipeline while `ratatoskr-demo --loop` is running.

## Testing

Every change comes with tests, and the suite must be green before a pull request is opened:

    pytest

No test needs a running Decider server: it is mocked, like Bedrock, Pocket, Todoist, Telegram, and TypeSafe. Two tests skip where what they need is missing: the Hugging Face check without `.venv-decider` and the downloaded weights, and the real-recording shape test until a real recording is saved. A skip is fine locally; the maintainer runs the full set before merging.

The project also keeps a set of mutation checks: deliberate single defects that the suite must catch. If you add a rule, add the mutation that breaks it and confirm the suite goes red. `python tools/check_tests_bite.py` runs them; the set is listed in that file and described in BUILD_REPORT.md.

Three rules for tests, taken from how the project was built:

- A checker is a callable that can fail. Verify by running it, never by reading the code it checks.
- Make every new test fail on purpose once before trusting it.
- One test per branch of a rule. The priority rule, the hold rules, and the gate each have a test per step; a new step gets its own.

## What a pull request needs

- An issue reference for anything that is not a plain bug fix.
- Tests, as above, and a green suite.
- No secrets, tokens, or real names anywhere in the diff, the tests, or the fixtures. The secret scan is part of the test suite, so it runs on every clone, and the maintainer runs the forbidden-names scan before merging; a hit in either blocks the merge.
- Fixture transcripts and names are fictional. Do not add a recording of a real conversation, even your own.
- Standard-library Python where possible. A new third-party dependency needs a reason in the pull request and a line in NOTICE with its license.
- The license header at the top of any new first-party `.py` file (copy it from a neighbor); a test checks for it.

By submitting a pull request you agree that your contribution is licensed under the Apache License 2.0, as section 5 of the license provides.

## The invariants

Some behavior is the point of the project and is not up for change in a pull request. A change that does any of the following will be declined:

- Passes an unsure privacy answer. A sensitivity answer below its floor holds; it is never read as a no. An explicit, recorded owner release (the Release button on a photo held as unsure) authorizes continuation past a gate's unsure hold for that unchanged photo, and the ledger records it as the owner's; a model clear and an owner release are never confused.
- Lets the `urgent` tag, or anything else, bypass a verdict Decider has given.
- Stores a transcript, or puts recording text into a notice, a log, or the wrist line.
- Binds the Decider server to anything other than the loopback address.
- Raises the spend ceiling in code or lets config raise it past five dollars.
- Weakens a floor without a bench result showing why.

If you think one of these is wrong, open an issue and make the case. The README's Safety section says what each one protects.

## What is welcome

- New device adapters behind the same envelope (`device_id`, `kind`, timestamp, typed payload), with the secrets they need declared in config.
- Labeled bench tasks, especially real-shaped ones. The bench is thirty fixtures and five live tasks; more labeled tasks are the most useful thing a contributor can add. Keep them fictional.
- Example criteria lines for `prompts/route.toml` that improve the project sort on your own tasks, with the before-and-after sure counts.
- Fixes to the deadline question's wording, with the bench run that shows the change.
- Documentation fixes of any size.

## Reporting a security problem

If you find a way for recording text to leave the machine that the README's Safety section says it cannot, do not open a public issue. Write to dan.lee.odinson@gmail.com with the steps to reproduce, and you will get a reply within a week.
