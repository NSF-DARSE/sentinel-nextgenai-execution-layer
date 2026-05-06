# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-05-06

Initial production release. The system is deployed to Google Cloud Run with
auto-deploy from `main` via GitHub Actions, and processes loan documents
end-to-end with reason-coded recommendations.

### Added
- **Application-level decision endpoint** (`GET /batches/{id}/decision`): merges
  per-document extractions into a single application profile (income from
  paystub > W-2 > tax return > bank deposits; risk signals from bank statement;
  authenticity = min across docs) and runs the deterministic scorecard once.
  See `src/api/app/aggregator.py`.
- **Reason-coded customer view**: deterministic 100-point score, derived risk
  band, plain-English findings with severity icons, and an explicit ECOA / Reg B
  note for any review path. Replaces the prior hardcoded "Low Risk" label.
- **Business review queue** with full scorecard per item, reason codes
  formatted for adverse-action notices, and one-click approve/reject that
  auto-attaches reason codes to rejection notes.
- **PII redaction at the edge**: Presidio + spaCy `en_core_web_lg` redact PII
  inside the worker before any text reaches Vertex AI.
- **Application-as-unit document set check**: `DOC_SET_COMPLETE` /
  `DOC_SET_INCOMPLETE` reason codes annotate the audit trail with which
  document types were received.
- **Frontend poll cap** (~2 minutes): protects users from infinite spinners
  when the pipeline is stuck.
- **CI that runs tests**: `.github/workflows/ci.yml` installs API requirements,
  downloads the spaCy model, runs the full pytest suite on every push.
- **Packaging metadata**: `pyproject.toml` at repo root with pinned dependencies
  and an optional `[test]` extras group.

### Fixed
- **Worker scale-to-zero on Cloud Run** (commit `92288a7`). Cloud Run services
  scale to zero when idle, but the Celery worker has no incoming HTTP traffic —
  it pulls from Redis. Without `--min-instances=1`, the worker container was
  reaped after the startup probe and the queue backed up forever, leaving
  batches stuck on `RUNNING`. Now pinned at `min-instances=1, max-instances=3,
  --cpu 2 --memory 2Gi` for stable processing.
- **Reason codes were hidden in the UI** (commit `5324095`). The frontend
  hardcoded "Low Risk" and never rendered the score breakdown that
  `scorer.py` already produced. Customer + business views now render every
  reason code with severity-tagged icons.
- **Per-document scoring conflicts** (commit `4708042`). The bank statement
  was being penalized for `INCOME_MISSING` even when the paystub uploaded
  alongside it had a verified $2,000/mo. The application is the unit of
  decision, not the document.
- **Worker stability under burst load** (commit `e2003c7`). `concurrency=1,
  prefetch-multiplier=1` prevents head-of-line blocking when one document
  triggers a slow LLM call.
- **TypeError on null monthly_net** (commit `15d3639`). Customer view crashed
  when the LLM returned `null` income; now formats safely as "Not extracted".
- **NameError fixes** in status tracker and customer portal (commits
  `b15cf98`, `e4f03c8`).
- **Duplicate file detection** with SHA-256 hashing at upload time (commit
  `d427919` and follow-ups).

### Changed
- **Customer-facing copy** is now addressed directly to the applicant, not
  written as a meta-explanation about the system. One-sentence "your decision
  in plain English" replaces a multi-paragraph justification block.
- **Worker concurrency is bounded** rather than auto-scaled, trading peak
  throughput for predictable per-instance memory headroom (spaCy
  `en_core_web_lg` is ~750 MB).
- **ECOA / Reg B framing** is now explicit in the UI: any review or rejection
  surfaces the specific reason codes that justified it.

### Deprecated / Removed
- **Phase 3 agentic pipeline** (Google ADK Document Evaluation Agent +
  Credit Analysis Agent + orchestrator) was scrapped in favor of the
  deterministic scorecard. Removed from the roadmap.

### Security
- **PII never reaches the LLM**. Redaction happens in the worker before
  `extract_document` calls Vertex AI; the model only sees typed placeholders.
- **Data minimization**: raw PDFs and unredacted parsed text are deleted
  from GCS once extraction completes (`worker.py: _cleanup_raw_artifacts`).
  Only PII-free artifacts persist (redacted text, score breakdown,
  authenticity report).

### Known Issues
- Burst capacity is hard-capped by Vertex AI Gemini 2.5 Flash quota
  (~300 RPM/region/project default).
- Single-worker concurrency=1 trades throughput for memory headroom; for
  larger production loads, migrate workers to GKE with HPA on Redis depth.
- OCR for scanned/image PDFs is not supported; digital text only.
- Single-tenant authentication only.

[1.0.0]: https://github.com/Khey17/sentinel-nextgenai-execution-layer/releases/tag/v1.0.0
