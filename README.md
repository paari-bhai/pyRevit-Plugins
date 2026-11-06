# pyRevit plugins

Personal pyRevit extension: `PaariTools.extension`.

## Install

1. Clone this repo somewhere, e.g. `C:\pyRevit-Plugins`.
2. In Revit: **pyRevit tab → Settings → Custom Extension Directories → Add folder** and pick the repo folder (the one that contains `PaariTools.extension`).
3. Save settings and **Reload** pyRevit. A **PaariTools** tab appears.

## SheetFlow (PaariTools → Export → SheetFlow)

Batch exporter for sheets and views with no export limit. Built for Revit 2022 to 2024
(native PDF export needs 2022 or newer).

**Selection tab**
- Switch between Sheets and Views; filter by a Revit View/Sheet Set; search by number, name or revision.
- Columns: number, name, current revision and date, paper size and orientation detected from the title block, and an editable **Custom File Name**.
- Save the checked items as a new View/Sheet Set.
- "Fill custom names from template" writes the naming template into each row so you can tweak single names.

**Format tab** (tick a format in its tab header to enable it)
- **PDF**: centre or offset placement with margin, fit to page or zoom %, vector or raster hidden-line processing, raster quality, colour/greyscale/black lines, export DPI, the Revit hide options (ref planes, unreferenced tags, scope boxes, crop boundaries, halftone, coincident lines, links in blue), separate files or one combined file.
- **DWG / DGN**: use a saved Revit export setup or in-session defaults; AutoCAD version; views on sheets as xrefs; shared coordinates.
- **DWF/DWFx**, **NWC** (3D views, needs the Navisworks NWC exporter), **IFC** (views), **IMG** (PNG/JPG/TIFF, DPI, pixel size), **XML** (sheet list with parameters).
- **File naming** for all formats with tokens like `{Sheet Number}-{Sheet Name}-{Current Revision}`. Any sheet, title block or project parameter name works as a token.

**Create tab**
- Output folder (environment variables like `%USERPROFILE%` work), same folder or a subfolder per format.
- Paper size and orientation per row (Auto = from the title block).
- Progress bar with per-row status and an estimated time left (learned from your past exports per model), a Stop button, optional CSV report, and open the folder when done.

Settings are remembered between runs. Named profiles live in
`%APPDATA%\pyRevit\SheetFlow\profiles`.

**Speed mode** (Create tab): temporarily unloads Revit links that don't appear on any
exported sheet or view, then reloads them when the export finishes. Off by default.

## Model Health (PaariTools → Model → Model Health)

Scans the open model for the usual causes of slowness and shows each as OK / Watch / Fix:
file size, warnings, loaded and unused Revit links, imported and linked CAD, unused raster
images, in-place families, views not on sheets, unused view templates, groups, purgeable
elements (Revit 2024 native purge; unused loadable families on 2022/2023), CAD line
patterns and open views.

Fixes:
- **Delete / purge** actions always show the list first so you can untick items. Each item is
  deleted on its own and rolled back if Revit would delete anything else with it. Profile and
  detail-item families are never offered, because sweeps, mullions and repeating details use
  them without placed instances. The whole clean-up is one Ctrl+Z.
- **Close all but the active view.**
- **Export warnings** to CSV, grouped by type.
- **Save and compact** (non-workshared files; for workshared files use Sync with "Compact").

### Not included yet
- Exporting sheets from linked models.
- Scheduled / unattended exports.
- Printing through a PDF printer driver (native export is used instead).
