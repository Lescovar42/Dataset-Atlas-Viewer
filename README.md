# Impervious surface dataset viewer

Local and Vercel-compatible viewer for five Boston-area aerial-image tiles from April 2005 and their MassGIS impervious-surface reference labels.

## Run locally on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe build.py
.\.venv\Scripts\python.exe viewer.py
```

Open http://127.0.0.1:8765. Existing extracted data under `data/raw/` is reused. `start-viewer.ps1` also runs the viewer when local data is available.

## Deploy to Vercel

1. Import https://github.com/Lescovar42/Dataset-Atlas-Viewer into Vercel.
2. Use the repository root as the Root Directory and **Flask** as the Framework Preset.
3. Keep the configured Build Command, `python build.py`. Leave Output Directory at the framework default.
4. Deploy. The application requires no environment variables or external storage account.

`vercel.json` supplies the Flask preset, build command, function duration, and bundle exclusions. `.python-version` pins Python 3.14.

During the build, `build.py` fetches six public MassGIS ZIP archives, verifies their SHA-256 checksums, extracts the imagery, and crops the 1 m label raster to the five image tiles. The function receives about 82 MiB of raster files, including the original compressed four-band JP2 images and small label pyramids. It reads these files without preparing data during requests. Browser scripts and styles are copied to `public/static/` for Vercel's CDN.

The Linux build also bundles `libexpat.so.1`. The entrypoint loads it before importing Rasterio because Vercel's Lambda runtime omits this system library.

The repository contains application code and a download manifest. Raw datasets, local preview caches, generated deployment files, virtual environments, and local settings are Git-ignored. A build requires outbound access to the MassGIS download service. Changed upstream archives cause a checksum error, which requires reviewing and updating `data-sources.json`.

## Verify preparation

```powershell
.\.venv\Scripts\python.exe build.py
.\.venv\Scripts\python.exe scripts/check_deployment.py
node --check viewer/app.js
```

The GitHub Actions workflow runs these checks on Linux using the Vercel-style WSGI entrypoint. It checks image rendering, label overlays, infrared/NDVI modes, thumbnails, static assets, and 15 source-pixel comparisons across all five tiles. A successful workflow verifies the Linux build and app routes. Vercel's actual deployment and bundle acceptance are verified when the project is imported and deployed there.

## Data sources and interpretation

- [MassGIS 2005 aerial imagery](https://www.mass.gov/info-details/massgis-data-2005-aerial-imagery) supplies 0.5 m red, green, blue, and near-infrared imagery. The supplied JP2 files use the publisher's lossy compression.
- [MassGIS Impervious Surface 2005](https://www.mass.gov/info-details/massgis-data-impervious-surface-2005) supplies 1 m labels with `1` for impervious and `0` for non-impervious. The reference map was created with semi-automated interpretation, manual review, and added road buffers.
- The files use EPSG:26986, NAD83 / Massachusetts Mainland. RGB enhancement changes the display. The inspector reports original image values. NDVI is calculated from 8-bit image values and should be treated as an exploratory index.

Leaflet is bundled under its included BSD-2-Clause license in `viewer/vendor/LICENSE.leaflet`.
