# -*- coding: utf-8 -*-
"""Default export settings and named profiles saved as JSON in %APPDATA%."""
import copy
import json
import os

FORMATS = ["PDF", "DWG", "DGN", "DWF", "NWC", "IFC", "IMG", "XML"]

DEFAULTS = {
    "enabled": {"PDF": True, "DWG": False, "DGN": False, "DWF": False,
                "NWC": False, "IFC": False, "IMG": False, "XML": False},
    "naming": {
        "sheet_template": "{Sheet Number}-{Sheet Name}",
        "view_template": "{View Type}-{View Name}",
    },
    "pdf": {
        "placement": "Center",          # Center | Offset
        "margin": "NoMargin",           # NoMargin | PrinterLimit | UserDefined
        "offset_x_mm": 0.0,
        "offset_y_mm": 0.0,
        "zoom": "FitToPage",            # FitToPage | Zoom
        "zoom_percent": 100,
        "hidden_line": "Vector",        # Vector | Raster
        "raster_quality": "High",       # Low | Medium | High | Presentation
        "colors": "Color",              # Color | GrayScale | BlackLine
        "export_quality": "DPI300",     # DPI72 .. DPI4000
        "always_raster": False,
        "view_links_blue": False,
        "hide_ref_planes": True,
        "hide_unref_tags": True,
        "hide_scope_boxes": True,
        "hide_crop": True,
        "halftone_thin": False,
        "mask_coincident": False,
        "combine": False,
        "combined_name": "{Project Number}-Combined",
        "batch": True,                  # export sheets in batches (faster), then rename
    },
    "dwg": {
        "setup": "<In-session>",
        "version": "Default",
        "colors": "TrueColorPerView",   # TrueColorPerView | TrueColor | IndexColors | FromSetup
        "merged_views": True,           # False = views on sheets as xrefs
        "shared_coords": False,
        "hide_ref_planes": True,
        "hide_scope_boxes": True,
        "hide_unref_tags": True,
    },
    "dgn": {
        "setup": "<In-session>",
        "merged_views": True,
        "shared_coords": False,
        "hide_ref_planes": True,
        "hide_scope_boxes": True,
        "hide_unref_tags": True,
    },
    "dwf": {
        "dwfx": False,
        "combine": False,
        "export_object_data": True,
        "image_quality": "Default",     # Default | Low | Medium | High
        "image_format": "Lossless",     # Lossless | Lossy
        "hide_crop": True,
    },
    "nwc": {
        "coordinates": "Shared",        # Shared | Internal
        "export_links": False,
        "export_parts": False,
        "convert_properties": True,
        "export_room_geometry": False,
        "divide_by_levels": True,
        "export_urls": True,
        "export_element_ids": True,
        "find_missing_materials": True,
        "parameters": "All",            # All | Elements | None
    },
    "ifc": {
        "version": "IFC2x3CV2",
        "base_quantities": False,
        "split_walls": False,
        "space_boundaries": 0,
        "export_links": False,
        "current_view_only": True,
    },
    "img": {
        "file_type": "PNG",             # PNG | JPEGLossless | JPEGMedium | JPEGSmallest | TIFF | BMP | TARGA
        "resolution": "DPI_300",        # DPI_72 | DPI_150 | DPI_300 | DPI_600
        "pixel_size": 4096,
        "fit_direction": "Horizontal",
    },
    "xml": {
        "include_all_params": True,
    },
    "create": {
        "folder": "",
        "split_by_format": False,
        "report": "None",               # None | CSV
        "open_folder": True,
    },
}


def _deep_merge(base, override):
    result = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def profiles_dir():
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(root, "pyRevit", "SheetFlow", "profiles")
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def list_profiles():
    try:
        return sorted(os.path.splitext(f)[0] for f in os.listdir(profiles_dir())
                      if f.lower().endswith(".json") and not f.startswith("_"))
    except Exception:
        return []


def load_profile(name=None):
    """Load a named profile, or the last-used settings when name is None."""
    path = os.path.join(profiles_dir(), (name or "_last") + ".json")
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
    return _deep_merge(DEFAULTS, data)


def save_profile(cfg, name=None):
    path = os.path.join(profiles_dir(), (name or "_last") + ".json")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    return path


def delete_profile(name):
    path = os.path.join(profiles_dir(), name + ".json")
    if os.path.exists(path):
        os.remove(path)
