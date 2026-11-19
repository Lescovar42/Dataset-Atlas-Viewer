"""Local, georeferenced viewer for the project's aerial imagery and labels."""

import argparse
from functools import lru_cache
from io import BytesIO
import json
import math
from pathlib import Path
import threading

from flask import Flask, abort, jsonify, request, send_file
import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform as transform_coordinates
from rasterio.windows import Window


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "viewer_cache"
CACHE_VERSION = 1
app = Flask(__name__, static_folder=str(ROOT / "viewer"), static_url_path="/static")
catalog = {}
read_lock = threading.Lock()


def write_pyramid(path, pixels, transform, crs, categorical=False):
    temporary = path.with_suffix(".partial.tif")
    with rasterio.open(
        temporary, "w", driver="GTiff", width=pixels.shape[2],
        height=pixels.shape[1], count=pixels.shape[0], dtype="uint8",
        crs=crs, transform=transform, tiled=True, blockxsize=256,
        blockysize=256, compress="deflate", predictor=2,
    ) as destination:
        destination.write(pixels)
        destination.build_overviews(
            [2, 4, 8, 16, 32, 64],
            Resampling.nearest if categorical else Resampling.average,
        )
    temporary.replace(path)


def prepare():
    images = sorted(RAW.rglob("*.jp2"))
    labels = sorted(RAW.rglob("imp_ne6.img"))
    if not images or not labels:
        raise RuntimeError(f"Expected extracted .jp2 files and imp_ne6.img under {RAW}")
    CACHE.mkdir(parents=True, exist_ok=True)
    sources = images + labels
    signature = [[str(p.relative_to(ROOT)), p.stat().st_size, p.stat().st_mtime_ns] for p in sources]
    manifest = CACHE / "catalog.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous.get("version") == CACHE_VERSION and previous.get("signature") == signature:
            complete = all((CACHE / f"{t['id']}_{kind}.tif").exists()
                           for t in previous["tiles"] for kind in ["image", "label"])
            if complete:
                return previous

    result = {"version": CACHE_VERSION, "signature": signature, "tiles": []}
    samples = []
    with rasterio.open(labels[0]) as label_source:
        for path in images:
            print(f"Preparing {path.stem} ...", flush=True)
            with rasterio.open(path) as source:
                if source.crs != label_source.crs or source.crs.to_epsg() != 26986:
                    raise RuntimeError("This viewer expects the supplied EPSG:26986 image and label rasters.")
                bounds = list(source.bounds)
                if source.count != 4:
                    raise RuntimeError(f"{path.name} must have four bands.")
                pixels = source.read()
                samples.append(source.read(out_shape=(4, 256, 256)).reshape(4, -1))
                write_pyramid(CACHE / f"{path.stem}_image.tif", pixels, source.transform, source.crs)
                del pixels
                width = round(bounds[2] - bounds[0])
                height = round(bounds[3] - bounds[1])
                label_transform = from_bounds(*bounds, width, height)
                mask = np.full((height, width), 255, dtype="uint8")
                reproject(
                    source=rasterio.band(label_source, 1), destination=mask,
                    src_transform=label_source.transform, src_crs=label_source.crs,
                    dst_transform=label_transform, dst_crs=source.crs,
                    dst_nodata=255, resampling=Resampling.nearest,
                )
                valid = mask != 255
                impervious = int(np.count_nonzero(mask == 1))
                valid_count = int(np.count_nonzero(valid))
                if np.any(valid & ~np.isin(mask, [0, 1])):
                    raise RuntimeError("Unexpected label values in reference raster.")
                write_pyramid(CACHE / f"{path.stem}_label.tif", mask[None], label_transform, source.crs, True)
                result["tiles"].append({
                    "id": path.stem, "bounds": bounds, "width": source.width,
                    "height": source.height, "image_resolution": abs(source.res[0]),
                    "label_resolution": 1, "impervious_pixels": impervious,
                    "valid_pixels": valid_count,
                    "impervious_percent": 100 * impervious / valid_count if valid_count else None,
                    "image_file": str(path.relative_to(ROOT)),
                    "label_file": str(labels[0].relative_to(ROOT)),
                })
    all_samples = np.concatenate(samples, axis=1)
    useful = np.any(all_samples != 0, axis=0)
    result["stretch"] = np.percentile(all_samples[:, useful], [2, 98], axis=1).T.tolist()
    result["origin"] = [min(t["bounds"][0] for t in result["tiles"]),
                        min(t["bounds"][1] for t in result["tiles"])]
    result["bounds"] = [result["origin"][0], result["origin"][1],
                        max(t["bounds"][2] for t in result["tiles"]),
                        max(t["bounds"][3] for t in result["tiles"])]
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(manifest)
    return result


def color_image(pixels, mode, enhanced):
    valid = np.any(pixels != 0, axis=0)
    if mode == "ndvi":
        red, nir = pixels[0].astype("float32"), pixels[3].astype("float32")
        ndvi = (nir - red) / np.maximum(nir + red, 1)
        stops = np.array([-1, 0, .2, .5, 1])
        colors = np.array([[46, 72, 113], [193, 164, 134], [214, 217, 125],
                           [69, 150, 89], [18, 83, 54]])
        rgb = np.stack([np.interp(ndvi, stops, colors[:, i]) for i in range(3)], axis=-1).astype("uint8")
    else:
        bands = [3, 0, 1] if mode == "infrared" else [0, 1, 2]
        rgb = np.moveaxis(pixels[bands], 0, -1).astype("float32")
        if enhanced:
            ranges = np.array(catalog["stretch"])[bands]
            rgb = (rgb - ranges[:, 0]) * 255 / np.maximum(ranges[:, 1] - ranges[:, 0], 1)
        rgb = np.clip(rgb, 0, 255).astype("uint8")
    return np.dstack([rgb, valid.astype("uint8") * 255])


def tile_pixels(tile, kind, bounds, size):
    count = 4 if kind == "image" else 1
    output = np.full((count, size, size), 255 if kind == "label" else 0, dtype="uint8")
    resolution = (bounds[2] - bounds[0]) / size
    centers_x = bounds[0] + (np.arange(size) + .5) * resolution
    centers_y = bounds[3] - (np.arange(size) + .5) * resolution
    west, south, east, north = tile["bounds"]
    columns = np.flatnonzero((centers_x >= west) & (centers_x < east))
    rows = np.flatnonzero((centers_y > south) & (centers_y <= north))
    if not len(columns) or not len(rows):
        return output
    left, right = int(columns[0]), int(columns[-1]) + 1
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    intersection = [max(west, bounds[0] + left * resolution),
                    max(south, bounds[3] - bottom * resolution),
                    min(east, bounds[0] + right * resolution),
                    min(north, bounds[3] - top * resolution)]
    with rasterio.open(CACHE / f"{tile['id']}_{kind}.tif") as source:
        window = source.window(*intersection)
        output[:, top:bottom, left:right] = source.read(
            window=window, out_shape=(source.count, bottom - top, right - left),
            resampling=Resampling.nearest if kind == "label" else Resampling.bilinear,
        )
    return output


@lru_cache(maxsize=512)
def render_tile(layer, mode, enhanced, z, x, y):
    size = 256
    span = size / (2 ** z)
    ox, oy = catalog["origin"]
    left, top = ox + x * span, oy - y * span
    bounds = [left, top - span, left + span, top]
    rgba = np.zeros((size, size, 4), dtype="uint8")
    with read_lock:
        for tile in catalog["tiles"]:
            west, south, east, north = tile["bounds"]
            if bounds[0] >= east or bounds[2] <= west or bounds[1] >= north or bounds[3] <= south:
                continue
            if layer == "image":
                pixels = tile_pixels(tile, "image", bounds, size)
                colored = color_image(pixels, mode, enhanced)
                visible = colored[:, :, 3] > 0
            else:
                mask = tile_pixels(tile, "label", bounds, size)[0]
                colored = np.zeros_like(rgba)
                colored[mask == 1] = [255, 139, 88, 255]
                if layer == "label":
                    colored[mask == 0] = [52, 158, 141, 255]
                visible = colored[:, :, 3] > 0
            rgba[visible] = colored[visible]
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG", compress_level=2)
    return output.getvalue()


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/api/catalog")
def get_catalog():
    return jsonify({k: v for k, v in catalog.items() if k not in ["signature", "version"]})


@app.get("/tiles/<layer>/<int(signed=True):z>/<int(signed=True):x>/<int(signed=True):y>.png")
def get_tile(layer, z, x, y):
    mode = request.args.get("mode", "rgb")
    if layer not in ["image", "label", "overlay"] or mode not in ["rgb", "infrared", "ndvi"]:
        abort(400)
    if not -8 <= z <= 2 or abs(x) > 100000 or abs(y) > 100000:
        abort(400)
    data = render_tile(layer, mode, request.args.get("enhance", "1") == "1", z, x, y)
    return send_file(BytesIO(data), mimetype="image/png", max_age=86400)


@app.get("/api/thumbnail/<tile_id>.png")
def thumbnail(tile_id):
    tile = next((t for t in catalog["tiles"] if t["id"] == tile_id), None)
    if tile is None:
        abort(404)
    with read_lock, rasterio.open(CACHE / f"{tile_id}_image.tif") as source:
        rgba = color_image(source.read(out_shape=(4, 128, 128)), "rgb", True)
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", max_age=86400)


@app.get("/api/inspect")
def inspect_pixel():
    try:
        east, north = float(request.args["x"]), float(request.args["y"])
        if not math.isfinite(east) or not math.isfinite(north):
            raise ValueError()
    except (KeyError, ValueError):
        abort(400)
    tile = next((t for t in catalog["tiles"] if
                 t["bounds"][0] <= east < t["bounds"][2] and
                 t["bounds"][1] < north <= t["bounds"][3]), None)
    if tile is None:
        return jsonify({"covered": False})
    with read_lock:
        with rasterio.open(CACHE / f"{tile['id']}_image.tif") as image:
            row, col = image.index(east, north)
            values = image.read(window=Window(col, row, 1, 1))[:, 0, 0].tolist()
        with rasterio.open(CACHE / f"{tile['id']}_label.tif") as label:
            lr, lc = label.index(east, north)
            value = int(label.read(1, window=Window(lc, lr, 1, 1))[0, 0])
    lon, lat = transform_coordinates("EPSG:26986", "EPSG:4326", [east], [north])
    denominator = values[3] + values[0]
    return jsonify({"covered": any(values), "tile": tile["id"], "bands": values,
                    "label": value if value in [0, 1] else None,
                    "row": row, "col": col, "east": east, "north": north,
                    "latitude": lat[0], "longitude": lon[0],
                    "ndvi": (values[3] - values[0]) / denominator if denominator else None})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    catalog = prepare()
    print(f"Ready: {len(catalog['tiles'])} tiles. Viewer http://127.0.0.1:{args.port}", flush=True)
    if not args.prepare:
        app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False)
