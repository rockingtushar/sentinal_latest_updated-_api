from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.utils.paths import ensure_dirs, OUTPUT_DIR

ensure_dirs()

app = FastAPI(
    title="Sentinel-1 Crop Health API",
    version="1.0",
    description="Upload shapefile zip -> download Sentinel-1 VV/VH -> refine+filter -> crop health"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500",
        "http://localhost:5500"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-BBOX", "X-CRS",]
)

app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

app.include_router(router)
