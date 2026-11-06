# -*- coding: utf-8 -*-
"""Batch export sheets and views to PDF, DWG, DGN, DWF, NWC, IFC, images and XML."""
__title__ = "SheetFlow"
__author__ = "paari-bhai"

import os
import time

from pyrevit import forms, revit, script

import System
from System import Action
from System.Windows import Visibility
from System.Windows.Threading import DispatcherPriority
from Autodesk.Revit.DB import PrintRange, ViewSet

from sheetflow import collect, eta, exporters, naming, notify, runner, settings
from modelhealth import core as health
from sheetflow.settings import FORMATS

doc = revit.doc
logger = script.get_logger()

XAML = os.path.join(os.path.dirname(__file__), "ui.xaml")
NOTIFY_XAML = os.path.join(os.path.dirname(__file__), "notify.xaml")
ALL_SETS = "<All sheets / views>"

PDF_MARGINS = ["NoMargin", "PrinterLimit", "UserDefined"]
PDF_RASTER_QUALITY = ["Low", "Medium", "High", "Presentation"]
PDF_COLORS = ["Color", "GrayScale", "BlackLine"]
PDF_DPI = ["DPI72", "DPI144", "DPI300", "DPI600", "DPI1200", "DPI2400", "DPI4000"]
ACAD_VERSIONS = ["Default", "R2018", "R2013", "R2010", "R2007"]
DWG_COLORS = [  # (label, ExportColorMode member)
    ("True colour per view (RGB as shown)", "TrueColorPerView"),
    ("True colour (RGB from object styles)", "TrueColor"),
    ("Index colours (AutoCAD 255)", "IndexColors"),
    ("Use the export setup's colours", "FromSetup"),
]
DWG_COLOR_LABELS = [c[0] for c in DWG_COLORS]
DWG_COLOR_BY_LABEL = dict(DWG_COLORS)
DWG_LABEL_BY_COLOR = dict((v, k) for k, v in DWG_COLORS)

# PDF setting key -> UI controls that need that PDFExportOptions property
PDF_CONTROLS = {
    "hidden_line": ["rb_pdf_vector", "rb_pdf_raster"],
    "export_quality": ["cb_pdf_dpi"],
    "always_raster": ["chk_pdf_always_raster"],
    "raster_quality": ["cb_pdf_rq"],
    "colors": ["cb_pdf_colors"],
    "view_links_blue": ["chk_pdf_links_blue"],
    "hide_ref_planes": ["chk_pdf_hide_ref"],
    "hide_unref_tags": ["chk_pdf_hide_tags"],
    "hide_scope_boxes": ["chk_pdf_hide_scope"],
    "hide_crop": ["chk_pdf_hide_crop"],
    "halftone_thin": ["chk_pdf_halftone"],
    "mask_coincident": ["chk_pdf_mask"],
    "margin": ["cb_pdf_margin"],
    "zoom": ["rb_pdf_zoom", "tb_pdf_zoom"],
}
DWF_QUALITY = ["Default", "Low", "Medium", "High"]
DWF_FORMAT = ["Lossless", "Lossy"]
NWC_COORDS = ["Shared", "Internal"]
NWC_PARAMS = ["All", "Elements", "None"]
IFC_VERSIONS = ["IFC2x3CV2", "IFC2x3", "IFC2x3FM", "IFC2x3BFM", "IFC4", "IFC4RV", "IFC4DTV",
                "IFC2x2", "IFCCOBIE"]
IFC_SPACE = ["0 - None", "1 - 1st level", "2 - 2nd level"]
IMG_TYPES = ["PNG", "JPEGLossless", "JPEGMedium", "JPEGSmallest", "TIFF", "BMP", "TARGA"]
IMG_RES = ["DPI_72", "DPI_150", "DPI_300", "DPI_600"]
IMG_FIT = ["Horizontal", "Vertical"]
REPORTS = ["None", "CSV"]


def fill(combo, values, selected=None):
    combo.Items.Clear()
    for v in values:
        combo.Items.Add(v)
    if selected is not None and selected in values:
        combo.SelectedItem = selected
    elif values:
        combo.SelectedIndex = 0


def to_float(text, default=0.0):
    try:
        return float(text)
    except Exception:
        return default


def to_int(text, default=0):
    try:
        return int(float(text))
    except Exception:
        return default


class SheetFlowWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML)
        self._cancel = False
        self._busy = False
        self.cfg = settings.load_profile()

        self.sheets = collect.collect_sheets(doc)
        self.views = collect.collect_views(doc)
        self.vs_sets = collect.collect_view_sheet_sets(doc)
        self.tb_model.Text = u"{0}  |  {1} sheets, {2} views".format(
            doc.Title, len(self.sheets), len(self.views))

        # static choices
        fill(self.cb_vsset, [ALL_SETS] + sorted(self.vs_sets.keys()))
        fill(self.cb_pdf_margin, PDF_MARGINS)
        fill(self.cb_pdf_rq, PDF_RASTER_QUALITY)
        fill(self.cb_pdf_colors, PDF_COLORS)
        fill(self.cb_pdf_dpi, PDF_DPI)
        setups = exporters.predefined_setups(doc)
        fill(self.cb_dwg_setup, setups)
        fill(self.cb_dgn_setup, setups)
        fill(self.cb_dwg_version, ACAD_VERSIONS)
        fill(self.cb_dwg_colors, DWG_COLOR_LABELS)
        fill(self.cb_dwf_quality, DWF_QUALITY)
        fill(self.cb_dwf_format, DWF_FORMAT)
        fill(self.cb_nwc_coords, NWC_COORDS)
        fill(self.cb_nwc_params, NWC_PARAMS)
        fill(self.cb_ifc_version, IFC_VERSIONS)
        fill(self.cb_ifc_space, IFC_SPACE)
        fill(self.cb_img_type, IMG_TYPES)
        fill(self.cb_img_res, IMG_RES)
        fill(self.cb_img_fit, IMG_FIT)
        fill(self.cb_report, REPORTS)
        fill(self.cb_set_size, exporters.PAPER_CHOICES)
        fill(self.cb_set_orient, exporters.ORIENTATION_CHOICES)
        tokens = list(naming.COMMON_TOKENS)
        for n in collect.sheet_parameter_names(doc, self.sheets or self.views):
            if n not in tokens:
                tokens.append(n)
        fill(self.cb_tokens, tokens)
        self.refresh_profiles()

        if not exporters.pdf_supported():
            self.en_pdf.IsEnabled = False
            self.en_pdf.ToolTip = "Native PDF export needs Revit 2022 or newer"
        else:
            # Grey out PDF options this Revit version's API doesn't have.
            for key, controls in PDF_CONTROLS.items():
                prop = exporters.PDF_OPTION_PROPS[key]
                if not exporters.option_available("PDFExportOptions", prop):
                    for name in controls:
                        ctrl = getattr(self, name)
                        ctrl.IsEnabled = False
                        ctrl.ToolTip = "Not available in this Revit version ({0})".format(prop)
        if not exporters.nwc_supported():
            self.tb_nwc_note.Text += "  (Exporter not detected on this machine.)"

        self.apply_cfg(self.cfg)
        self.wire_events()
        self.refresh_items()
        self.update_nav()

    # ------------------------------------------------------------------ wiring
    def wire_events(self):
        self.rb_sheets.Checked += self.on_filter_changed
        self.rb_views.Checked += self.on_filter_changed
        self.cb_vsset.SelectionChanged += self.on_filter_changed
        self.tb_search.TextChanged += self.on_filter_changed
        self.chk_only_checked.Checked += self.on_filter_changed
        self.chk_only_checked.Unchecked += self.on_filter_changed
        self.btn_check_all.Click += lambda s, a: self.set_checked(self.visible_items(), True)
        self.btn_check_none.Click += lambda s, a: self.set_checked(self.visible_items(), False)
        self.btn_check_selected.Click += self.on_check_highlighted
        self.btn_fill_names.Click += self.on_fill_names
        self.btn_clear_names.Click += self.on_clear_names
        self.btn_save_vsset.Click += self.on_save_vsset

        self.tb_sheet_template.TextChanged += self.update_preview
        self.tb_view_template.TextChanged += self.update_preview
        self.btn_token_sheet.Click += lambda s, a: self.insert_token(self.tb_sheet_template)
        self.btn_token_view.Click += lambda s, a: self.insert_token(self.tb_view_template)

        self.btn_browse.Click += self.on_browse
        self.btn_notify.Click += self.on_notify_settings
        self.btn_apply_paper.Click += self.on_apply_paper

        self.tabs.SelectionChanged += self.on_tab_changed
        self.btn_back.Click += lambda s, a: self.go(-1)
        self.btn_next.Click += lambda s, a: self.go(1)
        self.btn_create.Click += self.on_create
        self.btn_cancel.Click += self.on_cancel

        self.btn_profile_load.Click += self.on_profile_load
        self.btn_profile_save.Click += self.on_profile_save
        self.btn_profile_delete.Click += self.on_profile_delete
        self.Closing += self.on_closing

    # ------------------------------------------------------------------ settings <-> UI
    def apply_cfg(self, cfg):
        en = cfg["enabled"]
        for fmt in FORMATS:
            box = getattr(self, "en_" + fmt.lower())
            box.IsChecked = bool(en.get(fmt)) and box.IsEnabled

        n = cfg["naming"]
        self.tb_sheet_template.Text = n["sheet_template"]
        self.tb_view_template.Text = n["view_template"]

        p = cfg["pdf"]
        self.rb_pdf_center.IsChecked = p["placement"] == "Center"
        self.rb_pdf_offset.IsChecked = p["placement"] != "Center"
        fill(self.cb_pdf_margin, PDF_MARGINS, p["margin"])
        self.tb_pdf_offx.Text = str(p["offset_x_mm"])
        self.tb_pdf_offy.Text = str(p["offset_y_mm"])
        self.rb_pdf_fit.IsChecked = p["zoom"] != "Zoom"
        self.rb_pdf_zoom.IsChecked = p["zoom"] == "Zoom"
        self.tb_pdf_zoom.Text = str(p["zoom_percent"])
        self.rb_pdf_vector.IsChecked = p["hidden_line"] != "Raster"
        self.rb_pdf_raster.IsChecked = p["hidden_line"] == "Raster"
        fill(self.cb_pdf_rq, PDF_RASTER_QUALITY, p["raster_quality"])
        fill(self.cb_pdf_colors, PDF_COLORS, p["colors"])
        fill(self.cb_pdf_dpi, PDF_DPI, p["export_quality"])
        self.chk_pdf_always_raster.IsChecked = p["always_raster"]
        self.chk_pdf_links_blue.IsChecked = p["view_links_blue"]
        self.chk_pdf_hide_ref.IsChecked = p["hide_ref_planes"]
        self.chk_pdf_hide_tags.IsChecked = p["hide_unref_tags"]
        self.chk_pdf_hide_scope.IsChecked = p["hide_scope_boxes"]
        self.chk_pdf_hide_crop.IsChecked = p["hide_crop"]
        self.chk_pdf_halftone.IsChecked = p["halftone_thin"]
        self.chk_pdf_mask.IsChecked = p["mask_coincident"]
        self.rb_pdf_separate.IsChecked = not p["combine"]
        self.rb_pdf_combine.IsChecked = p["combine"]
        self.tb_pdf_combined_name.Text = p["combined_name"]
        self.chk_pdf_batch.IsChecked = p.get("batch", True)
        if not exporters.pdf_batch_supported():
            self.chk_pdf_batch.IsEnabled = False

        d = cfg["dwg"]
        fill(self.cb_dwg_setup, list(self.cb_dwg_setup.Items), d["setup"])
        fill(self.cb_dwg_version, ACAD_VERSIONS, d["version"])
        fill(self.cb_dwg_colors, DWG_COLOR_LABELS,
             DWG_LABEL_BY_COLOR.get(d.get("colors"), DWG_COLOR_LABELS[0]))
        self.chk_dwg_xref.IsChecked = not d["merged_views"]
        self.chk_dwg_shared.IsChecked = d["shared_coords"]
        self.chk_dwg_hide_ref.IsChecked = d["hide_ref_planes"]
        self.chk_dwg_hide_scope.IsChecked = d["hide_scope_boxes"]
        self.chk_dwg_hide_tags.IsChecked = d["hide_unref_tags"]

        g = cfg["dgn"]
        fill(self.cb_dgn_setup, list(self.cb_dgn_setup.Items), g["setup"])
        self.chk_dgn_xref.IsChecked = not g["merged_views"]
        self.chk_dgn_shared.IsChecked = g["shared_coords"]
        self.chk_dgn_hide_ref.IsChecked = g["hide_ref_planes"]
        self.chk_dgn_hide_scope.IsChecked = g["hide_scope_boxes"]
        self.chk_dgn_hide_tags.IsChecked = g["hide_unref_tags"]

        w = cfg["dwf"]
        fill(self.cb_dwf_quality, DWF_QUALITY, w["image_quality"])
        fill(self.cb_dwf_format, DWF_FORMAT, w["image_format"])
        self.chk_dwf_dwfx.IsChecked = w["dwfx"]
        self.chk_dwf_combine.IsChecked = w["combine"]
        self.chk_dwf_objdata.IsChecked = w["export_object_data"]
        self.chk_dwf_hide_crop.IsChecked = w["hide_crop"]

        c = cfg["nwc"]
        fill(self.cb_nwc_coords, NWC_COORDS, c["coordinates"])
        fill(self.cb_nwc_params, NWC_PARAMS, c["parameters"])
        self.chk_nwc_links.IsChecked = c["export_links"]
        self.chk_nwc_parts.IsChecked = c["export_parts"]
        self.chk_nwc_props.IsChecked = c["convert_properties"]
        self.chk_nwc_rooms.IsChecked = c["export_room_geometry"]
        self.chk_nwc_levels.IsChecked = c["divide_by_levels"]
        self.chk_nwc_urls.IsChecked = c["export_urls"]
        self.chk_nwc_ids.IsChecked = c["export_element_ids"]
        self.chk_nwc_materials.IsChecked = c["find_missing_materials"]

        i = cfg["ifc"]
        fill(self.cb_ifc_version, IFC_VERSIONS, i["version"])
        self.cb_ifc_space.SelectedIndex = max(0, min(2, int(i["space_boundaries"])))
        self.chk_ifc_view_only.IsChecked = i["current_view_only"]
        self.chk_ifc_quantities.IsChecked = i["base_quantities"]
        self.chk_ifc_split.IsChecked = i["split_walls"]
        self.chk_ifc_links.IsChecked = i["export_links"]

        m = cfg["img"]
        fill(self.cb_img_type, IMG_TYPES, m["file_type"])
        fill(self.cb_img_res, IMG_RES, m["resolution"])
        fill(self.cb_img_fit, IMG_FIT, m["fit_direction"])
        self.tb_img_px.Text = str(m["pixel_size"])

        self.chk_xml_params.IsChecked = cfg["xml"]["include_all_params"]

        self.notify_cfg = dict(cfg["notify"])
        self.update_notify_status()
        cr = cfg["create"]
        self.tb_folder.Text = cr["folder"]
        self.rb_split_folder.IsChecked = cr["split_by_format"]
        self.rb_same_folder.IsChecked = not cr["split_by_format"]
        fill(self.cb_report, REPORTS, cr["report"])
        self.chk_open_folder.IsChecked = cr["open_folder"]
        self.chk_unload_links.IsChecked = cr.get("unload_hidden_links", False)

    def gather_cfg(self):
        cfg = settings.load_profile()  # defaults + last used, then overwrite from the UI
        b = lambda c: bool(c.IsChecked)
        for fmt in FORMATS:
            cfg["enabled"][fmt] = b(getattr(self, "en_" + fmt.lower()))
        cfg["naming"]["sheet_template"] = self.tb_sheet_template.Text or naming.DEFAULT_SHEET_TEMPLATE
        cfg["naming"]["view_template"] = self.tb_view_template.Text or naming.DEFAULT_VIEW_TEMPLATE
        cfg["pdf"].update({
            "placement": "Center" if b(self.rb_pdf_center) else "Offset",
            "margin": self.cb_pdf_margin.SelectedItem,
            "offset_x_mm": to_float(self.tb_pdf_offx.Text),
            "offset_y_mm": to_float(self.tb_pdf_offy.Text),
            "zoom": "Zoom" if b(self.rb_pdf_zoom) else "FitToPage",
            "zoom_percent": to_int(self.tb_pdf_zoom.Text, 100),
            "hidden_line": "Raster" if b(self.rb_pdf_raster) else "Vector",
            "raster_quality": self.cb_pdf_rq.SelectedItem,
            "colors": self.cb_pdf_colors.SelectedItem,
            "export_quality": self.cb_pdf_dpi.SelectedItem,
            "always_raster": b(self.chk_pdf_always_raster),
            "view_links_blue": b(self.chk_pdf_links_blue),
            "hide_ref_planes": b(self.chk_pdf_hide_ref),
            "hide_unref_tags": b(self.chk_pdf_hide_tags),
            "hide_scope_boxes": b(self.chk_pdf_hide_scope),
            "hide_crop": b(self.chk_pdf_hide_crop),
            "halftone_thin": b(self.chk_pdf_halftone),
            "mask_coincident": b(self.chk_pdf_mask),
            "combine": b(self.rb_pdf_combine),
            "batch": b(self.chk_pdf_batch),
            "combined_name": self.tb_pdf_combined_name.Text or "{Model Name}-Combined",
        })
        cfg["dwg"].update({
            "setup": self.cb_dwg_setup.SelectedItem,
            "version": self.cb_dwg_version.SelectedItem,
            "colors": DWG_COLOR_BY_LABEL.get(self.cb_dwg_colors.SelectedItem, "TrueColorPerView"),
            "merged_views": not b(self.chk_dwg_xref),
            "shared_coords": b(self.chk_dwg_shared),
            "hide_ref_planes": b(self.chk_dwg_hide_ref),
            "hide_scope_boxes": b(self.chk_dwg_hide_scope),
            "hide_unref_tags": b(self.chk_dwg_hide_tags),
        })
        cfg["dgn"].update({
            "setup": self.cb_dgn_setup.SelectedItem,
            "merged_views": not b(self.chk_dgn_xref),
            "shared_coords": b(self.chk_dgn_shared),
            "hide_ref_planes": b(self.chk_dgn_hide_ref),
            "hide_scope_boxes": b(self.chk_dgn_hide_scope),
            "hide_unref_tags": b(self.chk_dgn_hide_tags),
        })
        cfg["dwf"].update({
            "dwfx": b(self.chk_dwf_dwfx),
            "combine": b(self.chk_dwf_combine),
            "export_object_data": b(self.chk_dwf_objdata),
            "image_quality": self.cb_dwf_quality.SelectedItem,
            "image_format": self.cb_dwf_format.SelectedItem,
            "hide_crop": b(self.chk_dwf_hide_crop),
            "combined_name": cfg["pdf"]["combined_name"],
        })
        cfg["nwc"].update({
            "coordinates": self.cb_nwc_coords.SelectedItem,
            "parameters": self.cb_nwc_params.SelectedItem,
            "export_links": b(self.chk_nwc_links),
            "export_parts": b(self.chk_nwc_parts),
            "convert_properties": b(self.chk_nwc_props),
            "export_room_geometry": b(self.chk_nwc_rooms),
            "divide_by_levels": b(self.chk_nwc_levels),
            "export_urls": b(self.chk_nwc_urls),
            "export_element_ids": b(self.chk_nwc_ids),
            "find_missing_materials": b(self.chk_nwc_materials),
        })
        cfg["ifc"].update({
            "version": self.cb_ifc_version.SelectedItem,
            "space_boundaries": max(0, self.cb_ifc_space.SelectedIndex),
            "current_view_only": b(self.chk_ifc_view_only),
            "base_quantities": b(self.chk_ifc_quantities),
            "split_walls": b(self.chk_ifc_split),
            "export_links": b(self.chk_ifc_links),
        })
        cfg["img"].update({
            "file_type": self.cb_img_type.SelectedItem,
            "resolution": self.cb_img_res.SelectedItem,
            "fit_direction": self.cb_img_fit.SelectedItem,
            "pixel_size": to_int(self.tb_img_px.Text, 4096),
        })
        cfg["xml"]["include_all_params"] = b(self.chk_xml_params)
        cfg["notify"] = dict(self.notify_cfg)
        cfg["create"].update({
            "folder": self.tb_folder.Text or "",
            "split_by_format": b(self.rb_split_folder),
            "report": self.cb_report.SelectedItem or "None",
            "open_folder": b(self.chk_open_folder),
            "unload_hidden_links": b(self.chk_unload_links),
        })
        return cfg

    # ------------------------------------------------------------------ selection tab
    @property
    def mode_items(self):
        return self.sheets if self.rb_sheets.IsChecked else self.views

    def visible_items(self):
        items = self.mode_items
        vsset = self.cb_vsset.SelectedItem
        if vsset and vsset != ALL_SETS:
            ids = self.vs_sets.get(vsset, set())
            items = [i for i in items if collect.eid_int(i.id) in ids]
        q = (self.tb_search.Text or "").strip().lower()
        if q:
            items = [i for i in items
                     if q in i.number.lower() or q in i.name.lower() or q in i.revision.lower()]
        if self.chk_only_checked.IsChecked:
            items = [i for i in items if i.checked]
        return items

    def refresh_items(self):
        self.dg_items.ItemsSource = self.visible_items()
        self.update_status()
        self.update_preview()

    def checked_items(self):
        """Checked sheets then views; current mode follows the grid's (sortable) order."""
        result = []
        seen = set()
        for row in self.dg_items.Items:
            if getattr(row, "checked", False):
                result.append(row)
                seen.add(id(row))
        current = self.mode_items
        for i in current:
            if i.checked and id(i) not in seen:
                result.append(i)
        other = self.views if current is self.sheets else self.sheets
        others = [i for i in other if i.checked]
        return result + others if current is self.sheets else others + result

    def update_status(self):
        ns = sum(1 for i in self.sheets if i.checked)
        nv = sum(1 for i in self.views if i.checked)
        self.tb_status.Text = "{0} sheets and {1} views selected  |  Total selected: {2}".format(
            ns, nv, ns + nv)

    def set_checked(self, items, value):
        for i in items:
            i.checked = value
        self.dg_items.Items.Refresh()
        self.update_status()

    def on_filter_changed(self, sender, args):
        self.refresh_items()

    def item_check_clicked(self, sender, args):
        self.update_status()
        self.update_preview()

    def header_check_clicked(self, sender, args):
        self.set_checked(self.visible_items(), bool(sender.IsChecked))

    def on_check_highlighted(self, sender, args):
        self.set_checked(list(self.dg_items.SelectedItems), True)

    def on_fill_names(self, sender, args):
        self.dg_items.CommitEdit()
        st, vt = self.tb_sheet_template.Text, self.tb_view_template.Text
        targets = [i for i in self.visible_items() if i.checked] or self.visible_items()
        for i in targets:
            i.custom_name = naming.render(st if i.is_sheet else vt, i)
        self.dg_items.Items.Refresh()

    def on_clear_names(self, sender, args):
        for i in self.mode_items:
            i.custom_name = ""
        self.dg_items.Items.Refresh()

    def on_save_vsset(self, sender, args):
        items = self.checked_items()
        if not items:
            forms.alert("Check some sheets or views first.")
            return
        name = forms.ask_for_string(prompt="Name for the new View/Sheet Set:",
                                    title="Save V/S Set")
        if not name:
            return
        if name in self.vs_sets:
            forms.alert("A View/Sheet Set named '{0}' already exists.".format(name))
            return
        try:
            vs = ViewSet()
            for i in items:
                vs.Insert(i.element)
            with revit.Transaction("SheetFlow: save View/Sheet Set"):
                pm = doc.PrintManager
                pm.PrintRange = PrintRange.Select
                setting = pm.ViewSheetSetting
                setting.CurrentViewSheetSet.Views = vs
                setting.SaveAs(name)
        except Exception as ex:
            forms.alert("Could not save the set:\n{0}".format(ex))
            return
        self.vs_sets = collect.collect_view_sheet_sets(doc)
        fill(self.cb_vsset, [ALL_SETS] + sorted(self.vs_sets.keys()), name)

    # ------------------------------------------------------------------ naming
    def insert_token(self, textbox):
        token = self.cb_tokens.Text or self.cb_tokens.SelectedItem
        if not token:
            return
        pos = textbox.CaretIndex
        text = textbox.Text or ""
        snippet = "{" + token + "}"
        textbox.Text = text[:pos] + snippet + text[pos:]
        textbox.CaretIndex = pos + len(snippet)
        textbox.Focus()

    def update_preview(self, *args):
        items = self.checked_items() or self.mode_items
        if not items:
            self.tb_name_preview.Text = ""
            return
        i = items[0]
        name = naming.file_name_for(i, self.tb_sheet_template.Text or "",
                                    self.tb_view_template.Text or "")
        self.tb_name_preview.Text = u"Preview: {0}".format(name)

    # ------------------------------------------------------------------ create tab
    def on_browse(self, sender, args):
        folder = forms.pick_folder()
        if folder:
            self.tb_folder.Text = folder

    def on_apply_paper(self, sender, args):
        rows = list(self.dg_create.SelectedItems) or list(self.dg_create.Items)
        for i in rows:
            i.paper_size = self.cb_set_size.SelectedItem or "Auto"
            i.paper_orientation = self.cb_set_orient.SelectedItem or "Auto"
        self.dg_create.Items.Refresh()

    def build_create_grid(self):
        cfg = self.gather_cfg()
        enabled = [f for f in FORMATS if cfg["enabled"].get(f)]
        items = self.checked_items()
        for i in items:
            fmts = []
            for f in enabled:
                if f == "NWC" and not i.is_3d:
                    continue
                if f == "IFC" and i.is_sheet:
                    continue
                fmts.append(f)
            i.formats = ", ".join(fmts) or "(none applies)"
            i.status = ""
        self.dg_create.ItemsSource = items
        self.tb_progress.Text = "Ready: {0} item(s), {1} format(s)".format(len(items), len(enabled))
        est = eta.Estimator(eta.plan(items, cfg), eta.load_history(), doc.Title)
        rem = est.remaining()
        if rem is not None and items and enabled:
            self.tb_progress.Text += "  -  estimated {0}, based on past exports".format(
                eta.fmt_duration(rem))
        self.pb_progress.Value = 0
        self.tb_progress_detail.Text = ""

    def on_tab_changed(self, sender, args):
        if not System.Object.ReferenceEquals(args.OriginalSource, self.tabs):
            return
        self.dg_items.CommitEdit()
        if self.tabs.SelectedIndex == 2:
            self.build_create_grid()
        if self.tabs.SelectedIndex == 1:
            self.update_preview()
        self.update_nav()

    def go(self, step):
        self.tabs.SelectedIndex = max(0, min(2, self.tabs.SelectedIndex + step))

    def update_nav(self):
        idx = self.tabs.SelectedIndex
        self.btn_back.IsEnabled = idx > 0 and not self._busy
        self.btn_next.IsEnabled = idx < 2 and not self._busy
        self.btn_create.IsEnabled = not self._busy

    def do_events(self):
        # Let WPF repaint and handle the Stop button while Revit is busy exporting.
        self.Dispatcher.Invoke(Action(lambda: None), DispatcherPriority.Background)

    def on_cancel(self, sender, args):
        self._cancel = True
        self.tb_progress.Text = "Stopping after the current file..."

    def on_create(self, sender, args):
        self.dg_items.CommitEdit()
        cfg = self.gather_cfg()
        items = self.checked_items()
        enabled = [f for f in FORMATS if cfg["enabled"].get(f)]
        if not items:
            forms.alert("Nothing is checked on the Selection tab.")
            self.tabs.SelectedIndex = 0
            return
        if not enabled:
            forms.alert("Tick at least one format on the Format tab.")
            self.tabs.SelectedIndex = 1
            return
        folder = os.path.expandvars(os.path.expanduser(cfg["create"]["folder"].strip()))
        if not cfg["create"]["folder"].strip():
            forms.alert("Pick an output folder first.")
            self.tabs.SelectedIndex = 2
            return
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as ex:
            forms.alert("Can't create the output folder:\n{0}".format(ex))
            return

        settings.save_profile(cfg)
        if self.tabs.SelectedIndex != 2:
            self.tabs.SelectedIndex = 2
        else:
            self.build_create_grid()

        self._cancel = False
        self._busy = True
        self.update_nav()
        self.btn_cancel.Visibility = Visibility.Visible
        self.tb_progress.Text = "Exporting..."
        self.do_events()

        def progress(step, total, item, fmt, text):
            if item is not None:
                item.status = (item.status + "  " if item.status else "") + "{0}: {1}".format(fmt, text)
            else:
                for i in items:
                    i.status = (i.status + "  " if i.status else "") + "{0}: {1}".format(fmt, text)
            estimator.mark(fmt, ok=not text.startswith("Skipped"))
            self.pb_progress.Value = 100.0 * step / max(total, 1)
            self.tb_progress.Text = "Completed {0:.0f}%  ({1} of {2})  -  {3}".format(
                self.pb_progress.Value, step, total, estimator.text())
            label = u"{0}  {1}".format(
                fmt, (item.number + " - " + item.name) if item else "combined file")
            self.tb_progress_detail.Text = label
            notifier.step(step, label, text, estimator.text())
            self.dg_create.Items.Refresh()
            self.do_events()
            return not self._cancel

        history = eta.load_history()
        estimator = eta.Estimator(eta.plan(items, cfg), history, doc.Title)
        self.tb_progress.Text = "Exporting...  {0}".format(estimator.text())
        notifier = notify.ExportNotifier(cfg["notify"], doc.Title, runner.count_steps(items, cfg))
        rem = estimator.remaining()
        notifier.started_run(len(items), [f for f in FORMATS if cfg["enabled"].get(f)],
                             eta.fmt_duration(rem) if rem is not None else "not known yet")
        crashed = None
        self.do_events()
        t_start = time.time()
        unloaded = []
        link_note = ""
        try:
            if cfg["create"].get("unload_hidden_links"):
                unloaded = self.unload_hidden_links(items)
                if unloaded:
                    link_note = "\nSpeed mode unloaded {0} link(s) during export.".format(
                        len(unloaded))
            estimator.start()
            results, cancelled = runner.run(doc, items, cfg, progress)
        except Exception as ex:
            logger.exception("Export failed")
            crashed = ex
            results, cancelled = [], True
        finally:
            if unloaded:
                self.tb_progress.Text = "Reloading {0} link(s)...".format(len(unloaded))
                self.do_events()
                failed_links = health.restore_links(unloaded)
                if failed_links:
                    link_note += "\nCould not reload: {0}. Reload them in Manage Links.".format(
                        ", ".join(failed_links))
            self._busy = False
            self.btn_cancel.Visibility = Visibility.Collapsed
            self.update_nav()

        ok = sum(1 for r in results if r.ok)
        skipped = sum(1 for r in results if not r.ok and r.message.startswith("Skipped"))
        failed = len(results) - ok - skipped
        report_path = None
        if cfg["create"]["report"] == "CSV" and results:
            try:
                report_path = runner.write_report(cfg, results)
            except Exception as ex:
                logger.warning("Could not write report: {0}".format(ex))
        eta.save_history(history, doc.Title, estimator.observed())
        elapsed = time.time() - t_start
        summary = "{0}{1} done, {2} skipped, {3} failed in {4}m {5:02d}s.".format(
            "Stopped. " if cancelled else "", ok, skipped, failed,
            int(elapsed // 60), int(elapsed % 60))
        self.tb_progress.Text = summary
        if failed or skipped:
            for r in results:
                if not r.ok:
                    logger.warning(u"{0} {1} {2}: {3}".format(
                        r.fmt, r.item.number, r.item.name, r.message))
        summary += link_note
        if exporters.UNSUPPORTED:
            notes = sorted(exporters.UNSUPPORTED)
            for n in notes:
                logger.info(u"Option not available in this Revit version, skipped: {0}".format(n))
            summary += "\n\nNotes (options this Revit version lacks, batch info):\n  " + \
                "\n  ".join(notes[:8]) + ("\n  ..." if len(notes) > 8 else "")
        if failed:
            first = [r for r in results if not r.ok and not r.message.startswith("Skipped")][0]
            summary += u"\n\nFirst failure: {0} {1}: {2}".format(
                first.fmt, first.item.number, first.message)
        if report_path:
            summary += "\nReport: {0}".format(report_path)
        if crashed is not None:
            summary = "Export stopped with an error:\n{0}\n\n{1}".format(crashed, summary)
        if notifier.active:
            notifier.finished(summary, cancelled=cancelled, crashed=crashed)
            notifier.wait(15)
            if notifier.errors:
                summary += "\n\nEmail could not be sent: {0}".format(notifier.errors[0])
            else:
                summary += "\n\nStatus emailed to {0}.".format(cfg["notify"]["to"])
        if cfg["create"]["open_folder"] and ok:
            try:
                os.startfile(folder)
            except Exception:
                pass
        forms.alert(summary, title="SheetFlow")

    def update_notify_status(self):
        n = self.notify_cfg
        if notify.is_configured(n):
            self.tb_notify_status.Text = u"Email: on, {0} to {1}".format(
                n.get("frequency", "").lower(), n.get("to"))
        elif n.get("enabled"):
            self.tb_notify_status.Text = "Email: on, but not fully set up"
        else:
            self.tb_notify_status.Text = "Email: off"

    def on_notify_settings(self, sender, args):
        dlg = NotifyWindow(self.notify_cfg)
        dlg.ShowDialog()
        if dlg.result is not None:
            self.notify_cfg = dlg.result
            self.update_notify_status()
            settings.save_profile(self.gather_cfg())

    def unload_hidden_links(self, items):
        view_ids = []
        for i in items:
            view_ids.append(i.id)
            if i.is_sheet:
                view_ids.extend(list(i.element.GetAllPlacedViews()))
        hidden = health.links_not_visible(doc, view_ids)
        if not hidden:
            return []
        self.tb_progress.Text = "Unloading {0} link(s) not on these sheets...".format(len(hidden))
        self.do_events()
        return health.unload_links(doc, hidden)

    # ------------------------------------------------------------------ profiles
    def refresh_profiles(self, selected=None):
        fill(self.cb_profile, settings.list_profiles(), selected)

    def on_profile_load(self, sender, args):
        name = self.cb_profile.SelectedItem
        if name:
            self.apply_cfg(settings.load_profile(name))
            self.update_preview()

    def on_profile_save(self, sender, args):
        name = forms.ask_for_string(prompt="Profile name:", title="Save profile",
                                    default=self.cb_profile.SelectedItem or "")
        if not name:
            return
        name = naming.sanitize(name)
        settings.save_profile(self.gather_cfg(), name)
        self.refresh_profiles(name)

    def on_profile_delete(self, sender, args):
        name = self.cb_profile.SelectedItem
        if name and forms.alert("Delete profile '{0}'?".format(name), yes=True, no=True):
            settings.delete_profile(name)
            self.refresh_profiles()

    def on_closing(self, sender, args):
        if self._busy:
            args.Cancel = True
            return
        try:
            settings.save_profile(self.gather_cfg())
        except Exception:
            pass


class NotifyWindow(forms.WPFWindow):
    """Email settings with a test button."""

    def __init__(self, ncfg):
        forms.WPFWindow.__init__(self, NOTIFY_XAML)
        self.result = None
        n = dict(settings.DEFAULTS["notify"])
        n.update(ncfg or {})
        self.chk_enabled.IsChecked = n["enabled"]
        self.tb_to.Text = n["to"]
        fill(self.cb_provider, sorted(notify.PRESETS.keys()), n.get("provider", "Gmail"))
        self.tb_host.Text = n["smtp_host"]
        self.tb_port.Text = str(n["smtp_port"])
        self.chk_ssl.IsChecked = n["ssl"]
        self.tb_user.Text = n.get("username", "")
        fill(self.cb_freq, notify.FREQUENCIES, n.get("frequency", "Every 25%"))
        self.tb_pw_state.Text = ("A password is saved (encrypted for your Windows account). "
                                 "Leave blank to keep it." if notify.has_password()
                                 else "No password saved yet.")
        self.cb_provider.SelectionChanged += self.on_provider
        self.btn_test.Click += self.on_test
        self.btn_save.Click += self.on_save
        self.btn_cancel.Click += lambda s, a: self.Close()

    def on_provider(self, sender, args):
        preset = notify.PRESETS.get(self.cb_provider.SelectedItem)
        if preset and preset[0]:
            self.tb_host.Text, port, ssl = preset
            self.tb_port.Text = str(port)
            self.chk_ssl.IsChecked = ssl

    def collect(self):
        return {
            "enabled": bool(self.chk_enabled.IsChecked),
            "to": (self.tb_to.Text or "").strip(),
            "from": "",
            "username": (self.tb_user.Text or "").strip(),
            "provider": self.cb_provider.SelectedItem or "Custom",
            "smtp_host": (self.tb_host.Text or "").strip(),
            "smtp_port": to_int(self.tb_port.Text, 587),
            "ssl": bool(self.chk_ssl.IsChecked),
            "frequency": self.cb_freq.SelectedItem or "Every 25%",
        }

    def store_password(self):
        if self.pb_password.Password:
            notify.save_password(self.pb_password.Password)

    def on_test(self, sender, args):
        n = self.collect()
        if not n["to"] or not n["smtp_host"]:
            forms.alert("Fill in the email address and mail server first.")
            return
        self.store_password()
        try:
            notify.send_now(n, "SheetFlow test email",
                            "Email notifications from SheetFlow are working.\n\nModel: {0}".format(
                                doc.Title))
            forms.alert("Test email sent to {0}. Check your inbox (and spam).".format(n["to"]))
        except Exception as ex:
            forms.alert("Sending failed:\n{0}\n\nFor Gmail you need an App Password "
                        "(Google Account > Security > 2-Step Verification > App passwords), "
                        "not your normal password.".format(ex))

    def on_save(self, sender, args):
        self.store_password()
        self.result = self.collect()
        self.Close()


if __name__ == "__main__":
    if doc is None or doc.IsFamilyDocument:
        forms.alert("Open a project model to export sheets and views.", exitscript=True)
    SheetFlowWindow().ShowDialog()
