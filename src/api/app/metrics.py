"""
metrics.py — Custom Prometheus collectors for the Sentinel pipeline.

Two sources:
  - PostgreSQL (via SQLAlchemy): job counts by status — queried at scrape time.
  - Redis: step durations + redaction entity counts written by Celery workers.

Design: all collectors are read-only from the API's perspective.
Workers write to Redis; the API exposes everything through /metrics.
"""
from __future__ import annotations

import os
import logging

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.registry import Collector

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Redis key prefixes written by workers
_STEP_SUM_KEY   = "sentinel:step:{}:duration_sum"
_STEP_COUNT_KEY = "sentinel:step:{}:duration_count"
_STEP_FAIL_KEY  = "sentinel:step:{}:failures"
_ENTITY_KEY     = "sentinel:entities:{}"
_JOBS_KEY       = "sentinel:jobs:{}"        # succeeded / failed
_PII_BLOCK_KEY  = "sentinel:pii_leak_blocks"

PIPELINE_STEPS = ["parse", "authenticate", "redact", "extract"]


class SentinelJobCollector(Collector):
    """Exposes job counts by status, queried live from PostgreSQL."""

    def collect(self):
        try:
            from app.db import SessionLocal
            from app.models import Job, JobStatus
            from sqlalchemy import func

            db = SessionLocal()
            try:
                rows = (
                    db.query(Job.status, func.count(Job.id))
                    .group_by(Job.status)
                    .all()
                )
                g = GaugeMetricFamily(
                    "sentinel_jobs_total",
                    "Total Sentinel jobs by status",
                    labels=["status"],
                )
                for status, count in rows:
                    g.add_metric([status.value], count)
                yield g
            finally:
                db.close()
        except Exception:
            log.exception("SentinelJobCollector failed")


class SentinelWorkerCollector(Collector):
    """
    Exposes pipeline step durations and redaction entity counts
    written to Redis by Celery workers.
    """

    def _redis(self):
        import redis
        return redis.from_url(REDIS_URL, decode_responses=True)

    def collect(self):
        try:
            r = self._redis()

            # ── Step duration averages ────────────────────────────────────
            step_avg = GaugeMetricFamily(
                "sentinel_pipeline_step_duration_seconds_avg",
                "Average duration of each pipeline step in seconds",
                labels=["step"],
            )
            step_count_metric = GaugeMetricFamily(
                "sentinel_pipeline_step_total",
                "Total executions of each pipeline step",
                labels=["step"],
            )
            for step in PIPELINE_STEPS:
                raw_sum   = r.get(_STEP_SUM_KEY.format(step))
                raw_count = r.get(_STEP_COUNT_KEY.format(step))
                if raw_sum and raw_count:
                    s, c = float(raw_sum), float(raw_count)
                    if c > 0:
                        step_avg.add_metric([step], s / c)
                        step_count_metric.add_metric([step], c)
            yield step_avg
            yield step_count_metric

            # ── Redaction entity counts ───────────────────────────────────
            entity_metric = GaugeMetricFamily(
                "sentinel_redaction_entities_total",
                "Total PII entities redacted by type",
                labels=["entity_type"],
            )
            entity_keys = r.keys("sentinel:entities:*")
            for key in entity_keys:
                entity_type = key.split(":")[-1]
                val = r.get(key)
                if val:
                    entity_metric.add_metric([entity_type], float(val))
            yield entity_metric

            # ── Job outcome counters (from workers) ──────────────────────
            for status in ("succeeded", "failed"):
                val = r.get(_JOBS_KEY.format(status))
                if val:
                    g = GaugeMetricFamily(
                        f"sentinel_worker_jobs_{status}_total",
                        f"Jobs marked {status} by workers",
                    )
                    g.add_metric([], float(val))
                    yield g

            # ── Step failure counts ──────────────────────────────────────
            fail_metric = GaugeMetricFamily(
                "sentinel_step_failures_total",
                "Total failures per pipeline step",
                labels=["step"],
            )
            for step in PIPELINE_STEPS:
                val = r.get(_STEP_FAIL_KEY.format(step))
                if val:
                    fail_metric.add_metric([step], float(val))
            yield fail_metric

            # ── PII leak blocks ──────────────────────────────────────────
            pii_val = r.get(_PII_BLOCK_KEY)
            g = GaugeMetricFamily(
                "sentinel_pii_leak_blocks_total",
                "Times the output PII scan blocked a response (should be 0)",
            )
            g.add_metric([], float(pii_val) if pii_val else 0.0)
            yield g

        except Exception:
            log.exception("SentinelWorkerCollector failed")


def record_step(step: str, duration_seconds: float) -> None:
    """Called by Celery workers to record step timing in Redis."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.incrbyfloat(_STEP_SUM_KEY.format(step), duration_seconds)
        r.incr(_STEP_COUNT_KEY.format(step))
    except Exception:
        log.warning("Failed to record step metric: %s", step)


def record_entities(audit: list[dict]) -> None:
    """Called by Celery workers after redaction to count entity types."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        pipe = r.pipeline()
        for entry in audit:
            pipe.incr(_ENTITY_KEY.format(entry["entity_type"]))
        pipe.execute()
    except Exception:
        log.warning("Failed to record entity metrics")


def record_job_outcome(status: str) -> None:
    """Called by workers on task completion. status: 'succeeded' | 'failed'"""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.incr(_JOBS_KEY.format(status))
    except Exception:
        log.warning("Failed to record job outcome metric: %s", status)


def record_step_failure(step: str) -> None:
    """Called by Celery workers when a pipeline step fails."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.incr(_STEP_FAIL_KEY.format(step))
    except Exception:
        log.warning("Failed to record step failure metric: %s", step)


def record_pii_leak_block() -> None:
    """Called when the output PII scan fires and blocks a response."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.incr(_PII_BLOCK_KEY)
    except Exception:
        log.warning("Failed to record PII leak block metric")
