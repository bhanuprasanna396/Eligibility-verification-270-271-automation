from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.appointments import router as appointments_router
from app.api.checks import router as checks_router
from app.api.gaps import router as gaps_router

app = FastAPI(
    title="Eligibility Verification System",
    description="Automates 270/271 eligibility checks for scheduled appointments",
    version="1.0.0",
)

app.include_router(appointments_router)
app.include_router(checks_router)
app.include_router(gaps_router)

# Serve the dashboard UI from /static, with / → index.html
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("static/index.html")


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
