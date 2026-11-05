# -*- coding: utf-8 -*-
"""Format exporters built on the native Revit export APIs.

Each exporter has the signature
    export_<fmt>(doc, folder, item, name, cfg) -> list of written file paths
and raises ExportSkipped / Exception on failure. PDF and DWF can also combine
many items into one file (see export_pdf_combined / export_dwf_combined).
"""
import datetime
import os
import time

from System.Collections.Generic import List
from Autodesk.Revit.DB import ElementId, ViewSet, Transaction

FT_TO_MM = 304.8
IN_SESSION = "<In-session>"

# Paper size label -> ExportPaperFormat member name
PAPER_FORMAT_NAMES = {
    "A0": "ISO_A0", "A1": "ISO_A1", "A2": "ISO_A2", "A3": "ISO_A3", "A4": "ISO_A4",
    "ARCH C": "ARCH_C", "ARCH D": "ARCH_D", "ARCH E": "ARCH_E", "ARCH E1": "ARCH_E1",
    "ANSI A": "ANSI_A", "ANSI B": "ANSI_B", "ANSI C": "ANSI_C", "ANSI D": "ANSI_D",
    "ANSI E": "ANSI_E",
}
PAPER_CHOICES = ["Auto"] + sorted(PAPER_FORMAT_NAMES.keys())
ORIENTATION_CHOICES = ["Auto", "Landscape", "Portrait"]

IMAGE_EXT = {
    "PNG": ".png", "JPEGLossless": ".jpg", "JPEGMedium": ".jpg", "JPEGSmallest": ".jpg",
    "TIFF": ".tif", "BMP": ".bmp", "TARGA": ".tga",
}


class ExportSkipped(Exception):
    """The item can't be exported to this format (e.g. NWC from a sheet)."""


def enum_member(enum_type, name, default=None):
    try:
        return getattr(enum_type, name)
    except Exception:
        return default


def _ids(items):
    lst = List[ElementId]()
    for i in items:
        lst.Add(i.id)
    return lst


def _try_set(obj, attr, value):
    """Set an option if this Revit version has it; ignore otherwise."""
    if value is None:
        return
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _paper_format(item):
    from Autodesk.Revit.DB import ExportPaperFormat
    name = PAPER_FORMAT_NAMES.get(item.paper_size)
    if name is None:
        return ExportPaperFormat.Default
    return enum_member(ExportPaperFormat, name, ExportPaperFormat.Default)


def _orientation(item):
    from Autodesk.Revit.DB import PageOrientationType
    return enum_member(PageOrientationType, item.paper_orientation or "Auto",
                       enum_member(PageOrientationType, "Auto"))


# --------------------------------------------------------------------------- PDF
def pdf_supported():
    try:
        from Autodesk.Revit.DB import PDFExportOptions  # noqa: F401  (Revit 2022+)
        return True
    except ImportError:
        return False


def _pdf_options(cfg):
    from Autodesk.Revit.DB import (
        PDFExportOptions, PaperPlacementType, MarginType, ZoomType, HiddenLineViewsType,
        RasterQualityType, ColorDepthType, PDFExportQualityType,
    )
    p = cfg
    o = PDFExportOptions()
    if p["placement"] == "Center":
        o.PaperPlacement = PaperPlacementType.Center
    else:
        o.PaperPlacement = PaperPlacementType.LowerLeft
        o.MarginType = enum_member(MarginType, p["margin"], MarginType.NoMargin)
        if p["margin"] == "UserDefined":
            _try_set(o, "OriginOffsetX", float(p["offset_x_mm"]) / FT_TO_MM)
            _try_set(o, "OriginOffsetY", float(p["offset_y_mm"]) / FT_TO_MM)
    if p["zoom"] == "Zoom":
        o.ZoomType = ZoomType.Zoom
        o.ZoomPercentage = int(p["zoom_percent"])
    else:
        o.ZoomType = ZoomType.FitToPage
    o.HiddenLineViews = (HiddenLineViewsType.RasterProcessing if p["hidden_line"] == "Raster"
                         else HiddenLineViewsType.VectorProcessing)
    o.RasterQuality = enum_member(RasterQualityType, p["raster_quality"], RasterQualityType.High)
    o.ColorDepth = enum_member(ColorDepthType, p["colors"], ColorDepthType.Color)
    _try_set(o, "ExportQuality", enum_member(PDFExportQualityType, p["export_quality"]))
    o.AlwaysUseRaster = bool(p["always_raster"])
    o.ViewLinksInBlue = bool(p["view_links_blue"])
    o.HideReferencePlane = bool(p["hide_ref_planes"])
    o.HideUnreferencedViewTags = bool(p["hide_unref_tags"])
    o.HideScopeBoxes = bool(p["hide_scope_boxes"])
    o.HideCropBoundaries = bool(p["hide_crop"])
    o.ReplaceHalftoneWithThinLines = bool(p["halftone_thin"])
    o.MaskCoincidentLines = bool(p["mask_coincident"])
    _try_set(o, "StopOnError", False)
    return o


def export_pdf(doc, folder, item, name, cfg):
    if not pdf_supported():
        raise ExportSkipped("Native PDF export needs Revit 2022 or newer")
    o = _pdf_options(cfg)
    o.Combine = True          # one view per call, so this just lets us set FileName
    o.FileName = name
    o.PaperFormat = _paper_format(item)
    _try_set(o, "PaperOrientation", _orientation(item))
    if not doc.Export(folder, _ids([item]), o):
        raise Exception("Revit reported the PDF export failed")
    return [os.path.join(folder, name + ".pdf")]


def export_pdf_combined(doc, folder, items, name, cfg):
    if not pdf_supported():
        raise ExportSkipped("Native PDF export needs Revit 2022 or newer")
    from Autodesk.Revit.DB import ExportPaperFormat
    o = _pdf_options(cfg)
    o.Combine = True
    o.FileName = name
    # Mixed sizes in one PDF: let Revit size every page from its title block.
    sizes = set(i.paper_size for i in items)
    o.PaperFormat = _paper_format(items[0]) if len(sizes) == 1 else ExportPaperFormat.Default
    orients = set(i.paper_orientation for i in items)
    if len(orients) == 1:
        _try_set(o, "PaperOrientation", _orientation(items[0]))
    if not doc.Export(folder, _ids(items), o):
        raise Exception("Revit reported the PDF export failed")
    return [os.path.join(folder, name + ".pdf")]


# --------------------------------------------------------------------------- DWG / DGN
def predefined_setups(doc):
    from Autodesk.Revit.DB import BaseExportOptions
    try:
        return [IN_SESSION] + sorted(BaseExportOptions.GetPredefinedSetupNames(doc))
    except Exception:
        return [IN_SESSION]


def _apply_cad_common(o, cfg):
    _try_set(o, "MergedViews", bool(cfg["merged_views"]))
    _try_set(o, "SharedCoords", bool(cfg["shared_coords"]))
    _try_set(o, "HideReferencePlane", bool(cfg["hide_ref_planes"]))
    _try_set(o, "HideScopeBox", bool(cfg["hide_scope_boxes"]))
    _try_set(o, "HideUnreferenceViewTags", bool(cfg["hide_unref_tags"]))


def export_dwg(doc, folder, item, name, cfg):
    from Autodesk.Revit.DB import DWGExportOptions, ACADVersion
    o = None
    if cfg["setup"] and cfg["setup"] != IN_SESSION:
        o = DWGExportOptions.GetPredefinedOptions(doc, cfg["setup"])
    if o is None:
        o = DWGExportOptions()
        _apply_cad_common(o, cfg)
    else:
        # A saved setup owns layers/colours/etc; only the xref choice comes from here.
        _try_set(o, "MergedViews", bool(cfg["merged_views"]))
    if cfg["version"] and cfg["version"] != "Default":
        _try_set(o, "FileVersion", enum_member(ACADVersion, cfg["version"]))
    if not doc.Export(folder, name, _ids([item]), o):
        raise Exception("Revit reported the DWG export failed")
    return [os.path.join(folder, name + ".dwg")]


def export_dgn(doc, folder, item, name, cfg):
    from Autodesk.Revit.DB import DGNExportOptions
    o = None
    if cfg["setup"] and cfg["setup"] != IN_SESSION:
        try:
            o = DGNExportOptions.GetPredefinedOptions(doc, cfg["setup"])
        except Exception:
            o = None
    if o is None:
        o = DGNExportOptions()
        _apply_cad_common(o, cfg)
    else:
        _try_set(o, "MergedViews", bool(cfg["merged_views"]))
    if not doc.Export(folder, name, _ids([item]), o):
        raise Exception("Revit reported the DGN export failed")
    return [os.path.join(folder, name + ".dgn")]


# --------------------------------------------------------------------------- DWF / DWFx
def _dwf_options(cfg, item=None):
    from Autodesk.Revit.DB import (DWFExportOptions, DWFXExportOptions, DWFImageQuality,
                                   DWFImageFormat)
    o = DWFXExportOptions() if cfg["dwfx"] else DWFExportOptions()
    o.ExportObjectData = bool(cfg["export_object_data"])
    _try_set(o, "ImageQuality", enum_member(DWFImageQuality, cfg["image_quality"]))
    _try_set(o, "ImageFormat", enum_member(DWFImageFormat, cfg["image_format"]))
    _try_set(o, "CropBoxVisible", not bool(cfg["hide_crop"]))
    if item is not None and item.paper_size != "Auto":
        _try_set(o, "PaperFormat", _paper_format(item))
    if item is not None and item.paper_orientation == "Portrait":
        _try_set(o, "PortraitLayout", True)
    return o


def _view_set(items):
    vs = ViewSet()
    for i in items:
        vs.Insert(i.element)
    return vs


def export_dwf(doc, folder, item, name, cfg):
    o = _dwf_options(cfg, item)
    o.MergedViews = False
    if not doc.Export(folder, name, _view_set([item]), o):
        raise Exception("Revit reported the DWF export failed")
    return [os.path.join(folder, name + (".dwfx" if cfg["dwfx"] else ".dwf"))]


def export_dwf_combined(doc, folder, items, name, cfg):
    o = _dwf_options(cfg)
    o.MergedViews = True
    if not doc.Export(folder, name, _view_set(items), o):
        raise Exception("Revit reported the DWF export failed")
    return [os.path.join(folder, name + (".dwfx" if cfg["dwfx"] else ".dwf"))]


# --------------------------------------------------------------------------- NWC
def nwc_supported():
    try:
        from Autodesk.Revit.DB import OptionalFunctionalityUtils
        return OptionalFunctionalityUtils.IsNavisworksExporterAvailable()
    except Exception:
        return False


def export_nwc(doc, folder, item, name, cfg):
    from Autodesk.Revit.DB import (NavisworksExportOptions, NavisworksExportScope,
                                   NavisworksCoordinates, NavisworksParameters)
    if not item.is_3d:
        raise ExportSkipped("NWC only exports 3D views")
    if not nwc_supported():
        raise ExportSkipped("Navisworks exporter for Revit is not installed")
    o = NavisworksExportOptions()
    o.ExportScope = NavisworksExportScope.View
    o.ViewId = item.id
    o.Coordinates = (NavisworksCoordinates.Internal if cfg["coordinates"] == "Internal"
                     else NavisworksCoordinates.Shared)
    o.ExportLinks = bool(cfg["export_links"])
    o.ExportParts = bool(cfg["export_parts"])
    o.ConvertElementProperties = bool(cfg["convert_properties"])
    o.ExportRoomGeometry = bool(cfg["export_room_geometry"])
    o.DivideFileIntoLevels = bool(cfg["divide_by_levels"])
    o.ExportUrls = bool(cfg["export_urls"])
    o.ExportElementIds = bool(cfg["export_element_ids"])
    o.FindMissingMaterials = bool(cfg["find_missing_materials"])
    _try_set(o, "Parameters", enum_member(NavisworksParameters, cfg["parameters"]))
    doc.Export(folder, name, o)
    return [os.path.join(folder, name + ".nwc")]


# --------------------------------------------------------------------------- IFC
def export_ifc(doc, folder, item, name, cfg):
    from Autodesk.Revit.DB import IFCExportOptions, IFCVersion
    if item.is_sheet:
        raise ExportSkipped("IFC exports views, not sheets")
    o = IFCExportOptions()
    _try_set(o, "FileVersion", enum_member(IFCVersion, cfg["version"]))
    o.ExportBaseQuantities = bool(cfg["base_quantities"])
    o.WallAndColumnSplitting = bool(cfg["split_walls"])
    o.SpaceBoundaryLevel = int(cfg["space_boundaries"])
    if cfg["current_view_only"]:
        o.FilterViewId = item.id
        o.AddOption("VisibleElementsOfCurrentView", "true")
    o.AddOption("ExportLinkedFiles", "true" if cfg["export_links"] else "false")
    t = Transaction(doc, "SheetFlow IFC export")
    t.Start()
    try:
        ok = doc.Export(folder, name + ".ifc", o)
    finally:
        # The IFC exporter needs an open transaction but nothing should stay in the model.
        t.RollBack()
    if not ok:
        raise Exception("Revit reported the IFC export failed")
    return [os.path.join(folder, name + ".ifc")]


# --------------------------------------------------------------------------- Images
def export_img(doc, folder, item, name, cfg):
    from Autodesk.Revit.DB import (ImageExportOptions, ExportRange, ImageFileType,
                                   ImageResolution, ZoomFitType, FitDirectionType)
    ftype = enum_member(ImageFileType, cfg["file_type"], ImageFileType.PNG)
    ext = IMAGE_EXT.get(cfg["file_type"], ".png")
    o = ImageExportOptions()
    o.ExportRange = ExportRange.SetOfViews
    o.SetViewsAndSheets(_ids([item]))
    o.FilePath = os.path.join(folder, name)
    o.HLRandWFViewsFileType = ftype
    o.ShadowViewsFileType = ftype
    o.ImageResolution = enum_member(ImageResolution, cfg["resolution"], ImageResolution.DPI_300)
    o.ZoomType = ZoomFitType.FitToPage
    o.PixelSize = int(cfg["pixel_size"])
    o.FitDirection = (FitDirectionType.Vertical if cfg["fit_direction"] == "Vertical"
                      else FitDirectionType.Horizontal)
    started = time.time() - 2
    before = set(os.listdir(folder))
    doc.ExportImage(o)
    # Revit appends " - Sheet - <number> - <name>" to the path; rename to what we asked for.
    target = os.path.join(folder, name + ext)
    produced = []
    for f in os.listdir(folder):
        full = os.path.join(folder, f)
        if not f.startswith(name) or os.path.splitext(f)[1].lower() != ext:
            continue
        if f not in before or os.path.getmtime(full) >= started:
            produced.append(full)
    produced = [p for p in produced if os.path.normcase(p) != os.path.normcase(target)] or produced
    if not produced:
        raise Exception("Revit did not write an image file")
    src = produced[0]
    if os.path.normcase(src) != os.path.normcase(target):
        if os.path.exists(target):
            os.remove(target)
        os.rename(src, target)
    return [target]


# --------------------------------------------------------------------------- XML
def _xml_escape(s):
    s = u"{0}".format(s if s is not None else "")
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _element_params(element):
    out = []
    for p in element.Parameters:
        try:
            if not p.HasValue:
                continue
            val = p.AsString()
            if val is None:
                val = p.AsValueString()
            if val is None:
                continue
            out.append((p.Definition.Name, val))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def export_xml(doc, folder, items, name, cfg):
    """Write one XML file listing the exported sheets/views and their parameters."""
    path = os.path.join(folder, name + ".xml")
    lines = [u'<?xml version="1.0" encoding="utf-8"?>',
             u'<Export model="{0}" date="{1}">'.format(
                 _xml_escape(doc.Title), datetime.datetime.now().isoformat())]
    for i in items:
        lines.append(u'  <{0} id="{1}" number="{2}" name="{3}" revision="{4}" size="{5}" '
                     u'orientation="{6}">'.format(
                         i.kind, i.id, _xml_escape(i.number), _xml_escape(i.name),
                         _xml_escape(i.revision), _xml_escape(i.size),
                         _xml_escape(i.orientation)))
        if cfg.get("include_all_params", True):
            for pname, pval in _element_params(i.element):
                lines.append(u'    <Parameter name="{0}">{1}</Parameter>'.format(
                    _xml_escape(pname), _xml_escape(pval)))
        lines.append(u'  </{0}>'.format(i.kind))
    lines.append(u'</Export>')
    import io
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"\n".join(lines))
    return [path]


PER_ITEM = {
    "PDF": export_pdf, "DWG": export_dwg, "DGN": export_dgn, "DWF": export_dwf,
    "NWC": export_nwc, "IFC": export_ifc, "IMG": export_img,
}
EXTENSIONS = {
    "PDF": ".pdf", "DWG": ".dwg", "DGN": ".dgn", "DWF": ".dwf", "NWC": ".nwc",
    "IFC": ".ifc", "IMG": ".png", "XML": ".xml",
}
