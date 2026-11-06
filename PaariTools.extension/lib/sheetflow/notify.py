# -*- coding: utf-8 -*-
"""Email status updates for long exports.

Mail is sent on a background thread (it never touches the Revit API), so a slow
mail server can't hold up the export. The SMTP password is encrypted with
Windows DPAPI for the current Windows user and never stored in plain text.
"""
import os
import time

import clr
clr.AddReference("System")
clr.AddReference("System.Security")
from System.IO import File
from System.Net import NetworkCredential, ServicePointManager, SecurityProtocolType
from System.Net.Mail import SmtpClient, MailMessage
from System.Security.Cryptography import ProtectedData, DataProtectionScope
from System.Text import Encoding
from System.Threading import Thread, ThreadStart

from sheetflow.settings import profiles_dir

PRESETS = {
    "Gmail": ("smtp.gmail.com", 587, True),
    "Outlook / Office 365": ("smtp.office365.com", 587, True),
    "Outlook.com / Hotmail": ("smtp-mail.outlook.com", 587, True),
    "Custom": ("", 587, True),
}
FREQUENCIES = ["Start and finish", "Every 25%", "Every 10%", "Every sheet"]

_ENTROPY = Encoding.UTF8.GetBytes("SheetFlow-smtp")


def _secret_path():
    return os.path.join(os.path.dirname(profiles_dir()), "smtp.bin")


def save_password(password):
    data = ProtectedData.Protect(Encoding.UTF8.GetBytes(password or ""), _ENTROPY,
                                 DataProtectionScope.CurrentUser)
    File.WriteAllBytes(_secret_path(), data)


def load_password():
    path = _secret_path()
    if not os.path.exists(path):
        return ""
    try:
        data = File.ReadAllBytes(path)
        return Encoding.UTF8.GetString(
            ProtectedData.Unprotect(data, _ENTROPY, DataProtectionScope.CurrentUser))
    except Exception:
        return ""


def has_password():
    return os.path.exists(_secret_path())


def send_now(ncfg, subject, body):
    """Send synchronously. Raises on failure (used by the test button)."""
    try:
        ServicePointManager.SecurityProtocol = ServicePointManager.SecurityProtocol | \
            SecurityProtocolType.Tls12
    except Exception:
        pass
    client = SmtpClient(ncfg["smtp_host"], int(ncfg["smtp_port"]))
    client.EnableSsl = bool(ncfg.get("ssl", True))
    client.Timeout = 30000
    user = ncfg.get("username") or ncfg["to"]
    client.Credentials = NetworkCredential(user, load_password())
    msg = MailMessage(ncfg.get("from") or user, ncfg["to"], subject, body)
    try:
        client.Send(msg)
    finally:
        msg.Dispose()
        client.Dispose()


def send_async(ncfg, subject, body, on_error=None):
    def work():
        try:
            send_now(ncfg, subject, body)
        except Exception as ex:
            if on_error is not None:
                try:
                    on_error(ex)
                except Exception:
                    pass
    t = Thread(ThreadStart(work))
    t.IsBackground = True
    t.Start()
    return t


def is_configured(ncfg):
    return bool(ncfg.get("enabled") and ncfg.get("to") and ncfg.get("smtp_host")
                and has_password())


class ExportNotifier(object):
    """Decides when to email during a run and builds the messages."""

    def __init__(self, ncfg, model, total):
        self.ncfg = ncfg
        self.model = model
        self.total = max(total, 1)
        self.active = is_configured(ncfg)
        self.started = time.time()
        self.lines = []          # "PDF  A101 - Plan: Done (12.3s)"
        self.failures = []
        self.next_pct = self._step_pct()
        self.errors = []
        self.threads = []

    def _step_pct(self):
        freq = self.ncfg.get("frequency", "Every 25%")
        return {"Every 25%": 25, "Every 10%": 10, "Every sheet": 0}.get(freq)

    def _send(self, subject, body):
        if not self.active:
            return
        self.threads.append(send_async(self.ncfg, subject, body, self.errors.append))

    def _elapsed(self):
        s = int(time.time() - self.started)
        return "{0}m {1:02d}s".format(s // 60, s % 60)

    def started_run(self, n_items, formats, eta_text):
        self._send(u"SheetFlow started: {0} item(s) - {1}".format(n_items, self.model),
                   u"Export started for {0}\n\nItems: {1}\nFormats: {2}\nEstimate: {3}\n".format(
                       self.model, n_items, ", ".join(formats), eta_text))

    def step(self, step, label, text, eta_text):
        line = u"{0}: {1}".format(label, text)
        self.lines.append(line)
        if text.startswith("Failed"):
            self.failures.append(line)
        if self.next_pct is None:
            return
        pct = 100.0 * step / self.total
        if step >= self.total:
            return  # the finish email covers it
        if self.next_pct == 0 or pct >= self.next_pct:
            if self.next_pct:
                while self.next_pct <= pct:
                    self.next_pct += self._step_pct()
            recent = self.lines[-10:]
            self._send(
                u"SheetFlow {0:.0f}% ({1}/{2}) - {3}".format(pct, step, self.total, eta_text),
                u"{0}\n\nProgress: {1} of {2} ({3:.0f}%)\nElapsed: {4}\nTime left: {5}\n"
                u"Failures so far: {6}\n\nLatest:\n{7}\n".format(
                    self.model, step, self.total, pct, self._elapsed(), eta_text,
                    len(self.failures), u"\n".join(recent)))

    def finished(self, summary, cancelled=False, crashed=None):
        if crashed is not None:
            subject = u"SheetFlow FAILED - {0}".format(self.model)
        elif cancelled:
            subject = u"SheetFlow stopped - {0}".format(self.model)
        elif self.failures:
            subject = u"SheetFlow done with {0} failure(s) - {1}".format(
                len(self.failures), self.model)
        else:
            subject = u"SheetFlow done - {0}".format(self.model)
        body = u"{0}\n\n{1}\nElapsed: {2}\n".format(self.model, summary, self._elapsed())
        if crashed is not None:
            body += u"\nError: {0}\n".format(crashed)
        if self.failures:
            body += u"\nFailures:\n" + u"\n".join(self.failures[:50]) + u"\n"
        body += u"\nAll items:\n" + u"\n".join(self.lines[:500]) + u"\n"
        self._send(subject, body)

    def wait(self, seconds=20):
        """Give queued mails a moment to go out before the window closes."""
        deadline = time.time() + seconds
        for t in self.threads:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            t.Join(int(remaining * 1000))
