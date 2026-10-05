param(
    [int]$WarmupWaitSeconds = 120
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LocalRoot = Join-Path $RepoRoot '.local-scorer'
$Config = Join-Path $LocalRoot 'config.yaml'
$BaseUrl = 'http://127.0.0.1:8099'
$Port = 8099
$ModelSpecs = @(
    @{ Id = 'effort-kev-08b'; File = 'Kev-0.8B-Q8_0.gguf'; Hash = '27278f34eb3273bceea4c053dc50dd61a5161da21a718c4aacdf8fd5830771d0' },
    @{ Id = 'effort-kev-4b'; File = 'Kev-4B-Q8_0.gguf'; Hash = '7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da' }
)
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $LocalRoot 'logs'
$Runner = Join-Path $LocalRoot 'benchmark_runner.py'
$ReportDir = Join-Path $RepoRoot 'docs\reviews'

function Assert-LocalFiles {
    if (-not (Test-Path -LiteralPath $Config)) { throw 'Run scripts/setup_local_scorers.ps1 first; config.yaml is missing.' }
    if (-not (Test-Path -LiteralPath $Python)) { throw '.venv\Scripts\python.exe is missing.' }
    foreach ($model in $ModelSpecs) {
        $path = Join-Path (Join-Path $LocalRoot 'models') $model.File
        if (-not (Test-Path -LiteralPath $path)) { throw "Model file is missing: $($model.File)" }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $model.Hash) { throw "SHA-256 mismatch for $($model.File); refusing to send prompts." }
    }
}

function Assert-FreePort([int]$PortNumber) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $PortNumber)
    try { $listener.Start() }
    catch { throw "Port $PortNumber is already occupied; this script will not stop another process." }
    finally { $listener.Stop() }
}

Assert-LocalFiles
Assert-FreePort $Port
New-Item -ItemType Directory -Force -Path $LogDir, $ReportDir | Out-Null

$llamaSwap = Get-ChildItem -LiteralPath (Join-Path $LocalRoot 'runtime') -Filter 'llama-swap.exe' -File -Recurse | Select-Object -First 1
if (-not $llamaSwap) { throw 'llama-swap.exe is missing; run the setup script first.' }
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$stdout = Join-Path $LogDir "llama-swap-$stamp.stdout.log"
$stderr = Join-Path $LogDir "llama-swap-$stamp.stderr.log"
$configArg = "`"$Config`""
$process = Start-Process -FilePath $llamaSwap.FullName -ArgumentList @('-config', $configArg, '-listen', '127.0.0.1:8099') -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$processId = $process.Id

try {
    $ready = $false
    $startupDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $startupDeadline -and -not $ready) {
        if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { throw 'llama-swap exited during startup; see its proxy-only logs.' }
        try { Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 2 | Out-Null; $ready = $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'llama-swap did not become healthy within 30 seconds.' }

    $env:LOCAL_SCORER_REPO = $RepoRoot
    $env:LOCAL_SCORER_ENDPOINT = "$BaseUrl/v1/systemone"
    $env:LOCAL_SCORER_OUTPUT = Join-Path $LocalRoot "benchmark-$stamp.json"
    $env:LOCAL_SCORER_MODELS = ($ModelSpecs.Id -join ',')
    $env:LOCAL_SCORER_WARMUP_WAIT = [string]$WarmupWaitSeconds

    $runnerSource = @'
import importlib.util, json, os, random, statistics, sys, time, types
from datetime import datetime, timezone
from pathlib import Path

repo = Path(os.environ["LOCAL_SCORER_REPO"])
pkg = types.ModuleType("hermes_plugin_adaptive_effort")
pkg.__path__ = [str(repo / "hermes-adaptive-effort")]
sys.modules[pkg.__name__] = pkg
from hermes_plugin_adaptive_effort import middleware

SEED = 20261005
VARIANTS = ["account access", "a delayed delivery", "a billing question", "a scheduling change", "a device issue"]
EN = {
  "low": [
    "Translate this short sentence into French: The meeting starts at noon.",
    "Correct the spelling in this sentence: We recieved your message.",
    "Give a one-line definition of {v}.",
    "Rewrite this sentence politely: Please reply today.",
    "Convert 18 Celsius to Fahrenheit.",
  ],
  "medium": [
    "Compare two practical ways to resolve {v}, then recommend one with a brief reason.",
    "Turn these three steps for handling {v} into a clear checklist and identify one missing step.",
    "Summarize the pros and cons of two options for {v} for a small team.",
    "Draft a short response about {v} that acknowledges the issue and proposes a next step.",
    "Given a limited budget and a one-week deadline, outline a sensible plan for {v}.",
  ],
  "high": [
    "Design a robust approach to {v} across three services; analyze failure modes, privacy tradeoffs, and a rollback strategy.",
    "Diagnose an intermittent {v} incident from incomplete evidence, rank competing causes, and propose tests that distinguish them.",
    "Create a migration plan for {v} with conflicting requirements, dependencies, measurable success criteria, and a safe rollback.",
    "Evaluate two architectures for {v} under uncertain load and strict reliability constraints; quantify assumptions and second-order risks.",
    "Develop a decision framework for {v} that handles edge cases, conflicting stakeholder goals, and limited observability.",
  ],
}
FR = {
  "low": [
    "Traduis en anglais cette phrase courte : La réunion commence à midi.",
    "Corrige l'orthographe : Nous avons bien recu votre message.",
    "Donne une définition en une phrase de {v}.",
    "Reformule poliment : Merci de répondre aujourd'hui.",
    "Convertis 18 degrés Celsius en Fahrenheit.",
  ],
  "medium": [
    "Compare deux moyens pratiques de résoudre {v}, puis recommande-en un avec une brève justification.",
    "Transforme ces trois étapes pour {v} en liste claire et indique une étape manquante.",
    "Résume les avantages et limites de deux options pour {v} pour une petite équipe.",
    "Rédige une réponse courte sur {v}, reconnais le problème et propose une prochaine étape.",
    "Avec un budget limité et une semaine, propose un plan raisonnable pour {v}.",
  ],
  "high": [
    "Conçois une approche robuste de {v} sur trois services; analyse les pannes, la confidentialité et le retour arrière.",
    "Diagnostique un incident intermittent de {v} avec des indices incomplets, classe les causes et propose des tests discriminants.",
    "Planifie la migration de {v} avec exigences contradictoires, dépendances, critères mesurables et retour arrière sûr.",
    "Évalue deux architectures pour {v} sous charge incertaine et forte exigence de fiabilité; explicite hypothèses et risques indirects.",
    "Élabore un cadre de décision pour {v} couvrant cas limites, objectifs divergents et observabilité limitée.",
  ],
}

def corpus():
    rows = []
    for label in ("low", "medium", "high"):
        for language, banks in (("en", EN), ("fr", FR)):
            for i in range(10):
                template = banks[label][i % len(banks[label])]
                prompt = template.format(v=VARIANTS[i % len(VARIANTS)])
                rows.append({"label": label, "language": language, "prompt": prompt})
    random.Random(SEED).shuffle(rows)
    return rows

def pct(values, p):
    values = sorted(values)
    if not values: return None
    return values[min(len(values)-1, int((len(values)-1)*p))]

def confusion(samples):
    predicted = ("low", "medium", "high", "unscored")
    return {expected: {label: sum(
                sample["expected"] == expected and (sample.get("label") or "unscored") == label
                for sample in samples)
            for label in predicted}
            for expected in ("low", "medium", "high")}

def language_metrics(samples):
    result = {}
    for language in ("en", "fr"):
        selected = [sample for sample in samples if sample["language"] == language]
        result[language] = {
            "count": len(selected),
            "successful_count": sum(bool(sample.get("label")) for sample in selected),
            "accuracy": (sum(sample.get("label") == sample["expected"] for sample in selected)
                         / len(selected) if selected else None),
        }
    return result

def call(model, prompt):
    settings = dict(middleware.DEFAULTS)
    settings.update({
      "scorer_provider": "custom", "scorer_model": model,
      "custom_endpoint": os.environ["LOCAL_SCORER_ENDPOINT"],
      "custom_api_format": "systemone", "custom_auth": "none",
      "prompt_sharing_provider": "custom", "timeout_s": 3.0,
      "prompt_chars": 4000, "mode": "auto",
    })
    return middleware.run_probe(prompt, settings)

def wait_loaded(model, seconds):
    import urllib.request
    deadline = time.monotonic() + seconds
    expected = {"effort-kev-08b": "kev-08b", "effort-kev-4b": "kev-4b"}.get(model)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8099/running", timeout=2) as response:
                state = json.load(response)
            running = state.get("running", []) if isinstance(state, dict) else state
            if isinstance(running, list) and any(
                isinstance(item, dict) and item.get("model") == expected
                and item.get("state") == "ready" for item in running
            ):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

rows = corpus()
models = os.environ["LOCAL_SCORER_MODELS"].split(",")
results = {"seed": SEED, "corpus_size": len(rows), "repetitions": 3,
           "timeout_s": 3.0, "endpoint": os.environ["LOCAL_SCORER_ENDPOINT"], "models": {}}
for model in models:
    readiness_started = time.monotonic()
    cold = call(model, rows[0]["prompt"])
    ready = wait_loaded(model, int(os.environ["LOCAL_SCORER_WARMUP_WAIT"]))
    readiness_ms = (time.monotonic() - readiness_started) * 1000.0
    if not ready:
        raise RuntimeError(f"{model} did not become ready within the configured wait")
    warmup = call(model, rows[0]["prompt"])
    samples = []
    for repetition in range(3):
        for row in rows:
            answer = call(model, row["prompt"])
            samples.append({"expected": row["label"], "language": row["language"],
                            "score": answer.get("score"), "label": answer.get("label"),
                            "failure": answer.get("failure"),
                            "elapsed_ms": answer.get("elapsed_ms")})
    latencies = [sample["elapsed_ms"] for sample in samples if isinstance(sample.get("elapsed_ms"), (int,float))]
    successes = [sample for sample in samples if sample.get("label")]
    results["models"][model] = {
      "cold": {key: cold.get(key) for key in ("score", "label", "failure", "elapsed_ms")},
      "model_ready_after_first_request_ms": readiness_ms,
      "warmup": {key: warmup.get(key) for key in ("score", "label", "failure", "elapsed_ms")},
      "ready_after_cold": ready, "sample_count": len(samples),
      "failure_counts": {code: sum(sample["failure"] == code for sample in samples)
                         for code in sorted({s["failure"] for s in samples if s["failure"]})},
      "confusion": confusion(samples), "accuracy_by_language": language_metrics(samples),
      "latency_ms": {"p50": pct(latencies, .50), "p95": pct(latencies, .95),
                     "mean": statistics.mean(latencies) if latencies else None},
      "accuracy_vs_fixed_annotation": (sum(s["label"] == s["expected"] for s in successes) / len(successes)
                                       if successes else None),
      "successful_count": len(successes), "warm_samples": samples,
    }
results["created_at_utc"] = datetime.now(timezone.utc).isoformat()
Path(os.environ["LOCAL_SCORER_OUTPUT"]).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": os.environ["LOCAL_SCORER_OUTPUT"],
                  "models": {key: {"cold": value["cold"], "ready_after_cold": value["ready_after_cold"],
                                   "model_ready_after_first_request_ms": value["model_ready_after_first_request_ms"],
                                   "warmup": value["warmup"],
                                   "latency_ms": value["latency_ms"], "failure_counts": value["failure_counts"],
                                   "successful_count": value["successful_count"]}
                              for key, value in results["models"].items()}}, ensure_ascii=False))
'@

    Set-Content -LiteralPath $Runner -Value $runnerSource -Encoding utf8
    & $Python $Runner
    if ($LASTEXITCODE -ne 0) { throw "Benchmark runner failed with exit code $LASTEXITCODE." }

    $jsonPath = $env:LOCAL_SCORER_OUTPUT
    $data = Get-Content -LiteralPath $jsonPath -Raw | ConvertFrom-Json
    $reportPath = Join-Path $ReportDir "local-scorer-benchmark-$stamp.md"
    $lines = @(
        '# Local scorer benchmark', '',
        "Generated: $($data.created_at_utc)",
        'Host: Windows Zenon; Intel Core i5-13500; NVIDIA RTX 4070 Ti 12 GB; driver 616.64.',
        'Runtime: llama.cpp b11396 Windows CUDA 12.4 x64; llama-swap v262.',
        'Models: ggml-org Kev Q8_0, 0.8B revision `e551e319d483ff57e1ff208924b349d397cffc1c`, SHA-256 `27278f34eb3273bceea4c053dc50dd61a5161da21a718c4aacdf8fd5830771d0`; 4B revision `d924f2e2c3872da8b8aaf3eb4453b4126deceb79`, SHA-256 `7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da`.',
        'Method: 60 synthetic bilingual prompts, fixed annotation and seed 20261005; one cold plugin probe per model followed by 3 repetitions (180 warm calls per model). The plugin custom scorer path ran with `auth=none`, provider-specific prompt consent, and a 3-second timeout. No prompt text is retained in this report.',
        'Interpretation: Kev is documented for English; French results and agreement with the fixed labels are exploratory. Cold request latency is reported separately from warm samples. The fixed labels are a benchmark rubric, not human gold annotations.', ''
    )
    foreach ($model in $ModelSpecs) {
        $result = $data.models.PSObject.Properties[$model.Id].Value
        $failures = if ($result.failure_counts) { ($result.failure_counts | ConvertTo-Json -Compress) } else { 'none' }
        $lines += @(
            "## $($model.Id)", '',
            "Cold: $([math]::Round($result.cold.elapsed_ms, 1)) ms; label=$($result.cold.label); failure=$($result.cold.failure); ready after cold=$($result.ready_after_cold).",
            "Model ready after first request: $([math]::Round($result.model_ready_after_first_request_ms, 1)) ms; warmup: $([math]::Round($result.warmup.elapsed_ms, 1)) ms; label=$($result.warmup.label); failure=$($result.warmup.failure).",
            "Warm: n=$($result.sample_count), success=$($result.successful_count), p50=$([math]::Round($result.latency_ms.p50, 1)) ms, p95=$([math]::Round($result.latency_ms.p95, 1)) ms, mean=$([math]::Round($result.latency_ms.mean, 1)) ms.",
            "Timeout/errors: $failures.",
            "Agreement with synthetic fixed labels among successful calls: $([math]::Round(100 * $result.accuracy_vs_fixed_annotation, 1))%.", ''
        )
        $lines += @(
            'Confusion matrix (fixed label as row, predicted label as column):',
            '| Fixed \\ Predicted | Low | Medium | High | Unscored |',
            '|---|---:|---:|---:|---:|'
        )
        foreach ($expected in @('low', 'medium', 'high')) {
            $row = $result.confusion.PSObject.Properties[$expected].Value
            $lines += "| $expected | $($row.low) | $($row.medium) | $($row.high) | $($row.unscored) |"
        }
        $en = $result.accuracy_by_language.en
        $fr = $result.accuracy_by_language.fr
        $lines += @(
            '',
            "English agreement: $([math]::Round(100 * $en.accuracy, 1))% ($($en.successful_count)/$($en.count) successful). French exploratory agreement: $([math]::Round(100 * $fr.accuracy, 1))% ($($fr.successful_count)/$($fr.count) successful).", ''
        )
    }
    Set-Content -LiteralPath $reportPath -Value $lines -Encoding utf8
    Write-Host "Report written to $reportPath"
}
finally {
    Remove-Item Env:LOCAL_SCORER_REPO, Env:LOCAL_SCORER_ENDPOINT, Env:LOCAL_SCORER_OUTPUT, Env:LOCAL_SCORER_MODELS, Env:LOCAL_SCORER_WARMUP_WAIT -ErrorAction SilentlyContinue
    try {
        $process.Refresh()
        if (-not $process.HasExited -and $process.MainModule.FileName -eq $llamaSwap.FullName) {
            $process.Kill($true)
            $process.WaitForExit(10000)
        }
    } catch {
        Write-Warning "Could not confirm shutdown of benchmark-owned llama-swap PID $processId."
    }
}
