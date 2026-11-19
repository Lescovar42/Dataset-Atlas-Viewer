"""WSGI entrypoint used by Vercel's Flask preset."""

import ctypes
import os
from pathlib import Path


# Rasterio's wheel expects Expat from the OS; the Lambda runtime omits it.
native_expat = Path(__file__).resolve().parent / "native" / "libexpat.so.1"
if os.name == "posix" and native_expat.is_file():
    ctypes.CDLL(str(native_expat), mode=ctypes.RTLD_GLOBAL)

from viewer import app
