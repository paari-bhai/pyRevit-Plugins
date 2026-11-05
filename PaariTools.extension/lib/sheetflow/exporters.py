# -*- coding: utf-8 -*-
"""Format exporters built on the native Revit export APIs.

Each exporter has the signature
    export_<fmt>(doc, folder, item, name, cfg) -> list of written file paths
and raises ExportSkipped / ExportFailed on failure. PDF and DWF can also combine
many items into one file (see export_pdf_combined / export_dwf_combined).

Revit 2022, 2023 and 2024 expose different option properties and enum members.
Nothing here imports or sets an option directly: every class and enum is looked
up by name and every property goes through OptionSetter, which skips (and
records) anything the running Revit version doesn't have instead of failing.
"""
import datetime
import io
import os
import re
import shutil
import time

from System.Collections.Generic import List
import Autodesk.Revit.DB as DB

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

# Options this Revit version doesn't support, collected during a run so the UI
# can tell the user once instead of failing every sheet.
UNSUPPORTED = set()


class ExportSkipped(Exception):
    """The item can't be exported to this format (e.g. NWC from a sheet)."""


class ExportFailed(Exception):
    """Revit didn't produce the file, with a reason a person can act on."""


# --------------------------------------------------------------------------- helpers
def db_type(name):
    """A Revit API class/enum by name, or None if this Revit version lacks it."""
    return getattr(DB, name, None)


def enum_member(enum_type, name, default=None):
    if isinstance(enum_type, str):
        enum_type = db_type(enum_type)
    if enum_type is None or name is None:
        return default
    try:
        return getattr(enum_type, name)
    except Exception:
        return default


def has_property(obj, prop):
    """True if a .NET options object has this property in the running Revit."""
    try:
        return obj.GetType().GetProperty(prop) is not None
    except Exception:
        # e.g. AmbiguousMatchException when a subclass redeclares the property
        return hasattr(obj, prop)


def option_available(class_name, prop):
    """True if e.g. PDFExportOptions.HiddenLineViews exists in this Revit."""
    cls = db_type(class_name)
    if cls is None:
        return False
    try:
        import clr
        return clr.GetClrType(cls).GetProperty(prop) is not None
    except Exception:
        return hasattr(cls, prop)


class OptionSetter(object):
    """Sets option properties, skipping ones this Revit version doesn't have."""

    def __init__(self, options, label):
        self.o = options
        self.label = label

    def set(self, prop, value):
        if value is None:
            return False
        if not has_property(self.o, prop):
            UNSUPPORTED.add("{0}.{1}".format(self.label, prop))
            return False
        try:
            setattr(self.o, prop, value)
            return True
        except Exception as ex:
            UNSUPPORTED.add("{0}.{1} ({2})".format(self.label, prop, ex))
            return False

    def enum(self, prop, enum_name, member, default_member=None):
        val = enum_member(enum_name, member)
        if val is None and default_member is not None:
            val = enum_member(enum_name, default_member)
        if val is None:
            UNSUPPORTED.add("{0}.{1}={2}".format(self.label, prop, member))
            return False
        return self.set(prop, val)


def _ids(items):
    lst = List[DB.ElementId]()
    for i in items:
        lst.Add(i.id)
    return lst


def _view_set(items):
    vs = DB.ViewSet()
    for i in items:
        vs.Insert(i.element)
    return vs


def check_writable(path):
    """Fail early with a clear message if the target file is open elsewhere."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "a"):
            pass
    except (IOError, OSError):
        raise ExportFailed("{0} is open in another program; close it and run again".format(
            os.path.basename(path)))
    except Exception:
        pass


def verify_written(path, started):
    """Make sure Revit actually wrote (or rewrote) the file."""
    if not os.path.exists(path):
        raise ExportFailed("Revit finished but no file was written")
    try:
        if os.path.getmtime(path) < started - 2:
            raise ExportFailed("Revit finished but the file was not updated")
        if os.path.getsize(path) == 0:
            raise ExportFailed("Revit wrote an empty file")
    except ExportFailed:
        raise
    except Exception:
        pass


def run_export(action, path, retries=1):
    """Call a Revit export, retry once on a transient failure, verify the result."""
    check_writable(path)
    last = None
    for attempt in range(retries + 1):
        started = time.time()
        try:
            result = action()
            if result is False:
                raise ExportFailed("Revit reported the export failed")
            verify_written(path, started)
            return
        except ExportSkipped:
            raise
        except Exception as ex:
            last = ex
            if attempt < retries:
                time.sleep(1.0)
    if isinstance(last, ExportFailed):
        raise last
    raise ExportFailed(str(last))


def _paper_format(item):
    name = PAPER_FORMAT_NAMES.get(item.paper_size)
    default = enum_member("ExportPaperFormat", "Default")
    if name is None:
        return default
    return enum_member("ExportPaperFormat", name, default)


def _orientation(item):
    return enum_member("PageOrientationType", item.paper_orientation or "Auto",
                       enum_member("PageOrientationType", "Auto"))


# --------------------------------------------------------------------------- PDF
def pdf_supported():
    return db_type("PDFExportOptions") is not None


# UI control -> PDFExportOptions property it needs (used to grey out unsupported options)
PDF_OPTION_PROPS = {
    "hidden_line": "HiddenLineViews",
    "export_quality": "ExportQuality",
    "always_raster": "AlwaysUseRaster",
    "raster_quality": "RasterQuality",
    "colors": "ColorDepth",
    "view_links_blue": "ViewLinksInBlue",
    "hide_ref_planes": "HideReferencePlane",
    "hide_unref_tags": "HideUnreferencedViewTags",
    "hide_scope_boxes": "HideScopeBoxes",
    "hide_crop": "HideCropBoundaries",
    "halftone_thin": "ReplaceHalftoneWithThinLines",
    "mask_coincident": "MaskCoincidentLines",
    "margin": "MarginType",
    "zoom": "ZoomType",
}


def _pdf_options(p):
    o = DB.PDFExportOptions()
    s = OptionSetter(o, "PDF")
    if p["placement"] == "Center":
        s.enum("PaperPlacement", "PaperPlacementType", "Center")
    else:
        s.enum("PaperPlacement", "PaperPlacementType", "LowerLeft")
        s.enum("MarginType", "MarginType", p["margin"], "NoMargin")
        if p["margin"] == "UserDefined":
            s.set("OriginOffsetX", float(p["offset_x_mm"]) / FT_TO_MM)
            s.set("OriginOffsetY", float(p["offset_y_mm"]) / FT_TO_MM)
    if p["zoom"] == "Zoom":
        s.enum("ZoomType", "ZoomType", "Zoom")
        s.set("ZoomPercentage", max(1, min(1000, int(p["zoom_percent"]))))
    else:
        s.enum("ZoomType", "ZoomType", "FitToPage")
    if has_property(o, "HiddenLineViews"):
        s.enum("HiddenLineViews", "HiddenLineViewsType",
               "RasterProcessing" if p["hidden_line"] == "Raster" else "VectorProcessing")
    s.enum("RasterQuality", "RasterQualityType", p["raster_quality"], "High")
    s.enum("ColorDepth", "ColorDepthType", p["colors"], "Color")
    if has_property(o, "ExportQuality"):
        s.enum("ExportQuality", "PDFExportQualityType", p["export_quality"], "DPI300")
    s.set("AlwaysUseRaster", bool(p["always_raster"]))
    s.set("ViewLinksInBlue", bool(p["view_links_blue"]))
    s.set("HideReferencePlane", bool(p["hide_ref_planes"]))
    s.set("HideUnreferencedViewTags", bool(p["hide_unref_tags"]))
    s.set("HideScopeBoxes", bool(p["hide_scope_boxes"]))
    s.set("HideCropBoundaries", bool(p["hide_crop"]))
    s.set("ReplaceHalftoneWithThinLines", bool(p["halftone_thin"]))
    s.set("MaskCoincidentLines", bool(p["mask_coincident"]))
    s.set("StopOnError", False)
    return o, s


def export_pdf(doc, folder, item, name, cfg):
    if not pdf_supported():
        raise ExportSkipped("Native PDF export needs Revit 2022 or newer")
    o, s = _pdf_options(cfg)
    s.set("Combine", True)        # one view per call, so this just lets us set FileName
    s.set("FileName", name)
    s.set("PaperFormat", _paper_format(item))
    if item.paper_orientation != "Auto":
        s.set("PaperOrientation", _orientation(item))
    path = os.path.join(folder, name + ".pdf")
    run_export(lambda: doc.Export(folder, _ids([item]), o), path)
    return [path]


def export_pdf_combined(doc, folder, items, name, cfg):
    if not pdf_supported():
        raise ExportSkipped("Native PDF export needs Revit 2022 or newer")
    o, s = _pdf_options(cfg)
    s.set("Combine", True)
    s.set("FileName", name)
    # Mixed sizes in one PDF: let Revit size every page from its title block.
    sizes = set(i.paper_size for i in items)
    s.set("PaperFormat", _paper_format(items[0]) if len(sizes) == 1
          else enum_member("ExportPaperFormat", "Default"))
    orients = set(i.paper_orientation for i in items)
    if len(orients) == 1 and items[0].paper_orientation != "Auto":
        s.set("PaperOrientation", _orientation(items[0]))
    path = os.path.join(folder, name + ".pdf")
    run_export(lambda: doc.Export(folder, _ids(items), o), path)
    return [path]


# --------------------------------------------------------------------------- PDF batch
# Exporting many sheets in one Revit call (Combine = False) avoids Revit setting
# up the PDF engine again for every sheet. Revit names those files itself, so we
# name them by sheet number (unique per model), then rename each to the file
# name the user asked for. Anything we can't match falls back to one-by-one.
PDF_BATCH_SIZE = 20


def pdf_batch_supported():
    return (pdf_supported() and db_type("TableCellCombinedParameterData") is not None
            and option_available("PDFExportOptions", "Combine"))


def _match_key(text):
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def export_pdf_batch(doc, folder, items, names, cfg):
    """Export sheets in one call. items share paper size/orientation.

    Returns {index in items: final path} for the sheets that came out; the
    caller exports any missing index one by one.
    """
    tmp = os.path.join(folder, "_sheetflow_tmp_{0}".format(int(time.time() * 1000)))
    os.makedirs(tmp)
    done = {}
    try:
        o, s = _pdf_options(cfg)
        s.set("Combine", False)
        s.set("PaperFormat", _paper_format(items[0]))
        if items[0].paper_orientation != "Auto":
            s.set("PaperOrientation", _orientation(items[0]))
        rule = DB.TableCellCombinedParameterData.Create()
        rule.CategoryId = DB.ElementId(DB.BuiltInCategory.OST_Sheets)
        rule.ParamId = DB.ElementId(DB.BuiltInParameter.SHEET_NUMBER)
        rule.Prefix = ""
        rule.Suffix = ""
        rule.Separator = ""
        rules = List[DB.TableCellCombinedParameterData]()
        rules.Add(rule)
        o.SetNamingRule(rules)

        if doc.Export(tmp, _ids(items), o) is False:
            raise ExportFailed("Revit reported the PDF export failed")

        produced = {}
        clashes = set()
        for f in os.listdir(tmp):
            if not f.lower().endswith(".pdf"):
                continue
            key = _match_key(os.path.splitext(f)[0])
            if key in produced:
                clashes.add(key)
            produced[key] = os.path.join(tmp, f)

        wanted = {}
        for idx, item in enumerate(items):
            key = _match_key(item.number)
            wanted.setdefault(key, []).append(idx)
        for key, idxs in wanted.items():
            if len(idxs) != 1 or key in clashes or key not in produced:
                continue  # ambiguous or missing: caller exports these one by one
            idx = idxs[0]
            src = produced[key]
            if os.path.getsize(src) == 0:
                continue
            target = os.path.join(folder, names[idx] + ".pdf")
            try:
                check_writable(target)
                if os.path.exists(target):
                    os.remove(target)
                shutil.move(src, target)
                done[idx] = target
            except Exception:
                continue
        if produced and not done:
            sample = ", ".join(sorted(os.path.basename(p) for p in produced.values())[:3])
            UNSUPPORTED.add("PDF batch: Revit's file names didn't match sheet numbers "
                            "(got e.g. {0}), so sheets were redone one by one".format(sample))
        elif not produced:
            UNSUPPORTED.add("PDF batch: Revit wrote no files in batch mode, so sheets were "
                            "exported one by one")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return done


# --------------------------------------------------------------------------- DWG / DGN
DWG_COLOR_MODES = ["TrueColorPerView", "TrueColor", "IndexColors", "FromSetup"]


def predefined_setups(doc):
    try:
        return [IN_SESSION] + sorted(DB.BaseExportOptions.GetPredefinedSetupNames(doc))
    except Exception:
        return [IN_SESSION]


def _apply_cad_common(s, cfg):
    s.set("MergedViews", bool(cfg["merged_views"]))
    s.set("SharedCoords", bool(cfg["shared_coords"]))
    s.set("HideReferencePlane", bool(cfg["hide_ref_planes"]))
    s.set("HideScopeBox", bool(cfg["hide_scope_boxes"]))
    s.set("HideUnreferenceViewTags", bool(cfg["hide_unref_tags"]))


def _cad_options(doc, cls_name, cfg, label):
    cls = db_type(cls_name)
    o = None
    if cfg.get("setup") and cfg["setup"] != IN_SESSION:
        try:
            o = cls.GetPredefinedOptions(doc, cfg["setup"])
        except Exception:
            o = None
        if o is None:
            UNSUPPORTED.add("{0} setup '{1}' not found, used in-session settings".format(
                label, cfg["setup"]))
    from_setup = o is not None
    if o is None:
        o = cls()
    s = OptionSetter(o, label)
    if from_setup:
        # A saved setup owns layers/linetypes/units; only the xref choice comes from here.
        s.set("MergedViews", bool(cfg["merged_views"]))
    else:
        _apply_cad_common(s, cfg)
    return o, s


def export_dwg(doc, folder, item, name, cfg):
    o, s = _cad_options(doc, "DWGExportOptions", cfg, "DWG")
    colors = cfg.get("colors", "TrueColorPerView")
    if colors and colors != "FromSetup":
        s.enum("Colors", "ExportColorMode", colors, "TrueColor")
    if cfg.get("version") and cfg["version"] != "Default":
        s.enum("FileVersion", "ACADVersion", cfg["version"])
    path = os.path.join(folder, name + ".dwg")
    run_export(lambda: doc.Export(folder, name, _ids([item]), o), path)
    return [path]


def export_dgn(doc, folder, item, name, cfg):
    o, s = _cad_options(doc, "DGNExportOptions", cfg, "DGN")
    path = os.path.join(folder, name + ".dgn")
    run_export(lambda: doc.Export(folder, name, _ids([item]), o), path)
    return [path]


# --------------------------------------------------------------------------- DWF / DWFx
def _dwf_options(cfg, item=None):
    o = DB.DWFXExportOptions() if cfg["dwfx"] else DB.DWFExportOptions()
    s = OptionSetter(o, "DWF")
    s.set("ExportObjectData", bool(cfg["export_object_data"]))
    s.enum("ImageQuality", "DWFImageQuality", cfg["image_quality"])
    s.enum("ImageFormat", "DWFImageFormat", cfg["image_format"])
    s.set("CropBoxVisible", not bool(cfg["hide_crop"]))
    if item is not None and item.paper_size != "Auto":
        s.set("PaperFormat", _paper_format(item))
    if item is not None and item.paper_orientation == "Portrait":
        s.set("PortraitLayout", True)
    return o, s


def _dwf_ext(cfg):
    return ".dwfx" if cfg["dwfx"] else ".dwf"


def export_dwf(doc, folder, item, name, cfg):
    o, s = _dwf_options(cfg, item)
    s.set("MergedViews", False)
    path = os.path.join(folder, name + _dwf_ext(cfg))
    run_export(lambda: doc.Export(folder, name, _view_set([item]), o), path)
    return [path]


def export_dwf_combined(doc, folder, items, name, cfg):
    o, s = _dwf_options(cfg)
    s.set("MergedViews", True)
    path = os.path.join(folder, name + _dwf_ext(cfg))
    run_export(lambda: doc.Export(folder, name, _view_set(items), o), path)
    return [path]


# --------------------------------------------------------------------------- NWC
def nwc_supported():
    try:
        return DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable()
    except Exception:
        return False


def export_nwc(doc, folder, item, name, cfg):
    if not item.is_3d:
        raise ExportSkipped("NWC only exports 3D views")
    if not nwc_supported():
        raise ExportSkipped("Navisworks NWC exporter for Revit is not installed")
    o = DB.NavisworksExportOptions()
    s = OptionSetter(o, "NWC")
    s.enum("ExportScope", "NavisworksExportScope", "View")
    s.set("ViewId", item.id)
    s.enum("Coordinates", "NavisworksCoordinates", cfg["coordinates"], "Shared")
    s.set("ExportLinks", bool(cfg["export_links"]))
    s.set("ExportParts", bool(cfg["export_parts"]))
    s.set("ConvertElementProperties", bool(cfg["convert_properties"]))
    s.set("ExportRoomGeometry", bool(cfg["export_room_geometry"]))
    s.set("DivideFileIntoLevels", bool(cfg["divide_by_levels"]))
    s.set("ExportUrls", bool(cfg["export_urls"]))
    s.set("ExportElementIds", bool(cfg["export_element_ids"]))
    s.set("FindMissingMaterials", bool(cfg["find_missing_materials"]))
    s.enum("Parameters", "NavisworksParameters", cfg["parameters"], "All")
    path = os.path.join(folder, name + ".nwc")
    run_export(lambda: doc.Export(folder, name, o), path)
    return [path]


# --------------------------------------------------------------------------- IFC
def export_ifc(doc, folder, item, name, cfg):
    if item.is_sheet:
        raise ExportSkipped("IFC exports views, not sheets")
    if doc.IsModifiable or doc.IsReadOnly:
        raise ExportSkipped("IFC needs an editable model with no other command running")
    o = DB.IFCExportOptions()
    s = OptionSetter(o, "IFC")
    s.enum("FileVersion", "IFCVersion", cfg["version"], "IFC2x3CV2")
    s.set("ExportBaseQuantities", bool(cfg["base_quantities"]))
    s.set("WallAndColumnSplitting", bool(cfg["split_walls"]))
    s.set("SpaceBoundaryLevel", int(cfg["space_boundaries"]))
    if cfg["current_view_only"]:
        s.set("FilterViewId", item.id)
        o.AddOption("VisibleElementsOfCurrentView", "true")
    o.AddOption("ExportLinkedFiles", "true" if cfg["export_links"] else "false")
    path = os.path.join(folder, name + ".ifc")

    def action():
        t = DB.Transaction(doc, "SheetFlow IFC export")
        t.Start()
        try:
            return doc.Export(folder, name + ".ifc", o)
        finally:
            # The IFC exporter needs an open transaction; nothing should stay in the model.
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
    run_export(action, path, retries=0)
    return [path]


# --------------------------------------------------------------------------- Images
def export_img(doc, folder, item, name, cfg):
    o = DB.ImageExportOptions()
    s = OptionSetter(o, "IMG")
    ftype = enum_member("ImageFileType", cfg["file_type"], DB.ImageFileType.PNG)
    ext = IMAGE_EXT.get(cfg["file_type"], ".png")
    s.enum("ExportRange", "ExportRange", "SetOfViews")
    o.SetViewsAndSheets(_ids([item]))
    o.FilePath = os.path.join(folder, name)
    s.set("HLRandWFViewsFileType", ftype)
    s.set("ShadowViewsFileType", ftype)
    s.enum("ImageResolution", "ImageResolution", cfg["resolution"], "DPI_300")
    s.enum("ZoomType", "ZoomFitType", "FitToPage")
    s.set("PixelSize", max(64, min(15000, int(cfg["pixel_size"]))))
    s.enum("FitDirection", "FitDirectionType", cfg["fit_direction"], "Horizontal")
    target = os.path.join(folder, name + ext)
    check_writable(target)
    started = time.time() - 2
    before = set(os.listdir(folder))
    doc.ExportImage(o)
    # Revit appends " - Sheet - <number> - <name>" to the path; rename to what we asked for.
    produced = []
    for f in os.listdir(folder):
        full = os.path.join(folder, f)
        if not f.startswith(name) or os.path.splitext(f)[1].lower() != ext:
            continue
        if f not in before or os.path.getmtime(full) >= started:
            produced.append(full)
    others = [p for p in produced if os.path.normcase(p) != os.path.normcase(target)]
    produced = others or produced
    if not produced:
        raise ExportFailed("Revit did not write an image file")
    src = produced[0]
    if os.path.normcase(src) != os.path.normcase(target):
        if os.path.exists(target):
            os.remove(target)
        os.rename(src, target)
    return [target]


# --------------------------------------------------------------------------- XML
def _xml_escape(s):
    s = u"{0}".format(s if s is not None else "")
    s = u"".join(c for c in s if c in u"\t\n\r" or ord(c) >= 32)
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
    check_writable(path)
    lines = [u'<?xml version="1.0" encoding="utf-8"?>',
             u'<Export model="{0}" date="{1}">'.format(
                 _xml_escape(doc.Title), datetime.datetime.now().isoformat())]
    for i in items:
        lines.append(u'  <{0} number="{1}" name="{2}" revision="{3}" size="{4}" '
                     u'orientation="{5}">'.format(
                         i.kind, _xml_escape(i.number), _xml_escape(i.name),
                         _xml_escape(i.revision), _xml_escape(i.size),
                         _xml_escape(i.orientation)))
        if cfg.get("include_all_params", True):
            for pname, pval in _element_params(i.element):
                lines.append(u'    <Parameter name="{0}">{1}</Parameter>'.format(
                    _xml_escape(pname), _xml_escape(pval)))
        lines.append(u'  </{0}>'.format(i.kind))
    lines.append(u'</Export>')
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"\n".join(lines))
    return [path]


PER_ITEM = {
    "PDF": export_pdf, "DWG": export_dwg, "DGN": export_dgn, "DWF": export_dwf,
    "NWC": export_nwc, "IFC": export_ifc, "IMG": export_img,
}
EXTENSIONS = {
    "PDF": ".pdf", "DWG": ".dwg", "DGN": ".dgn", "DWF": ".dwfx", "NWC": ".nwc",
    "IFC": ".ifc", "IMG": ".png", "XML": ".xml",
}
