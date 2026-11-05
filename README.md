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
- Progress bar with per-row status, a Stop button, optional CSV report, and open the folder when done.

Settings are remembered between runs. Named profiles live in
`%APPDATA%\pyRevit\SheetFlow\profiles`.

### Not included yet
- Exporting sheets from linked models.
- Scheduled / unattended exports.
- Printing through a PDF printer driver (native export is used instead).
