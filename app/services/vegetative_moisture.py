import io
import json
import numpy as np
import matplotlib.pyplot as plt

from rasterio.io import MemoryFile

from fastapi.responses import Response


def compute_vod_sar(input_tif_bytes: bytes):

    # --------------------------------
    # 1. Open GeoTIFF from memory
    # --------------------------------
    with MemoryFile(input_tif_bytes) as memfile:
        with memfile.open() as src:

            vv_db = src.read(1).astype(np.float32)
            vh_db = src.read(2).astype(np.float32)

            transform = src.transform
            crs = src.crs
            nodata = src.nodata
            bounds = src.bounds
            _profile = src.profile.copy()


    

    # --------------------------------
    # 3. Nodata Mask
    # --------------------------------
    mask = np.zeros(vv_db.shape, dtype=bool)
    if nodata is not None:
        mask = ~np.isfinite(vv_db) | ~np.isfinite(vh_db)

   

    # --------------------------------
    # 4. Convert dB → Linear
    # --------------------------------
    eps = 1e-10

    vv_lin = 10 ** (vv_db / 10)
    vh_lin = 10 ** (vh_db / 10)

    vv_lin = np.maximum(vv_lin, eps)
    vh_lin = np.maximum(vh_lin, eps)


    # --------------------------------
    # 5. Compute VOD-SAR
    # --------------------------------
    ratio = vv_lin / (vh_lin + eps)

    vod = np.log(ratio)

    vod[mask] = np.nan


    # --------------------------------
    # 6. Water Mask (Empirical)
    # --------------------------------
    water_mask = (vv_db < -18) & (vh_db < -22) & (~mask)


    # --------------------------------
    # 7. Z-Score Normalization
    # --------------------------------
    if np.all(np.isnan(vod)):
        raise ValueError("VOD array contains only NaN values")

    std = max(np.nanstd(vod), 1e-6)
    mean = np.nanmean(vod)


    z = (vod - mean) / (std + eps)


    # --------------------------------
    # 8. Classification
    # --------------------------------
    classified = np.zeros(z.shape, dtype=np.uint8)

    # Water
    classified[water_mask] = 1

    # Extreme Moisture
    classified[(z >= 1.5) & (~water_mask)] = 2

    # Above Normal
    classified[(z >= 0.5) & (z < 1.5)] = 3

    # Normal
    classified[(z >= -0.5) & (z < 0.5)] = 4

    # Deficient
    classified[(z >= -1.5) & (z < -0.5)] = 5

    # Severe Deficient
    classified[(z < -1.5)] = 6

    # NoData
    classified[np.isnan(z)] = 0


    # --------------------------------
    # 9. Area Statistics (Optional)
    # --------------------------------
    area_stats = {}

    pixel_area = abs(transform.a * transform.e)

    labels = {
        0: "NoData",
        1: "Water",
        2: "Extreme Moisture",
        3: "Above Normal",
        4: "Normal",
        5: "Deficient",
        6: "Severe Deficient"
    }

    for k, name in labels.items():
        count = np.sum(classified == k)
        area_stats[name] = float(count * pixel_area)


    # --------------------------------
    # 10. Plot
    # --------------------------------
    colors = [
        "#00000000",  # NoData
        "#08306b",  # Water
        "#41b6c4",  # Extreme Moisture
        "#2ca25f",  # Above Normal
        "#ffffbf",  # Normal
        "#fdae61",  # Deficient
        "#d73027"   # Severe
    ]

    cmap = plt.matplotlib.colors.ListedColormap(colors)

    cbounds = np.arange(0, 8)

    norm = plt.matplotlib.colors.BoundaryNorm(cbounds, cmap.N)


    plt.figure(figsize=(11, 9))

    img = plt.imshow(classified, cmap=cmap, norm=norm)

    
    plt.axis("off")


    # --------------------------------
    # 11. Export to PNG
    # --------------------------------
    buf = io.BytesIO()

    plt.gca().set_position([0, 0, 1, 1])
    plt.gcf().set_facecolor("none")

    plt.savefig(
        buf,
        format="png",
        dpi=200,
        bbox_inches="tight",
        pad_inches=0,
        transparent=True
    )


    plt.close()

    buf.seek(0)

    png_bytes = buf.read()


    # --------------------------------
    # 12. Return API Response
    # --------------------------------
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-BBOX": ",".join(map(str, bounds)),
            "X-CRS": str(crs),
            "X-AREA-STATS": json.dumps(area_stats),
            # "X-SIZE": f"{classified.shape[1]}x{classified.shape[0]}"
        }
    )


