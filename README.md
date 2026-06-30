# Provenance Guard

Provenance Guard is a Flask backend that classifies submitted text for likely authorship provenance, returns a confidence score and reader-facing transparency label, supports creator appeals, rate-limits submissions, and records every decision in a structured audit log.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The repo expects a local `.env` file that is not committed:

```text
GROQ_API_KEY=your_key_here
```

## API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Basic service health check |
| `POST /submit` | Submit text for provenance analysis |
| `POST /appeal` | Contest a classification and move content under review |
| `GET /log` | Return recent structured audit-log entries |

Example submission:

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that responsible deployment requires careful oversight from stakeholders across various sectors.\",\"creator_id\":\"test-user-1\"}" | python -m json.tool
```

Example appeal:

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d "{\"content_id\":\"PASTE-CONTENT-ID-HERE\",\"creator_reasoning\":\"I wrote this myself from personal experience and can provide revision drafts.\"}" | python -m json.tool
```

## Architecture Overview

A submission enters through `POST /submit`, where Flask validates `text` and `creator_id`. The text is passed to two independent signals: a Groq LLM classifier and a local stylometric heuristic scorer. The confidence scorer combines both AI-likelihood scores, the label generator maps the score to a reader-facing transparency label, and the full decision is written to `audit_log.jsonl` before the JSON response is returned.

Appeals enter through `POST /appeal`. The creator provides a `content_id` and reasoning, the content status changes to `under_review`, and an appeal event is logged alongside the original classification data.

See `planning.md` for the ASCII architecture diagram and full spec.

## Detection Signals

| Signal | What it measures | Why I chose it | What it misses |
| --- | --- | --- | --- |
| Groq LLM classifier | Holistic semantic and stylistic cues: generic phrasing, specificity, coherence, and AI-like wording | It can judge writing quality and context in a way simple formulas cannot | Polished human writing and edited AI writing can look similar |
| Stylometric heuristics | Sentence length variance, type-token ratio, average sentence length, and punctuation density | It is transparent, local, deterministic, and independent from the LLM | Poems, formal essays, and non-native English writing can have unusual structure |

The LLM signal returns an AI-likelihood score from `0.0` to `1.0`, a verdict, and a rationale. The stylometric signal returns an AI-likelihood score from `0.0` to `1.0` plus raw metrics so the decision is inspectable.

## Confidence Scoring

The final confidence is an AI-likelihood score, not just a binary certainty score. The LLM signal is weighted at 65% and the stylometric signal at 35%. If the signals disagree by more than `0.35` and point in opposite label directions, the score is pulled toward `0.5` because direct contradiction means uncertainty.

Thresholds:

| Score range | Result |
| --- | --- |
| `0.72` to `1.00` | `likely_ai` |
| `0.36` to `0.71` | `uncertain` |
| `0.00` to `0.35` | `likely_human` |

The uncertain band is intentionally wide because falsely labeling a human creator as AI-generated is more harmful than missing some AI-generated content.

Example scoring checks:

| Input type | Example combined score | Label |
| --- | ---: | --- |
| AI-like formal passage about AI ethics | `0.79` | `likely_ai` |
| Casual personal restaurant reaction | `0.32` | `likely_human` |
| Formal human-style academic paragraph | `0.58` | `uncertain` |
| Lightly edited remote-work paragraph | `0.54` | `uncertain` |

These cases were chosen because they exercise different parts of the scoring range instead of only proving that the endpoint returns a constant value.

## Transparency Labels

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard label: This content shows strong signs of AI generation. Our system is highly confident, but creators can appeal if this label is incorrect." |
| High-confidence human | "Provenance Guard label: This content shows strong signs of human authorship. Our system is highly confident, though no automated review is perfect." |
| Uncertain | "Provenance Guard label: We do not have enough confidence to identify this as AI-generated or human-written. Readers should treat the authorship as uncertain." |

## Appeals Workflow

Creators can contest a classification with:

```json
{
  "content_id": "existing-content-id",
  "creator_reasoning": "I wrote this myself and can provide drafts."
}
```

The system verifies the content exists, changes the status to `under_review`, and appends an appeal event to the audit log with the original attribution, original confidence, and creator reasoning. Automated re-classification is intentionally not included; the goal is to create a review queue for a human moderator.

## Rate Limiting

`POST /submit` is limited to:

```text
10 per minute;100 per day
```

I chose this because a normal writer is unlikely to submit more than 10 pieces in one minute, while a script trying to flood the classifier would hit the minute limit quickly. The daily limit still allows active testing and repeated drafts without leaving the endpoint open to unlimited abuse.

Rate-limit test command:

```bash
for i in {1..12}; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"This is a test submission for rate limit testing purposes only. It has enough words to pass validation.\",\"creator_id\":\"ratelimit-test\"}"
done
```

Expected evidence:

```text
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

The audit log is stored as structured JSON Lines in `audit_log.jsonl` and can also be viewed with:

```bash
curl -s http://localhost:5000/log | python -m json.tool
```

Sample entries:

```json
[
  {
    "event": "classification",
    "content_id": "3f7a2b1e-1111-4444-8888-abcdef000001",
    "creator_id": "test-user-1",
    "timestamp": "2026-06-30T23:10:00.123Z",
    "status": "classified",
    "attribution": "likely_ai",
    "confidence": 0.79,
    "signals": {
      "llm": { "score": 0.84, "verdict": "likely_ai", "source": "groq" },
      "stylometric": { "score": 0.69, "metrics": { "type_token_ratio": 0.52 } }
    }
  },
  {
    "event": "classification",
    "content_id": "3f7a2b1e-1111-4444-8888-abcdef000002",
    "creator_id": "test-user-2",
    "timestamp": "2026-06-30T23:12:00.123Z",
    "status": "classified",
    "attribution": "likely_human",
    "confidence": 0.32,
    "signals": {
      "llm": { "score": 0.24, "verdict": "likely_human", "source": "groq" },
      "stylometric": { "score": 0.47, "metrics": { "type_token_ratio": 0.78 } }
    }
  },
  {
    "event": "appeal",
    "content_id": "3f7a2b1e-1111-4444-8888-abcdef000001",
    "creator_id": "test-user-1",
    "timestamp": "2026-06-30T23:15:00.123Z",
    "status": "under_review",
    "original_attribution": "likely_ai",
    "original_confidence": 0.79,
    "appeal_reasoning": "I wrote this myself and can provide revision history."
  }
]
```

## Known Limitations

Poetry with deliberate repetition may be mislabeled as AI-like because the stylometric signal treats low vocabulary diversity and uniform sentence length as suspicious.

Formal human writing may land in the uncertain band because academic style often has long sentences, restrained punctuation, and polished transitions.

The in-memory content store resets when the Flask server restarts. The audit log persists, but appeals for pre-restart content IDs require a database-backed content table in a production version.

## Spec Reflection

The planning spec helped most with confidence thresholds. Writing the uncertain range before coding made the implementation more cautious about false positives instead of forcing every score into a binary label.

One implementation divergence is the fallback LLM estimator. The spec centers Groq as the first signal, but I added a local fallback so the demo and tests still work if the API key is missing or the network is unavailable. The fallback is clearly marked in the signal source and is not meant to replace Groq in production.

## AI Usage

1. I directed AI assistance to turn the architecture and detection-signal spec into a Flask app skeleton with `/submit`, a Groq signal function, structured responses, and audit logging. I revised the generated shape to keep the response fields aligned with the rubric and to avoid committing secrets.

2. I directed AI assistance to design the stylometric signal and confidence combiner. I revised the thresholds to widen the uncertain band and added disagreement handling so conflicting signals reduce confidence instead of producing an overconfident label.

3. I directed AI assistance to draft README evidence sections. I revised the wording to include the exact label variants, rate-limit rationale, audit-log samples, and specific limitations required by the assignment.

## Verification Checklist

```bash
python verify_project.py
python app.py
curl -s http://localhost:5000/health | python -m json.tool
curl -s http://localhost:5000/log | python -m json.tool
```

Before final submission, record a short walkthrough showing `/submit`, `/appeal`, `/log`, the changing label text, and the README sections above.
