# Known Issues

Honest list of limitations and bugs in v1.0.0. Each entry names the impact and
the workaround if one exists.

## Capacity / Performance

### 1. Vertex AI Gemini quota is the hard ceiling

**Impact.** Default Vertex AI Gemini 2.5 Flash quota is ~300 requests per
minute per region per project. Even with infinite Celery workers, the
extraction step is throttled to ~300 docs/min total. The per-worker
`rate_limit="100/m"` in `worker.py:439` is a safety throttle, not the
real limit.

**Workaround.** Request a quota increase from Google Cloud for production
loads, or batch extractions via the Vertex AI batch API for non-real-time
workloads.

### 2. Single-instance worker is a queue bottleneck

**Impact.** The worker runs at `--concurrency=1, --prefetch-multiplier=1`
(see `src/api/entrypoint.sh:39`). One slow LLM call means the next document
waits. Steady-state throughput is ~5 docs/min per instance. With
`max-instances=3`, the practical ceiling is ~15 docs/min.

**Workaround.** Increase `--max-instances` on the worker for higher peaks.
For real production scale, migrate workers off Cloud Run services to GKE
with horizontal pod autoscaling on Redis queue depth — Cloud Run services
do not autoscale on Redis traffic.

### 3. spaCy model load is a per-instance memory tax

**Impact.** `en_core_web_lg` is ~750 MB; combined with Python runtime and
Gemini SDK, the worker is sized at 2 GiB. Increasing concurrency above 1
on a 2 GiB instance risks OOM during the redaction step.

**Workaround.** Bump worker memory to 4 GiB before raising concurrency, or
swap to `en_core_web_sm` if you accept lower NER recall.

## Functional gaps

### 4. OCR for scanned/image PDFs is not supported

**Impact.** `pdfplumber` extracts text only from digital PDFs. Image-based
or scanned PDFs produce empty text and are rejected by the keyword
classifier as non-financial.

**Workaround.** Pre-process scanned PDFs with Google Document AI before
upload. Native OCR support is planned for v1.1.

### 5. Single-tenant authentication only

**Impact.** The Streamlit frontend uses a sidebar `Mode:` selector
(Customer / Business) for demo purposes. There is no real auth, so anyone
with the URL can access the Business dashboard.

**Workaround.** Front the deployment with Identity-Aware Proxy (IAP) or
Auth0. Real RBAC with Customer / Business / Auditor roles is planned for
v1.1.

### 6. No cross-document consistency check

**Impact.** The aggregator merges fields but does not verify that the
paystub income and the bank-statement deposits are consistent (within
±20%). A fraudulent paystub paired with a clean bank statement could pass
the combined scorecard.

**Workaround.** None today. A `CROSS_DOC_INCOME_MISMATCH` reason code is
planned for v1.1.

## Operational

### 7. Worker billing on Cloud Run with min-instances=1

**Impact.** Pinning the worker at `--min-instances=1 --no-cpu-throttling`
keeps it always-billed (~$40–60/month for the demo configuration). This
is the price of running Celery on Cloud Run *services*.

**Workaround.** Cost-optimize by moving to Cloud Run Jobs triggered by
Cloud Scheduler, or to GKE with cluster autoscaler. Both are bigger
architectural changes than v1.0.0 needs.

### 8. PPT and Sphinx docs are not auto-versioned

**Impact.** `Sentinel_Deliverable.pptx` is rebuilt manually via
`python build_deliverable_ppt.py`. The Sphinx docs in `docs/` are not
auto-built in CI. Both can drift from the code if not regenerated.

**Workaround.** Re-run the build script after material code changes.
Adding a Sphinx + PPT build job to CI is on the v1.1 list.

### 9. Frontend status uses unauthenticated polling

**Impact.** The Streamlit frontend polls `/batches/{id}` every 2 seconds.
A determined user could DoS the API by holding many open browser tabs.

**Workaround.** Cloud Run autoscaling absorbs reasonable load; for
hardening, switch to server-sent events or WebSockets so the API pushes
state changes instead of being polled.

### 10. CI runs the test suite but not the integration smoke flow

**Impact.** `.github/workflows/ci.yml` runs unit tests on every push. It
does not stand up the docker-compose stack and run an end-to-end batch.
A fully E2E regression therefore relies on the live deployed system.

**Workaround.** Manually re-run the demo flow (upload bank statement +
paystub, watch for combined SUCCEEDED) after material changes. An E2E
test job is planned for v1.1.
