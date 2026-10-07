# Model effort validation report

> Template only. Do not describe a route as validated until the corresponding run has a checked manifest, observed HTTP carrier, completed response, and reconciled cost evidence. Never paste prompts, response bodies, raw errors, headers, credentials, environment values, or session IDs into this report.

## Campaign identity

- State: `not_run` / `blocked` / `in_progress` / `completed`
- Campaign ID:
- Manifest schema: `hermes-adaptive-effort.validation-manifest.v1`
- Manifest hash:
- Source commit:
- Plugin SHA-256:
- Hermes source/version:
- Case version: `2026-10-07`
- Score origin: `controlled` (real scorer calls: `0`)
- Local checks and lint evidence:
- Current runner gate/result:

## Dollar envelopes

These are separate dollar envelopes. The Codex limit is a quota change, not a dollar budget or reservation.

| Provider | Working cap | Hard cap | Starting counter | Reconciled spend | Reserved/unresolved | Remaining working budget | Ending counter | Evidence/source and timestamp |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| OpenRouter | $1.80 | $2.00 | — | — | — | — | — | — |
| OpenCode Go | $2.70 | $3.00 | — | — | — | — | — | — |

## OpenAI Codex quota envelope

Maximum permitted increase: **30 percentage points** on the Codex 300-minute window, for the same account used by Hermes. Fill in the baseline, current/ending observations, hard maximum and operational stop threshold from the specific campaign. This has no dollar value and no dollar reservation.

| Meter observation | Campaign baseline? | Hermes account match? | Maximum increase | Ending meter / measured delta |
| --- | --- | --- | --- | --- |
| — | — | — | +30 percentage points | — |

Monitor this quota independently from the OpenRouter and OpenCode Go dollar caps. Report whether a hard pre-send per-request quota bound including retries is demonstrated; do not imply such a bound from meter monitoring alone.

## Route summary

| Provider | Exact model | API | Verdict | Values produced | Values observed on HTTP | Response complete | Cost reconciled | Limit or exclusion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| — | — | — | — | — | — | — | — | — |

Use only these verdicts: `Validated for this value`, `Variation observed`, `Mapping limitation`, `No usable control`, `Incompatibility`, `Access / availability`, `Unverified`.

## Case results

One row per result object from `hermes-adaptive-effort.validation-result.v1`. Keep the stable case/attempt IDs and manifest hash so each row can be joined to its checked manifest.

| Case ID | Attempt ID | Provider / exact model | API | Decision score / target | Before effort | Middleware effort | HTTP wire effort | HTTP status | Response complete | Synthetic answer | Input/output tokens | Provider cost evidence | Reservation | State / normalized failure |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

## Conclusions

- Which exact models accepted the current control:
- Which exact models showed at least two distinct accepted effort values:
- Which models or values remain unsupported, excluded, or unverified:
- GLM-5.2 mapping limitation (`low`, `medium`, and `high` currently converge to `high`):
- Route exclusions and reasons:
- OpenRouter and Go totals remain within their separate working/hard caps:
- Unresolved reservations and next reconciliation action:

Do not infer a server-side reasoning budget change from request transmission, latency, or response length. Do not infer quality, cost savings, or cache effects from this compatibility campaign.
