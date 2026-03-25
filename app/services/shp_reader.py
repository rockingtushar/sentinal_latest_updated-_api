import tempfile
import zipfile
import os
import geopandas as gpd


def extract_aoi_from_zip_bytes(zip_bytes: bytes, job_id: str):
    with tempfile.TemporaryDirectory(prefix=f"s1zip_{job_id}_") as tmpdir:
        zip_path = os.path.join(tmpdir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmpdir)

        shp_files = []
        for root, _, files in os.walk(tmpdir):
            for fn in files:
                if fn.lower().endswith(".shp"):
                    shp_files.append(os.path.join(root, fn))

        if not shp_files:
            raise RuntimeError("No .shp found")

        gdf = gpd.read_file(shp_files[0])

        if gdf.empty:
            raise RuntimeError("Shapefile empty")

      
        if gdf.crs is None:
            raise RuntimeError("Shapefile CRS missing")
        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        features = []

        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None:
                continue

            features.append({
                "type": "Feature",
                "properties": {},
                "geometry": geom.__geo_interface__
            })

        if not features:
            raise RuntimeError("No valid geometries found")
        # ----  Width / Height calculation ----

        minx, miny, maxx, maxy = geom.bounds

        bbox_width = maxx - minx
        bbox_height = maxy - miny

        if bbox_width <= 0 or bbox_height <= 0:
            raise RuntimeError("Invalid geometry bounds")

        BASE = 512.0  # Same as Requests Builder default

        scale = BASE / max(bbox_width, bbox_height)

        width = bbox_width * scale
        height = bbox_height * scale

        return {
            "type": "FeatureCollection",
            "features": features,
            "width": round(width, 3),
            "height": round(height, 3)
        }


def extract_aoi_from_zip_bytes_V2(zip_bytes: bytes, job_id: str):
    """
    Extract zip bytes into temp folder -> read shapefile
    -> return GeoJSON FeatureCollection + width/height 
    """
    with tempfile.TemporaryDirectory(prefix=f"s1zip_{job_id}_") as tmpdir:
        zip_path = os.path.join(tmpdir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmpdir)

        shp_files = []
        for root, _, files in os.walk(tmpdir):
            for fn in files:
                if fn.lower().endswith(".shp"):
                    shp_files.append(os.path.join(root, fn))

        if not shp_files:
            raise RuntimeError("No .shp found. Zip must contain .shp .shx .dbf .prj")

        shp_path = shp_files[0]
        gdf = gpd.read_file(shp_path)

        if gdf.empty:
            raise RuntimeError("Shapefile empty")

        # Reproject to EPSG:4326 (like Requests Builder default)
        if gdf.crs is None:
            raise RuntimeError("Shapefile has no CRS")

        gdf = gdf.to_crs(epsg=4326)

        geom = gdf.geometry.iloc[0]
        if geom is None:
            raise RuntimeError("Geometry missing")

        # ----  Width / Height calculation ----

        minx, miny, maxx, maxy = geom.bounds

        bbox_width = maxx - minx
        bbox_height = maxy - miny

        if bbox_width <= 0 or bbox_height <= 0:
            raise RuntimeError("Invalid geometry bounds")

        BASE = 512.0  # Same as Requests Builder default

        scale = BASE / max(bbox_width, bbox_height)

        width = bbox_width * scale
        height = bbox_height * scale

        # -------------------------------------------

        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {},
                "geometry": geom.__geo_interface__
            }],
            "width": round(width, 3),
            "height": round(height, 3)
        }

