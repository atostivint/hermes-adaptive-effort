"""Plugin registration: manifest + register(ctx) contract."""

from __future__ import annotations



from conftest import PLUGIN_DIR, import_plugin

init_module = import_plugin("__init__")


class FakeCtx:
    def __init__(self):
        self.middleware = []
        self.hooks = []
        self.commands = []
        self.plugin_id = "jev-auto-effort"

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))
        return object()

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))
        return object()

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands.append((name, handler, description))
        return object()

    def get_config(self, key, default=None):
        return default


def test_manifest_exists_and_declares_the_capability():
    manifest_path = PLUGIN_DIR / "plugin.yaml"
    assert manifest_path.exists()
    try:
        from utils import fast_safe_load
    except Exception:  # pragma: no cover - core not importable in a bare env
        fast_safe_load = None
    text = manifest_path.read_text(encoding="utf-8")
    if fast_safe_load is None:
        data = {}
        for line in text.splitlines():
            if ":" in line and not line.startswith(("-", " ", "#")):
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
    else:
        data = fast_safe_load(text) or {}
    assert data.get("name") == "jev-auto-effort"
    assert data.get("kind", "standalone") in {"standalone", "general"}
    assert "llm_request" in text


def test_register_registers_llm_request_middleware_and_session_cleanup():
    ctx = FakeCtx()
    init_module.register(ctx)
    kinds = [k for k, _ in ctx.middleware]
    assert kinds == ["llm_request"]
    assert callable(ctx.middleware[0][1])
    hooks = [k for k, _ in ctx.hooks]
    assert "on_session_end" in hooks


def test_registered_callback_is_the_middleware_handler():
    middleware = import_plugin("middleware")
    ctx = FakeCtx()
    init_module.register(ctx)
    assert ctx.middleware[0][1] is middleware.on_llm_request
