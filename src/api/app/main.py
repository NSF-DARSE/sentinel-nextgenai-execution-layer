from fastapi import FastAPI

app = FastAPI(title="Sentinel API", version="0.0.1")

@app.get("/health")
def health():
    return {"status": "ok"}