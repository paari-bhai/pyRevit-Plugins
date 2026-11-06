# -*- coding: utf-8 -*-
"""Estimated time remaining for an export run.

Seconds per sheet are learned per format while exporting. Before the first
file of a format finishes, the average from earlier runs on this machine is
used (stored per model, with an all-models fallback).
"""
import json
import os
import time

from sheetflow.settings import FORMATS, profiles_dir

HISTORY_FILE = "timings.json"
ALL_MODELS = "_all"


def _history_path():
    return os.path.join(os.path.dirname(profiles_dir()), HISTORY_FILE)


def load_history():
    try:
        with open(_history_path(), "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_history(history, model, observed):
    """Blend this run's seconds-per-item into the stored averages."""
    for key in (model, ALL_MODELS):
        rates = history.setdefault(key, {})
        for fmt, rate in observed.items():
            old = rates.get(fmt)
            rates[fmt] = rate if old is None else round(0.5 * old + 0.5 * rate, 3)
    try:
        with open(_history_path(), "w") as f:
            json.dump(history, f, indent=2, sort_keys=True)
    except Exception:
        pass


def applies(fmt, item):
    if fmt == "NWC":
        return item.is_3d
    if fmt == "IFC":
        return not item.is_sheet
    return True


def plan(items, cfg):
    """[(fmt, units)] in the order the runner ticks, one entry per progress step.

    units = how many sheets/views that step's time covers (0 for steps that
    will just be skipped, n for a combined file).
    """
    steps = []
    for fmt in FORMATS:
        if not cfg["enabled"].get(fmt):
            continue
        if fmt == "XML":
            steps.append((fmt, 0))          # metadata only, effectively instant
        elif fmt in ("PDF", "DWF") and cfg[fmt.lower()].get("combine"):
            steps.append((fmt, len(items)))
        else:
            steps.extend((fmt, 1 if applies(fmt, i) else 0) for i in items)
    return steps


def fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "under a minute" if seconds > 20 else "a few seconds"
    if seconds < 3600:
        return "about {0} min".format(int(round(seconds / 60.0)))
    h, m = divmod(int(round(seconds / 60.0)), 60)
    return "about {0} h {1:02d} min".format(h, m)


class Estimator(object):
    def __init__(self, steps, history, model):
        self.steps = steps
        self.prior = dict(history.get(ALL_MODELS, {}))
        self.prior.update(history.get(model, {}))
        self.secs = {}
        self.units = {}
        self.pos = 0
        self.last = None

    def start(self):
        self.last = time.time()

    def mark(self, fmt, ok=True):
        """Record that the next planned step finished."""
        now = time.time()
        if self.pos < len(self.steps):
            pfmt, units = self.steps[self.pos]
            self.pos += 1
            if ok and units:
                self.secs[pfmt] = self.secs.get(pfmt, 0.0) + (now - self.last)
                self.units[pfmt] = self.units.get(pfmt, 0) + units
        self.last = now

    def rate(self, fmt):
        if self.units.get(fmt):
            return self.secs[fmt] / self.units[fmt]
        if fmt in self.prior:
            return self.prior[fmt]
        done_units = sum(self.units.values())
        if done_units:
            return sum(self.secs.values()) / done_units
        return None

    def remaining(self):
        """Seconds left, or None if there is nothing to go on yet."""
        total = 0.0
        for fmt, units in self.steps[self.pos:]:
            if not units:
                continue
            r = self.rate(fmt)
            if r is None:
                return None
            total += r * units
        return total

    def observed(self):
        return dict((f, self.secs[f] / self.units[f]) for f in self.secs if self.units.get(f))

    def text(self):
        rem = self.remaining()
        if rem is None:
            return "estimating time left..."
        if rem <= 0:
            return "finishing"
        finish = time.localtime(time.time() + rem)
        return "{0} left (done around {1})".format(fmt_duration(rem),
                                                   time.strftime("%H:%M", finish))
