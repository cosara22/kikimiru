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

Banned phrases live **outside the repository** — never inside it, in any form.
Hashing them is not enough: the normalization function ships with the repo and the
hashes were unsalted, so the config file worked as a free oracle for verifying guesses.
Store the config at `$KIKIMIRU_GATE_CONFIG`, or at the OS default
(`%APPDATA%\kikimiru\` on Windows, `${XDG_CONFIG_HOME:-~/.config}/kikimiru/` elsewhere):

```bash
python tools/gen_gate_hashes.py --where          # show the config path
python tools/gen_gate_hashes.py                  # add phrases (one per line, via stdin)
python tools/gen_gate_hashes.py --init-empty     # no private phrases? declare that explicitly
```

The gate runs fail-closed with `--require-config`, which is what the pre-push hook and CI
use — a missing config must not silently mean "zero banned phrases, everything passes".
CI reads the config from the `KIKIMIRU_GATE_CONFIG` repository secret; pull requests from
forks run without it and skip phrase matching only.

**Known limits** (do not treat a green gate as a full guarantee):
- The gate scans the **working tree only** — it does not scan git history. Something
  committed once and later deleted is still published when you push. If you ever commit
  something private, do not just delete it in a follow-up commit; ask for help rewriting
  history before it's pushed anywhere.
- The pre-push hook can be bypassed with `git push --no-verify`. The same gate also runs
  in CI (`.github/workflows/release-gate.yml`) as a second, independent check.
- Metadata inside media files **is** scanned (PNG `tEXt`/`iTXt`/`zTXt`/`eXIf`, JPEG
  `APPn`/`COM`, MP3 ID3v1/ID3v2), but only for those formats, and the gate can never read
  what the pixels or the audio actually show. Look at any screenshot you add.
- Run `python -m unittest tests.test_release_gate -v` after changing the gate itself.

## Language

Code comments and commit messages may be in Japanese or English.
