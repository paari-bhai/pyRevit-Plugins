# -*- coding: utf-8 -*-
"""Explain why a sheet is slow to export: what each view on it has to draw."""
import time

import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, ViewType


def _eid(element_id):
    try:
        return int(element_id.Value)
    except AttributeError:
        return element_id.IntegerValue


def _cat_id(name):
    bic = getattr(BuiltInCategory, name, None)
    return int(bic) if bic is not None else None


# Categories that are typically expensive to draw/export in landscape and site models
HEAVY_CATEGORIES = [
    ("Topography", "OST_Topography"),
    ("Toposolid", "OST_Toposolid"),
    ("Planting", "OST_Planting"),
    ("Site", "OST_Site"),
    ("Entourage", "OST_Entourage"),
    ("Floors", "OST_Floors"),
    ("Hardscape", "OST_Hardscape"),
    ("Detail items", "OST_DetailComponents"),
    ("Raster images", "OST_RasterImages"),
]


class ViewProfile(object):
    def __init__(self, view):
        self.view = view
        self.name = view.Name
        self.vtype = str(view.ViewType)
        self.scale = getattr(view, "Scale", 0)
        self.elements = 0
        self.by_cat = {}
        self.links = []
        self.cad = 0
        self.filled_regions = 0
        self.crop_off = False
        self.detail = ""
        self.linked_elements = None   # Revit 2024+: elements visible from links
        self.scan_seconds = 0.0
        self.notes = []
        self.score = 0.0


def profile_view(doc, view):
    p = ViewProfile(view)
    t0 = time.time()
    try:
        p.crop_off = not view.CropBoxActive
    except Exception:
        pass
    try:
        p.detail = str(view.DetailLevel)
    except Exception:
        pass
    try:
        visible = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        elements = list(visible)
    except Exception:
        p.notes.append("couldn't list elements in this view")
        return p
    p.elements = len(elements)
    wanted = dict((_cat_id(bic), label) for label, bic in HEAVY_CATEGORIES if _cat_id(bic))
    for el in elements:
        if isinstance(el, DB.RevitLinkInstance):
            p.links.append(el)
            continue
        if isinstance(el, DB.ImportInstance):
            p.cad += 1
            continue
        if isinstance(el, DB.FilledRegion):
            p.filled_regions += 1
            continue
        cat = el.Category
        if cat is None:
            continue
        label = wanted.get(_eid(cat.Id))
        if label:
            p.by_cat[label] = p.by_cat.get(label, 0) + 1

    # Revit 2024+ can count what each visible link draws in this view.
    if p.links:
        total = 0
        try:
            for li in p.links:
                total += FilteredElementCollector(doc, view.Id, li.Id)\
                    .WhereElementIsNotElementType().GetElementCount()
            p.linked_elements = total
        except Exception:
            p.linked_elements = None
    p.scan_seconds = time.time() - t0
    _score(p)
    return p


def _score(p):
    s = p.elements / 1000.0
    if p.linked_elements:
        s += p.linked_elements / 1000.0
    elif p.links:
        s += 5.0 * len(p.links)       # unknown size: assume heavy
    s += 3.0 * p.cad
    s += p.filled_regions / 50.0
    s += 4.0 * (p.by_cat.get("Topography", 0) + p.by_cat.get("Toposolid", 0))
    s += p.by_cat.get("Planting", 0) / 100.0 + p.by_cat.get("Entourage", 0) / 100.0
    s += 2.0 * p.by_cat.get("Raster images", 0)
    if p.crop_off and p.view.ViewType not in (ViewType.Legend, ViewType.DraftingView):
        s *= 1.5
    p.score = s

    if p.links:
        names = ", ".join(sorted(set(li.Name.split(":")[0].strip() for li in p.links)))[:120]
        extra = " ({0:,} linked elements drawn)".format(p.linked_elements) \
            if p.linked_elements else ""
        p.notes.append("Revit links visible: {0}{1}. Hide links in this view's "
                       "Visibility/Graphics if they aren't needed on the sheet.".format(names, extra))
    if p.cad:
        p.notes.append("{0} CAD file(s) visible. Large DWG underlays are very slow to "
                       "re-export as DWG.".format(p.cad))
    topo = p.by_cat.get("Topography", 0) + p.by_cat.get("Toposolid", 0)
    if topo:
        p.notes.append("{0} topography/toposolid element(s). Dense surfaces with many points "
                       "and contours export very slowly; simplify the surface or hide "
                       "contours if they aren't needed.".format(topo))
    if p.filled_regions > 200:
        p.notes.append("{0} filled regions. Many hatched regions make big, slow DWGs.".format(
            p.filled_regions))
    if p.by_cat.get("Planting", 0) > 300:
        p.notes.append("{0} planting families. Use a simpler symbolic representation at "
                       "this scale (coarse detail level or a lighter family).".format(
                           p.by_cat["Planting"]))
    if p.by_cat.get("Raster images", 0):
        p.notes.append("Raster images on the view. Large images are slow; downscale them.")
    if p.crop_off and p.view.ViewType not in (ViewType.Legend, ViewType.DraftingView):
        p.notes.append("Crop region is off, so Revit processes the whole model extent. "
                       "Turn on and tighten the crop.")
    if p.detail == "Fine" and p.scale and p.scale >= 200:
        p.notes.append("Fine detail at 1:{0}. Medium or Coarse is usually enough at this "
                       "scale and draws far less.".format(p.scale))


def profile_sheet(doc, sheet):
    views = []
    for vid in sheet.GetAllPlacedViews():
        v = doc.GetElement(vid)
        if v is not None:
            views.append(profile_view(doc, v))
    views.sort(key=lambda p: -p.score)
    return views
