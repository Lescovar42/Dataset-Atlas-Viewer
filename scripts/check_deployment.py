"""Exercise the deployed entrypoint and compare its pixels with source data."""

from io import BytesIO
import json
import os
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import rasterio
from rasterio.windows import Window


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["VERCEL"] = "1"

from app import app


def main():
    client = app.test_client()
    health = client.get("/api/health")
    assert health.status_code == 200, health.data
    assert health.json == {"status": "ok", "tiles": 5, "crs": "EPSG:26986"}
    assert client.get("/").status_code == 200
    catalog = client.get("/api/catalog").json
    assert len(catalog["tiles"]) == 5
    assert sum(tile["valid_pixels"] for tile in catalog["tiles"]) == 80_000_000

    for tile in catalog["tiles"]:
        response = client.get(f"/api/thumbnail/{tile['id']}.png")
        assert response.status_code == 200
        assert Image.open(BytesIO(response.data)).size == (128, 128)
        assert (ROOT / tile["image_path"]).is_file()
        assert (ROOT / tile["label_path"]).is_file()
        with rasterio.open(ROOT / tile["image_file"]) as image:
            with rasterio.open(ROOT / tile["label_file"]) as labels:
                for row, col in [(500, 500), (3211, 4255), (7000, 6000)]:
                    east, north = image.xy(row, col)
                    inspected = client.get(f"/api/inspect?x={east}&y={north}")
                    assert inspected.status_code == 200, inspected.data
                    values = image.read(window=Window(col, row, 1, 1))[:, 0, 0].tolist()
                    lr, lc = labels.index(east, north)
                    label = int(labels.read(1, window=Window(lc, lr, 1, 1))[0, 0])
                    assert inspected.json["bands"] == values
                    assert inspected.json["label"] == label

    routes = [
        "/tiles/image/-4/1/-2.png",
        "/tiles/image/-4/1/-2.png?mode=infrared",
        "/tiles/image/-4/1/-2.png?mode=ndvi",
        "/tiles/label/-4/1/-2.png",
        "/tiles/overlay/-4/1/-2.png",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        pixels = np.asarray(Image.open(BytesIO(response.data)))
        assert pixels.shape == (256, 256, 4), route
        assert np.count_nonzero(pixels[:, :, 3]) > 0, route

    assert client.get("/api/inspect?x=1&y=1").json == {"covered": False}
    assert client.get("/api/inspect?x=nan&y=0").status_code == 400
    assert client.get("/tiles/unknown/0/0/0.png").status_code == 400
    for asset in ["app.js", "style.css", "vendor/leaflet.js", "vendor/leaflet.css"]:
        assert (ROOT / "public" / "static" / asset).is_file()
    print("PASS: serverless entrypoint, 15 source-pixel comparisons, all display modes, thumbnails, CDN assets.")


if __name__ == "__main__":
    main()
