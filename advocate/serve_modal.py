"""Open Door advocate model: Qwen2.5-7B-Instruct (Apache 2.0) on Modal CPU, OpenAI-compatible.

Deploy:            uv run modal deploy serve_modal.py
Warm for the demo: ADVOCATE_MIN_CONTAINERS=1 uv run modal deploy serve_modal.py
Inside test:       uv run modal run tests/test_endpoint_inside.py

This Modal account has no payment method, so every GPU type is refused: CPU only.
Measured numbers, the URL and the Gateway BYOK form values are in ENDPOINT.md.
"""
import os
import subprocess

import modal

# Serving choice: the official prebuilt llama.cpp server image (ghcr.io/ggml-org/llama.cpp).
# It has llama-server already compiled for linux/amd64, so the image build is only a pull plus
# the weight download (about 2 minutes). llama-cpp-python[server] would compile llama.cpp from source.
LLAMA_TAG = "server-b11046"  # pinned build, published 2026-09-19

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"  # use this exact string as ADVOCATE_MODEL
HF_REPO = "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"
GGUF_1 = "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"  # llama.cpp finds part 2 next to part 1
GGUF_2 = "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
PORT = 8000
CPU = 16.0  # physical cores
MEMORY_MIB = 8192  # 4.7 GB weights + 8k context KV cache + buffers

# Weights are baked into the image (ungated repo, no token), so a cold start never re-downloads.
image = (
    modal.Image.from_registry(f"ghcr.io/ggml-org/llama.cpp:{LLAMA_TAG}", add_python="3.12")
    .entrypoint([])  # the upstream ENTRYPOINT is llama-server itself; Modal needs its own
    .run_commands(
        "mkdir -p /models && cd /models"
        f" && curl -fL --retry 3 -O {HF_REPO}/{GGUF_1}"
        f" && curl -fL --retry 3 -O {HF_REPO}/{GGUF_2}"
    )
)

app = modal.App("opendoor-advocate-llm", image=image)


def llama_cmd(host: str) -> list[str]:
    return [
        "/app/llama-server", "-m", f"/models/{GGUF_1}", "--alias", MODEL_ID,
        "--host", host, "--port", str(PORT),
        "-c", "8192", "-np", "1", "-t", str(int(CPU)),
    ]  # fmt: skip


# @app.server is the current Modal primitive for a process that speaks HTTP on a port.
# Proxy auth is REQUIRED by default (we never set unauthenticated=True): callers must send
# Modal-Key / Modal-Secret headers or "Authorization: Bearer <token id>.<token secret>".
# It has no 150 s request cap (web_server has one), which matters at CPU speeds.
# Trade-off: when scaled to zero the first request gets a 503 while the container boots. Retry.
@app.server(
    port=PORT,
    cpu=CPU,
    memory=MEMORY_MIB,
    min_containers=int(os.environ.get("ADVOCATE_MIN_CONTAINERS", "0")),  # 1 for the demo
    max_containers=2,
    target_concurrency=1,  # one generation per container; a second container only under overlap
    scaledown_window=20 * 60,
    startup_timeout=10 * 60,
    routing_region="eu-west",  # the Pydantic AI Gateway we call through is the EU one
)
class Llm:
    @modal.enter()
    def start(self):
        self.proc = subprocess.Popen(llama_cmd("0.0.0.0"))

    @modal.exit()
    def stop(self):
        self.proc.terminate()
