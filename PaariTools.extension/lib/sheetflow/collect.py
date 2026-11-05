# -*- coding: utf-8 -*-
"""Collect sheets, views, view/sheet sets and title block sizes from a document."""
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, FilteredElementCollector, ViewSheet,
    View, ViewType, ViewSheetSet, FamilyInstance,
)

FT_TO_MM = 304.8

# Landscape dimensions (mm) of common paper sizes. Name -> (long, short)
PAPER_SIZES = [
    ("A0", 1189.0, 841.0),
    ("A1", 841.0, 594.0),
    ("A2", 594.0, 420.0),
    ("A3", 420.0, 297.0),
    ("A4", 297.0, 210.0),
    ("ARCH E", 1219.2, 914.4),
    ("ARCH E1", 1066.8, 762.0),
    ("ARCH D", 914.4, 609.6),
    ("ARCH C", 609.6, 457.2),
    ("ANSI E", 1117.6, 863.6),
    ("ANSI D", 863.6, 558.8),
    ("ANSI C", 558.8, 431.8),
    ("ANSI B", 431.8, 279.4),
    ("ANSI A", 279.4, 215.9),
]
SIZE_TOLERANCE_MM = 6.0

# View types that can be exported as standalone views
EXPORTABLE_VIEW_TYPES = set([
    ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.Elevation, ViewType.Section,
    ViewType.ThreeD, ViewType.Detail, ViewType.DraftingView, ViewType.AreaPlan,
    ViewType.EngineeringPlan, ViewType.Legend, ViewType.Schedule, ViewType.Rendering,
    ViewType.Walkthrough,
])


def eid_int(element_id):
    """ElementId -> int across Revit versions (.Value in 2024+, .IntegerValue before)."""
    try:
        return int(element_id.Value)
    except AttributeError:
        return element_id.IntegerValue


def match_paper_size(width_mm, height_mm):
    """Return (size_name, orientation) for a sheet of the given size."""
    if not width_mm or not height_mm:
        return "", ""
    long_side = max(width_mm, height_mm)
    short_side = min(width_mm, height_mm)
    orientation = "Landscape" if width_mm >= height_mm else "Portrait"
    for name, l, s in PAPER_SIZES:
        if abs(long_side - l) <= SIZE_TOLERANCE_MM and abs(short_side - s) <= SIZE_TOLERANCE_MM:
            return name, orientation
    return "{0:.0f}x{1:.0f}".format(width_mm, height_mm), orientation


def _param_str(element, bip):
    try:
        p = element.get_Parameter(bip)
        if p and p.HasValue:
            return p.AsString() or p.AsValueString() or ""
    except Exception:
        pass
    return ""


def param_value(element, name):
    """Value of a parameter on an element by display name (or a few aliases)."""
    if element is None:
        return ""
    p = element.LookupParameter(name)
    if p is None or not p.HasValue:
        return ""
    try:
        val = p.AsString()
        if val is None:
            val = p.AsValueString()
        return val or ""
    except Exception:
        return ""


class ExportItem(object):
    """One row in the selection grid: a sheet or a view."""

    def __init__(self, doc, element, is_sheet, titleblock=None):
        self.doc = doc
        self.element = element
        self.id = element.Id
        self.is_sheet = is_sheet
        self.checked = False
        self.titleblock = titleblock
        self.status = ""
        self.formats = ""
        if is_sheet:
            self.number = element.SheetNumber
            self.name = element.Name
            self.revision = _param_str(element, BuiltInParameter.SHEET_CURRENT_REVISION)
            self.rev_date = _param_str(element, BuiltInParameter.SHEET_CURRENT_REVISION_DATE)
            self.rev_desc = _param_str(element, BuiltInParameter.SHEET_CURRENT_REVISION_DESCRIPTION)
            w = h = 0.0
            if titleblock is not None:
                try:
                    w = titleblock.get_Parameter(BuiltInParameter.SHEET_WIDTH).AsDouble() * FT_TO_MM
                    h = titleblock.get_Parameter(BuiltInParameter.SHEET_HEIGHT).AsDouble() * FT_TO_MM
                except Exception:
                    w = h = 0.0
            self.width_mm = w
            self.height_mm = h
            self.size, self.orientation = match_paper_size(w, h)
        else:
            self.number = str(element.ViewType)
            self.name = element.Name
            self.revision = ""
            self.rev_date = ""
            self.rev_desc = ""
            self.width_mm = self.height_mm = 0.0
            self.size, self.orientation = "", ""
        self.detected_size = self.size
        self.detected_orientation = self.orientation
        # Paper size / orientation picked on the Create tab ("Auto" = use title block)
        self.paper_size = "Auto"
        self.paper_orientation = "Auto"
        self.custom_name = ""

    @property
    def is_3d(self):
        return (not self.is_sheet) and self.element.ViewType == ViewType.ThreeD

    @property
    def kind(self):
        return "Sheet" if self.is_sheet else "View"

    def value_for(self, token):
        """Resolve a naming token like 'Sheet Number' or any parameter name."""
        t = token.strip()
        builtin = {
            "Sheet Number": self.number if self.is_sheet else "",
            "Sheet Name": self.name if self.is_sheet else "",
            "View Name": self.name,
            "View Type": "" if self.is_sheet else self.number,
            "Number": self.number,
            "Name": self.name,
            "Current Revision": self.revision,
            "Revision": self.revision,
            "Current Revision Date": self.rev_date,
            "Current Revision Description": self.rev_desc,
            "Size": self.size,
            "Orientation": self.orientation,
            "Project Number": self.doc.ProjectInformation.Number or "",
            "Project Name": self.doc.ProjectInformation.Name or "",
            "Model Name": self.doc.Title,
        }
        if t in builtin:
            return builtin[t]
        val = param_value(self.element, t)
        if not val and self.titleblock is not None:
            val = param_value(self.titleblock, t)
        if not val:
            val = param_value(self.doc.ProjectInformation, t)
        return val


def collect_titleblocks(doc):
    """Map sheet id (int) -> first title block instance on that sheet."""
    result = {}
    tbs = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks)\
        .WhereElementIsNotElementType().ToElements()
    for tb in tbs:
        if isinstance(tb, FamilyInstance):
            key = eid_int(tb.OwnerViewId)
            if key not in result:
                result[key] = tb
    return result


def collect_sheets(doc):
    tbs = collect_titleblocks(doc)
    sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
    items = []
    for s in sheets:
        if s.IsPlaceholder or s.IsTemplate:
            continue
        items.append(ExportItem(doc, s, True, tbs.get(eid_int(s.Id))))
    items.sort(key=lambda i: i.number)
    return items


def collect_views(doc):
    views = FilteredElementCollector(doc).OfClass(View).ToElements()
    items = []
    for v in views:
        if v.IsTemplate or isinstance(v, ViewSheet):
            continue
        if v.ViewType not in EXPORTABLE_VIEW_TYPES:
            continue
        try:
            if not v.CanBePrinted:
                continue
        except Exception:
            pass
        items.append(ExportItem(doc, v, False))
    items.sort(key=lambda i: (i.number, i.name))
    return items


def collect_view_sheet_sets(doc):
    """Return dict name -> set of element id ints."""
    result = {}
    for vss in FilteredElementCollector(doc).OfClass(ViewSheetSet).ToElements():
        ids = set()
        for v in vss.Views:
            ids.add(eid_int(v.Id))
        result[vss.Name] = ids
    return result


def sheet_parameter_names(doc, items):
    """Parameter names available for naming, from the first sheet/view and its title block."""
    names = set()
    for item in items[:1]:
        for p in item.element.Parameters:
            names.add(p.Definition.Name)
        if item.titleblock is not None:
            for p in item.titleblock.Parameters:
                names.add(p.Definition.Name)
    for p in doc.ProjectInformation.Parameters:
        names.add(p.Definition.Name)
    return sorted(names)
