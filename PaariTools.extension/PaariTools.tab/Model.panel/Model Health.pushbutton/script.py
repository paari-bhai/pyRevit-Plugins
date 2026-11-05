# -*- coding: utf-8 -*-
"""Find what makes this model slow and clean it up safely."""
__title__ = "Model\nHealth"
__author__ = "paari-bhai"

import datetime
import os

from pyrevit import forms, revit, script

from modelhealth import core

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()
XAML = os.path.join(os.path.dirname(__file__), "ui.xaml")

DESTRUCTIVE = {
    "remove_unused_links": "Remove unused links",
    "delete_cad_imports": "Delete CAD imports",
    "delete_unused_images": "Delete unused images",
    "delete_unplaced_views": "Delete unplaced views",
    "delete_unused_templates": "Delete unused view templates",
    "delete_unused_groups": "Delete unused group types",
    "purge_families": "Purge unused families",
    "purge_unused": "Purge unused",
}
# Items most people want gone are pre-ticked; views and CAD start unticked.
PRECHECKED = set(["remove_unused_links", "delete_unused_images", "delete_unused_templates",
                  "delete_unused_groups", "purge_families", "purge_unused"])


class HealthWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML)
        self.tb_model.Text = doc.Title
        self.btn_rescan.Click += lambda s, a: self.rescan()
        self.btn_close.Click += lambda s, a: self.Close()
        self.rescan()

    def rescan(self):
        try:
            findings = core.scan(doc, uidoc)
        except Exception as ex:
            logger.exception("Scan failed")
            forms.alert("Scan failed:\n{0}".format(ex))
            findings = []
        for f in findings:
            f.fix_visibility = "Visible" if f.fix else "Collapsed"
        order = {"Fix": 0, "Watch": 1, "OK": 2, "Info": 3}
        findings.sort(key=lambda f: order.get(f.level, 9))
        self.dg_findings.ItemsSource = findings
        n_fix = sum(1 for f in findings if f.level == "Fix")
        n_watch = sum(1 for f in findings if f.level == "Watch")
        self.tb_summary.Text = "{0} to fix, {1} to watch. Scanned {2}.".format(
            n_fix, n_watch, datetime.datetime.now().strftime("%H:%M"))

    def fix_clicked(self, sender, args):
        finding = sender.DataContext
        fix = getattr(finding, "fix", None)
        if not fix:
            return
        try:
            if fix in DESTRUCTIVE:
                self.run_delete(fix)
            elif fix == "close_views":
                n = core.close_other_views(uidoc)
                forms.alert("Closed {0} view(s).".format(n), title="Model Health")
            elif fix == "save_compact":
                if forms.alert("Save the model now with compact?\n\nThis overwrites the file "
                               "on disk.", yes=True, no=True, title="Model Health"):
                    forms.alert(core.save_compact(doc), title="Model Health")
            elif fix == "export_warnings":
                path = forms.save_file(file_ext="csv", default_name="{0}-warnings".format(
                    doc.Title))
                if path:
                    n, kinds = core.export_warnings_csv(doc, path)
                    forms.alert("Exported {0} warnings ({1} kinds) to:\n{2}".format(
                        n, kinds, path), title="Model Health")
        except Exception as ex:
            logger.exception("Fix failed")
            forms.alert("That didn't work:\n{0}".format(ex), title="Model Health")
        self.rescan()

    def run_delete(self, fix):
        title = DESTRUCTIVE[fix]
        candidates = core.candidates_for(doc, uidoc, fix)
        if not candidates:
            forms.alert("Nothing to clean up here.", title=title)
            return
        items = [forms.TemplateListItem(c, checked=fix in PRECHECKED) for c in candidates]
        picked = forms.SelectFromList.show(
            items, title="{0}: tick what to delete".format(title), multiselect=True,
            button_name="Delete ticked", width=700, height=600)
        if not picked:
            return
        if not forms.alert("Delete {0} item(s)?\n\nAnything still in use is skipped. "
                           "You can undo the whole step with Ctrl+Z.".format(len(picked)),
                           yes=True, no=True, title=title):
            return
        deleted, skipped = core.safe_delete(doc, picked, "Model Health: " + title)
        msg = "Deleted {0}. Skipped {1}.".format(len(deleted), len(skipped))
        if skipped:
            msg += "\n\nSkipped:\n" + "\n".join(
                u"- {0}: {1}".format(l, r) for l, r in skipped[:15])
            if len(skipped) > 15:
                msg += "\n... and {0} more (see the pyRevit output).".format(len(skipped) - 15)
            for l, r in skipped:
                logger.info(u"Skipped {0}: {1}".format(l, r))
        forms.alert(msg, title=title)


if __name__ == "__main__":
    if doc is None or doc.IsFamilyDocument:
        forms.alert("Open a project model first.", exitscript=True)
    HealthWindow().ShowDialog()
