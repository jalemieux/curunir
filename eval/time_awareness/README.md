# Time-injection tactic comparison

A *variant-comparison* harness (not a graded suite). It A/B/C/D-compares ways of
telling the model the current time, on the dimension that matters for auto-cache
providers (the configured `MODEL`, e.g. `openrouter/z-ai/glm-5.2`): **does the
tactic keep the cacheable prefix byte-stable so prompt-cache reads stay high,
while still giving the model a fresh clock?**

The tactic is selected at SUT boot via `CURUNIR_TIME_TACTIC` (read in
`Agent.handle`):

| tactic | where "now" goes | rewrites prefix? | fresh? |
|---|---|---|---|
| `boot` | frozen "now" in the system prefix | no | NO (stale) — = `main` today |
| `prefix_live` | live "now" in the system prefix, every turn | **yes** | yes |
| `trailing` | live "now" appended after history (non-persisted) | no | yes — = PR #432 |
| `user_inline` | live "now" folded into the current user turn | no (persists) | yes |

## Run

From the repo root with `.venv` active and `.env` populated:

```bash
python eval/time_awareness/compare.py                              # all four tactics
python eval/time_awareness/compare.py --tactics prefix_live trailing
python eval/time_awareness/compare.py --bloat-history 40000        # load history so the penalty shows
```

The harness boots its own fresh SUT per tactic (so the port must be free),
drives one fixed multi-turn conversation over a single session, and reads the
per-turn `prompt_tokens` / `cached_prompt_tokens` the agent already emits in its
turn-final stats frame. The discriminator is the steady-state **uncached** token
count per turn. A same-session time probe (`date`, `AM/PM`) is a sanity gate.

## What it found (glm-5.2 / cloudflare auto-cache)

- **A short conversation does not discriminate.** With ~150 tokens of history all
  four tactics land at 97-99% cached — because the timestamp sits *after* the big
  static system block (~5.5k tokens), which stays a cacheable prefix and dwarfs
  the tiny history.
- **The penalty is in the history, and only shows with `--bloat-history`.** With
  ~10k tokens of history, steady-state uncached tokens/turn:
  - `boot` ~16-95, `trailing` ~150-280 (full history caches),
  - `prefix_live` **~8,000 every turn** — the history after the moved timestamp
    never caches, and the waste scales linearly with conversation length.
- **Time correctness: 2/2 for every live tactic** — all give the model a usable
  same-session clock. `boot`'s real failure mode is *resume* (a new process with
  an old session), which is out of scope here and covered by #431's own tests.

**Conclusion:** PR #432's `trailing` keeps the per-turn timestamp *outside* the
cacheable region, so its uncached cost is constant (~one note) regardless of
history length, while the naive in-prefix timestamp (`prefix_live`) re-bills the
entire history every turn. `user_inline` is equally cache-friendly but persists
stale stamps into history. `trailing` is the right call.

Caveats: single run (n=1/turn), provider cache TTL/warmup adds turn-level noise,
and this is GLM/cloudflare auto-cache specifically — Anthropic explicit caching
and other providers behave differently (though the prefix-stability principle is
provider-agnostic).
