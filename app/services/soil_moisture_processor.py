import io
import numpy as np
import matplotlib.pyplot as plt
from rasterio.io import MemoryFile
from scipy.ndimage import uniform_filter
from rasterio.features import geometry_mask
from shapely.geometry import shape


def refined_lee_filter(img, size=7):

    img = img.astype(np.float32)

    nan_mask = np.isnan(img)

    if np.all(nan_mask):
        return img

    img_filled = np.where(nan_mask, np.nanmean(img), img)

    img_mean = uniform_filter(img_filled, size)
    img_sqr_mean = uniform_filter(img_filled**2, size)

    img_var = img_sqr_mean - img_mean**2

    overall_var = np.nanmean(img_var)

    if not np.isfinite(overall_var) or overall_var <= 0:
        return img

    weights = img_var / (img_var + overall_var + 1e-9)

    weights = np.clip(weights, 0, 1)

    result = img_mean + weights*(img_filled - img_mean)

    result[nan_mask] = np.nan

    return result.astype(np.float32)


def normalize_percentile(x, valid_mask, pmin, pmax):
    if np.sum(valid_mask) == 0:
        return np.zeros_like(x, dtype=np.float32)

    lo = np.percentile(x[valid_mask], pmin)
    hi = np.percentile(x[valid_mask], pmax)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1).astype(np.float32)


def soil_moisture_to_rgba(classes):

    rgba = np.zeros((classes.shape[0], classes.shape[1], 4), dtype=np.uint8)

    rgba[classes == 1] = [255, 140, 0, 255]     # Very Dry ||  Dark Orange  
    rgba[classes == 2] = [232, 232, 43, 255]     # Dry || Yellow (Golden Yellow / Light Yellow-Green)  
    rgba[classes == 3] = [255, 255, 204, 255]    # Normal || Light Yellow (Cream)  
    rgba[classes == 4] = [55, 227, 55, 255]      # Wet || Bright Green (Lime Green)  
    rgba[classes == 5] = [0, 100, 0, 255]        # Very Wet || Dark Green  

    rgba[classes == 0] = [0, 0, 139, 255]        # Water → Dark Blue

    rgba[classes == 255] = [0, 0, 0, 0]          # Outside AOI

    return rgba


def process_soil_moisture_to_png_bytes(raw_tif_bytes: bytes, aoi_geojson: dict):

    print("\n========== SOIL MOISTURE DEBUG ==========")

    with MemoryFile(raw_tif_bytes) as memfile:
        with memfile.open() as src:

            print("Raster Loaded")
            print("Bands:", src.count)
            print("Size:", src.width, "x", src.height)

            vv_uint8 = src.read(1).astype(np.float32)
            vh_uint8 = src.read(2).astype(np.float32)

            print("\nVV UINT8 min/max:", np.nanmin(vv_uint8), np.nanmax(vv_uint8))
            print("VH UINT8 min/max:", np.nanmin(vh_uint8), np.nanmax(vh_uint8))

            # UINT8 → dB conversion (Evalscript ranges)
            vv_db = vv_uint8 * (17.0/255.0) - 22.0
            vh_db = vh_uint8 * (20.0/255.0) - 30.0

            print("\nVV dB min/max:", np.nanmin(vv_db), np.nanmax(vv_db))
            print("VH dB min/max:", np.nanmin(vh_db), np.nanmax(vh_db))


            mask_band = src.read(3)

            vv_db[mask_band == 0] = np.nan
            vh_db[mask_band == 0] = np.nan

            print("\nVV after cleaning min/max:",
                  np.nanmin(vv_db), np.nanmax(vv_db))

            if not np.any(np.isfinite(vv_db)):
                raise ValueError("No valid Sentinel-1 data")

            bounds = src.bounds
            crs = src.crs.to_string()
            transform = src.transform
            height = src.height
            width = src.width

            print("\nBounds:", bounds)
            print("CRS:", crs)

            geoms = [shape(f["geometry"]) for f in aoi_geojson["features"]]

            print("AOI Features:", len(geoms))

            mask = geometry_mask(
                geoms,
                transform=transform,
                invert=True,
                out_shape=(height, width)
            )

            print("AOI pixels:", np.sum(mask))

            vv_db[~mask] = np.nan
            vh_db[~mask] = np.nan

    print("\nApplying Refined Lee Filter...")

    vv_filt = refined_lee_filter(vv_db)
    vh_filt = refined_lee_filter(vh_db)

    print("VV filtered min/max:",
        np.nanmin(vv_filt), np.nanmax(vv_filt))

    print("VH filtered min/max:",
        np.nanmin(vh_filt), np.nanmax(vh_filt))


    # =========================
    # WATER DETECTION
    # =========================

    water_threshold = -15
    vh_threshold = -22

    print("Water threshold VV dB:", water_threshold)
    print("Water threshold VH dB:", vh_threshold)

   
    water_mask = (
        (vv_db < water_threshold) &
        (vh_db < vh_threshold)
    )

    print("Water pixels:", np.sum(water_mask))


    

    if np.any(water_mask):
        print("Water VV range:",
            np.nanmin(vv_db[water_mask]),
            np.nanmax(vv_db[water_mask]))

    vv_filt[water_mask] = np.nan

    valid_mask = np.isfinite(vv_filt)

    print("Valid pixels after water removal:",
        np.sum(valid_mask))


    if np.sum(valid_mask) < 10:
        raise ValueError("AOI too small or no valid SAR pixels")


    print("\nNormalizing VV -> Soil Moisture Index")

    smi = normalize_percentile(vv_filt, valid_mask, 2, 98)

    print("SMI min/max:",
        np.nanmin(smi), np.nanmax(smi))


    # =========================
    # CLASSIFICATION
    # =========================

    classes = np.zeros_like(smi, dtype=np.uint8)

    classes[smi < 0.2] = 1
    classes[(smi >= 0.2) & (smi < 0.4)] = 2
    classes[(smi >= 0.4) & (smi < 0.6)] = 3
    classes[(smi >= 0.6) & (smi < 0.8)] = 4
    classes[smi >= 0.8] = 5

    classes[water_mask] = 0
    classes[~mask] = 255


    print("\nClass Distribution")

    for i in range(6):
        print("Class", i, "pixels:", np.sum(classes == i))


    # =========================
    # AREA %
    # =========================

    valid_area_mask = (classes != 255) & (classes != 0)

    total_valid = np.sum(valid_area_mask)

    print("\nTotal valid pixels:", total_valid)
    area_stats = {
        "Very Dry": 0,
        "Dry": 0,
        "Moderate": 0,
        "Wet": 0,
        "Very Wet": 0
    }

    if total_valid > 0:

        class_mapping = {
            1: "Very Dry",
            2: "Dry",
            3: "Moderate",
            4: "Wet",
            5: "Very Wet"
        }

        for class_id, label in class_mapping.items():

            class_pixels = np.sum(classes == class_id)

            percent = (class_pixels / total_valid) * 100

            area_stats[label] = round(float(percent), 2)

            print(label, "=", area_stats[label], "%")
   
    print("Water class pixels:", np.sum(classes == 0))
    print("Very Dry pixels:", np.sum(classes == 1))
    rgba = soil_moisture_to_rgba(classes)

    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    png_bytes = buf.getvalue()

    stats = {
        "soil_moisture_min": float(np.nanmin(smi)),
        "soil_moisture_max": float(np.nanmax(smi)),
        "soil_moisture_mean": float(np.nanmean(smi)),
    }

    print("\nFinal Stats:", stats)
    print("========== SOIL MOISTURE DEBUG END ==========\n")

    return png_bytes, stats, bounds, crs, area_stats