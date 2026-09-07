# Reference verification — 2026-09-07

- Installed declared Pixi dependencies; lockfile generated for osx-arm64 and linux-64.
  Execution was tested on this Mac, not on Linux.
- 30 deterministic reference tests passed, including a real authenticated HTTP
  loopback service with simulated OpenRouter. No model is called by this suite.
- 9 profile schema tests passed after credential discovery was added; all 11
  message examples remain valid. Vendored schema matches the profile byte-for-byte.
- Package check passed; public CogSpec reference validation passed.
- Smith core/profile check: zero errors, one expected warning that this custom
  runtime is not Smith template machinery. Runtime evidence comes from this
  repository's test suite, not from the skipped template-runtime checks.
- Both cog-author and cog-build-evaluator were invoked through their real CLIs
  against the authenticated gateway with simulated OpenRouter outputs. Their
  result envelopes had no contract problems; both gateway runs retained separate
  draft-binding provenance. Their legacy envelopes still do not import that metadata.
- Live public catalog access through the implemented command succeeded for
  openai/gpt-4.1-mini (three catalog endpoints at verification time).
- OPENROUTER_API_KEY was not available. Live authenticated preparation,
  admission probes and real model inference have not been verified. Test keys
  were process-local fixture strings, not working service credentials.
- No paid requests, background service installation or GitHub publication occurred.

To complete live verification, configure the two dedicated credential references,
review the example model/routing choice, prepare/admit the binding, run one
completion and inspect its run record. Functional probe mode makes separate
provider and host inference calls; choose it explicitly when needed.
