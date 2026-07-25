# LOG

Newest first. Prepend-only; corrections are new entries, never rewrites.

## 2026-07-25 — full audit + remediation steps 1-2

**Verdict:** the core write path was silently destroying data on every entry
mutation. Fixed. Six other confirmed data-loss/security defect clusters remain,
sequenced in `PLAN.md`.

**Session arc.** Cut the `v0.1.0b2` release (tag pushed by user), then ran a
full audit: 8 parallel read-only review tracks over the whole tree, plus 3
adversarial verifiers whose job was to *refute* the highest-severity claims.
Then landed remediation steps 1 and 2.

**Load-bearing numbers.**
- Suite: 1396 passed at session start → **1413 passed, 8 skipped, 20 deselected**
  at end. `ruff` clean, `pyright` 0 errors throughout.
- `git audit-coverage src/` baseline: **0/22202 lines (0.0%)** — no commit has
  ever carried a `Human-audited: yes` trailer.
- Mutation testing on the existing suite: **20 of 29 killed**. The suite is
  load-bearing, not decorative; the 9 survivors are listed under test gaps.
- Tier manifest: **48 A / 32 B / 9 C** over 89 modules. Audited scope narrowed
  to a 12-module write-path core (see `PLAN.md`).

**Commits (branch `claude/audit-remediation`, not merged, not pushed):**
`e1f7d6b` plan + tier manifest · `33b5523` record-projection fix ·
`6668e61` same fix for pdf attach/retry + dedupe merge · `6a242a2` truncated
download detection.

### Do NOT re-pursue

Dead ends and refuted claims — each cost real time to establish:

- **Do not make `apply_write_plan` merge onto the on-disk entry.** Tried; it
  breaks the updater-callback contract (a callback's returned entry is already
  complete) and reverts caller edits. Caught by the `with_bib_lock` contention
  test, which is right. The merge belongs at plan *construction*.
- **`pzi update` does not overwrite populated fields.** The original audit claim
  said it poisons the whole library; refuted — `_conservative_enrich` fills only
  empty fields and `_needs_update` skips complete entries. What survives is
  narrower: wrong-candidate *selection* fills blank fields, and
  `metadata_confidence_min_score` is warning-only.
- **No browser processes leak past `pzi server` exit.** The "6 surviving
  chromium processes" finding was a sandbox artifact — PID 1 here is the agent
  harness, which does not reap, so dead children linger as zombies. The real
  defect is different: the browser tree is held for the process lifetime with no
  way to release it (thread-affinity, still open).
- **The Chrome profile clone is not a practical secret exposure.** The
  `chmod 0700` before `copytree` is genuinely dead code (`copystat` reinstates
  the source mode), but real browser profiles are already 0700, so the clone
  inherits that. Fix the dead code, do not treat it as a leak.
- **`IncompleteRead` is not the Content-Length truncation case.** A body with a
  known length that stops early returns *short without raising* — silent, and
  worse than the raising case. Both are now handled; don't re-derive this.
- **The `%%EOF`/minimum-size PDF heuristics were deliberately skipped**, not
  forgotten. Rationale in `PLAN.md` step 2: 128 `%PDF-` stubs across 32 test
  files, false-reject risk, and a warning channel doesn't exist yet.
- **Fixing dedupe's self-comparison is necessary but not sufficient.**
  `compute_similarity_hint(record, records)` includes the record itself so every
  best match is its own citekey, but excluding self still returned no match for
  a pair differing by one character at the default threshold. Tokenization or
  threshold needs looking at too.

### Next session entry point

Read `PLAN.md` (fix order + tier manifest), then start **step 3, destructive
write paths** — `promote_service` first, since it holds five of the confirmed
defects and blocks the step-1 projection leftovers at `:819`/`:908`. Every
defect is inlined there with `file:line`, trigger, and symptom; the verifier's
probe scripts were scratchpad-local and are gone.

Working practice for the audited scope: characterization test first (red),
then the fix, then `ruff` + `pyright` + `pytest -m "not browser"`, then a
commit carrying `Human-audited: yes` only if the user confirms reviewing every
staged hunk.

**Unsafe to run against a real library until step 3 lands:**
`pzi update --promote`, `pzi fix reindex`, `pzi fix clean --fix`.
