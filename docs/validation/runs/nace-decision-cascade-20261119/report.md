# Nace decision cascade vs one-shot — offline comparison (2026-11-19)

Run metadata (all factual, no prompt text or credentials):

- Scorer: Nace Drex, default `https://console.nace.ai/v1/systemone`, model `drex-v2.0`, resolved from `NACE_API_KEY` (Bitwarden) in the invoking shell; env var removed in `finally`.
- Both strategies used a 30 s per-call timeout (the adapter default is 3 s); the same timeout was used for both, so the comparison is fair. No HTTP error codes were observed (`observed_codes=[]`), so the 401/402 stop did not trigger.
- Corpus: `tests/fixtures/scorer_eval_corpus.json` (86 synthetic items; `expected` is a labelling judgement, not answer-quality ground truth).
- Subset: 30 items, deterministic stratified sample — each of the 8 tags contributes up to 4 items drawn evenly across that tag's difficulty range; the same 30 items were scored by both strategies. IDs: c002 c007 c011 c013 c015 c018 c021 c025 c033 c034 c040 c041 c044 c054 c059 c064 c068 c070 c072 c073 c075 c077 c078 c080 c081 c082 c083 c084 c085 c086. Expected-level spread: minimal 3, low 9, medium 7, high 8, xhigh 1, max 2.
- Ladder: `effort.CHOICE_LEVELS` = `minimal, low, medium, high, xhigh, max`.
- One-shot: `NaceClient.classify_effort_detail(prompt, ladder)` → `rubric.questions_for(None, ladder)`, one named choice.
- Cascade: binary search over the same ladder, one custom System One `choice` question named `effort` with criteria `yes`/`no` per call, strict parse of `answers.effort.choice` (`type == "choice"`, value in `{yes,no}`), abort on a failed answer, cap 4 questions/item.
- Total calls: 112 (one-shot 30, cascade 82), against a budget of 150. Max cascade questions observed: 3 (the cap of 4 was never reached).

## 1. Agreement with `expected`

Overall: one-shot **17/30 = 56.7 %**; cascade **5/30 = 16.7 %**. Delta (cascade − one-shot): **−40.0 percentage points**.

| tag | one-shot | cascade | delta (pp) |
|---|---|---|---|
| `ambiguous` | 3/8 = 37.5 % | 2/8 = 25.0 % | −12.5 |
| `correction_or_replan` | 3/5 = 60.0 % | 0/5 = 0.0 % | −60.0 |
| `fix_vs_explain` | 3/5 = 60.0 % | 1/5 = 20.0 % | −40.0 |
| `mechanical_edit` | 3/4 = 75.0 % | 1/4 = 25.0 % | −50.0 |
| `multi_step` | 5/10 = 50.0 % | 1/10 = 10.0 % | −40.0 |
| `short_followup` | 5/5 = 100.0 % | 2/5 = 40.0 % | −60.0 |
| `strict_format` | 2/5 = 40.0 % | 0/5 = 0.0 % | −40.0 |
| `trivial_vs_architectural` | 3/5 = 60.0 % | 1/5 = 20.0 % | −40.0 |

The cascade drops on **all eight tags**, every drop larger than 10 points. Its errors are polarized: it repeatedly collapses mid-level tasks to `minimal` (e.g. c013, c015, c018, c021, c070, c072, c077, c078, c080) and pushes a few others to `max` (c040, c075). One-shot labels were also imperfect but stayed near the hand label far more often.

## 2. Agreement between the two strategies

Both strategies returned a label for all 30 items (no failures, no `None`). They agreed on **6/30 = 20.0 %** and disagreed on **24/30 = 80.0 %**. Of the 6 agreements, 2 were both correct and 4 were both wrong. Of the 24 disagreements, the cascade matched `expected` where one-shot did not in 3 cases (c007, c059, c086), one-shot matched where the cascade did not in 15, and neither matched in 6. Nace's `confidence` field was not read by either strategy.

## 3. Calls, latency, failures

| strategy | mean calls/item | max calls/item | mean latency/item | total calls | failures | invalid answers | `None` labels |
|---|---|---|---|---|---|---|---|
| one-shot | 1.00 | 1 | 427.1 ms | 30 | 0 | 0 | 0 |
| cascade | 2.73 | 3 | 1119.3 ms | 82 | 0 | 0 | 0 |

The cascade costs 2.73× the calls and 2.62× the latency per item. Strict parsing rejected nothing: 0 invalid answers and 0 transport/HTTP failures in either strategy. Every yes/no answer arrived with `type == "choice"` and a value in `{yes,no}`, and every one-shot answer was an in-vocabulary level.

## 4. Verdict

Decision rule, fixed before running: implement an opt-in cascade only if (a) its agreement with `expected` is at least 10 points above one-shot overall, (b) no tag drops by more than 10 points, and (c) no more than 2 invalid answers in 30 items.

- (a) **Fail** — cascade is 16.7 % vs one-shot 56.7 %, i.e. 40 points *below*, not 10 above.
- (b) **Fail** — all 8 tags drop, by 12.5 to 60 points.
- (c) **Pass** — 0 invalid answers.

**Recommendation: do not implement an opt-in cascade mode.** The data showed that turning a single 6-way named choice into a chain of yes/no threshold questions made agreement with the hand labels roughly three times worse on the same items, on every tag, while costing ~2.7× the calls and ~2.6× the latency. The yes/no phrasing appears to push Nace toward the extremes of the ladder rather than recover a calibrated level. A single named choice is both cheaper and more accurate here.

## gaps

- `expected` labels in the corpus are hand judgements, not measurements of answer quality: agreement with them rewards matching the labeller, not producing a better first reply. No end-task quality was measured.
- Nace's `confidence` was not used by either strategy; the cascade may behave differently if a confidence-aware decision rule were tried, but that was not tested.
- The cascade would break the current "at most one effort scorer call per turn" invariant (AGENTS.md invariant 4). Adopting it would require an explicit contract change, a new status/Desktop event shape and new breaker/latency accounting; none of that is in scope here.
- Single run, one scorer, one model, 30 items: no repeated-run variance, no second scorer, no confidence intervals. The timeout was raised from the adapter default 3 s to 30 s for both strategies; no timeouts occurred, but this is a deviation from the exact production timeout.
- With 3–5 items per tag, a tag percentage moves in 20–33 point steps; the per-tag column is directional, not precise.
- No runtime file, test, config or credential store was modified; no commit or push was made.
