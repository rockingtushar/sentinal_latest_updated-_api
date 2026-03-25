import os
import requests
from shapely.geometry import shape
from shapely.ops import unary_union
from app.services.sentinelhub_auth import get_access_token



EVALSCRIPT_S1_VV_VH_DB = """//VERSION=3
function setup() {
  return {
    input: ["VV", "VH", "dataMask"],
    output: [
      {
        id: "default",      // Visualization
        bands: 4,
        sampleType: "UINT8"
      },
      {
        id: "analytic",     // Processing
        bands: 2,
        sampleType: "FLOAT32"
      }
    ]
  };
}

function toUInt8(value) {
  let min = -20;
  let max = -5;

  let clamped = Math.max(min, Math.min(max, value));
  return Math.round(255 * (clamped - min) / (max - min));
}

function evaluatePixel(samples) {

  let eps = 1e-6;

  let VV_dB = 10 * Math.log10(Math.max(samples.VV, eps));
  let VH_dB = 10 * Math.log10(Math.max(samples.VH, eps));

  let combined = (VV_dB + VH_dB) / 2;
  let gray = toUInt8(combined);

  return {
    default: [gray, gray, gray, samples.dataMask * 255],
    analytic: [VV_dB, VH_dB]
  };
}
"""


EVALSCRIPT_S1_VV_VH_DB_V2 = """//VERSION=3
function setup() {
  return {
    input: ["VV", "VH"],
    output: {
      bands: 2,
      sampleType: "FLOAT32"
    }
  };
}

function evaluatePixel(samples) {
  let eps = 1e-6;
  let VV_dB = 10 * Math.log10(Math.max(samples.VV, eps));
  let VH_dB = 10 * Math.log10(Math.max(samples.VH, eps));
  return [VV_dB, VH_dB];
}
"""

EVALSCRIPT_S1_VV_VH_DB_V3 = """//VERSION=3
function setup() {
  return {
    input: ["VV", "VH", "dataMask"],
    output: {
      bands: 3,
      sampleType: "UINT8"
    }
  };
}

// VV scaling range
let VV_min = -22;
let VV_max = -5;

// VH scaling range
let VH_min = -30;
let VH_max = -10;

function scaleToUInt8(value, min, max) {

  let clamped = Math.max(min, Math.min(max, value));

  return Math.round(
    255 * (clamped - min) / (max - min)
  );
}

function evaluatePixel(samples) {

  let eps = 1e-6;

  // Convert to dB
  let VV_dB = 10 * Math.log10(Math.max(samples.VV, eps));
  let VH_dB = 10 * Math.log10(Math.max(samples.VH, eps));

  let vv_uint8 = scaleToUInt8(VV_dB, VV_min, VV_max);
  let vh_uint8 = scaleToUInt8(VH_dB, VH_min, VH_max);

  return [
    vv_uint8,                 // Band 1 = VV
    vh_uint8,                 // Band 2 = VH
    samples.dataMask * 255    // Band 3 = Mask
  ];
}
"""

def _build_geometry(aoi_geojson: dict) -> dict:
    """
    Converts FeatureCollection with:
    - multiple Polygon features
    - MultiPolygon features
    into ONE merged geometry for Sentinel Hub
    """

    features = aoi_geojson.get("features", [])
    if not features:
        raise ValueError("AOI GeoJSON has no features")

    shapely_geoms = []

    for f in features:
        geom = f.get("geometry")
        if not geom:
            continue

        if geom["type"] not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"Unsupported geometry type: {geom['type']}")

        shapely_geoms.append(shape(geom))

    if not shapely_geoms:
        raise ValueError("No valid geometries found in AOI")

    
    merged_geom = unary_union(shapely_geoms)
    print("DEBUG: AOI feature count =", len(aoi_geojson["features"]))
    print("DEBUG: Merged geometry type =", merged_geom.geom_type)
    return merged_geom.__geo_interface__


# Return Image in VV & VH in float 32
def download_s1_vv_vh_db_geotiff_bytes_V2(
    aoi_geojson: dict,
    time_from: str,
    time_to: str,
    resolution: str = "HIGH",
    orbit_direction: str | None = None
) -> bytes:
    """
    Supports:
    - single crop polygon
    - multiple crop polygons
    - MultiPolygon AOI
    """

    base_url = os.getenv(
        "SENTINELHUB_BASE_URL",
        "https://services.sentinel-hub.com"
    )

    token = get_access_token()
    url = f"{base_url}/api/v1/process"

    geometry = _build_geometry(aoi_geojson)
    
    width = aoi_geojson.get("width")
    height = aoi_geojson.get("height")

    data_filter = {
        "timeRange": {
            "from": time_from,
            "to": time_to
        },
        "mosaickingOrder": "mostRecent"
    }

    
    if orbit_direction in ("ASCENDING", "DESCENDING"):
        data_filter["orbitDirection"] = orbit_direction

    payload = {
        "input": {
            "bounds": {
                "geometry": geometry
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": data_filter
                }
            ],
            "resolution": resolution
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {
                    "identifier": "default",
                    "format": {
                        "type": "image/tiff"
                    }
                }
            ]
        },
        "evalscript": EVALSCRIPT_S1_VV_VH_DB_V2
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=600)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(
            f"Sentinel Hub request failed: {resp.text}"
        ) from e

    return resp.content

def download_s1_vv_vh_db_geotiff_bytes_V3(
    aoi_geojson: dict,
    time_from: str,
    time_to: str,
    resolution: str = "HIGH",
    orbit_direction: str | None = None
) -> bytes:
    """
    Supports:
    - single polygon shapefile
    - multiple polygon shapefile
    - MultiPolygon shapefile
    - mixed Polygon + MultiPolygon
    """

    base_url = os.getenv(
        "SENTINELHUB_BASE_URL",
        "https://services.sentinel-hub.com"
    )

    token = get_access_token()
    url = f"{base_url}/api/v1/process"

 
    geometry = _build_geometry(aoi_geojson)
    width = aoi_geojson.get("width")
    height = aoi_geojson.get("height")
    data_filter = {
        "timeRange": {
            "from": time_from,
            "to": time_to
        },
        "mosaickingOrder": "mostRecent"
    }

    if orbit_direction in ("ASCENDING", "DESCENDING"):
        data_filter["orbitDirection"] = orbit_direction

    payload = {
        "input": {
            "bounds": {
                "geometry": geometry,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"} 
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": data_filter
                }
           ]
    
            
        },
        "resolution": resolution,
        
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {
                    "identifier": "default",
                    "format": {
                        "type": "image/tiff"
                    }
                }
            ]
        },
        "evalscript": EVALSCRIPT_S1_VV_VH_DB_V3
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    resp = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=600
    )

    try:
        resp.raise_for_status()
 

    except requests.HTTPError as e:
        raise RuntimeError(
            f"Sentinel Hub request failed: {resp.text}"
        ) from e

    return resp.content

def download_s1_vv_vh_db_geotiff_bytes(
    aoi_geojson: dict,
    time_from: str,
    time_to: str,
    resolution: int = 10,
    orbit_direction: str | None = None
) -> bytes:
    """
    Supports:
    - single polygon shapefile
    - multiple polygon shapefile
    - MultiPolygon shapefile
    - mixed Polygon + MultiPolygon
    """

    base_url = os.getenv(
        "SENTINELHUB_BASE_URL",
        "https://services.sentinel-hub.com"
    )

    token = get_access_token()
    url = f"{base_url}/api/v1/process"

 
    geometry = _build_geometry(aoi_geojson)

    data_filter = {
        "timeRange": {
            "from": time_from,
            "to": time_to
        },
        "mosaickingOrder": "mostRecent"
    }

    if orbit_direction in ("ASCENDING", "DESCENDING"):
        data_filter["orbitDirection"] = orbit_direction

    payload = {
        "input": {
            "bounds": {
                "geometry": geometry,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"} 
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": data_filter
                }
           ]
    
            
        },
        
        "output": {
            "responses": [
                {
                    "identifier": "default",
                    "format": {
                        "type": "image/tiff"
                    }
                }
            ]
        },
        "evalscript": EVALSCRIPT_S1_VV_VH_DB
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    resp = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=600
    )

    try:
        resp.raise_for_status()
    
   
        # uploads_dir = r"C:\ExcelGeomatics\Tushar\DemoProject\sentinel1_crop_health_api\data\uploads"
        # os.makedirs(uploads_dir, exist_ok=True)

        # raw_path = os.path.join(uploads_dir, "debug_raw_sentinel.tif")

        # with open(raw_path, "wb") as f:
        #     f.write(resp.content)

        # print("Saved RAW Sentinel TIFF to:", raw_path)  

    except requests.HTTPError as e:
        raise RuntimeError(
            f"Sentinel Hub request failed: {resp.text}"
        ) from e

    return resp.content