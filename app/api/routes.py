from fastapi import APIRouter
from app.api.crop_health import router as crop_health_router

router = APIRouter()
router.include_router(crop_health_router, tags=["Sentinel-1 Crop Health"])
