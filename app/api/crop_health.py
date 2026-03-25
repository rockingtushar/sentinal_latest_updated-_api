import uuid
from datetime import datetime, timedelta
from fastapi import File, UploadFile, Form, HTTPException
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import Response
from dotenv import load_dotenv
import json
from fastapi.responses import StreamingResponse
from app.utils.paths import ensure_dirs
from app.services.shp_reader import extract_aoi_from_zip_bytes, extract_aoi_from_zip_bytes_V2
from app.services.sentinelhub_process import download_s1_vv_vh_db_geotiff_bytes, download_s1_vv_vh_db_geotiff_bytes_V3
from app.services.crop_health_processor import process_crop_health_to_png_bytes
from app.services.vegetative_moisture import compute_vod_sar
from app.services.soil_moisture_processor import process_soil_moisture_to_png_bytes
from app.services.sentinelhub_process import download_s1_vv_vh_db_geotiff_bytes_V2
from pydantic import BaseModel
from typing import Dict, Any
from io import BytesIO
load_dotenv()
ensure_dirs()

router = APIRouter()


@router.post("/s1/vegetative-moisture")
async def get_Image(
    file: UploadFile = File(...),

    # REQUIRED single date
    date: str = Query(
        ...,
        description="YYYY-MM-DD format date(from past 8 Days)" ),
):
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip shapefile allowed")

    zip_bytes = await file.read()
    if not zip_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    job_id = str(uuid.uuid4())
    aoi_geojson = extract_aoi_from_zip_bytes_V2(zip_bytes, job_id)


    try:
        center_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt 

    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")


    input_tif_bytes = download_s1_vv_vh_db_geotiff_bytes_V2(
        aoi_geojson,
        time_from,
        time_to
    )
    
    return compute_vod_sar(input_tif_bytes)



@router.post("/s1/crop-health/run")
async def run_crop_health(
    file: UploadFile = File(...),
    date: str = Query(
        ...,
        description="YYYY-MM-DD format date"
    ),
):
    # -----------------------------
    # 1. Validate upload
    # -----------------------------
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip shapefile allowed")

    zip_bytes = await file.read()
    if not zip_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    job_id = str(uuid.uuid4())

    # -----------------------------
    # 2. Extract AOI (multi polygon supported)
    # -----------------------------
    aoi_geojson = extract_aoi_from_zip_bytes(zip_bytes, job_id)

    # -----------------------------
    # 3. Date window (-8 days)
    # -----------------------------
    try:
        center_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt


    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    # -----------------------------
    # 4. Download Sentinel-1 TIFF
    # -----------------------------
    raw_tif_bytes = download_s1_vv_vh_db_geotiff_bytes(
        aoi_geojson,
        time_from,
        time_to
    )

    # -----------------------------
    # 5. Process → PNG
    # -----------------------------
    try:
        png_bytes, stats, bounds, crs, area_stats = process_crop_health_to_png_bytes(raw_tif_bytes, aoi_geojson)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # -----------------------------
    # 6. RETURN PNG IMAGE (IMPORTANT)
    # -----------------------------
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            # optional debug info
            "X-BBOX": ",".join(map(str, bounds)),
            "X-CRS": crs,
            "X-AREA-STATS": json.dumps(area_stats)
        }
    )

class GeoJSONCropHealthRequest(BaseModel):
    geometry: Dict[str, Any]
    date: str
class GeoJSONSoilMoistureRequest(BaseModel):
    geometry: Dict[str, Any]
    date: str

@router.post("/s1/crop-health/run-geojson")
async def run_crop_health_geojson(request: GeoJSONCropHealthRequest):

    # -----------------------------
    # 1. Validate date
    # -----------------------------
    try:
        center_dt = datetime.strptime(request.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt

    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    # -----------------------------
    # 2. Validate geometry
    # -----------------------------
    geometry_input = request.geometry

    if geometry_input.get("type") == "FeatureCollection":
        aoi_geojson = geometry_input

    elif geometry_input.get("type") in ("Polygon", "MultiPolygon"):
        aoi_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": geometry_input
                }
            ]
        }

    else:
        raise HTTPException(status_code=400, detail="Invalid GeoJSON format")

    # -----------------------------
    # 3. Download Sentinel-1 TIFF
    # -----------------------------
    raw_tif_bytes = download_s1_vv_vh_db_geotiff_bytes(
        aoi_geojson,
        time_from,
        time_to
    )

    # -----------------------------
    # 4. Process → PNG
    # -----------------------------
    png_bytes, stats, bounds, crs, area_stats = process_crop_health_to_png_bytes(
        raw_tif_bytes,
        aoi_geojson
    )

    # -----------------------------
    # 5. Return PNG
    # -----------------------------
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-BBOX": ",".join(map(str, bounds)),
            "X-CRS": crs,
            "X-AREA-STATS": json.dumps(area_stats)
        }
    )


@router.post("/s1/crop-health/run-geojson-V2")
async def run_crop_health_geojson(request: GeoJSONCropHealthRequest):

    # -----------------------------
    # 1. Validate date
    # -----------------------------
    try:
        center_dt = datetime.strptime(request.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt

    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    # -----------------------------
    # 2. Validate geometry
    # -----------------------------
    geometry_input = request.geometry

    if geometry_input.get("type") == "FeatureCollection":
        aoi_geojson = geometry_input

    elif geometry_input.get("type") in ("Polygon", "MultiPolygon"):
        aoi_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": geometry_input
                }
            ]
        }

    else:
        raise HTTPException(status_code=400, detail="Invalid GeoJSON format")

    # -----------------------------
    # 3. Download Sentinel-1 TIFF
    # -----------------------------
    raw_tif_bytes = download_s1_vv_vh_db_geotiff_bytes_V2(
        aoi_geojson,
        time_from,
        time_to
    )

    return compute_vod_sar(raw_tif_bytes)


@router.post("/s1/soil-moisture/run")
async def run_soil_moisture(
    file: UploadFile = File(...),
    date: str = Query(..., description="YYYY-MM-DD format date"),
):

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip shapefile allowed")

    zip_bytes = await file.read()
    job_id = str(uuid.uuid4())

    aoi_geojson = extract_aoi_from_zip_bytes(zip_bytes, job_id)

    try:
        center_dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt

    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    raw_tif_bytes = download_s1_vv_vh_db_geotiff_bytes_V3(
        aoi_geojson,
        time_from,
        time_to
    )

    png_bytes, stats, bounds, crs, area_stats = process_soil_moisture_to_png_bytes(
        raw_tif_bytes,
        aoi_geojson
    )

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-BBOX": ",".join(map(str, bounds)),
            "X-CRS": crs,
            "X-AREA-STATS": json.dumps(area_stats)
        }
    )


@router.post("/s1/soil-moisture/run-geojson")
async def run_soil_moisture_geojson(request: GeoJSONSoilMoistureRequest):

    # -----------------------------
    # 1. Validate date
    # -----------------------------
    try:
        center_dt = datetime.strptime(request.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD"
        )

    from_dt = center_dt - timedelta(days=8)
    to_dt = center_dt

    time_from = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    time_to = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    # -----------------------------
    # 2. Validate geometry
    # -----------------------------
    geometry_input = request.geometry

    if geometry_input.get("type") == "FeatureCollection":
        aoi_geojson = geometry_input

    elif geometry_input.get("type") in ("Polygon", "MultiPolygon"):
        aoi_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": geometry_input
                }
            ]
        }

    else:
        raise HTTPException(status_code=400, detail="Invalid GeoJSON format")

    # -----------------------------
    # 3. Download Sentinel-1 TIFF
    # -----------------------------
    raw_tif_bytes = download_s1_vv_vh_db_geotiff_bytes_V3(
        aoi_geojson,
        time_from,
        time_to
    )

    # -----------------------------
    # 4. Process → PNG
    # -----------------------------
    png_bytes, stats, bounds, crs, area_stats = process_soil_moisture_to_png_bytes(
        raw_tif_bytes,
        aoi_geojson
    )

    # -----------------------------
    # 5. Return PNG
    # -----------------------------
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-BBOX": ",".join(map(str, bounds)),
            "X-CRS": crs,
            "X-AREA-STATS": json.dumps(area_stats)
        }
    )