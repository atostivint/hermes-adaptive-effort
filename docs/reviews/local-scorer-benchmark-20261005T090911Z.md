# Local scorer benchmark

Generated: 10/05/2026 11:09:54
Host: Windows Zenon; Intel Core i5-13500; NVIDIA RTX 4070 Ti 12 GB; driver 616.64.
Runtime: llama.cpp b11396 Windows CUDA 12.4 x64; llama-swap v262.
Models: ggml-org Kev Q8_0, 0.8B revision `e551e319d483ff57e1ff208924b349d397cffc1c`, SHA-256 `27278f34eb3273bceea4c053dc50dd61a5161da21a718c4aacdf8fd5830771d0`; 4B revision `d924f2e2c3872da8b8aaf3eb4453b4126deceb79`, SHA-256 `7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da`.
Method: 60 synthetic bilingual prompts, fixed annotation and seed 20261005; one cold plugin probe per model followed by 3 repetitions (180 warm calls per model). The plugin custom scorer path ran with `auth=none`, provider-specific prompt consent, and a 3-second timeout. No prompt text is retained in this report. One-second `nvidia-smi` sampling during the final run observed up to 58% GPU utilization and 8,753 MiB total GPU memory use; this is host-level telemetry, not per-process attribution.
Interpretation: Kev is documented for English; French results and agreement with the fixed labels are exploratory. Cold request latency is reported separately from warm samples. The fixed labels are a benchmark rubric, not human gold annotations.

## effort-kev-08b

Cold: 2406 ms; label=low; failure=; ready after cold=True.
Model ready after first request: 2438 ms; warmup: 62 ms; label=low; failure=.
Warm: n=180, success=180, p50=78 ms, p95=110 ms, mean=76.7 ms.
Timeout/errors: {}.
Agreement with synthetic fixed labels among successful calls: 50%.

Confusion matrix (fixed label as row, predicted label as column):
| Fixed / Predicted | Low | Medium | High | Unscored |
|---|---:|---:|---:|---:|
| low | 30 | 30 | 0 | 0 |
| medium | 0 | 60 | 0 | 0 |
| high | 0 | 60 | 0 | 0 |

English agreement: 53.3% (90/90 successful). French exploratory agreement: 46.7% (90/90 successful).

## effort-kev-4b

Cold: 3063 ms; label=; failure=timeout; ready after cold=True.
Model ready after first request: 9110 ms; warmup: 93 ms; label=low; failure=.
Warm: n=180, success=180, p50=94 ms, p95=125 ms, mean=92.3 ms.
Timeout/errors: {}.
Agreement with synthetic fixed labels among successful calls: 60%.

Confusion matrix (fixed label as row, predicted label as column):
| Fixed / Predicted | Low | Medium | High | Unscored |
|---|---:|---:|---:|---:|
| low | 54 | 6 | 0 | 0 |
| medium | 6 | 54 | 0 | 0 |
| high | 0 | 60 | 0 | 0 |

English agreement: 53.3% (90/90 successful). French exploratory agreement: 66.7% (90/90 successful).
