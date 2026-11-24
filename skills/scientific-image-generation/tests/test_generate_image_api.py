import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FAKE_KEY = "test-key-should-never-be-written"


def minimal_png(width=1024, height=576):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00\x00\x00\x00\x00"


PNG_B64 = base64.b64encode(minimal_png()).decode("ascii")
RESPONSES = {
    "ok": (200, {
        "modelVersion": "fake-banana",
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [
                {"text": "draft", "thought": True},
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(minimal_png(10, 10)).decode()}, "thought": True},
                {"text": "这是最终图。"},
                {"inlineData": {"mimeType": "image/png", "data": PNG_B64}},
            ]},
        }],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 1290},
    }),
    "safety": (200, {"promptFeedback": {"blockReason": "SAFETY"}}),
    "text_only": (200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "I cannot draw that."}]}}]}),
    "http_error": (400, {"error": {"code": 400, "message": "Invalid imageSize", "status": "INVALID_ARGUMENT"}}),
}
OPENROUTER = {
    "b64": (200, {"created": 1, "data": [{"b64_json": PNG_B64, "media_type": "image/png"}], "usage": {"total_tokens": 4175, "cost": 0.04}}),
    "url": (200, {"created": 1, "data": [{"url": "__BASE__/files/out.png", "media_type": "image/png"}], "usage": {"cost": 0.04}}),
    "empty": (200, {"created": 1, "data": []}),
    "no_credit": (402, {"error": {"code": 402, "message": "Insufficient credits", "metadata": {"limit_source": "openrouter_credits"}}}),
}
ENV_VARS = {
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_IMAGE_MODEL", "GEMINI_API_BASE",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "VERTEX_ACCESS_TOKEN", "VERTEX_API_KEY", "VERTEX_API_BASE",
    "OPENROUTER_API_KEY", "OPENROUTER_API_BASE", "OPENROUTER_IMAGE_MODEL",
}


@pytest.fixture
def fake_gemini():
    state = {"mode": "ok", "openrouter": "b64", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = minimal_png(800, 600)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state["requests"].append({
                "path": self.path,
                "key": self.headers.get("x-goog-api-key"),
                "auth": self.headers.get("Authorization"),
                "body": json.loads(self.rfile.read(length)),
            })
            if self.path.endswith("/images"):
                status, payload = OPENROUTER[state["openrouter"]]
                payload = json.loads(json.dumps(payload).replace("__BASE__", state["root"]))
            else:
                status, payload = RESPONSES[state["mode"]]
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["root"] = f"http://127.0.0.1:{server.server_port}"
    state["base"] = state["root"] + "/v1"
    yield state
    server.shutdown()


def run(script, *args, env_extra=None, check=True):
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS}
    env["PATH"] = os.path.dirname(sys.executable)  # no gcloud on PATH: token must come from env
    env.update(env_extra or {})
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed


def run_dir_from(stdout):
    for line in stdout.splitlines():
        if line.startswith("Run directory:"):
            return Path(line.split(":", 1)[1].strip())
    raise AssertionError(stdout)


def prepare(tmp_path, *extra):
    out = run(
        "prepare_image_run.py",
        "--prompt", "细胞信号通路示意图：受体、激酶、转录因子",
        "--required-labels", "受体", "激酶", "转录因子",
        "--aspect-ratio", "16:9", "--image-size", "2k",
        "--backend", "api-image",
        "--output-dir", tmp_path / "runs",
        *extra,
    )
    return run_dir_from(out.stdout)


def env_for(fake):
    return {"GEMINI_API_KEY": FAKE_KEY, "GEMINI_API_BASE": fake["base"]}


def all_text(run_dir):
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in run_dir.iterdir() if p.suffix in {".json", ".txt", ".md"})


def test_success_registers_final_image_and_redacts(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path)
    run("generate_image_api.py", "--run-dir", run_dir, env_extra=env_for(fake_gemini))

    sent = fake_gemini["requests"][0]
    assert sent["path"] == "/v1/models/gemini-nano-banana-2.1:generateContent"
    assert sent["key"] == FAKE_KEY
    config = sent["body"]["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
    assert "受体" in sent["body"]["contents"][0]["parts"][0]["text"]

    check = json.loads((run_dir / "check.json").read_text(encoding="utf-8"))
    assert (check["width"], check["height"]) == (1024, 576)  # final image, not the thought draft
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["backend"] == "api-image"
    assert metadata["model"] == "gemini-nano-banana-2.1"
    assert metadata["usage"]["candidatesTokenCount"] == 1290
    summary = json.loads((run_dir / "response_summary.json").read_text(encoding="utf-8"))
    assert summary["text"] == "这是最终图。"
    manifest = json.loads((run_dir / "figure_manifest.json").read_text(encoding="utf-8"))
    assert manifest["task"]["model"] == "gemini-nano-banana-2.1"
    assert manifest["artifacts"]["request"] == str(run_dir / "request.json")

    text = all_text(run_dir)
    assert FAKE_KEY not in text
    assert PNG_B64 not in text


def test_reference_image_is_sent_but_not_stored(tmp_path, fake_gemini):
    ref = tmp_path / "parent.png"
    ref.write_bytes(minimal_png(640, 360))
    run_dir = prepare(tmp_path, "--reference-image", ref, "--reference-role", "edit_image")
    assert "见随附图片" in (run_dir / "image_prompt.txt").read_text(encoding="utf-8")
    assert str(ref) not in (run_dir / "image_prompt.txt").read_text(encoding="utf-8")

    run("generate_image_api.py", "--run-dir", run_dir, "--model", "gemini-3-pro-image", env_extra=env_for(fake_gemini))
    sent = fake_gemini["requests"][0]
    assert sent["path"].endswith("/models/gemini-3-pro-image:generateContent")
    inline = sent["body"]["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "image/png"
    assert base64.b64decode(inline["data"]) == minimal_png(640, 360)
    stored = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    assert stored["body"]["contents"][0]["parts"][1]["inline_data"]["data"].startswith("<omitted")


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("safety", "Gemini blocked the prompt: SAFETY"),
        ("text_only", "Gemini response contained no image"),
        ("http_error", "Gemini request failed (HTTP 400)"),
    ],
)
def test_failures_write_blocker_without_image(tmp_path, fake_gemini, mode, reason):
    fake_gemini["mode"] = mode
    run_dir = prepare(tmp_path)
    completed = run("generate_image_api.py", "--run-dir", run_dir, env_extra=env_for(fake_gemini), check=False)
    assert completed.returncode == 2
    blocker = json.loads((run_dir / "blocker.json").read_text(encoding="utf-8"))
    assert blocker["reason"] == reason
    assert not list(run_dir.glob("generated_image.*"))
    assert FAKE_KEY not in all_text(run_dir)


def test_missing_key_blocks_before_any_request(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path)
    status = json.loads(run("run_scientific_image_workflow.py", "status", "--run-dir", run_dir).stdout)
    assert "generate_image_api.py" in status["next"]
    completed = run("generate_image_api.py", "--run-dir", run_dir, env_extra={"GEMINI_API_BASE": fake_gemini["base"]}, check=False)
    assert completed.returncode == 2
    assert fake_gemini["requests"] == []
    assert json.loads((run_dir / "blocker.json").read_text(encoding="utf-8"))["reason"] == "missing GEMINI_API_KEY"


def test_edit_repair_sends_previous_image_as_reference(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path)
    run("generate_image_api.py", "--run-dir", run_dir, env_extra=env_for(fake_gemini))
    review = {
        "overall_ready_to_ship": False,
        "quality_review_axes": [
            {"name": n, "status": "FAIL" if n == "text_readability" else "PASS", "score": 5, "rationale": "x"}
            for n in ["content_accuracy", "layout_quality", "text_readability", "aesthetic_quality", "artifact_severity"]
        ],
        "missing_required_labels": ["转录因子"],
        "forbidden_labels_seen": [],
        "issues": ["第三个标签拼错"],
        "critic_suggestions": "修正第三个标签。",
        "revised_description": "保持构图，只把第三个标签改为转录因子。",
        "recommended_next_action": "edit",
        "next_edit_prompt": "只修正第三个标签",
    }
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    run("host_semantic_review.py", "record", "--run-dir", run_dir, "--review-json", review_path)

    repair = run("run_scientific_image_workflow.py", "repair", "--run-dir", run_dir)
    repair_dir = run_dir_from(repair.stdout)
    repair_input = json.loads((repair_dir / "run_input.json").read_text(encoding="utf-8"))
    assert repair_input["backend"] == "api-image"
    assert repair_input["reference_role"] == "edit_image"
    assert list(repair_dir.glob("reference_image.*"))

    run("generate_image_api.py", "--run-dir", repair_dir, env_extra=env_for(fake_gemini))
    assert "inline_data" in fake_gemini["requests"][-1]["body"]["contents"][0]["parts"][1]


def test_vertex_oauth_uses_project_location_and_bearer(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path, "--image-provider", "vertex")
    token = "ya29.fake-token-should-not-be-written"
    run("generate_image_api.py", "--run-dir", run_dir, env_extra={
        "GOOGLE_CLOUD_PROJECT": "demo-proj", "VERTEX_ACCESS_TOKEN": token, "VERTEX_API_BASE": fake_gemini["base"],
    })
    sent = fake_gemini["requests"][0]
    assert sent["path"] == "/v1/projects/demo-proj/locations/global/publishers/google/models/gemini-nano-banana-2.1:generateContent"
    assert sent["auth"] == "Bearer " + token
    assert sent["body"]["contents"][0]["role"] == "USER"
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert (metadata["image_provider"], metadata["auth_mode"]) == ("vertex", "oauth")
    assert token not in all_text(run_dir)


def test_vertex_express_uses_api_key_without_project(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path, "--image-provider", "vertex")
    run("generate_image_api.py", "--run-dir", run_dir, "--model", "gemini-3-pro-image", env_extra={
        "VERTEX_API_KEY": FAKE_KEY, "VERTEX_API_BASE": fake_gemini["base"],
    })
    sent = fake_gemini["requests"][0]
    assert sent["path"] == "/v1/publishers/google/models/gemini-3-pro-image:generateContent"
    assert sent["key"] == FAKE_KEY
    assert FAKE_KEY not in all_text(run_dir)


def test_vertex_without_credentials_blocks(tmp_path, fake_gemini):
    run_dir = prepare(tmp_path, "--image-provider", "vertex")
    completed = run("generate_image_api.py", "--run-dir", run_dir, env_extra={"VERTEX_API_BASE": fake_gemini["base"]}, check=False)
    assert completed.returncode == 2
    assert fake_gemini["requests"] == []
    assert json.loads((run_dir / "blocker.json").read_text(encoding="utf-8"))["reason"] == "missing Vertex credentials"
    completed = run("generate_image_api.py", "--run-dir", run_dir, env_extra={
        "GOOGLE_CLOUD_PROJECT": "demo-proj", "VERTEX_API_BASE": fake_gemini["base"],
    }, check=False)
    assert json.loads((run_dir / "blocker.json").read_text(encoding="utf-8"))["reason"] == "missing Vertex access token"


def openrouter_env(fake):
    return {"OPENROUTER_API_KEY": FAKE_KEY, "OPENROUTER_API_BASE": fake["base"]}


def test_openrouter_b64_with_reference(tmp_path, fake_gemini):
    ref = tmp_path / "parent.png"
    ref.write_bytes(minimal_png(640, 360))
    run_dir = prepare(tmp_path, "--image-provider", "openrouter", "--reference-image", ref, "--reference-role", "edit_image")
    run("generate_image_api.py", "--run-dir", run_dir, "--model", "gemini-3-pro-image", env_extra=openrouter_env(fake_gemini))
    sent = fake_gemini["requests"][0]
    assert sent["path"] == "/v1/images"
    assert sent["auth"] == "Bearer " + FAKE_KEY
    body = sent["body"]
    assert body["model"] == "google/gemini-3-pro-image"
    assert (body["aspect_ratio"], body["resolution"], body["response_format"]) == ("16:9", "2K", "b64_json")
    assert body["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["model"] == "google/gemini-3-pro-image"
    assert metadata["usage"]["cost"] == 0.04
    text = all_text(run_dir)
    assert FAKE_KEY not in text and PNG_B64 not in text


def test_openrouter_url_response_is_downloaded(tmp_path, fake_gemini):
    fake_gemini["openrouter"] = "url"
    run_dir = prepare(tmp_path, "--image-provider", "openrouter")
    run("generate_image_api.py", "--run-dir", run_dir, env_extra=openrouter_env(fake_gemini))
    assert fake_gemini["requests"][0]["body"]["model"] == "google/gemini-nano-banana-2.1"
    check = json.loads((run_dir / "check.json").read_text(encoding="utf-8"))
    assert (check["width"], check["height"]) == (800, 600)


@pytest.mark.parametrize(
    "mode,reason",
    [("empty", "OpenRouter response contained no image"), ("no_credit", "OpenRouter request failed (HTTP 402)")],
)
def test_openrouter_failures_block(tmp_path, fake_gemini, mode, reason):
    fake_gemini["openrouter"] = mode
    run_dir = prepare(tmp_path, "--image-provider", "openrouter")
    completed = run("generate_image_api.py", "--run-dir", run_dir, env_extra=openrouter_env(fake_gemini), check=False)
    assert completed.returncode == 2
    assert json.loads((run_dir / "blocker.json").read_text(encoding="utf-8"))["reason"] == reason
    assert not list(run_dir.glob("generated_image.*"))
