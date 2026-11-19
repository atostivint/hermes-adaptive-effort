"""Live Nace Drex Max validation probe.

Writes ids, answer values, response ``model``, failure codes and latencies only.
Never writes the task text or the API key to any artifact.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_DIR = Path(sys.argv[1]).resolve()
OUT_DIR = Path(sys.argv[2]).resolve()
CANDIDATES = ("drex-max", "drex-max-v1.1", "drex-max-finance", "drex-latest")
PROMPT = "Summarise in one sentence why a bounded retry can cost less than one high-effort answer."
TIMEOUT = 30.0

PKG = "hermes_plugin_adaptive_effort"


def _bootstrap() -> None:
    spec = importlib.machinery.ModuleSpec(PKG, None, is_package=True)
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[PKG] = pkg


def load(stem):
    full = f"{PKG}.{stem}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, str(PLUGIN_DIR / f"{stem}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


_bootstrap()
nace_client = load("nace_client")
rubric = load("rubric")  # noqa: F401  (loads the shared parser modules through the adapter)
scorer_http = load("scorer_http")

KEY = os.environ.get("NACE_API_KEY", "").strip()
if not KEY:
    print(json.dumps({"fatal": "no NACE_API_KEY in environment"}))
    sys.exit(3)


class _CaptureResponse:
    """Proxy a real HTTP response, recording raw bytes on the single read json.load makes."""

    def __init__(self, inner, slot):
        self._inner = inner
        self._slot = slot

    @property
    def status(self):
        return getattr(self._inner, "status", None)

    def read(self, *args, **kwargs):
        raw = self._inner.read(*args, **kwargs)
        self._slot["raw"] = raw
        self._slot["status"] = getattr(self._inner, "status", None)
        return raw

    def __enter__(self):
        enter = getattr(self._inner, "__enter__", None)
        if enter:
            enter()
        return self

    def __exit__(self, *args):
        leave = getattr(self._inner, "__exit__", None)
        return leave(*args) if leave else False

    def close(self):
        close = getattr(self._inner, "close", None)
        if close:
            close()


def _make_capture():
    slot = {}

    def transport(request, timeout):
        try:
            return _CaptureResponse(scorer_http.default_transport(request, timeout), slot)
        except urllib.error.HTTPError as exc:
            try:
                slot["status"] = exc.code
                slot["raw"] = exc.read()
            except Exception:
                pass
            raise

    return slot, transport


def models_get():
    url = "https://console.nace.ai/v1/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {KEY}", "Accept": "application/json",
        "User-Agent": scorer_http.USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            data = {"error": "unparseable_body"}
    except Exception as exc:
        status = None
        data = {"error": type(exc).__name__}
    return {"status": status, "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "data": data}


def _model_items(data):
    if isinstance(data, dict):
        for key in ("data", "models"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []


def _returned_ids(data):
    result = []
    for item in _model_items(data):
        if isinstance(item, dict):
            result.append(item.get("name") or item.get("id"))
        else:
            result.append(item)
    return result


def _catalog_rows(data):
    rows = []
    for item in _model_items(data):
        if not isinstance(item, dict):
            continue
        rows.append({
            "name": item.get("name") or item.get("id"),
            "alias_for": item.get("alias_for"),
            "release_date": item.get("release_date"),
            "retired": "retired" in str(item.get("description", "")).lower(),
        })
    return rows


def _raw_info(slot):
    info = {"status": slot.get("status"), "model": None, "answers_shape": None,
            "answer_values": None, "top_keys": None, "raw_parse_error": False}
    raw = slot.get("raw")
    if raw is None:
        return info
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw)
    except Exception:
        info["raw_parse_error"] = True
        return info
    if not isinstance(data, dict):
        return info
    info["top_keys"] = sorted(data.keys())
    info["model"] = data.get("model")
    answers = data.get("answers")
    if isinstance(answers, dict):
        info["answers_shape"] = {
            k: (sorted(v.keys()) if isinstance(v, dict) else type(v).__name__)
            for k, v in answers.items()}
        info["answer_values"] = dict(answers)
    elif isinstance(answers, list):
        info["answers_shape"] = ["list", len(answers)]
    return info


def run_question(model_id, shape):
    slot, transport = _make_capture()
    client = nace_client.NaceClient(
        api_key=KEY, model=model_id, timeout=TIMEOUT, transport=transport)
    started = time.monotonic()
    try:
        if shape == "choice":
            value, failure = client.classify_effort_detail(PROMPT, ("low", "medium", "high"))
        elif shape == "score":
            value, failure = client.classify_detail(PROMPT)
        else:
            value, failure = client.classify_continuation(PROMPT)
    except Exception as exc:
        value, failure = None, f"exception:{type(exc).__name__}"
    latency = round((time.monotonic() - started) * 1000, 1)
    info = _raw_info(slot)
    return {
        "model_asked": model_id,
        "question_shape": shape,
        "response_model": info["model"],
        "answer": value,
        "failure": failure,
        "latency_ms": latency,
        "http_status": info["status"],
        "top_keys": info["top_keys"],
        "answers_shape": info["answers_shape"],
        "answer_values": info["answer_values"],
        "raw_parse_error": info["raw_parse_error"],
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = models_get()
    (OUT_DIR / "models_raw.json").write_text(
        json.dumps(models["data"], indent=2, ensure_ascii=False), encoding="utf-8")
    ids = _returned_ids(models["data"])
    available = [cid for cid in CANDIDATES if cid in ids]
    models_record = {
        "endpoint": "https://console.nace.ai/v1/models",
        "status": models["status"],
        "latency_ms": models["latency_ms"],
        "candidates": list(CANDIDATES),
        "available": available,
        "returned_ids": ids,
        "catalog": _catalog_rows(models["data"]),
    }
    (OUT_DIR / "models.json").write_text(json.dumps(models_record, indent=2), encoding="utf-8")

    results = []
    for cid in available:
        for shape in ("choice", "score", "continuation"):
            results.append(run_question(cid, shape))
            time.sleep(1.5)  # stay under Nace's burst rate limit
    with (OUT_DIR / "live_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT_DIR / "live_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"models_status": models["status"], "available": available,
                      "calls": len(results)}))


main()
