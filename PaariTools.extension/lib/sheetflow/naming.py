# -*- coding: utf-8 -*-
"""File name templates: '{Sheet Number}-{Sheet Name}' style tokens."""
import re

DEFAULT_SHEET_TEMPLATE = "{Sheet Number}-{Sheet Name}"
DEFAULT_VIEW_TEMPLATE = "{View Type}-{View Name}"

TOKEN_RE = re.compile(r"\{([^{}]+)\}")
INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

COMMON_TOKENS = [
    "Sheet Number", "Sheet Name", "View Name", "View Type", "Current Revision",
    "Current Revision Date", "Current Revision Description", "Size", "Orientation",
    "Project Number", "Project Name", "Model Name",
]


RESERVED = set(["CON", "PRN", "AUX", "NUL"] + ["COM%d" % i for i in range(1, 10)] +
               ["LPT%d" % i for i in range(1, 10)])
MAX_PATH = 250  # Windows MAX_PATH is 260 including the extension and terminator


def sanitize(name):
    name = INVALID_CHARS_RE.sub("_", name or "")
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.upper() in RESERVED:
        name += "_"
    return name or "unnamed"


def fit_path(folder, name, ext):
    """Shorten name so folder/name+ext stays under the Windows path limit."""
    room = MAX_PATH - len(folder) - 1 - len(ext) - 8  # 8 = room for " (n)" and xref suffixes
    if room < 10:
        raise ValueError("Output folder path is too long: {0}".format(folder))
    if len(name) > room:
        name = name[:room].rstrip(". ")
    return name


def render(template, item):
    def repl(match):
        return item.value_for(match.group(1)) or ""
    return sanitize(TOKEN_RE.sub(repl, template))


def file_name_for(item, sheet_template, view_template):
    """Custom name typed in the grid wins, else the template."""
    if item.custom_name and item.custom_name.strip():
        return sanitize(item.custom_name)
    template = sheet_template if item.is_sheet else view_template
    return render(template, item)


def unique_name(name, used):
    """Avoid two items writing the same file name in one run."""
    candidate = name
    n = 2
    while candidate.lower() in used:
        candidate = "{0} ({1})".format(name, n)
        n += 1
    used.add(candidate.lower())
    return candidate
