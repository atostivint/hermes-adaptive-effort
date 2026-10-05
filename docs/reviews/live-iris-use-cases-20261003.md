# Live Iris use-case evaluation — 2026-10-03

The deployed plugin at `062bc1acf8c7a0a5a2b679f764e8e5809a0900c0` successfully routed real Codex and DeepSeek conversation requests. The evaluation also found a completed-turn status bug: Hermes clears the plugin's decision ledger after each turn, so the focused-conversation chip loses its result after completion.

Fourteen synthetic cases completed successfully, comprising fifteen primary conversation API calls. This count excludes auxiliary runtime requests. Ten successful Jev classifications took 241–368 ms (mean 287 ms); one additional probe intentionally targeted an unreachable local endpoint. These are observations from one small run, not cost, cache-hit, latency-benefit, or model-quality benchmarks.

The harness used `/usr/local/lib/hermes-agent/run_agent.py:AIAgent` and the installed `/root/.hermes/plugins/jev-auto-effort` through Hermes's real middleware dispatcher. User turns used the real scorer and real provider transports. Fresh session IDs and scratch SQLite databases isolated synthetic transcripts from the operator's normal conversation database. Context files, external memory and background review were disabled. Tools were disabled except for one pure in-memory echo tool. Mode overrides and the deliberately unavailable scorer were confined to the test processes; operator configuration and deployed plugin files were unchanged.

| Case | Provider / model | Jev score | Request effort | Observation |
|---|---|---:|---|---|
| Greeting | Codex / gpt-6.1-sol | 0.00 | high → low | Friendly answer within requested length |
| Complex ledger design, same conversation | Codex / gpt-6.1-sol | 2.00 | high → high | Fresh classification; appropriate complexity level |
| Simple arithmetic after complex task | Codex / gpt-6.1-sol | 0.00 | high → low | Returned `4`; prior high decision did not pin the turn |
| Moderate Python coding, same conversation | Codex / gpt-6.1-sol | 0.98 | high → medium | Returned correct generator-compatible implementation |
| Greeting | OpenCode Go / deepseek-v4.1-flash | 0.00 | high → low | Answer completed successfully |
| Complex design after greeting | OpenCode Go / deepseek-v4.1-flash | 2.00 | high → high | Current prompt was classified, rather than the opening greeting |
| Unsupported model | OpenCode Go / space-bunny-free | — | absent → absent | `unsupported`, zero probes; answer was `4` |
| Recommend mode | Codex / gpt-6.1-sol | 0.00 | high → high | Recommended low without applying it |
| Off mode | Codex / gpt-6.1-sol | — | high → high | No classification or rewrite; answer was `4` |
| Scorer unavailable | Codex / gpt-6.1-sol | — | high → high | `transport_error`; provider still answered `4` |
| Real tool loop | Codex / gpt-6.1-sol | 0.01 | high → low, twice | Two API requests shared one turn ID and one Jev probe; one change event; answer `loop-ok` |
| Reasoning explicitly disabled | Codex / gpt-6.1-sol | — | absent → absent | Zero probes, no effort field invented; answer was `4` |
| Actual HTTP verification: arithmetic | Codex / gpt-6.1-sol | 0.00 | high → low | HTTP body contained `reasoning.effort: low`; answer `4` |
| Actual HTTP verification: coding | OpenCode Go / deepseek-v4.1-flash | 0.98 | high → medium | HTTP body contained `reasoning_effort: medium`; answer completed |

The final two cases additionally instrumented synchronous and asynchronous `httpx` sends. The retained evidence contains only host, path, model and reasoning fields: no prompts, headers or credentials. It confirms the selected values reached `/backend-api/codex/responses` and `/zen/go/v1/chat/completions`. The capture also contains an effort-less request per route; the metadata alone does not identify their origin. They are not counted as evidence that every auxiliary request is routed.

The main product finding is reproducible in all fourteen cases. During request construction, `/status` contains the correct `conversation_id`, score and target. After `run_conversation()` completes, it contains zero sessions. Hermes's `agent/turn_finalizer.py` invokes `on_session_end` with a `turn_id` at the end of a user turn. The plugin's `middleware.on_session_end()` interprets that as termination of the whole conversation and removes all of its entries. The Desktop chip reads those entries, so a correct applied change can disappear from the chip after delivery. The effort-only `/changes` feed retains its events.

A follow-up should distinguish completed turns from actual conversation teardown, or retain a separate bounded last-result view for the chip while clearing decision state. It must also check cache-hostile `cache_safe` session pinning and child-goal lifetime: both currently share cleanup affected by this lifecycle mismatch. These consequences are inferred from the code; this run did not exercise a real Anthropic/cache-hostile route or live delegated children. No plugin fix was applied during this evaluation.

Response feedback: the Codex coding answer passed six direct checks, including a generator, empty input, all-duplicate input, order preservation and a unique zero. Its implementation retains only distinct-value counts. DeepSeek's coding answer works for the stated example but materializes the input, and its claim that materialization is required is too strong: the Codex answer demonstrates otherwise. In the difficult design case, Codex gave a more complete account of atomic debit/credit application, deduplication and rollback fencing. DeepSeek's account-by-account design did not fully establish atomic cross-account commit or safe rollback after new writes. Those are review judgments, not a formal proof or a general ranking of the models. The consensus/outbox checks were informed by the [Raft paper](https://raft.github.io/raft.pdf) and [AWS's transactional outbox guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

The observed effort levels fit these cases: low for trivial replies, medium for a small coding task and high for a distributed-consistency problem. Selecting high does not itself certify the answer's correctness.

Evidence is retained alongside this report: [initial cases](live-iris-use-cases-20261003-initial.json), [tool-loop and disabled-reasoning cases](live-iris-use-cases-20261003-extra.json), [HTTP-verification cases](live-iris-use-cases-20261003-wire-cases.json), [HTTP metadata](live-iris-use-cases-20261003-http-wire.json), and the [initial harness archive](live-iris-use-cases-20261003-harness.py.txt). Remote scratch runs are `jev-live-cases-20261003T061026Z`, `jev-live-cases-20261003T061331Z` and `jev-live-cases-20261003T061618Z` under `/root/.hermes/cache/scratch`. Iris's gateway and dashboard services remained active after evaluation.
