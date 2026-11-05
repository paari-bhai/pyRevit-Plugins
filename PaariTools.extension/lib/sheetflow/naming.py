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


def sanitize(name):
    name = INVALID_CHARS_RE.sub("_", name or "")
    name = name.strip().rstrip(".")
    return name or "unnamed"


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
