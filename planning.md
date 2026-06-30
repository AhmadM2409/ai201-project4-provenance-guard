# Provenance Guard Planning

## Architecture

```text
Submission flow
Client POST /submit
  -> Flask request validation (text + creator_id)
  -> Groq LLM signal (raw text -> AI-likelihood score + rationale)
  -> Stylometric signal (raw text -> structural metrics + AI-likelihood score)
  -> Confidence scorer (two scores -> combined confidence)
  -> Label generator (confidence -> attribution + reader-facing label)
  -> Audit log (decision, score, signals, status)
  -> JSON response (content_id, attribution, confidence, label, signals)

Appeal flow
Client POST /appeal
  -> Flask request validation (content_id + creator_reasoning)
  -> Content status update (classified -> under_review)
  -> Audit log (appeal linked to original decision)
  -> JSON response (content_id, status, confirmation)
```

A submitted text moves through validation, two independent detection signals, weighted confidence scoring, label generation, and structured audit logging before the API returns a decision. Appeals do not automatically reclassify content; they preserve the original decision, attach creator reasoning, and move the content to `under_review` so a human reviewer can inspect it.

## Detection Signals

Signal 1 is a Groq LLM classification using `llama-3.3-70b-versatile`. It measures holistic writing cues such as coherence, generic phrasing, specificity, and whether the passage reads like generated prose. It outputs an AI-likelihood score from `0.0` to `1.0`, a verdict, and a rationale. Its blind spot is that polished human writing and lightly edited AI writing can look similar.

Signal 2 is stylometric heuristics computed in Python. It measures sentence length variance, type-token ratio, average sentence length, and punctuation density. It outputs an AI-likelihood score from `0.0` to `1.0` plus the raw metrics. Its blind spot is that genre strongly affects structure: formal essays, poems, and non-native English writing may look more uniform than casual human writing.

The combined score weights the LLM signal at 65% and stylometry at 35%. If the signals disagree by more than `0.35` and point in opposite label directions, the score is pulled toward `0.5` because direct contradiction is evidence of uncertainty.

## Uncertainty Representation

The score means AI-likelihood, not certainty in a binary decision. A `0.95` means both signals strongly suggest AI generation. A `0.60` means there are AI-like signals, but not enough for a confident label. A `0.20` means the content has strong human-authorship signals.

Thresholds:

| Combined score | Attribution | Meaning |
| --- | --- | --- |
| `>= 0.72` | `likely_ai` | Strong enough evidence to show a high-confidence AI label |
| `0.36` to `0.71` | `uncertain` | Mixed or weak evidence; avoid overstating authorship |
| `<= 0.35` | `likely_human` | Strong enough evidence to show a high-confidence human label |

These thresholds intentionally make the uncertain range wide because a false positive against a human creator is more harmful than a false negative.

## Transparency Label Design

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard label: This content shows strong signs of AI generation. Our system is highly confident, but creators can appeal if this label is incorrect." |
| High-confidence human | "Provenance Guard label: This content shows strong signs of human authorship. Our system is highly confident, though no automated review is perfect." |
| Uncertain | "Provenance Guard label: We do not have enough confidence to identify this as AI-generated or human-written. Readers should treat the authorship as uncertain." |

## Appeals Workflow

Any creator with the `content_id` can submit an appeal. The appeal request includes `content_id` and `creator_reasoning`. When received, the system verifies that the content exists in the current review store, changes its status from `classified` to `under_review`, and writes a structured appeal event to the audit log containing the original attribution, original confidence, and the creator's reasoning.

A human reviewer would see the original classification event, both signal scores, the label that was shown, and the appeal entry. That context is enough to decide whether to remove, keep, or manually revise the label.

## Anticipated Edge Cases

Short poems with repeated simple phrases may be misclassified as AI-like because stylometry sees low vocabulary diversity and uniform sentence length.

Formal human essays may score higher than expected because polished academic language can resemble generated writing.

Heavily edited AI text may score uncertain or human because human edits can add irregularity and personal details that weaken both signals.

Very short submissions are rejected below 40 characters because both signals become unreliable with too little text.

## API Surface

`POST /submit` accepts JSON with `text` and `creator_id`. It returns `content_id`, `creator_id`, `timestamp`, `status`, `attribution`, `confidence`, `label`, and individual signal outputs.

`POST /appeal` accepts JSON with `content_id` and `creator_reasoning`. It returns a confirmation and changes the content status to `under_review`.

`GET /log` returns recent structured audit log entries as JSON.

`GET /health` returns a basic service status.

## AI Tool Plan

M3: Provide the detection signals section and architecture diagram. Ask the AI tool to generate the Flask skeleton, `POST /submit`, and the Groq signal function. Verify with a hardcoded route first, then direct calls to the signal function, then a real `/submit` request.

M4: Provide the detection signals section, uncertainty thresholds, and architecture diagram. Ask for a stylometric signal and confidence combiner. Verify by testing clearly AI-like, clearly human-like, and two borderline passages while inspecting both individual scores.

M5: Provide label variants, appeals workflow, and the architecture diagram. Ask for label mapping, `POST /appeal`, rate limiting, and audit-log completion. Verify all three labels can be reached, appeals update status, and rate limiting returns `429` after the chosen threshold.
