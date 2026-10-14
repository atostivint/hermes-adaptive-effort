"""Single retry on a transient scorer HTTP status: helper rules, every adapter, one breaker count."""

from __future__ import annotations

import email.utils
import io
import json
import types
import urllib.error

import pytest

from conftest import import_plugin

scorer_retry = import_plugin("scorer_retry")
middleware = import_plugin("middleware")
jev_client = import_plugin("jev_client")
nace_client = import_plugin("nace_client")
openrouter_client = import_plugin("openrouter_client")
openai_client = import_plugin("openai_decision_client")
cloudflare_client = import_plugin("cloudflare_client")
custom_client = import_plugin("custom_client")


class FakeTime:
    """Fake monotonic/wall clock; ``sleep`` advances it and records the wait."""

    def __init__(self, wall=1_000_000.0):
        self.now = 0.0
        self.wall = wall
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.wall + self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(scorer_retry, "time", fake)
    return fake


def http_error(status, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError("https://scorer.invalid/x", status, "err", headers, io.BytesIO(b""))


class Sequence:
    """Transport popping one outcome per call (exceptions are raised); records timeouts."""

    def __init__(self, *outcomes, fake=None, cost=0.0):
        self.outcomes = list(outcomes)
        self.fake = fake
        self.cost = cost
        self.timeouts = []

    def __call__(self, request, timeout=None):
        self.timeouts.append(timeout)
        if self.fake is not None:
            self.fake.now += self.cost
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


# ── helper rules ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_status_then_success_retries_once(fake_time, status):
    transport = Sequence(http_error(status), "ok", fake=fake_time, cost=0.05)
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert len(transport.timeouts) == 2
    assert fake_time.sleeps == [scorer_retry.DEFAULT_WAIT_S]
    assert transport.timeouts[0] == 2.0
    assert transport.timeouts[1] == pytest.approx(2.0 - 0.05 - 0.1)


def test_response_object_with_transient_status_is_retried(fake_time):
    first = types.SimpleNamespace(status=503, headers={})
    transport = Sequence(first, "ok")
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert len(transport.timeouts) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_other_statuses_are_not_retried(fake_time, status):
    transport = Sequence(http_error(status), "ok")
    with pytest.raises(urllib.error.HTTPError):
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert len(transport.timeouts) == 1 and fake_time.sleeps == []


@pytest.mark.parametrize("error", [
    TimeoutError("slow"),
    urllib.error.URLError(TimeoutError("slow")),
    urllib.error.URLError("refused"),
    ConnectionResetError("reset"),
])
def test_timeouts_and_transport_errors_are_not_retried(fake_time, error):
    transport = Sequence(error, "ok")
    with pytest.raises(type(error)):
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert len(transport.timeouts) == 1


def test_second_failure_is_raised_and_no_third_attempt(fake_time):
    transport = Sequence(http_error(503), http_error(502), "never")
    with pytest.raises(urllib.error.HTTPError) as caught:
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert caught.value.code == 502
    assert len(transport.timeouts) == 2


def test_retry_after_longer_than_budget_means_no_retry(fake_time):
    transport = Sequence(http_error(429, "30"), "ok")
    with pytest.raises(urllib.error.HTTPError) as caught:
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert caught.value.code == 429
    assert len(transport.timeouts) == 1 and fake_time.sleeps == []


def test_retry_after_seconds_is_honoured_when_it_fits(fake_time):
    transport = Sequence(http_error(429, "1"), "ok")
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert fake_time.sleeps == [1.0]
    assert transport.timeouts[1] == pytest.approx(1.0)


def test_retry_after_that_leaves_less_than_minimum_is_declined(fake_time):
    # A 2 s wait on a 2 s budget leaves nothing; a 1 s wait leaves 1 s (>= 0.3 s) and runs.
    declined = Sequence(http_error(503, "2"), "ok")
    with pytest.raises(urllib.error.HTTPError):
        scorer_retry.send_with_retry(lambda t: declined(None, t), 2.0)
    assert len(declined.timeouts) == 1


def test_retry_after_http_date_is_honoured_when_it_fits(fake_time):
    when = email.utils.formatdate(fake_time.wall + 1, usegmt=True)
    transport = Sequence(http_error(503, when), "ok")
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert fake_time.sleeps == [pytest.approx(1.0, abs=1.0)]
    assert len(transport.timeouts) == 2


def test_retry_after_http_date_too_far_is_declined(fake_time):
    when = email.utils.formatdate(fake_time.wall + 120, usegmt=True)
    transport = Sequence(http_error(503, when), "ok")
    with pytest.raises(urllib.error.HTTPError):
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert len(transport.timeouts) == 1


@pytest.mark.parametrize("value", ["soon", "-5", "", "1e3"])
def test_unusable_retry_after_falls_back_to_default_wait(fake_time, value):
    transport = Sequence(http_error(503, value), "ok")
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert fake_time.sleeps == [scorer_retry.DEFAULT_WAIT_S]


def test_slow_first_attempt_leaves_no_budget_so_no_retry(fake_time):
    # 1.75 s spent of 2.0 s: after the 0.1 s wait only 0.15 s would remain (< 0.3 s).
    transport = Sequence(http_error(503), "ok", fake=fake_time, cost=1.75)
    with pytest.raises(urllib.error.HTTPError):
        scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0)
    assert len(transport.timeouts) == 1


def test_total_elapsed_never_exceeds_the_deadline(fake_time):
    transport = Sequence(http_error(503), http_error(503), fake=fake_time, cost=0.0)

    def send(timeout):
        before = fake_time.now
        try:
            return transport(None, timeout)
        finally:
            fake_time.now = before + min(timeout, 0.9)  # each attempt burns up to its timeout

    with pytest.raises(urllib.error.HTTPError):
        scorer_retry.send_with_retry(send, 2.0)
    assert fake_time.now <= 2.0 + 1e-9


def test_success_on_first_attempt_never_sleeps(fake_time):
    transport = Sequence("ok")
    assert scorer_retry.send_with_retry(lambda t: transport(None, t), 2.0) == "ok"
    assert fake_time.sleeps == []


# ── every adapter ───────────────────────────────────────────────────────────

class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _ok(payload):
    return _Response(json.dumps(payload).encode("utf-8"))


SCORE = {"score": 1.25}
PAYLOADS = {
    "jev": {"answers": {"effort": SCORE}},
    "nace": {"model": "drex", "answers": {"effort": {"type": "score", "score": 1.25}}},
    "openrouter": {"choices": [{"message": {"content": '{"score": 1.25}'}}]},
    "openai_decision": {"answers": [{"name": "effort", "type": "score", "score": 1.25,
                                     "probabilities": [], "confidence": 1.0}],
                        "model": "gpt-6-luna", "usage": {}},
    "cloudflare": {"success": True, "errors": [], "messages": [],
                   "result": {"answers": {"effort": SCORE}}},
    "custom": {"answers": {"effort": SCORE}},
}


def _client(name, transport):
    if name == "jev":
        return jev_client.JevClient(api_key="k", transport=transport, timeout=2.0)
    if name == "nace":
        return nace_client.NaceClient(api_key="k", transport=transport, timeout=2.0)
    if name == "openrouter":
        return openrouter_client.OpenRouterClient(
            model="vendor/model", api_key="k", transport=transport, timeout=2.0)
    if name == "openai_decision":
        return openai_client.OpenAIDecisionClient(
            key_reader=lambda: "k", transport=transport, timeout=2.0)
    if name == "cloudflare":
        return cloudflare_client.CloudflareClient(
            account_id="0123456789abcdef0123456789abcdef", api_key="k",
            transport=transport, timeout=2.0)
    return custom_client.CustomClient(
        endpoint="https://scorer.example/v1/score", model="rubric-v1",
        transport=transport, timeout=2.0)


ADAPTERS = sorted(PAYLOADS)


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_retries_a_503_once_and_uses_the_answer(fake_time, name):
    transport = Sequence(http_error(503), _ok(PAYLOADS[name]))
    score, failure = _client(name, transport).classify_detail("Rename a function")
    assert (score, failure) == (pytest.approx(1.25), None)
    assert len(transport.timeouts) == 2
    assert transport.timeouts[0] == 2.0
    assert transport.timeouts[1] <= 2.0 - scorer_retry.DEFAULT_WAIT_S + 1e-9


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_reports_http_error_when_both_attempts_fail(fake_time, name):
    transport = Sequence(http_error(503), http_error(502))
    assert _client(name, transport).classify_detail("Rename a function") == (None, "http_error")
    assert len(transport.timeouts) == 2


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_429_with_retry_after_beyond_budget_is_one_http_error(fake_time, name):
    transport = Sequence(http_error(429, "30"), _ok(PAYLOADS[name]))
    assert _client(name, transport).classify_detail("Rename a function") == (None, "http_error")
    assert len(transport.timeouts) == 1


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_timeout_is_not_retried(fake_time, name):
    transport = Sequence(TimeoutError("slow"), _ok(PAYLOADS[name]))
    assert _client(name, transport).classify_detail("Rename a function") == (None, "timeout")
    assert len(transport.timeouts) == 1


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_401_is_not_retried(fake_time, name):
    transport = Sequence(http_error(401), _ok(PAYLOADS[name]))
    assert _client(name, transport).classify_detail("Rename a function") == (None, "http_error")
    assert len(transport.timeouts) == 1


@pytest.mark.parametrize("name", ADAPTERS)
def test_adapter_malformed_answer_after_200_is_not_retried(fake_time, name):
    transport = Sequence(_Response(b"not json"), _ok(PAYLOADS[name]))
    assert _client(name, transport).classify_detail("Rename a function") == (
        None, "malformed_response")
    assert len(transport.timeouts) == 1


# ── middleware sees one call ────────────────────────────────────────────────

@pytest.fixture
def clean_middleware(monkeypatch):
    middleware.reset_state()
    yield
    middleware.reset_state()


def test_failed_attempt_plus_failed_retry_is_one_breaker_failure_and_one_latency_sample(
        monkeypatch, fake_time, clean_middleware):
    values = {"mode": "always", "scorer_provider": "jev", "scorer_pause_failures": 3}
    transport = Sequence(http_error(503), http_error(503))
    monkeypatch.setattr(middleware, "_settings_provider",
                        lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(middleware, "_classifier_factory",
                        lambda **_kw: jev_client.JevClient(
                            api_key="k", transport=transport, timeout=2.0))
    middleware.on_llm_request(
        request={"model": "gpt-5.6-terra",
                 "messages": [{"role": "user", "content": "Rename a function."}],
                 "reasoning": {"effort": "medium", "summary": "auto"}},
        session_id="s1", turn_id="t1", provider="openai-codex", model="gpt-5.6-terra",
        api_mode="codex_responses")
    assert len(transport.timeouts) == 2
    assert middleware._SCORER_BREAKER["jev"]["failures"] == 1
    assert sum(middleware._SCORER_LATENCY["jev"]) == 1
