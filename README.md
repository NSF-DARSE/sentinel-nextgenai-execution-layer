# Case Study: Sentinel — Next-Gen AI Execution Layer

## Overview
Financial institutions receive **PDF-based unstructured documents** (especially **bank statements** and paystubs). Manual review is slow and expensive, but “just run an LLM on it” is risky because these docs contain **PII/PCI**.

**Sentinel** is a workflow orchestration + policy enforcement layer that enables **safe AI extraction** by ensuring the LLM only ever sees **redacted** content, while producing **auditable evidence** and **observability signals** that prove the system is doing what it claims.

**Stakeholders**
- **Business / Underwriting / Ops:** wants fast structured outputs (not the whole raw document)
- **Risk & Compliance / Security:** needs hard guarantees (no raw PII to LLM, safe logging, least privilege)
- **Data/Platform Engineering:** needs reliable orchestration (queues, retries, idempotency, DLQ)
- **ML/LLM Engineers:** need a controlled extraction workflow (schema, prompt/model versioning)
- **Auditors:** need traceable lineage (what ran, when, on what input, with what model/prompt)

**MVP focus**
PDFs → **safe redaction** → **LLM-based structured extraction (redacted only)** → validation → **audit + dashboard**

### Key mappings
| Phrase | Technical understanding |
|---|---|
| “Sentinel layer” | Orchestration + policy enforcement layer |
| “AI checks documents” | LLM extraction workflow (schema-defined structured output) |
| “Hide sensitive data” | PII detection + redaction/tokenization step |
| “Make sure nothing leaks” | Egress controls + safe logging + least privilege |
| “Track what happened” | Audit trail (lineage: inputs/outputs/steps/models) |
| “Show it’s working” | Observability dashboard (metrics/logs/traces) |
| “Flag if something is off” | Validation + confidence thresholds + review queue |

**Scope (MVP)**
- **PDF only** (start with **digital bank statement PDFs**)
- End-to-end: upload → parse → redact → LLM extract → validate → store → observe

**Core guarantees**
- **Privacy guarantee:** LLM receives **only redacted text** (never raw PII)
- **Auditability:** evidence for every step (timestamps, versions, artifact hashes/IDs)
- **Reliability:** job state, retries/backoff, idempotency, DLQ
- **Observability:** throughput/latency/failures + security indicators (redaction counts, policy blocks)

**Beyond MVP (explicitly out of scope for the first demo)**
- OCR for scanned PDFs
- Email/chat/image ingestion
- Multi-tenant governance + enterprise RBAC
- Policy engine (e.g., OPA), tokenization vault, stronger prompt-injection defenses

**Project status**
- [x] Repo initialized  
- [ ] API: upload PDF  
- [ ] Storage: raw PDF + metadata  
- [ ] Parse: extract text  
- [ ] PII detection + redaction  
- [ ] LLM extraction (redacted text only)  
- [ ] Validation + review state  
- [ ] Audit trail  
- [ ] Dashboard (Prometheus + Grafana)

---

## Repository Structure
- `src/` – source code  
  - `src/api/` – upload + status endpoints  
  - `src/workers/` – queue consumers, retries, DLQ handling  
  - `src/pipeline/` – parse → redact → extract → validate  
  - `src/redaction/` – PII detectors + redaction report generation  
  - `src/extraction/` – LLM schema + prompt/version plumbing  
  - `src/validation/` – schema + sanity checks + review routing  
  - `src/audit/` – event log + artifact lineage  
  - `src/observability/` – metrics/logging/tracing helpers  
- `docs/` – optional documentation (Sphinx scaffold)
- `data/` – input/output data (if applicable)  
  - e.g., redacted sample PDFs, test fixtures, golden JSON outputs (no raw sensitive docs)

---

Non-negotiables:
- No raw extracted text in logs (safe logging only)
- LLM input must be redacted-only (enforced by pipeline, not discipline)
- Idempotency (retries must not duplicate artifacts/results)
- Audit events for every step (start/end + success/failure + versions + artifact IDs)


## Getting Started
1. Clone the repository
2. Create a feature branch
3. Open a pull request early

---

## Documentation
This repository includes an optional Sphinx documentation scaffold.

Recommended docs to keep this employer-ready:
- Architecture & dataflow (pipeline diagram + artifacts per step)
- Security model (what is never logged, what the LLM never sees, egress controls)
- Audit model (event schema + lineage fields + artifact hashing)
- Validation rules (what triggers `NEEDS_REVIEW`)
- Observability (exact metrics emitted and what “good” looks like)

---

## Contributing
All changes must go through pull requests.
