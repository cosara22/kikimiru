# Contributing to kikimiru

## Contributor License Agreement (CLA)

By submitting a pull request or patch, you agree that:

1. You are the author of the contribution and have the right to submit it.
2. You grant the project maintainer a perpetual, worldwide, non-exclusive, irrevocable
   license to use, modify, distribute, and **relicense** your contribution as part of
   this project (including under future license versions or dual-licensing arrangements).
3. Your contribution is provided "as is", without warranties.

This keeps the copyright consolidated so the project can adjust licensing later
(e.g. dual licensing). If you cannot agree, please open an issue instead of a PR.

## Clean-room rule

This project deliberately contains **no code from third-party media servers**
(including GPL-licensed ones). Do not copy, port, or closely paraphrase code from
such projects — independent implementations only. Interoperability based on
publicly documented APIs/protocols is fine.

## Release gate

Before pushing, run:

```bash
python tools/release_gate.py
```

It must exit 0. Install it as a pre-push hook with `python tools/install_hooks.py`.
The gate enforces the project's core rule: **no bundled content, no private material,
media files only under `demo/` with provenance recorded in `demo/SOURCES.md`.**

Banned phrases are stored as normalized SHA-256 hashes in
`tools/release_gate_config.json`, never as plaintext — the config file itself must not
leak what it's protecting. To add a new banned phrase, pipe it through
`tools/gen_gate_hashes.py` (one phrase per line via stdin); the plaintext never touches
the repo.

**Known limits** (do not treat a green gate as a full guarantee):
- The gate scans the **working tree only** — it does not scan git history. Something
  committed once and later deleted is still published when you push. If you ever commit
  something private, do not just delete it in a follow-up commit; ask for help rewriting
  history before it's pushed anywhere.
- The pre-push hook can be bypassed with `git push --no-verify`. The same gate also runs
  in CI (`.github/workflows/release-gate.yml`) as a second, independent check.
- Run `python -m unittest tests.test_release_gate -v` after changing the gate itself.

## Language

Code comments and commit messages may be in Japanese or English.
