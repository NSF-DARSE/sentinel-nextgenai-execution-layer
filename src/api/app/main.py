from fastapi import FastAPI
from .routes import router

app = FastAPI(title="Sentinel API", version="0.0.1")

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(router)