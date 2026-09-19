# Advocate model endpoint (Modal, CPU only)

Deployment took place on 2026-09-19 via `serve_modal.py`. Since this Modal account does not have a payment method attached and declines all GPU allocations, the model executes on CPU using llama.cpp.

| | |
|---|---|
| App / Server | `opendoor-advocate-llm` / `Llm` (Modal Server primitive, `@app.server`) |
| URL | `https://m-simakhov--opendoor-advocate-llm-llm.eu-west.modal.direct` |
| OpenAI-compatible base URL | `https://m-simakhov--opendoor-advocate-llm-llm.eu-west.modal.direct/v1` |
| Model id string (`ADVOCATE_MODEL`) | `Qwen/Qwen2.5-7B-Instruct` (this is what `/v1/models` reports and what the response `model` field echoes) |
| Weights | `Qwen/Qwen2.5-7B-Instruct-GGUF`, `q4_k_m` (2 split files, 4.7 GB), official Qwen repo, ungated (downloaded with no token) |
| Licence | Apache 2.0. Repo tag `license:apache-2.0` on https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF ; LICENSE text at https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE |
| Serving | `ghcr.io/ggml-org/llama.cpp:server-b11046` (official prebuilt llama-server, build published 2026-09-19), weights baked into the image at build time |
| Resources | 16 physical CPU cores, 8192 MiB memory, context 8192 tokens, 1 slot per container, 16 threads |
| Scaling | `min_containers=0`, `max_containers=2`, `target_concurrency=1`, `scaledown_window=1200` (20 min), `startup_timeout=600` |
| Auth | Proxy auth REQUIRED (Modal default for Servers; `unauthenticated=True` is never set) |
| Routing | `routing_region="eu-west"` (the Gateway is `gateway-eu.pydantic.dev`); compute region unconstrained, so no regional price multiplier |

## Measured

All figures quoted below stem from executions carried out on 2026-09-19. As CPU hosts differ from one run to another, they should be interpreted as a general range.

* Cold starting the deployed Server required about 13 s from start to finish, with 8 s accounted for by model loading (the autoscaler requested 1 container at 12:31:38 UTC, the container tunnel established at 12:31:43, and llama-server announced "listening on http://0.0.0.0:8000" at 12:31:51). Looking inside the container, the duration from the start of the llama-server process to a 200 response on `/health` was 8.0 s.
* Regarding generation throughput across three runs of `tests/test_endpoint_inside.py` (evaluating a 102-token prompt producing 130 to 170 output tokens), speeds were 9.6, 12.8 and 11.6 tokens/s. Prompt evaluation reached 77 to 83 tokens/s. A previous test run using a different host achieved 25.2 tokens/s on this model, whereas attempting 32 threads produced 11.5 tokens/s (relying on 16 threads proves best because it matches the count of physical cores).
* One should anticipate approximately 10 tokens/s. Because the advocate restricts responses to a maximum of 800 tokens, producing an entire letter requires around 60 to 85 s on an active instance. Modal does not impose request timeouts on Servers (a 150 s ceiling applies to web_server functions, which prompted the adoption of `@app.server`).
* The system respects `max_completion_tokens` (the parameter transmitted by pydantic-ai): an upper limit of 16 delivered precisely 16 completion tokens alongside `finish_reason: "length"`.

## Cost (modal.com/pricing, fetched 2026-09-19)

Tariffs are $0.0000131 per physical core per second for CPU alongside $0.00000222 per GiB per second for RAM. Invoicing reflects whichever figure is greater between allocated resources and observed usage.

* Active instance: 16 x 0.0000131 x 3600 = $0.7546/h CPU + 8 x 0.00000222 x 3600 = $0.0639/h memory = **about $0.82 per hour per container**.
* Producing a single 800-token letter at 10 tokens/s: about 80 s = about $0.02.
* Every activation additionally incurs the 20 minute idle rundown period: about $0.27.
* Leaving a single container primed across a 2 hour presentation: about $1.64.

## Cold behaviour and the demo switch

Modal Servers do not hold requests in a queue when scaled down to zero. The initial call yields a **503** status while starting up the container, which becomes operational roughly 15 s later, requiring a single retry. To prevent this during presentation time, keep one container active in advance (both commands below work, each incurring about $0.82/h until restored to zero):

```bash
ADVOCATE_MIN_CONTAINERS=1 uv run modal deploy serve_modal.py          # redeploy with a warm container
uv run python -c "import modal; print(modal.Server.from_name('opendoor-advocate-llm','Llm').update_autoscaler(min_containers=1))"   # no redeploy
uv run python -c "import modal; print(modal.Server.from_name('opendoor-advocate-llm','Llm').update_autoscaler(min_containers=0))"   # back to zero after the demo
```

Triggering a redeployment will revert any manual `update_autoscaler` modifications back to the parameters specified in the decorator.

## Values for the Logfire Gateway BYOK form (human does this; no key or token exists in the repo)

1. Generate a proxy token with `uv run modal workspace proxy-tokens create` (this displays a `wk-...` id and a `ws-...` secret, with the secret presented only once). Should the token be restricted to an environment, execute `uv run modal workspace proxy-tokens allow <token-id> main`.
2. Within Logfire, proceed through Gateway to add provider:

| Field | Value |
|---|---|
| Provider name | `modal` (this is the `route='modal'` the code uses) |
| Base URL | `https://m-simakhov--opendoor-advocate-llm-llm.eu-west.modal.direct/v1` (replace the prefilled `https://api.modal.com/v1`) |
| Proxy token ID | your `wk-...` |
| Proxy token secret | your `ws-...` |

3. Inside `.env` (which must never be committed): `PYDANTIC_AI_GATEWAY_BASE_URL=https://gateway-eu.pydantic.dev/proxy`, `PYDANTIC_AI_GATEWAY_API_KEY=...`, `LOGFIRE_TOKEN=...`, `ADVOCATE_MODEL=Qwen/Qwen2.5-7B-Instruct`.

Execute an immediate validation check once a token is available (supplying the identical headers forwarded by the Gateway; using Bearer syntax `wk-id.ws-secret` is also supported):

```bash
curl -s https://m-simakhov--opendoor-advocate-llm-llm.eu-west.modal.direct/v1/chat/completions \
  -H "Modal-Key: $TOKEN_ID" -H "Modal-Secret: $TOKEN_SECRET" -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"Say hello in five words."}],"max_completion_tokens":20}'
```

## Response shape (pydantic-ai compatibility)

The raw JSON returned by llama.cpp includes an individual non-standard root attribute, `timings` (covering prompt processing and generation velocity). It lacks a `metadata` attribute, meaning the documentation recommendation to broaden `metadata.weight_versions` is unnecessary for this endpoint (keeping that logic in `advocate.py` causes no harm). Offline testing within `tests/test_endpoint_inside.py` established that this unmodified JSON conforms to `openai.types.chat.ChatCompletion`, meets pydantic-ai's internal `_ChatCompletion`, and succeeds in a full `Agent(OpenAIChatModel(...)).run()` replayed across an httpx2 MockTransport without requiring alterations.

## Verification done

```bash
uv run modal deploy serve_modal.py                 # deployed in 2.4 s (image cached from the build run)
uv run modal run tests/test_endpoint_inside.py     # in-container part ran 4 times (reply, tok/s, raw JSON, cap honoured); the full test incl. schema and auth checks passed end to end on the 4th run, after fixing local-only bugs (httpx2 import, usage property, macOS CA bundle)
```

* Submitting unauthenticated calls via `GET /v1/models` and `POST /v1/chat/completions` against the published URL produced **HTTP 401** `{"error":"proxy auth required"}` during both curl checks and script tests, verifying that proxy authentication is actively enforced.
* The Gateway hop was later exercised on 2026-09-19 by the live evidence runs through route `modal` (`challenge/evidence/rule_off.json`, `challenge/evidence/rule_on_v3.json`, and the echo test `challenge/evidence/echo.json`, all with dry_run false). An authenticated request from outside (not through the Gateway) remains UNVERIFIED; nevertheless, internal container testing tests the identical image, command and hardware resources on localhost.
