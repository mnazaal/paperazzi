# LOG

Newest first. Prepend-only; corrections are new entries, never rewrites.

## 2026-07-26 (latest) — design track item 2 finished: the JSON envelope

**Verdict:** design-track items 1 and 2 are done. Every command that reports a
result emits one envelope, on success and on failure, and `pzi doctor` is a
usable health gate. Items 3-5 remain.

**Load-bearing numbers.** Suite **1437 → 1441 passed**, 8 skipped, 20 deselected;
`ruff` clean, `pyright` 0 errors. Commit `4303744`.

**Shape.** `{command, status, bib_name, items, errors}` plus whatever else the
service reported. `cli_json.build_envelope` normalizes whichever key held the
list (`matches`/`results`/`bibs` → `items`), so services did not have to change.
`authors` became a list in `bib_service._author_names`, matching
`export --format json`; the entries table joins it for display.

**Verified against the installed CLI**, all twelve `--json` commands: each
returns a document carrying all five keys, with `delete nosuchkey` → 3 and
`doctor` with an unreachable translation-server → 5. The documented pipeline
`pzi search --json | jq -r '.items[].citekey' | xargs -r -n1 pzi entries --json`
runs clean.

**A verification harness bug worth remembering:** the first sweep reported ten of
twelve commands failing with exit 2. The cause was the harness, not the code —
zsh does not word-split unquoted parameters, so `pzi $cmd --json` passed
`"entries jones2023attn"` as a single command name. Check the harness before
believing a broad failure.

### Do NOT re-pursue

- **Do not add a separate NDJSON mode per command.** The review proposed it; the
  envelope already gives `jq -r '.items[].citekey'`, so it would be new surface
  buying nothing. `check --jsonl -` stays because a long audit benefits from
  streaming, and it suppresses the human table so the stream is not corrupted.

## 2026-07-26 (earlier) — design track: write-plan contract + CLI machine interface

**Verdict:** design-track items 1 and part of 2 are done. `WritePlan` now means
one thing at both sinks, and the CLI has an exit-code vocabulary, pipe-safe
stdout, and `--json` that survives failure. The `--json` envelope unification is
the open remainder.

**Load-bearing numbers.** Suite **1433 → 1437 passed**, 8 skipped, 20 deselected;
`ruff` clean, `pyright` 0 errors. Commits `7e15231`, `8229b5c`, `ecca0fb`.

**Write-plan contract.** `apply_write_plan` replaced while
`BatchWriteSession.apply_plan` merged; both now apply `plan["entry"]` verbatim
and update plans are merged at construction. Behaviour-preserving for writes —
after the step-4 venue fix a double merge was already idempotent — so the guard
is a contract test (unmodelled fields survive a batch update), not a bug repro.
The real user-visible win was elsewhere: the two dry-run preview callers built
bare projections, so `pzi update --dry-run` reported it would delete `pages`
and `publisher` and retype `@article` to `@unpublished`. Neither happens.

**Two gaps that only the real CLI showed**, both after unit tests were green:
- `pzi tag add --json` was rejected by the parser — the flag existed only on
  `tag list`, so the runner change was unreachable. Unit tests passed because
  they build a `Namespace` directly.
- A README pipeline example used `-q`, which is not a flag; the "successful"
  verification run was `xargs` invoking `pzi entries` with no argument on empty
  input. Always run the documented command verbatim.

### Do NOT re-pursue

- **Do not reintroduce a merge inside either sink.** The merge belongs at plan
  construction; merging at the sink is what gave one plan type two meanings, and
  merging inside `apply_write_plan` specifically breaks the updater-callback
  contract (tried 2026-07-25).
- **Exit code 1 is not "failure".** It means "ran fine, has something to
  report" (no search matches, duplicates found, integrity issues). A command
  that could not run exits 5. Do not "simplify" a failure path back to 1.
- **An unmatched `--target` is exit 5, not 3.** Config defines the set of
  libraries, so naming one that does not exist is a misconfiguration. `3` is
  reserved for a missing *entry*, which is the distinction scripts branch on.
  The two `--target` paths disagreed at first (3 vs 5); they must not diverge
  again.

### Next session entry point

`PLAN.md` design track item 2, the open half: one JSON envelope across all
commands, `--json` on the mutating commands (`update --promote` first), NDJSON
on stdout, and `pzi doctor`'s human-by-default rendering. Then item 3
(mislabelled pure modules) and item 4 (error channels).

## 2026-07-26 — review + steps 3b and 4: no command is known-unsafe any more

**Verdict:** `pzi fix reindex` and `pzi fix clean --fix` are cleared, and every
insert path now writes conference papers correctly. Steps 1-4 of `PLAN.md` are
done; 5-7 remain and are not data-safety blockers.

**Load-bearing numbers.** Suite **1424 → 1433 passed**, 8 skipped, 20 deselected;
`ruff` clean, `pyright` 0 errors. Nine tests, all confirmed red first.

**Session arc.** A full review first (three parallel read-only tracks:
functional-programming discipline, UNIX-philosophy CLI surface, architecture and
daily-use readiness), then implementation of the two remaining safety blockers.
The review's non-defect findings are now `PLAN.md`'s "Design track"; the
readiness verdict confirmed every open `PLAN.md` defect was still present at the
cited lines.

**Both fixed services were restructured into plan/execute rather than patched.**
They were the only two write services without the plan layer the rest of the
codebase has, which is why their dry-runs could diverge from their real runs.
`plan_reindex` and `plan_orphan_quarantine` are pure; execution is a thin loop.
Each recorded defect then became a consequence of building the plan correctly
rather than a guard bolted onto the effect.

**Found while fixing, beyond the recorded defects:**
- **`os.rename` clobbers its destination.** Reindexing onto a path where a PDF
  already sat destroyed that PDF silently. Now refused and reported.
- **Fixing the venue projection alone would have deleted venues.**
  `merge_projected_entry` read the venue only from `projected["journal"]`, so
  emitting `booktitle` from the projection would have dropped the venue of every
  proceedings entry it merged. Both halves changed together; the merge test is
  the guard.
- **Import dropped the source entry type entirely** — every imported
  `@inproceedings` became `@article`. `import_service` set
  `record["entry_type"]` and nothing downstream ever read it (`add_service` does
  not mention `entry_type` at all). Caught only by running the real CLI: the
  unit-level projection fix looked complete and the imported entry was still
  wrong. `entry_type` is now a declared record key honored by
  `resolve_entry_type`.

**Verified end-to-end through the installed CLI**, not just in tests: reindex
renamed the entry's own PDF and left a stray namesake alone; a second
`fix clean --fix` archived a same-named orphan as `oldkey-1.pdf` without
touching the first; a repeat `fix clean` audit exited 0 instead of looping;
import wrote `@inproceedings{...booktitle = {NeurIPS}}`.

### Do NOT re-pursue

- **Do not delete `_relocate_venue_for_entry_type`.** `PLAN.md` step 4 expected
  the projection fix to make it redundant; only its two insert-path call sites
  were dead. The retype-on-merge case still needs it, as the 2026-07-25
  adversarial review found — `merge_projected_entry` keeps the venue under the
  on-disk key by contract, so a promotion that retypes must move it.
- **Do not populate `entry_type` in `bibtex_entry_to_record`.** Records read out
  of the library must not carry it: `resolve_entry_type` would then hand
  promotion the preprint's own `@misc`/`@unpublished` type back and defeat the
  retyping that promotion exists to do. Only sources that genuinely declare a
  type (an imported `.bib`) set the key.
- **`rewrite_entries_in_order` is now `rewrite_entries_in_order_locked` and does
  not lock.** Reindex holds one exclusive lock across the PDF renames and the
  bib write; the old shape re-took the lock for the write, which is the window
  where a concurrent `pzi add` changed the entry count.

### Next session entry point

`PLAN.md` "Design track", item 1: unify the write-plan contract (all plan
constructors pre-merge, both sinks replace). Item 2, the CLI machine interface,
is independent and can go first if daily scripted use matters more than
internals.

## 2026-07-25 — step 3a: promote_service destructive write paths

**Verdict:** all five recorded `promote_service` defects reproduced and fixed,
plus the two step-1 projection leftovers. `reindex_service` and `clean_service`
remain open.

**Load-bearing numbers.** Suite **1413 → 1424 passed**, 8 skipped, 20 deselected;
`ruff` clean, `pyright` 0 errors. Eleven tests net: six characterization tests in
`test_promote_service.py` (each confirmed red against its recorded symptom before
the fix), two in `test_resolution_match.py`, and three regression tests from the
adversarial review below. One test renamed (`_score_confidence` deleted), one
config default assertion updated, two promote tests re-pinned to the new scale
(a threshold of 2 or 3 now passes no matter how scoring drifts).

**Reproduction evidence**, captured before touching source — the two that
matter most, since both are silent:
- Keep-mode: promoting a preprint that carries a `url` left the file with **one**
  entry, the preprint mutated to hold the published `doi`/`journal`, while the
  run reported `action=create` with `published_citekey=smith2024graph-2` — a
  citekey present nowhere in the file.
- Partial apply: with one malformed block in the `.bib`, `@article{smith2024graph-2}`
  was **committed** and the cross-reference note never landed, behind a reported
  `created 0`.

**What the fixes needed beyond the plan** — worth knowing before the next unit:
- Keep-mode's write is one `batch_write_session`; that is what makes the insert
  and the preprint note atomic *and* rejects an unparseable library up front.
  The published entry's own back-reference note moved into the record before
  planning, leaving a single in-session note update.
- `merge_projected_entry` preserves the on-disk entry type **by contract**, so
  the update-in-place branch must set the resolved type explicitly. Promotion is
  the one legitimate retyping case; do not "fix" this by weakening the contract.
- `_translation_candidates` was dropping the translation server's `item_type`
  (it sits beside the record, not inside it), so the entry-type fix had nothing
  to resolve from until that was carried through.
- Moving to the 0-100 gate exposed a **false reject in the shared scorer**: a
  candidate with no author list scored identically to one whose authors
  disagree, so a sparse provider record (title + venue only) failed a perfect
  title match. `score_match` now separates absent evidence (`author_unknown`)
  from disagreement. This deliberately changes `check_service` scoring too.

**Breaking:** `promote_confidence_threshold` is now a 0-100 score, default
3 → 60. A configured value must be restated on the new scale. In CHANGELOG.md.

**Adversarial review of the above caught three defects in the fixes themselves**
(all fixed, each with a regression test):
- **The two write paths have opposite `plan["entry"]` contracts.**
  `apply_write_plan` (behind `execute_write_plan`/`update_bib_entry`) *replaces*,
  so plans headed there must arrive pre-merged; `BatchWriteSession.apply_plan`
  *merges*, so plans headed there must be bare projections. The new note plan
  was pre-merged and then merged again, which round-trips `venue` through the
  record's single key twice — reading back an absent `journal` and **deleting a
  preprint's `booktitle`**. `add_service:529` passes `existing_entries` and is
  correct only because it targets `execute_write_plan`; checked.
- **Retyping an entry has to move its venue.** `merge_projected_entry` keeps the
  venue under whichever key the entry already used. Correct for an update, wrong
  when promotion retypes proceedings → article, which produced
  `@article{... booktitle = {Journal of ...}}`.
- **Moving to a batch session dropped `ConcurrentEditError`.** `batch_write_session`
  re-reads under the lock instead of digest-checking, so a citekey generated
  against the run's opening snapshot could collide with one added meanwhile —
  and `force_new` guarantees nothing else would catch it. Explicit check added.

**Also revised under review:** treating "candidate has no authors" as missing
evidence fixed a false reject in promotion but flipped `pzi check` from a false
alarm (`problematic`) to **false assurance** (`verified`) — a title match with
unconfirmed authorship is the fabricated-citation signature. `check_service` now
degrades that to `could_not_verify`. False assurance in a verification tool is
the worse failure; do not "simplify" this back.

### Do NOT re-pursue

- **Do not gate promotion on `_score_confidence`.** Deleted, not refactored. The
  coarse 0-6 feature count let three shared surnames clear the default threshold
  on their own while the diagnostics printed `confidence 0/100 … title_mismatch`.
  Selection and acceptance now share one scale precisely so they cannot disagree.
- **Do not fix the keep-mode overwrite by stripping identity alone.** Stripping
  the inherited preprint URL is necessary (it also stops the published entry
  being typed `@unpublished`) but is not sufficient — a shared DOI would collide
  the same way. The insert is planned `force_new=True`; the duplicate check
  upstream is what makes that safe.
- **`_add_note_to_citekey` is gone**, not accidentally orphaned. Both call sites
  became in-session note plans; a second locked write is exactly the partial-apply
  bug.

### Next session entry point

`PLAN.md` step 3, `reindex_service` — the wrong-PDF-attached defect
(`old_pdf_path` from `plan_pdf_path` instead of `record["local_pdf_path"]`) and
the missing rollback, then `clean_service`. Both are inlined there with
`file:line`, trigger, and symptom. Same practice: characterization test red
first, then fix, then `ruff` + `pyright` + `pytest -m "not browser"`.

**Still unsafe against a real library:** `pzi fix reindex`, `pzi fix clean --fix`.
`pzi update --promote` is now cleared.

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
