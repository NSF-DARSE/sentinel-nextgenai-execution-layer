from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import REGISTRY

from .routes import router
from .metrics import SentinelJobCollector, SentinelWorkerCollector

app = FastAPI(title="Sentinel API", version="0.0.1")

_ALLOWED_ORIGINS = [
    "http://localhost:8501",   # Streamlit (Phase 1 frontend)
    "http://localhost:3000",   # React fallback if we go that route
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Register custom DB + Redis collectors
REGISTRY.register(SentinelJobCollector())
REGISTRY.register(SentinelWorkerCollector())

# Auto-instrument all HTTP endpoints and expose /metrics
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
).instrument(app).expose(app, include_in_schema=False)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(router)
