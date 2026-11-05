# -*- coding: utf-8 -*-
"""Runs an export job: every checked item x every enabled format."""
import datetime
import io
import os
import time

from sheetflow import exporters, naming
from sheetflow.settings import FORMATS


class Result(object):
    def __init__(self, item, fmt, ok, message, paths=None, seconds=0.0):
        self.item = item
        self.fmt = fmt
        self.ok = ok
        self.message = message
        self.paths = paths or []
        self.seconds = seconds


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
    exporters.UNSUPPORTED.clear()
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
            try:
                name = naming.fit_path(folder, name, exporters.EXTENSIONS.get(fmt, ""))
            except ValueError:
                pass
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
        names = {}
        for item in items:
            try:
                ext = exporters.EXTENSIONS.get(fmt, "")
                name = naming.fit_path(folder, naming.file_name_for(item, sheet_t, view_t), ext)
                names[id(item)] = naming.unique_name(name, used)
            except Exception as ex:
                names[id(item)] = ex

        pending = list(items)
        if fmt == "PDF" and fcfg.get("batch", True) and exporters.pdf_batch_supported():
            pending, cancelled = _run_pdf_batches(doc, folder, items, names, fcfg, results, tick)
            if cancelled:
                return results, True

        for item in pending:
            started = time.time()
            try:
                name = names[id(item)]
                if isinstance(name, Exception):
                    raise name
                paths = fn(doc, folder, item, name, fcfg)
                secs = time.time() - started
                results.append(Result(item, fmt, True, "Done", paths, secs))
                status = "Done ({0:.1f}s)".format(secs)
            except exporters.ExportSkipped as ex:
                results.append(Result(item, fmt, False, "Skipped: {0}".format(ex)))
                status = "Skipped"
            except Exception as ex:
                results.append(Result(item, fmt, False, "Failed: {0}".format(ex),
                                      seconds=time.time() - started))
                status = "Failed"
            if not tick(item, fmt, status):
                return results, True
    return results, False


def _run_pdf_batches(doc, folder, items, names, fcfg, results, tick):
    """Export sheets in batches grouped by paper settings.

    Returns (items still to export one by one, cancelled).
    """
    leftovers = [i for i in items if not i.is_sheet or isinstance(names[id(i)], Exception)]
    groups = {}
    order = []
    for i in items:
        if i in leftovers:
            continue
        key = (i.paper_size, i.paper_orientation)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    for key in order:
        group = groups[key]
        for start in range(0, len(group), exporters.PDF_BATCH_SIZE):
            chunk = group[start:start + exporters.PDF_BATCH_SIZE]
            chunk_names = [names[id(i)] for i in chunk]
            t0 = time.time()
            try:
                done = exporters.export_pdf_batch(doc, folder, chunk, chunk_names, fcfg)
            except Exception:
                done = {}
            per = (time.time() - t0) / max(len(done), 1)
            for idx, item in enumerate(chunk):
                if idx in done:
                    results.append(Result(item, "PDF", True, "Done", [done[idx]], per))
                    if not tick(item, "PDF", "Done ({0:.1f}s, batched)".format(per)):
                        return [], True
                else:
                    leftovers.append(item)
    if leftovers and len(leftovers) < len(items):
        exporters.UNSUPPORTED.add(
            "PDF batch: {0} item(s) exported one by one instead".format(len(leftovers)))
    return leftovers, False


def write_report(cfg, results):
    root = os.path.expandvars(os.path.expanduser(cfg["create"]["folder"].strip()))
    path = os.path.join(root, "SheetFlow-report-{0}.csv".format(
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    def cell(v):
        v = u"{0}".format(v if v is not None else "")
        if any(c in v for c in u',"\n'):
            v = u'"' + v.replace(u'"', u'""') + u'"'
        return v

    rows = [["Type", "Number", "Name", "Revision", "Format", "Result", "Seconds", "Files"]]
    for r in results:
        rows.append([r.item.kind, r.item.number, r.item.name, r.item.revision, r.fmt,
                     r.message, "{0:.1f}".format(r.seconds), "; ".join(r.paths)])
    # utf-8 with BOM so Excel shows non-ASCII sheet names correctly
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        for row in rows:
            f.write(u",".join(cell(v) for v in row) + u"\r\n")
    return path
