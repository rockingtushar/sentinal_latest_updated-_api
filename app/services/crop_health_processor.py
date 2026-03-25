import io
import numpy as np
import matplotlib.pyplot as plt
from rasterio.io import MemoryFile
from scipy.ndimage import uniform_filter
from sklearn.cluster import KMeans
from rasterio.features import geometry_mask
from shapely.geometry import shape

def refined_lee_filter(img, size=7):
    img = img.astype(np.float32)

    if np.all(np.isnan(img)):
        return img

    img_mean = uniform_filter(img, size)
    img_sqr_mean = uniform_filter(img ** 2, size)
    img_var = img_sqr_mean - img_mean ** 2

    if np.all(np.isnan(img_var)):
        return img

    overall_var = np.nanmean(img_var)

    if not np.isfinite(overall_var) or overall_var <= 0:
        return img

    weights = img_var / (img_var + overall_var + 1e-9)
    weights = np.clip(weights, 0, 1)

    return (img_mean + weights * (img - img_mean)).astype(np.float32)



def normalize_percentile(x, valid_mask, pmin, pmax):
    if np.sum(valid_mask) == 0:
        return np.zeros_like(x, dtype=np.float32)

    lo = np.percentile(x[valid_mask], pmin)
    hi = np.percentile(x[valid_mask], pmax)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1).astype(np.float32)


def health_to_rgba(classes):
    rgba = np.zeros((classes.shape[0], classes.shape[1], 4), dtype=np.uint8)

    rgba[classes == 0] = [160,160,160,180]
    rgba[classes == 1] = [255,0,0,180]
    rgba[classes == 2] = [255,159,159,180]
    rgba[classes == 3] = [150,237,150,180]
    rgba[classes == 4] = [0,223,0,180]
    rgba[classes == 5] = [0,106,0,180]

    
    rgba[classes == 255] = [0,0,0,0]

    return rgba




def process_crop_health_to_png_bytes(raw_tif_bytes: bytes, aoi_geojson: dict):
    with MemoryFile(raw_tif_bytes) as memfile:
        with memfile.open() as src:
            vv_db = src.read(1).astype(np.float32)
            vh_db = src.read(2).astype(np.float32)
            vv_db[vv_db <= -50] = np.nan
            vh_db[vh_db <= -50] = np.nan
           
            bounds = src.bounds        # (minx, miny, maxx, maxy)
            crs = src.crs.to_string()  # e.g. EPSG:3857
            transform = src.transform
            height = src.height
            width = src.width

       
            geoms = [shape(f["geometry"]) for f in aoi_geojson["features"]]

            mask = geometry_mask(
                geoms,
                transform=transform,
                invert=True,
                out_shape=(height, width)
            )

            vv_db[~mask] = np.nan
            vh_db[~mask] = np.nan

    print("VV min/max:", np.nanmin(vv_db), np.nanmax(vv_db))
    print("VH min/max:", np.nanmin(vh_db), np.nanmax(vh_db))

    vv_filt = refined_lee_filter(vv_db)
    vh_filt = refined_lee_filter(vh_db)

    water_mask = (vv_db < -18) & (vh_db < -22)
    vv_filt[water_mask] = np.nan
    vh_filt[water_mask] = np.nan

    valid_mask = np.isfinite(vv_filt) & np.isfinite(vh_filt)
    if np.sum(valid_mask) < 10:
        raise ValueError("AOI too small or no valid SAR pixels")

    biomass = vv_filt - vh_filt
    biomass_norm = normalize_percentile(biomass, valid_mask, 5, 95)
    soil_norm = normalize_percentile(vv_filt, valid_mask, 2, 98)

    crop_health = (0.6 * biomass_norm + 0.4 * soil_norm).astype(np.float32)
    

    std_val = np.nanstd(crop_health)
    print("DEBUG crop health std:", std_val)

    if std_val < 0.02:
        classes = np.zeros_like(crop_health, dtype=np.uint8)
        classes[valid_mask] = 3
        classes[water_mask] = 0
    else:
        X = crop_health.reshape(-1, 1)
        valid_idx = np.isfinite(X[:, 0])
        X_valid = X[valid_idx]

        kmeans = KMeans(n_clusters=5, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X_valid)

        classified = np.full(X.shape[0], -1, dtype=np.int32)
        classified[valid_idx] = labels
        classified = classified.reshape(crop_health.shape)

        cluster_means = [
            np.nanmean(crop_health[classified == i])
            if np.any(classified == i) else -np.inf
            for i in range(5)
        ]

        order = np.argsort(cluster_means)[::-1]  # highest first

        classes = np.zeros_like(classified, dtype=np.uint8)

        for rank, cluster_id in enumerate(order):
        # rank 0 = highest health
            classes[classified == cluster_id] = 5 - rank

        # water always 0
        classes[water_mask] = 0
        classes[~mask] = 255
        max_idx = np.nanargmax(crop_health)
        print("Highest pixel class:", classes.flat[max_idx])

        print("\n===== CROP HEALTH CLUSTER RANGES =====")
        valid_area_mask = (classes != 255) & (classes != 0)
        total_valid = np.sum(valid_area_mask)

        area_stats = {
            "Poor": 0,
            "Below Normal": 0,
            "Normal": 0,
            "Above Normal": 0,
            "Excellent": 0
        }

        if total_valid > 0:
            class_mapping = {
                1: "Poor",
                2: "Below Normal",
                3: "Normal",
                4: "Above Normal",
                5: "Excellent"
            }

            for class_id, label in class_mapping.items():
                class_pixels = np.sum(classes == class_id)
                percent = (class_pixels / total_valid) * 100
                area_stats[label] = round(float(percent), 2)

        

        for i in range(5):
            mask_i = classified == i
            if np.any(mask_i):
                print(
                f"Cluster {i}: "
                f"min={np.nanmin(crop_health[mask_i]):.3f}, "
                f"max={np.nanmax(crop_health[mask_i]):.3f}, "
                f"mean={np.nanmean(crop_health[mask_i]):.3f}")
                
        print("Highest mean:", max(cluster_means))
        print("Lowest mean:", min(cluster_means))
        
        
        for rank, idx in enumerate(order):
            print(f"Rank {rank+1}: Cluster {idx} with mean health {cluster_means[idx]:.3f}")

            
        print("AREA %:", area_stats)


    rgba = health_to_rgba(classes)
    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    png_bytes = buf.getvalue()

    stats = {
        "crop_health_index_min": float(np.nanmin(crop_health)),
        "crop_health_index_max": float(np.nanmax(crop_health)),
        "crop_health_index_mean": float(np.nanmean(crop_health)),
    }

   
    return png_bytes, stats, bounds, crs,area_stats

