import json
import os
import re
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from groq import Groq
except ImportError:  # pragma: no cover - only happens before dependencies are installed
    Groq = None


load_dotenv()

AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", "audit_log.jsonl"))
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

LABELS = {
    "likely_ai": (
        "Provenance Guard label: This content shows strong signs of AI generation. "
        "Our system is highly confident, but creators can appeal if this label is incorrect."
    ),
    "likely_human": (
        "Provenance Guard label: This content shows strong signs of human authorship. "
        "Our system is highly confident, though no automated review is perfect."
    ),
    "uncertain": (
        "Provenance Guard label: We do not have enough confidence to identify this as AI-generated "
        "or human-written. Readers should treat the authorship as uncertain."
    ),
}

CONTENT_STORE = {}


app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def tokenize_sentences(text):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return sentences or ([text.strip()] if text.strip() else [])


def tokenize_words(text):
    return re.findall(r"[A-Za-z']+", text.lower())


def fallback_llm_score(text):
    words = tokenize_words(text)
    if not words:
        return 0.5, "fallback_empty"

    ai_markers = [
        "transformative",
        "paradigm",
        "it is important to note",
        "ethical implications",
        "stakeholders",
        "furthermore",
        "responsible deployment",
        "various sectors",
        "numerous",
        "essential",
    ]
    human_markers = ["honestly", "ok", "way", "like", "drag", "thirsty", "friend", "won't"]
    lowered = text.lower()
    ai_hits = sum(1 for marker in ai_markers if marker in lowered)
    human_hits = sum(1 for marker in human_markers if marker in lowered)
    score = 0.5 + (ai_hits * 0.06) - (human_hits * 0.08)
    return clamp(score), "fallback_lexical"


def llm_detection_signal(text):
    """Return an AI-likelihood score from 0.0 to 1.0 plus a short rationale."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or Groq is None:
        score, source = fallback_llm_score(text)
        return {
            "score": score,
            "verdict": score_to_attribution(score),
            "rationale": f"Groq unavailable; used {source} estimate for local testing.",
            "source": source,
        }

    client = Groq(api_key=api_key)
    prompt = (
        "You are one signal in a provenance classifier. Estimate whether the submitted text "
        "was AI-generated. Return only JSON with keys ai_likelihood_score, verdict, and rationale. "
        "ai_likelihood_score must be a number from 0.0 for clearly human-written to 1.0 for clearly AI-generated. "
        "Use uncertainty honestly and avoid over-penalizing polished human writing.\n\n"
        f"Text:\n{text}"
    )

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content)
        score = clamp(float(payload.get("ai_likelihood_score", 0.5)))
        return {
            "score": score,
            "verdict": payload.get("verdict", score_to_attribution(score)),
            "rationale": payload.get("rationale", "No rationale provided."),
            "source": "groq",
        }
    except Exception as exc:  # keep the app usable during local demos if the API is unavailable
        score, source = fallback_llm_score(text)
        return {
            "score": score,
            "verdict": score_to_attribution(score),
            "rationale": f"Groq request failed ({exc.__class__.__name__}); used {source} estimate.",
            "source": source,
        }


def stylometric_signal(text):
    """Return structural AI-likelihood from 0.0 to 1.0 using transparent heuristics."""
    words = tokenize_words(text)
    sentences = tokenize_sentences(text)
    if len(words) < 15:
        return {
            "score": 0.5,
            "metrics": {
                "average_sentence_length": len(words),
                "sentence_length_variance": 0.0,
                "type_token_ratio": 0.0,
                "punctuation_density": 0.0,
            },
            "rationale": "Text is too short for reliable stylometric scoring.",
        }

    sentence_lengths = [len(tokenize_words(sentence)) for sentence in sentences]
    avg_sentence_length = statistics.mean(sentence_lengths)
    sentence_variance = statistics.pvariance(sentence_lengths) if len(sentence_lengths) > 1 else 0.0
    type_token_ratio = len(set(words)) / len(words)
    punctuation_density = len(re.findall(r"[!?;:]", text)) / len(words)

    uniformity_component = 1.0 - clamp(sentence_variance / 80.0)
    vocabulary_component = 1.0 - clamp((type_token_ratio - 0.35) / 0.45)
    formality_component = clamp((avg_sentence_length - 12.0) / 18.0)
    punctuation_component = 1.0 - clamp(punctuation_density / 0.08)

    score = (
        uniformity_component * 0.35
        + vocabulary_component * 0.25
        + formality_component * 0.25
        + punctuation_component * 0.15
    )

    return {
        "score": round(clamp(score), 3),
        "metrics": {
            "average_sentence_length": round(avg_sentence_length, 2),
            "sentence_length_variance": round(sentence_variance, 2),
            "type_token_ratio": round(type_token_ratio, 3),
            "punctuation_density": round(punctuation_density, 3),
        },
        "rationale": (
            "Stylometry estimates AI likelihood from sentence uniformity, vocabulary diversity, "
            "sentence length, and punctuation density."
        ),
    }


def combine_signal_scores(llm_score, stylometric_score):
    raw = (llm_score * 0.65) + (stylometric_score * 0.35)
    disagreement = abs(llm_score - stylometric_score)

    signals_point_opposite_directions = (
        (llm_score >= 0.72 and stylometric_score <= 0.35)
        or (llm_score <= 0.35 and stylometric_score >= 0.72)
    )
    if disagreement > 0.35 and signals_point_opposite_directions:
        raw = (raw + 0.5) / 2

    return round(clamp(raw), 3)


def score_to_attribution(score):
    if score >= 0.72:
        return "likely_ai"
    if score <= 0.35:
        return "likely_human"
    return "uncertain"


def transparency_label(attribution):
    return LABELS.get(attribution, LABELS["uncertain"])


def append_audit_entry(entry):
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit_log(limit=50):
    if not AUDIT_LOG_PATH.exists():
        return []
    with AUDIT_LOG_PATH.open("r", encoding="utf-8") as audit_file:
        entries = [json.loads(line) for line in audit_file if line.strip()]
    return entries[-limit:]


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    creator_id = str(payload.get("creator_id", "")).strip()

    if not text or not creator_id:
        return jsonify({"error": "Both text and creator_id are required."}), 400
    if len(text) < 40:
        return jsonify({"error": "Text must be at least 40 characters for meaningful analysis."}), 400

    content_id = str(uuid.uuid4())
    llm_signal = llm_detection_signal(text)
    stylometry = stylometric_signal(text)
    confidence = combine_signal_scores(llm_signal["score"], stylometry["score"])
    attribution = score_to_attribution(confidence)
    label = transparency_label(attribution)

    decision = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": utc_now_iso(),
        "status": "classified",
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "llm": llm_signal,
            "stylometric": stylometry,
        },
    }
    CONTENT_STORE[content_id] = decision
    append_audit_entry({"event": "classification", **decision})

    return jsonify(decision), 200


@app.post("/appeal")
def appeal():
    payload = request.get_json(silent=True) or {}
    content_id = str(payload.get("content_id", "")).strip()
    creator_reasoning = str(payload.get("creator_reasoning", "")).strip()

    if not content_id or not creator_reasoning:
        return jsonify({"error": "Both content_id and creator_reasoning are required."}), 400
    if content_id not in CONTENT_STORE:
        return jsonify({"error": "content_id was not found in the current review store."}), 404

    CONTENT_STORE[content_id]["status"] = "under_review"
    appeal_entry = {
        "event": "appeal",
        "content_id": content_id,
        "creator_id": CONTENT_STORE[content_id]["creator_id"],
        "timestamp": utc_now_iso(),
        "status": "under_review",
        "original_attribution": CONTENT_STORE[content_id]["attribution"],
        "original_confidence": CONTENT_STORE[content_id]["confidence"],
        "appeal_reasoning": creator_reasoning,
    }
    append_audit_entry(appeal_entry)

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": "Appeal received. A human reviewer should inspect the original decision and creator reasoning.",
        }
    ), 200


@app.get("/log")
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": read_audit_log(limit=limit)})


if __name__ == "__main__":
    app.run(debug=True)
