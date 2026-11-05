# -*- coding: utf-8 -*-
"""Runs an export job: every checked item x every enabled format."""
import datetime
import io
import os

from sheetflow import exporters, naming
from sheetflow.settings import FORMATS


class Result(object):
    def __init__(self, item, fmt, ok, message, paths=None):
        self.item = item
        self.fmt = fmt
        self.ok = ok
        self.message = message
        self.paths = paths or []


def output_folder(cfg, fmt):
    root = os.path.expandvars(os.path.expanduser(cfg["create"]["folder"].strip()))
    folder = os.path.join(root, fmt) if cfg["create"]["split_by_format"] else root
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


def count_steps(items, cfg):
    total = 0
    for fmt in FORMATS:
        if not cfg["enabled"].get(fmt):
            continue
        if fmt == "XML" or (fmt in ("PDF", "DWF") and cfg[fmt.lower()].get("combine")):
            total += 1
        else:
            total += len(items)
    return total


def run(doc, items, cfg, progress=None):
    """Export items. progress(step, total, item, fmt, text) may return False to cancel."""
    results = []
    total = count_steps(items, cfg)
    step = [0]
    sheet_t = cfg["naming"]["sheet_template"]
    view_t = cfg["naming"]["view_template"]

    def tick(item, fmt, text):
        step[0] += 1
        if progress is not None:
            return progress(step[0], total, item, fmt, text) is not False
        return True

    for fmt in FORMATS:
        if not cfg["enabled"].get(fmt):
            continue
        fcfg = cfg[fmt.lower()]
        folder = output_folder(cfg, fmt)
        used = set()

        combine = fmt in ("PDF", "DWF") and fcfg.get("combine")
        if fmt == "XML" or combine:
            template = fcfg.get("combined_name") or "{Model Name}-Export"
            name = naming.render(template, items[0]) if items else "Export"
            if fmt == "XML":
                name = naming.sanitize("{0}-{1}".format(
                    doc.Title, datetime.datetime.now().strftime("%Y%m%d-%H%M")))
            fn = {"PDF": exporters.export_pdf_combined, "DWF": exporters.export_dwf_combined,
                  "XML": exporters.export_xml}[fmt]
            try:
                paths = fn(doc, folder, items, name, fcfg)
                for i in items:
                    results.append(Result(i, fmt, True, "Done", paths))
                status = "Done"
            except exporters.ExportSkipped as ex:
                results.extend(Result(i, fmt, False, "Skipped: {0}".format(ex)) for i in items)
                status = "Skipped"
            except Exception as ex:
                results.extend(Result(i, fmt, False, "Failed: {0}".format(ex)) for i in items)
                status = "Failed"
            if not tick(None, fmt, status):
                return results, True
            continue

        fn = exporters.PER_ITEM[fmt]
        for item in items:
            name = naming.unique_name(naming.file_name_for(item, sheet_t, view_t), used)
            try:
                paths = fn(doc, folder, item, name, fcfg)
                results.append(Result(item, fmt, True, "Done", paths))
                status = "Done"
            except exporters.ExportSkipped as ex:
                results.append(Result(item, fmt, False, "Skipped: {0}".format(ex)))
                status = "Skipped"
            except Exception as ex:
                results.append(Result(item, fmt, False, "Failed: {0}".format(ex)))
                status = "Failed"
            if not tick(item, fmt, status):
                return results, True
    return results, False


def write_report(cfg, results):
    root = os.path.expandvars(os.path.expanduser(cfg["create"]["folder"].strip()))
    path = os.path.join(root, "SheetFlow-report-{0}.csv".format(
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    def cell(v):
        v = u"{0}".format(v if v is not None else "")
        if any(c in v for c in u',"\n'):
            v = u'"' + v.replace(u'"', u'""') + u'"'
        return v

    rows = [["Type", "Number", "Name", "Revision", "Format", "Result", "Files"]]
    for r in results:
        rows.append([r.item.kind, r.item.number, r.item.name, r.item.revision, r.fmt,
                     r.message, "; ".join(r.paths)])
    # utf-8 with BOM so Excel shows non-ASCII sheet names correctly
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        for row in rows:
            f.write(u",".join(cell(v) for v in row) + u"\r\n")
    return path
