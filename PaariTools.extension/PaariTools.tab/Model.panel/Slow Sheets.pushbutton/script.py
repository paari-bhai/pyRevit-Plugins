# -*- coding: utf-8 -*-
"""Show what makes the chosen sheets slow to export, view by view."""
__title__ = "Slow\nSheets"
__author__ = "paari-bhai"

from pyrevit import forms, revit, script

from modelhealth import sheetprofile

doc = revit.doc
output = script.get_output()


def main():
    sheets = forms.select_sheets(title="Pick the sheets that export slowly", multiple=True)
    if not sheets:
        return
    output.print_md("# Why these sheets are slow")
    output.print_md("Each view on the sheet is ranked by how much Revit has to draw. "
                    "The top views and the notes under them are where the export time goes.")
    rows_all = []
    with forms.ProgressBar(title="Checking views ({value} of {max_value})",
                           cancellable=True) as pb:
        for n, sheet in enumerate(sheets):
            if pb.cancelled:
                break
            profiles = sheetprofile.profile_sheet(doc, sheet)
            pb.update_progress(n + 1, len(sheets))
            output.print_md(u"## {0} - {1}".format(sheet.SheetNumber, sheet.Name))
            if not profiles:
                output.print_md("No views placed on this sheet.")
                continue
            rows = []
            for p in profiles:
                rows.append([
                    p.name, p.vtype, "1:{0}".format(p.scale) if p.scale else "",
                    p.detail, "{0:,}".format(p.elements),
                    "{0} ({1})".format(len(p.links),
                                       "{0:,} el.".format(p.linked_elements)
                                       if p.linked_elements else "?") if p.links else "0",
                    str(p.cad), str(p.filled_regions),
                    str(p.by_cat.get("Topography", 0) + p.by_cat.get("Toposolid", 0)),
                    str(p.by_cat.get("Planting", 0)),
                    "off" if p.crop_off else "on",
                    "{0:.0f}".format(p.score),
                ])
                rows_all.append((p.score, sheet.SheetNumber, p))
            output.print_table(rows, columns=[
                "View", "Type", "Scale", "Detail", "Elements", "Links visible", "CAD",
                "Filled regions", "Topo", "Planting", "Crop", "Weight"])
            for p in profiles[:3]:
                if p.notes:
                    output.print_md(u"**{0}**".format(p.name))
                    for note in p.notes:
                        output.print_md(u"- " + note)

    if len(sheets) > 1 and rows_all:
        rows_all.sort(key=lambda r: -r[0])
        output.print_md("## Heaviest views across all picked sheets")
        output.print_table(
            [[num, p.name, "{0:.0f}".format(score), "; ".join(n.split(".")[0] for n in p.notes)]
             for score, num, p in rows_all[:10]],
            columns=["Sheet", "View", "Weight", "Main reasons"])


main()
