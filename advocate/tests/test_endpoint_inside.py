"""Verify the advocate endpoint WITHOUT a proxy token.

    uv run modal run tests/test_endpoint_inside.py

1. inside(): same image, same llama-server command, same cpu/memory as the deployed Server, but
   started on localhost inside a Modal container. POSTs a chat completion, prints the reply,
   tokens per second and the raw JSON, and checks the max_completion_tokens cap is honoured.
2. Locally: the raw JSON must validate against the OpenAI schema and run through pydantic-ai's
   OpenAIChatModel (replayed offline over an httpx MockTransport, no network, no keys).
3. Locally: an unauthenticated request to the deployed URL must be rejected (401/407).
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import serve_modal as sm  # noqa: E402

app = sm.app
image = sm.image.add_local_python_source("serve_modal")

PROMPT = (
    "Write a polite letter of about 150 words to the practice manager of Example Surgery A. "
    "Their registration page says: 'You must bring photo ID and proof of address to register.' "
    "The NHS guidance on nhs.uk says you do not need proof of address or ID to register with a GP. "
    "Ask them to update the page. Quote their sentence exactly."
)
BASE = f"http://127.0.0.1:{sm.PORT}"


def _chat(**body) -> dict:
    data = json.dumps({"model": sm.MODEL_ID, "messages": [{"role": "user", "content": PROMPT}], **body}).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=900))


@app.function(image=image, cpu=sm.CPU, memory=sm.MEMORY_MIB, timeout=1200)
def inside() -> dict:
    cpu = subprocess.run("nproc; grep -m1 'model name' /proc/cpuinfo", shell=True, capture_output=True, text=True).stdout
    t0 = time.time()
    proc = subprocess.Popen(sm.llama_cmd("127.0.0.1"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        assert proc.poll() is None, "llama-server exited during startup"
        try:
            if urllib.request.urlopen(f"{BASE}/health", timeout=2).status == 200:
                break
        except OSError:
            time.sleep(0.5)
    load_s = time.time() - t0

    t1 = time.time()
    raw = _chat(max_completion_tokens=400, temperature=0.3)
    wall_s = time.time() - t1
    capped = _chat(max_completion_tokens=16, temperature=0.3)
    models = json.load(urllib.request.urlopen(f"{BASE}/v1/models", timeout=10))
    proc.terminate()
    return {"cpu": cpu, "load_s": load_s, "wall_s": wall_s, "raw": raw, "capped_usage": capped["usage"],
            "capped_finish": capped["choices"][0]["finish_reason"], "model_ids": [m["id"] for m in models["data"]]}


def check_openai_schema(raw: dict) -> str:
    """The raw llama.cpp JSON must pass the OpenAI SDK model and pydantic-ai's own model, with no patching."""
    import httpx2 as httpx  # openai 3.x and pydantic-ai 2.x use httpx2, not httpx
    from openai.types.chat import ChatCompletion
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel, _ChatCompletion
    from pydantic_ai.providers.openai import OpenAIProvider

    ChatCompletion.model_validate(raw)
    _ChatCompletion.model_validate(raw)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
    provider = OpenAIProvider(base_url="http://offline/v1", api_key="offline", http_client=httpx.AsyncClient(transport=transport))
    result = asyncio.run(Agent(OpenAIChatModel(sm.MODEL_ID, provider=provider)).run("replay"))
    assert result.output == raw["choices"][0]["message"]["content"]
    assert result.usage.output_tokens == raw["usage"]["completion_tokens"]
    return result.output


def check_auth_is_on() -> tuple[str, int]:
    import ssl

    import certifi  # python.org macOS builds have no CA bundle; certifi ships with modal
    import modal

    url = modal.Server.from_name(sm.app.name, "Llm").get_url()
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        status = urllib.request.urlopen(f"{url}/v1/models", timeout=60, context=ctx).status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status in (401, 407), f"unauthenticated request was NOT rejected: HTTP {status}"
    return url, status


@app.local_entrypoint()
def main():
    r = inside.remote()
    raw = r["raw"]
    standard = {"id", "object", "created", "model", "choices", "usage", "system_fingerprint", "service_tier"}
    print("CPU:", r["cpu"].strip().replace("\n", " | "))
    print(f"server load (process start to /health 200): {r['load_s']:.1f} s")
    print("REPLY:\n" + raw["choices"][0]["message"]["content"])
    print("\nRAW JSON:\n" + json.dumps(raw, indent=1))
    print("\nnon-standard top-level fields:", sorted(set(raw) - standard))
    print("model ids served:", r["model_ids"])
    t = raw["timings"]
    print(f"prompt: {t['prompt_n']} tok at {t['prompt_per_second']:.1f} tok/s | "
          f"generation: {t['predicted_n']} tok at {t['predicted_per_second']:.1f} tok/s | wall {r['wall_s']:.1f} s")
    print("capped call (max_completion_tokens=16):", r["capped_usage"], r["capped_finish"])

    assert raw["model"] == sm.MODEL_ID and sm.MODEL_ID in r["model_ids"]
    assert r["capped_usage"]["completion_tokens"] <= 16 and r["capped_finish"] == "length"
    assert t["predicted_per_second"] >= 5, "slower than the 5 tok/s floor (hosts vary: 9.6 to 25 measured)"
    check_openai_schema(raw)
    print("OK: OpenAI SDK schema, pydantic-ai _ChatCompletion and an offline Agent replay all accept the raw JSON")
    url, status = check_auth_is_on()
    print(f"OK: unauthenticated GET {url}/v1/models -> HTTP {status}")
