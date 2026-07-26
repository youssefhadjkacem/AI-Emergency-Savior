import asyncio
import base64
import json
import logging
import os
import sys

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from hospital import router as hospital_router  # NEW: module hôpitaux

sys.path.append(os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Emergency Savior API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(hospital_router)  # NEW: monte /api/hospitals/...

# ── HF Space base URLs ─────────────────────────────────────────────────────────
HF_AUDIO_BASE = "https://youssef0081-emergency-savior-speech.hf.space/"
HF_CV_BASE    = "https://azizgharbi1-model-cv-pcd.hf.space/"
HF_OPT_BASE   = "https://youssef0081-emergency-savior-output.hf.space/"

# ── Gradio 4.x 2-step call endpoints ──────────────────────────────────────────
HF_AUDIO_URL  = "https://youssef0081-emergency-savior-speech.hf.space/gradio_api/call/gradio_pipeline"
HF_CV_URL     = "https://azizgharbi1-model-cv-pcd.hf.space/gradio_api/call/predict"
HF_OPT_URL    = "https://youssef0081-emergency-savior-output.hf.space/gradio_api/call/predict"

TIMEOUT = httpx.Timeout(180.0, connect=30.0)


# ── Helpers ────────────────────────────────────────────────────────────────────
def encode_bytes(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def get_mime(content_type: str) -> str:
    return {
        "image/jpeg": "image/jpeg",
        "image/jpg":  "image/jpeg",
        "image/png":  "image/png",
        "image/webp": "image/webp",
        "image/bmp":  "image/bmp",
    }.get(content_type, "image/jpeg")


async def wake_space(client: httpx.AsyncClient, base_url: str) -> None:
    try:
        await client.get(base_url, timeout=httpx.Timeout(15.0))
        logger.info(f"Woke: {base_url}")
    except Exception as e:
        logger.warning(f"Wake failed {base_url}: {e}")


async def gradio_call(client: httpx.AsyncClient, url: str, data: list) -> list:
    """
    Gradio 4.x 2-step call:
      1. POST  → {"event_id": "..."}
      2. GET   → SSE stream → parse "data:" line → return parsed list
    """
    # Step 1: submit
    resp1 = await client.post(url, json={"data": data})
    if resp1.status_code != 200:
        raise HTTPException(502, f"Gradio submit error {resp1.status_code} at {url}: {resp1.text[:400]}")

    event_id = resp1.json().get("event_id")
    if not event_id:
        raise HTTPException(502, f"No event_id returned from {url}")

    # Step 2: retrieve result (SSE)
    # FIXED: append event_id directly to the call URL instead of string replace
    result_url = f"{url}/{event_id}"

    resp2 = await client.get(result_url)
    if resp2.status_code != 200:
        raise HTTPException(502, f"Gradio result error {resp2.status_code}: {resp2.text[:400]}")

    # Parse SSE: find the last "data:" line
    result_data = []
    for line in resp2.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                result_data = json.loads(line[5:].strip())
            except Exception:
                pass

    return result_data


def parse_cv_output(hf_data: list) -> dict:
    annotated_b64 = None
    raw_img = hf_data[0] if len(hf_data) > 0 else None
    if isinstance(raw_img, str):
        annotated_b64 = raw_img.split(",", 1)[1] if "," in raw_img else raw_img
    elif isinstance(raw_img, dict):
        inner = raw_img.get("data", "")
        annotated_b64 = inner.split(",", 1)[1] if "," in inner else inner

    summary = hf_data[1] if len(hf_data) > 1 else ""
    conditions, risk_score, risk_level = [], 0.0, "UNKNOWN"

    for line in summary.splitlines():
        line = line.strip()
        if line.lower().startswith("detected conditions:"):
            raw = line.split(":", 1)[1].strip()
            conditions = [] if raw.lower() == "none" else [c.strip() for c in raw.split(",") if c.strip()]
        elif line.lower().startswith("risk score:"):
            try:
                risk_score = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.lower().startswith("risk level:"):
            risk_level = line.split(":", 1)[1].strip().upper()

    return {
        "annotated_image": annotated_b64,
        "conditions":      conditions,
        "risk_score":      risk_score,
        "risk_level":      risk_level,
        "summary":         summary,
    }


def parse_optimization_output(raw_text: str) -> dict:
    result = {
        "detected_symptoms":       [],
        "recommended_specialties": [],
        "best_provider":           None,
        "raw_output":              raw_text,
    }

    if not raw_text:
        return result

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        line_lower = line.lower()

        # ── Symptoms ──────────────────────────────────────────────────────────
        if (line_lower.startswith("symptômes détectés") or
                line_lower.startswith("symptomes détectés") or
                line_lower.startswith("symptomes detectes") or
                line_lower.startswith("detected symptoms")):
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                result["detected_symptoms"] = [
                    s.strip() for s in parts[1].split(",") if s.strip()
                ]

        # ── Specialties — lines like: "- Cardiologie (85.0%)" ────────────────
        elif line.startswith("-"):
            content = line.lstrip("- ").strip()
            if "(" in content and "%" in content:
                # Extract name and score
                paren_open  = content.rfind("(")
                paren_close = content.rfind("%")
                if paren_open != -1 and paren_close != -1:
                    name      = content[:paren_open].strip()
                    score_str = content[paren_open + 1: paren_close].strip()
                    try:
                        result["recommended_specialties"].append({
                            "specialty": name,
                            "score":     float(score_str),
                        })
                    except ValueError:
                        pass

        # ── Best provider ─────────────────────────────────────────────────────
        elif (line_lower.startswith("meilleur médecin") or
              line_lower.startswith("meilleur medecin") or
              line_lower.startswith("best doctor") or
              line_lower.startswith("best provider")):
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                result["best_provider"] = parts[1].strip()

    return result


@app.post("/analyze")
async def analyze_audio(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await wake_space(client, HF_AUDIO_BASE)

        upload_resp = await client.post(
            "https://youssef0081-emergency-savior-speech.hf.space/gradio_api/upload",
            files={"files": (audio.filename, audio_bytes, audio.content_type)},
        )
        if upload_resp.status_code != 200:
            raise HTTPException(502, f"Audio upload error: {upload_resp.text[:300]}")

        uploaded_path = upload_resp.json()[0]

        hf_data = await gradio_call(
            client, HF_AUDIO_URL,
            [
                {
                    "path": uploaded_path,
                    "meta": {"_type": "gradio.FileData"},
                    "mime_type": audio.content_type or "audio/wav",
                },
                True
            ]
        )

    pipeline_output = hf_data[0] if hf_data else {}
    if isinstance(pipeline_output, str):
        try:
            pipeline_output = json.loads(pipeline_output)
        except Exception:
            pass

    return {
        "transcript": pipeline_output.get("transcript", ""),
        "age_group":  pipeline_output.get("age_group", "unknown"),
    }


# ── /optimize — Optimization only ─────────────────────────────────────────────
@app.post("/optimize")
async def optimize(
    symptoms_text: str,
    age:           float | None = None,
    urgent:        bool         = False,
    budget:        float | None = None,
    location:      str  | None  = None,
):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await wake_space(client, HF_OPT_BASE)
        hf_data = await gradio_call(
            client, HF_OPT_URL,
            [
                symptoms_text,
                age if age is not None else 0,
                urgent,
                budget if budget is not None else 0,
                location or "",
            ]
        )

    raw_text = hf_data[0] if hf_data else ""
    return parse_optimization_output(raw_text)


# ── /analyze-full — All 3 pipelines ───────────────────────────────────────────
@app.post("/analyze-full")
async def analyze_full(
    audio:    UploadFile    = File(...),
    image:    UploadFile    = File(None),
    budget:   float | None  = None,
    location: str   | None  = None,
):
    audio_bytes = await audio.read()

    image_bytes = await image.read() if image else None
    data_uri    = (
        f"data:{get_mime(image.content_type)};base64,{encode_bytes(image_bytes)}"
        if image_bytes else None
    )

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:

        # Wake all spaces
        wake_targets = [HF_AUDIO_BASE, HF_OPT_BASE]
        if data_uri:
            wake_targets.append(HF_CV_BASE)
        await asyncio.gather(*[wake_space(client, u) for u in wake_targets])

        # Step 1: upload audio file to HF space
        upload_resp = await client.post(
            "https://youssef0081-emergency-savior-speech.hf.space/gradio_api/upload",
            files={"files": (audio.filename or "recording.wav", audio_bytes, "audio/wav")},
        )
        if upload_resp.status_code != 200:
            raise HTTPException(502, f"Audio upload error: {upload_resp.text[:300]}")

        uploaded_path = upload_resp.json()[0]

        # Step 2: call audio pipeline with uploaded file path
        audio_data = await gradio_call(
            client, HF_AUDIO_URL,
            [
                {
                    "path": uploaded_path,
                    "meta": {"_type": "gradio.FileData"},
                    "mime_type": "audio/wav",
                },
                True
            ]
        )

        pipeline_output = audio_data[0] if audio_data else {}
        if isinstance(pipeline_output, str):
            try:
                pipeline_output = json.loads(pipeline_output)
            except Exception:
                pass

        transcript  = pipeline_output.get("transcript", "")
        age_group   = pipeline_output.get("age_group", "unknown")
        age_map     = {"child": 10, "adult": 35, "senior": 70}
        numeric_age = age_map.get(age_group.lower(), 35)

        # Step 3: CV (optional)
        cv_result = {}
        if data_uri:
            try:
                cv_data   = await gradio_call(client, HF_CV_URL, [data_uri])
                cv_result = parse_cv_output(cv_data)
            except Exception as e:
                logger.warning(f"CV Space failed: {e}")

        risk_level = cv_result.get("risk_level", "UNKNOWN")
        is_urgent  = risk_level in ("HIGH", "CRITICAL") or age_group.lower() == "senior"

        # Step 4: optimization
        enriched_text = transcript
        if cv_result.get("conditions"):
            conditions_str = ", ".join(cv_result["conditions"])
            enriched_text = f"{transcript}. Visual assessment detected: {conditions_str}. Risk level: {risk_level}."

        opt_data = await gradio_call(
            client, HF_OPT_URL,
            [
                enriched_text,
                numeric_age,
                is_urgent,
                budget if budget is not None else 0,
                location or "",
            ]
        )
        raw_text   = opt_data[0] if opt_data else ""
        logger.info(f"OPTIMIZATION RAW OUTPUT:\n{raw_text}")
        opt_result = parse_optimization_output(raw_text)

    return {
        "transcript":              transcript,
        "age_group":               age_group,
        "annotated_image":         cv_result.get("annotated_image"),
        "wound_conditions":        cv_result.get("conditions", []),
        "risk_score":              cv_result.get("risk_score", 0.0),
        "risk_level":              risk_level,
        "enriched_symptoms":       enriched_text,
        "detected_symptoms":       opt_result["detected_symptoms"],
        "recommended_specialties": opt_result["recommended_specialties"],
        "best_provider":           opt_result["best_provider"],
        "optimization_raw":        opt_result["raw_output"],
        "auto_urgent":             is_urgent,
        "numeric_age_used":        numeric_age,
    }


# ── /health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "spaces": {
            "audio":        HF_AUDIO_URL,
            "cv":           HF_CV_URL,
            "optimization": HF_OPT_URL,
        },
    }