# Release Notes — v1.0.0

**Release date:** 2026-05-06
**Tag:** `v1.0.0`
**Status:** Initial production release

---

## At a glance

Sentinel is an AI-assisted, deterministic-decision underwriting pipeline for
loan documents. It ingests bank statements and paystubs, redacts PII at the
edge, extracts structured fields with Vertex AI Gemini 2.5 Flash, and produces
a single application-level recommendation backed by a 100-point scorecard with
reason codes that are ready for ECOA / Reg B adverse-action notices.

Live deployment: `https://sentinel-frontend-1041799394320.us-central1.run.app/`

## Highlights

- **AI as assistant, deterministic scorecard as decider.** The LLM extracts
  fields; a fixed-rule scorecard with named reason codes makes the
  recommendation. Every "review" or "rejection" comes paired with the specific
  codes that justified it.
- **Application-level decision.** Bank statement + paystub uploaded together
  are merged into one applicant profile before scoring. No more conflicting
  per-document verdicts for the same borrower.
- **PII never reaches the LLM.** Presidio + spaCy redact in-pipeline before
  the Vertex AI call; the model sees typed placeholders only.
- **Deployed and auto-deploying.** Three Cloud Run services (frontend, API,
  worker) on GCP, deployed from `main` via GitHub Actions on every push.

## Installation

For local development:

```bash
git clone https://github.com/Khey17/sentinel-nextgenai-execution-layer.git
cd sentinel-nextgenai-execution-layer
cp .env.example .env   # set GOOGLE_API_KEY for Vertex AI access
docker compose up --build
```

Then visit:
- Frontend (Streamlit): `http://localhost:8501`
- API docs (FastAPI/Swagger): `http://localhost:8000/docs`
- Grafana dashboard: `http://localhost:3000`

For programmatic install (Python ≥ 3.11):

```bash
pip install -e ".[test]"        # editable install with test extras
python -m spacy download en_core_web_lg
pytest tests/                   # run the test suite
```

## Migration notes

This is the initial release; no migration is required.

Future releases will document any breaking changes in this section. The
versioning policy is [Semantic Versioning](https://semver.org/):

- **MAJOR** — incompatible API changes (e.g. removed endpoint, changed
  response schema).
- **MINOR** — backwards-compatible feature additions.
- **PATCH** — backwards-compatible bug fixes.

## Known issues

| Issue | Impact | Workaround |
|---|---|---|
| Vertex AI Gemini quota cap (~300 RPM/region) | Caps burst throughput regardless of worker count | Request quota increase from GCP for production loads |
| Single worker concurrency = 1 | ~5 docs/min throughput per worker | Scale up `--max-instances` on the worker; longer-term, migrate to GKE with HPA on Redis depth |
| OCR not supported | Scanned/image PDFs are rejected by `classify_document` | Pre-OCR with Document AI before upload (deferred to v1.1) |
| Single-tenant auth only | No customer/officer separation by IAM | Use `Mode:` sidebar selector for demo; v1.1 will add IAP integration |
| PPT and Sphinx docs are not auto-versioned | Drift between code and docs | Regenerate via `python build_deliverable_ppt.py` after material changes |

## Compatibility

- **Python:** 3.11 (tested against the Dockerfile base image)
- **Operating systems:** Linux (Cloud Run), macOS (verified locally)
- **Cloud:** Google Cloud Platform — Cloud Run, Cloud SQL (Postgres),
  Memorystore (Redis), Cloud Storage, Vertex AI

## API surface (v1)

The following endpoints are stable for v1.0.0:

| Method | Path | Purpose |
|---|---|---|
| POST | `/documents/upload` | Single-document upload (legacy) |
| POST | `/batches/upload` | Multi-document upload — primary entry point |
| GET | `/batches/{id}` | Per-job statuses for a batch |
| GET | `/batches/{id}/decision` | **Combined application decision** (new in v1.0.0) |
| GET | `/jobs/{id}` | Single job status |
| GET | `/jobs/{id}/results` | Score breakdown, extraction, authenticity report |
| GET | `/jobs/{id}/redacted-preview` | Redacted text + redaction report |
| GET | `/jobs/review` | List of jobs in `NEEDS_REVIEW` state |
| POST | `/jobs/{id}/review` | Reviewer approve/reject decision |

Schema definitions live in `src/api/app/schemas.py`.

## Acknowledgements

- **Industry partner:** Best Egg (Mike Urban, CTO) — for the brief and
  domain context.
- **Faculty sponsor:** Prof. Sunita Chandrasekaran, University of Delaware.
- **Models:** Google Vertex AI (Gemini 2.5 Flash), spaCy
  (`en_core_web_lg`), Microsoft Presidio.

## What's next

See [CHANGELOG.md](./CHANGELOG.md) for the detailed history and the
"Future Work" slide in `Sentinel_Deliverable.pptx` for the v1.1+ roadmap.
