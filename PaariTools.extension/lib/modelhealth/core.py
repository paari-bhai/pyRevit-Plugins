# -*- coding: utf-8 -*-
"""Model health checks and guarded clean-up actions (Revit 2022-2024).

Every delete goes through safe_delete(): each candidate is deleted inside its
own SubTransaction and rolled back if Revit would also delete anything we
didn't expect (e.g. a type that is still used somewhere indirectly).
"""
import os

from System.Collections.Generic import List, HashSet
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (
    BuiltInCategory, ElementId, FilteredElementCollector, Transaction, SubTransaction,
    View, ViewSheet, ViewType,
)


def eid_int(element_id):
    try:
        return int(element_id.Value)
    except AttributeError:
        return element_id.IntegerValue


def _collect(doc, cls):
    return FilteredElementCollector(doc).OfClass(cls).ToElements()


def _safe_name(el):
    try:
        return el.Name
    except Exception:
        return "<{0}>".format(eid_int(el.Id))


class Candidate(object):
    """Something the user can choose to clean up."""

    def __init__(self, label, ids, allowed=None, protected=None):
        self.label = label
        self.ids = list(ids)
        # ints Revit may delete along with ids (e.g. a family's own types)
        self.allowed = set(eid_int(i) for i in self.ids) | set(allowed or [])
        # if set: only refuse when one of these ints would be deleted (used for views,
        # which legitimately take their own annotations and markers with them)
        self.protected = protected

    def __str__(self):
        return self.label


class Finding(object):
    def __init__(self, key, check, value, level, advice, fix=None, fix_label=""):
        self.key = key
        self.check = check
        self.value = value
        self.level = level          # OK | Watch | Fix | Info
        self.advice = advice
        self.fix = fix              # name of a fix function in FIXES, or None
        self.fix_label = fix_label


def _level(count, watch, fix):
    if count >= fix:
        return "Fix"
    if count >= watch:
        return "Watch"
    return "OK"


# --------------------------------------------------------------------------- data
def link_info(doc):
    """[(RevitLinkType, instance_count, loaded)]"""
    counts = {}
    for li in _collect(doc, DB.RevitLinkInstance):
        k = eid_int(li.GetTypeId())
        counts[k] = counts.get(k, 0) + 1
    out = []
    for lt in _collect(doc, DB.RevitLinkType):
        try:
            loaded = DB.RevitLinkType.IsLoaded(doc, lt.Id)
        except Exception:
            loaded = True
        # nested links show up as types too; only report top-level ones
        try:
            if lt.IsNestedLink:
                continue
        except Exception:
            pass
        out.append((lt, counts.get(eid_int(lt.Id), 0), loaded))
    return out


def cad_info(doc):
    imports, links = [], []
    for ii in _collect(doc, DB.ImportInstance):
        (links if ii.IsLinked else imports).append(ii)
    return imports, links


def image_types_unused(doc):
    if not hasattr(DB, "ImageType"):
        return []
    used = set()
    if hasattr(DB, "ImageInstance"):
        for inst in _collect(doc, DB.ImageInstance):
            used.add(eid_int(inst.GetTypeId()))
    out = []
    for it in _collect(doc, DB.ImageType):
        if eid_int(it.Id) not in used:
            out.append(it)
    return out


def placed_view_ids(doc):
    placed = set()
    for sheet in _collect(doc, ViewSheet):
        placed.add(eid_int(sheet.Id))
        for vid in sheet.GetAllPlacedViews():
            placed.add(eid_int(vid))
    for ssi in _collect(doc, DB.ScheduleSheetInstance):
        placed.add(eid_int(ssi.ScheduleId))
    return placed


SKIP_VIEW_TYPES = set(
    getattr(ViewType, n) for n in (
        "ProjectBrowser", "SystemBrowser", "Internal", "Undefined", "DrawingSheet", "Schedule",
        "ColumnSchedule", "PanelSchedule", "Report", "CostReport", "LoadsReport",
        "PresureLossReport")
    if hasattr(ViewType, n))


def unplaced_views(doc, active_view_id=None):
    placed = placed_view_ids(doc)
    out = []
    for v in _collect(doc, View):
        if v.IsTemplate or isinstance(v, ViewSheet) or v.ViewType in SKIP_VIEW_TYPES:
            continue
        if active_view_id is not None and v.Id == active_view_id:
            continue
        if eid_int(v.Id) in placed:
            continue
        out.append(v)
    out.sort(key=lambda v: (str(v.ViewType), v.Name))
    return out


def unused_view_templates(doc):
    used = set()
    templates = []
    for v in _collect(doc, View):
        if v.IsTemplate:
            templates.append(v)
            continue
        try:
            tid = v.ViewTemplateId
            if tid and tid != ElementId.InvalidElementId:
                used.add(eid_int(tid))
        except Exception:
            pass
    return [t for t in templates if eid_int(t.Id) not in used]


def unused_group_types(doc):
    used = set(eid_int(g.GetTypeId()) for g in _collect(doc, DB.Group))
    return [gt for gt in _collect(doc, DB.GroupType) if eid_int(gt.Id) not in used]


# Families in these categories are used without placed instances (profiles in
# sweeps/mullions/railings, detail items in repeating details), so never offer them.
_NEVER_PURGE_CATS = set()
for _name in ("OST_ProfileFamilies", "OST_DetailComponents", "OST_RailingSupport",
              "OST_RailingTermination", "OST_StairsLandings", "OST_StairsRuns"):
    _cat = getattr(BuiltInCategory, _name, None)
    if _cat is not None:
        _NEVER_PURGE_CATS.add(int(_cat))


def unused_families(doc):
    used = set()
    for fi in FilteredElementCollector(doc).OfClass(DB.FamilyInstance)\
            .WhereElementIsNotElementType():
        used.add(eid_int(fi.GetTypeId()))
    out = []
    for fam in _collect(doc, DB.Family):
        try:
            if fam.IsInPlace or not fam.IsEditable:
                continue
            cat = fam.FamilyCategory
            if cat is not None and eid_int(cat.Id) in _NEVER_PURGE_CATS:
                continue
            sym_ids = list(fam.GetFamilySymbolIds())
        except Exception:
            continue
        if sym_ids and not any(eid_int(s) in used for s in sym_ids):
            out.append(fam)
    out.sort(key=lambda f: ((f.FamilyCategory.Name if f.FamilyCategory else ""), f.Name))
    return out


def native_purge_supported(doc):
    return hasattr(doc, "GetUnusedElements")   # Revit 2024+


def native_unused_ids(doc):
    return list(doc.GetUnusedElements(HashSet[ElementId]()))


# --------------------------------------------------------------------------- scan
def scan(doc, uidoc):
    f = []
    path = doc.PathName
    if path and os.path.exists(path):
        mb = os.path.getsize(path) / (1024.0 * 1024.0)
        f.append(Finding("size", "File size", "{0:.0f} MB".format(mb),
                         "Fix" if mb > 500 else ("Watch" if mb > 250 else "OK"),
                         "Large files open, sync and export slowly. Purge and compact help most."))

    warnings = doc.GetWarnings()
    n = warnings.Count
    f.append(Finding("warnings", "Warnings", str(n), _level(n, 300, 1000),
                     "Each warning is re-checked when elements change. Export the list and "
                     "fix the biggest groups (overlaps, duplicates, room separation).",
                     "export_warnings", "Export warnings list..."))

    links = link_info(doc)
    loaded = [l for l in links if l[2]]
    unused = [l for l in links if l[1] == 0]
    f.append(Finding("links", "Revit links (loaded / total)",
                     "{0} / {1}".format(len(loaded), len(links)), _level(len(loaded), 4, 10),
                     "Every loaded link is held in memory and redrawn. Unload links you aren't "
                     "using right now (Manage Links), and remove ones with no instances.",
                     "remove_unused_links" if unused else None,
                     "Remove {0} unused link(s)...".format(len(unused))))

    imports, cad_links = cad_info(doc)
    f.append(Finding("cad_imports", "Imported CAD (not linked)", str(len(imports)),
                     _level(len(imports), 1, 5),
                     "Imported DWGs bloat the model and add line patterns and layers. "
                     "Link instead of import, or delete ones you don't need.",
                     "delete_cad_imports" if imports else None, "Delete CAD imports..."))
    f.append(Finding("cad_links", "Linked CAD", str(len(cad_links)), _level(len(cad_links), 10, 30),
                     "Fine in moderation. Remove ones no longer needed."))

    imgs = image_types_unused(doc)
    f.append(Finding("images", "Unused raster images", str(len(imgs)), _level(len(imgs), 1, 5),
                     "Images stay in the file even when not placed.",
                     "delete_unused_images" if imgs else None, "Delete unused images..."))

    inplace = [fam for fam in _collect(doc, DB.Family) if fam.IsInPlace]
    f.append(Finding("inplace", "In-place families", str(len(inplace)), _level(len(inplace), 20, 60),
                     "In-place families regenerate slowly and can't be reused. Rebuild "
                     "repeated ones as loadable families."))

    active = uidoc.ActiveView.Id if uidoc and uidoc.ActiveView else None
    views = unplaced_views(doc, active)
    f.append(Finding("views", "Views not on sheets", str(len(views)), _level(len(views), 150, 400),
                     "Unused views add to file size and project browser clutter.",
                     "delete_unplaced_views" if views else None, "Delete unplaced views..."))

    templates = unused_view_templates(doc)
    f.append(Finding("templates", "Unused view templates", str(len(templates)),
                     _level(len(templates), 20, 60), "Safe to remove if you don't need them.",
                     "delete_unused_templates" if templates else None, "Delete unused templates..."))

    groups = unused_group_types(doc)
    n_groups = len(_collect(doc, DB.Group))
    f.append(Finding("groups", "Groups placed / unused types",
                     "{0} / {1}".format(n_groups, len(groups)), _level(n_groups, 300, 1000),
                     "Many group instances slow down edits. Unused group types can go.",
                     "delete_unused_groups" if groups else None, "Delete unused group types..."))

    if native_purge_supported(doc):
        try:
            n_purge = len(native_unused_ids(doc))
        except Exception:
            n_purge = 0
        f.append(Finding("purge", "Purgeable elements", str(n_purge), _level(n_purge, 200, 1000),
                         "Same as Purge Unused. Run it a couple of times.",
                         "purge_unused" if n_purge else None, "Purge unused..."))
    else:
        fams = unused_families(doc)
        f.append(Finding("purge", "Unused loadable families", str(len(fams)),
                         _level(len(fams), 50, 200),
                         "Families with no placed instances. Profile and detail-item families are "
                         "left alone because they're used inside other types.",
                         "purge_families" if fams else None, "Purge unused families..."))

    patterns = [lp for lp in _collect(doc, DB.LinePatternElement)
                if _safe_name(lp).upper().startswith("IMPORT")]
    f.append(Finding("patterns", "Line patterns from CAD imports", str(len(patterns)),
                     _level(len(patterns), 20, 100),
                     "Leftovers from imported DWGs. Clean them up with a purge or by hand."))

    try:
        n_open = len(list(uidoc.GetOpenUIViews()))
    except Exception:
        n_open = 0
    f.append(Finding("open_views", "Open views", str(n_open), _level(n_open, 6, 15),
                     "Every open view uses memory and is redrawn. Close the ones you don't need.",
                     "close_views" if n_open > 1 else None, "Close all but the active view"))

    f.append(Finding("compact", "Save and compact", "",
                     "Info", "Compacting rewrites the file without the empty space left by "
                     "deletions. Do it after a purge.",
                     "save_compact", "Save and compact..."))
    return f


# --------------------------------------------------------------------------- candidates
def candidates_for(doc, uidoc, fix):
    """List of Candidate for a destructive fix, for the user to review."""
    if fix == "remove_unused_links":
        return [Candidate("Link: {0}".format(_safe_name(lt)), [lt.Id])
                for lt, count, _ in link_info(doc) if count == 0]
    if fix == "delete_cad_imports":
        out = []
        type_counts = {}
        imports, _ = cad_info(doc)
        for ii in imports:
            k = eid_int(ii.GetTypeId())
            type_counts[k] = type_counts.get(k, 0) + 1
        for ii in imports:
            t = doc.GetElement(ii.GetTypeId())
            where = "view-specific" if ii.ViewSpecific else "3D/model"
            allowed = [eid_int(ii.GetTypeId())] if type_counts[eid_int(ii.GetTypeId())] == 1 else []
            out.append(Candidate(u"{0} ({1}, id {2})".format(
                _safe_name(t) if t else "CAD", where, eid_int(ii.Id)), [ii.Id], allowed))
        return out
    if fix == "delete_unused_images":
        return [Candidate("Image: {0}".format(_safe_name(it)), [it.Id])
                for it in image_types_unused(doc)]
    if fix == "delete_unplaced_views":
        active = uidoc.ActiveView.Id if uidoc and uidoc.ActiveView else None
        views = unplaced_views(doc, active)
        chosen = set(eid_int(v.Id) for v in views)
        protected = set(eid_int(v.Id) for v in _collect(doc, View)) - chosen
        return [Candidate(u"{0}: {1}".format(v.ViewType, v.Name), [v.Id], protected=protected)
                for v in views]
    if fix == "delete_unused_templates":
        return [Candidate(u"Template: {0}".format(t.Name), [t.Id])
                for t in unused_view_templates(doc)]
    if fix == "delete_unused_groups":
        return [Candidate(u"Group type: {0}".format(_safe_name(g)), [g.Id])
                for g in unused_group_types(doc)]
    if fix == "purge_families":
        out = []
        for fam in unused_families(doc):
            cat = fam.FamilyCategory.Name if fam.FamilyCategory else "?"
            syms = [eid_int(s) for s in fam.GetFamilySymbolIds()]
            out.append(Candidate(u"{0}: {1}".format(cat, fam.Name), [fam.Id], syms))
        return out
    if fix == "purge_unused":
        ids = native_unused_ids(doc)
        allowed = set(eid_int(i) for i in ids)
        out = []
        for i in ids:
            el = doc.GetElement(i)
            if el is None:
                continue
            cat = el.Category.Name if el.Category else el.GetType().Name
            out.append(Candidate(u"{0}: {1}".format(cat, _safe_name(el)), [i], allowed))
        out.sort(key=lambda c: c.label)
        return out
    return []


def safe_delete(doc, candidates, title):
    """Delete candidates one by one; roll back any that would take more with them.

    Returns (deleted_labels, [(label, reason)]).
    """
    deleted, skipped = [], []
    t = Transaction(doc, title)
    t.Start()
    try:
        for c in candidates:
            ids = [i for i in c.ids if doc.GetElement(i) is not None]
            if not ids:
                continue  # already removed with an earlier candidate
            st = SubTransaction(doc)
            st.Start()
            try:
                lst = List[ElementId]()
                for i in ids:
                    lst.Add(i)
                result = [eid_int(i) for i in doc.Delete(lst)]
                if c.protected is not None:
                    extra = [i for i in result if i in c.protected]
                else:
                    extra = [i for i in result if i not in c.allowed]
                if extra:
                    st.RollBack()
                    skipped.append((c.label, "still used: would also delete {0} other "
                                             "element(s)".format(len(extra))))
                else:
                    st.Commit()
                    deleted.append(c.label)
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                skipped.append((c.label, str(ex)))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return deleted, skipped


def close_other_views(uidoc):
    active = uidoc.ActiveView.Id
    closed = 0
    for uiv in list(uidoc.GetOpenUIViews()):
        if uiv.ViewId != active:
            try:
                uiv.Close()
                closed += 1
            except Exception:
                pass
    return closed


def save_compact(doc):
    """Compact a non-workshared file. Returns a message."""
    if doc.IsWorkshared:
        return ("This is a workshared model. Use Synchronize with Central and tick "
                "'Compact Central Model' in the sync dialog.")
    if not doc.PathName:
        return "Save the model once first, then run this again."
    opts = DB.SaveOptions()
    opts.Compact = True
    before = os.path.getsize(doc.PathName)
    doc.Save(opts)
    after = os.path.getsize(doc.PathName)
    return "Saved and compacted: {0:.0f} MB -> {1:.0f} MB.".format(
        before / 1048576.0, after / 1048576.0)


def export_warnings_csv(doc, path):
    import io
    rows = []
    for w in doc.GetWarnings():
        ids = ";".join(str(eid_int(i)) for i in w.GetFailingElements())
        rows.append((w.GetDescriptionText(), ids))
    counts = {}
    for d, _ in rows:
        counts[d] = counts.get(d, 0) + 1

    def cell(v):
        v = u"{0}".format(v)
        return u'"' + v.replace(u'"', u'""') + u'"'
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(u"Warning,Count of this warning,Element IDs\r\n")
        for d, ids in sorted(rows, key=lambda r: (-counts[r[0]], r[0])):
            fh.write(u",".join([cell(d), cell(counts[d]), cell(ids)]) + u"\r\n")
    return len(rows), len(counts)


# --------------------------------------------------------------------------- export mode
def links_not_visible(doc, view_ids):
    """Loaded link types with no instance visible in any of the given views.

    Returns [] (unload nothing) if any view can't be checked, to stay safe.
    """
    visible = set()
    for vid in view_ids:
        try:
            for li in FilteredElementCollector(doc, vid).OfClass(DB.RevitLinkInstance):
                visible.add(eid_int(li.GetTypeId()))
        except Exception:
            v = doc.GetElement(vid)
            # schedules/legends can't hold links; anything else unknown -> be safe
            if v is not None and v.ViewType in (ViewType.Schedule, ViewType.Legend,
                                                ViewType.DraftingView):
                continue
            return []
    return [lt for lt, count, loaded in link_info(doc)
            if loaded and count > 0 and eid_int(lt.Id) not in visible]


def unload_links(doc, link_types):
    """Unload for this session. Returns list of (link_type, how) to restore."""
    done = []
    for lt in link_types:
        try:
            if doc.IsWorkshared and hasattr(lt, "UnloadLocally"):
                lt.UnloadLocally(None)
                done.append((lt, "local"))
            else:
                lt.Unload(None)
                done.append((lt, "full"))
        except Exception:
            continue
    return done


def restore_links(unloaded):
    failed = []
    for lt, how in unloaded:
        try:
            if how == "local":
                lt.RevertLocalUnloadStatus()
            else:
                lt.Reload()
        except Exception:
            failed.append(_safe_name(lt))
    return failed
