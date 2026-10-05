# Zenon local model effort check — 2026-10-05

This is a bounded, local-only check of the models present in the known `.local-scorer` store. It does not establish compatibility for every model or service installed on Zenon.

## Models and runtime

The local store contains two GGUF files: `Kev-0.8B-Q8_0.gguf` (812,406,304 bytes) and `Kev-4B-Q8_0.gguf` (4,483,801,504 bytes). The available runtime is llama.cpp `llama-server` build 11396 (CUDA 12.4) behind llama-swap v262. The existing local scorer configuration maps them to the `effort-kev-08b` and `effort-kev-4b` OpenAI-compatible chat model names.

No active local scorer was listening when checked. I started a private helper using that existing config on loopback port 8123, left the existing port 8099 and config untouched, and stopped only the helper I started after testing. Port 8123 was no longer listening afterward.

## Requests and observations

For each model, I sent a synthetic chat request with no effort field, then with `reasoning_effort: "low"` and `reasoning_effort: "high"`. All six returned HTTP 200. Each used a 16-token completion cap and ended with `finish_reason: "length"`.

I repeated those six requests with a 48-token cap; all again returned HTTP 200 and ended with `finish_reason: "length"`. Two further control requests at a 128-token cap also returned HTTP 200 and ended at the limit. Those responses had an empty `message.content` and a `message.reasoning_content` field (453 characters for 0.8B, 519 for 4B); the assistant turn was still incomplete. The API response contained no `reasoning_tokens` usage field and did not echo a `reasoning_effort` value.

The server therefore accepted requests containing both values, but these tests do not show that either value changed model behavior: the turns did not finish, and no controlled difference in reasoning effort can be inferred from field acceptance. The local OpenAPI metadata probe did not expose a chat-completions schema describing either `reasoning_effort` or `reasoning`; I found no positive schema evidence that llama.cpp implements this control. Treat the field as unverified and potentially ignored on this runtime.

## Scope limits

The audit covered the two GGUFs in the known `.local-scorer/models` directory, not every mounted drive or application-specific model cache. A bounded read-only inventory also found the LM Studio model folder empty, Ollama metadata only, and `C:/AI`, `C:/LLM`, and `C:/models` absent. The checked Downloads contents were voice ONNX files and unrelated binaries, not candidate LLM weights. W:, X:, Y:, and Z: were checked only at their top level; they were not searched recursively because they contain personal/shared data. No additional model candidate was found in the locations checked, but this does not establish that no other local models exist.

The audit did not run the old scorer benchmark, contact a cloud endpoint, inspect or print credentials, install/download models, or alter production configuration. No answer text or hidden reasoning text is reproduced here.
