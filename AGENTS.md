# Cog OpenRouter contributor instructions
Read COG.md, README.md and docs/spec-findings.md. This is a custom model-access
runtime, not Smith's context-cog template. No Smith machinery is vendored.
Provider code must never admit its own binding. The reference host is a separate
host-side implementation and independently checks fresh provider evidence.
No provider/model/routing override on inference. No agent tools or subscriptions.
Keep credentials in the dedicated environment variables, never files or logs.
Run pixi run test and pixi run check. Keep the vendored contract byte-identical
to the explicitly recorded profile schema; note any draft revisions.
