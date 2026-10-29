"""FactorDesk smart-assistant core.

No Qt and no database in this file, so every part can be tested on its own:
  * Agent          – Gemini (free tier) tool-calling loop: understands free speech, asks questions, runs tools
  * WakeEngine     – always-on wake word + yes/no confirmation (Vosk, runs fully offline)
  * speaker helpers – voice-print comparison (Vosk speaker model)
  * model helpers  – download / locate Vosk models, check wake word vocabulary
"""
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
import zipfile

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash-lite"]
MODEL_URLS = {
    "fa": "https://alphacephei.com/vosk/models/vosk-model-small-fa-0.42.zip",
    "spk": "https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip",
}
SAMPLE_RATE = 16000

_TBL = str.maketrans({"ي": "ی", "ك": "ک", "‌": " ", "‏": None, "‎": None})


def norm(text):
    t = str(text or "").translate(_TBL).lower()
    t = t.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return re.sub(r"\s+", " ", t).strip()


# ───────────────────────── LLM agent (Gemini) ─────────────────────────
class AgentError(Exception):
    pass


def _friendly(code, body):
    low = body.lower()
    if code == 400 and "api key" in low: return "کلید Gemini نامعتبر است."
    if code in (401, 403): return "دسترسی به Gemini مجاز نیست (کلید نامعتبر یا منطقه‌ی شما پشتیبانی نمی‌شود)."
    if code == 429: return "سهمیه‌ی رایگان Gemini تمام شده یا درخواست‌ها زیاد است؛ کمی بعد دوباره امتحان کن."
    return f"خطای سرویس هوش مصنوعی ({code}): {body[:200]}"


def _post_json(url, payload, key, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Agent:
    """One conversation with the model. send_text() starts it, send_results() returns tool results.
    Each call returns dict(text=str, calls=[dict(name, args, id)])."""

    def __init__(self, api_key, model, system_prompt, tools, max_steps=8, url_template=GEMINI_URL, models=None):
        self.key = api_key
        self.models = [model] + [m for m in (models or DEFAULT_MODELS) if m != model]
        self.idx = 0
        self.system = system_prompt
        self.tools = tools
        self.max_steps = max_steps
        self.url_template = url_template
        self.contents = []
        self.steps = 0

    def _request(self):
        payload = {
            "systemInstruction": {"parts": [{"text": self.system}]},
            "contents": self.contents,
            "tools": [{"functionDeclarations": self.tools}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0.2},
        }
        while self.idx < len(self.models):
            url = self.url_template.format(model=self.models[self.idx])
            try:
                return _post_json(url, payload, self.key)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")
                missing = e.code in (400, 404) and ("not found" in body.lower() or "not supported" in body.lower())
                if missing and self.idx + 1 < len(self.models):
                    self.idx += 1
                    continue
                raise AgentError(_friendly(e.code, body))
            except urllib.error.URLError as e:
                raise AgentError("اتصال به اینترنت یا سرویس هوش مصنوعی برقرار نشد: " + str(getattr(e, "reason", e)))
        raise AgentError("هیچ‌کدام از مدل‌های هوش مصنوعی در دسترس نیست.")

    def _turn(self):
        self.steps += 1
        if self.steps > self.max_steps:
            raise AgentError("تعداد مراحل گفتگو از حد مجاز گذشت.")
        data = self._request()
        cands = data.get("candidates") or []
        if not cands:
            raise AgentError("پاسخی از هوش مصنوعی دریافت نشد (ممکن است درخواست مسدود شده باشد).")
        content = cands[0].get("content") or {"parts": []}
        content["role"] = "model"
        self.contents.append(content)          # keep the raw content (thought signatures must go back unchanged)
        parts = content.get("parts") or []
        text = "".join(p["text"] for p in parts if "text" in p and not p.get("thought")).strip()
        calls = []
        for p in parts:
            fc = p.get("functionCall")
            if fc:
                calls.append(dict(name=fc.get("name", ""), args=fc.get("args") or {}, id=fc.get("id")))
        return dict(text=text, calls=calls)

    def send_text(self, text):
        self.contents.append({"role": "user", "parts": [{"text": text}]})
        return self._turn()

    def send_results(self, results):
        """results: list of dict(name, response, id)"""
        parts = []
        for r in results:
            fr = {"name": r["name"], "response": r["response"]}
            if r.get("id"): fr["id"] = r["id"]
            parts.append({"functionResponse": fr})
        self.contents.append({"role": "user", "parts": parts})
        return self._turn()


def _obj(props, required=None):
    return {"type": "object", "properties": props, **({"required": required} if required else {})}


_ITEM = _obj({
    "name": {"type": "string", "description": "product name exactly as spoken"},
    "quantity": {"type": "number", "description": "quantity, default 1"},
    "unit_price": {"type": "number", "description": "price per unit in toman (digits). Omit if the user did not say it"},
    "description": {"type": "string"},
}, ["name"])
_KIND = {"type": "string", "enum": ["نقدی", "اعتباری", "چکی", "ترکیبی"]}

TOOL_ASK = {"name": "ask_user", "description": "Ask the user ONE short clarifying question in Persian and wait for the spoken answer. Use it whenever a required detail is missing or ambiguous. Never guess customers or prices.",
            "parameters": _obj({"question": {"type": "string"}}, ["question"])}

TOOL_CONFIRM = {"name": "confirm_user", "description": "Ask a short YES/NO question in Persian and wait for the spoken answer (result has confirmed=true/false). Use it to check your best guess when the request is unclear ('منظورتان … است؟'), before teaching, or before anything risky.",
                "parameters": _obj({"question": {"type": "string"}}, ["question"])}
TOOL_TEACH_ALIAS = {"name": "teach_alias", "description": "Remember that when the owner says `spoken` they mean `meaning` (an exact customer or product name). Ask confirm_user first.",
                    "parameters": _obj({"kind": {"type": "string", "enum": ["customer", "product"]}, "spoken": {"type": "string"}, "meaning": {"type": "string"}}, ["kind", "spoken", "meaning"])}
TOOL_TEACH_RULE = {"name": "teach_rule", "description": "Remember a standing rule or preference the owner stated in plain words (for example default payment kind for company customers). Ask confirm_user first.",
                   "parameters": _obj({"rule": {"type": "string"}}, ["rule"])}
TOOLS_BASE = [TOOL_ASK, TOOL_CONFIRM, TOOL_TEACH_ALIAS, TOOL_TEACH_RULE]


def fields_tool(schema, description):
    """schema: {key: dict(desc, type='string'|'number', enum=[...]?)} -> one `set_fields` tool whose parameters are those keys.
    Every form field that is declared in the app's schema becomes a voice-settable parameter."""
    props = {}
    for k, v in schema.items():
        p = {"type": v.get("type", "string"), "description": v.get("desc", "")}
        if v.get("enum"): p["enum"] = list(v["enum"])
        props[k] = p
    return {"name": "set_fields", "description": description, "parameters": _obj(props)}


_PAY_METHODS = ["نقدی", "کارتخوان", "کارت‌به‌کارت", "حواله", "واریز بانکی", "چک", "سایر"]
_CHQ_STATUSES = ["نزد ما", "در انتظار وصول", "وصول‌شده", "برگشتی", "مستردشده"]
_J = "Jalali date as digits YYYY/MM/DD"
# Every field that can be filled by voice. A new field = one line here + one setter in main.py (form_setters).
FORM_FIELDS = {
    "customer": {"name": {"desc": "customer / shop name"}, "phone": {"desc": "phone number"}, "address": {"desc": "address"}, "notes": {"desc": "notes"}},
    "payment": {"customer_name": {"desc": "customer as spoken"}, "related_invoice_no": {"desc": "number of the invoice this money is for"},
                "date": {"desc": _J}, "amount": {"type": "number", "desc": "amount in toman"}, "method": {"desc": "payment method", "enum": _PAY_METHODS},
                "reference": {"desc": "tracking / reference number"}, "notes": {"desc": "notes"}},
    "cheque": {"customer_name": {"desc": "customer as spoken"}, "related_invoice_no": {"desc": "related invoice number"}, "cheque_no": {"desc": "cheque number"},
               "bank": {"desc": "bank name"}, "issuer": {"desc": "cheque issuer"}, "amount": {"type": "number", "desc": "amount in toman"},
               "due_date": {"desc": _J + " (cheque due date)"}, "status": {"desc": "status", "enum": _CHQ_STATUSES}, "notes": {"desc": "notes"}},
    "invoice": {"customer_name": {"desc": "customer as spoken"}, "date": {"desc": "invoice date, " + _J}, "kind": {"desc": "payment kind", "enum": ["نقدی", "اعتباری", "چکی", "ترکیبی"]},
                "due_date": {"desc": "due date, " + _J}, "description": {"desc": "general description of the invoice"},
                "discount": {"type": "number", "desc": "discount in toman"}, "paid_amount": {"type": "number", "desc": "amount paid up front in toman"},
                "notes": {"desc": "internal note"}},
}


def open_form_tool():
    """open_form(form, ...every field of customer/payment/cheque...) so a spoken sentence can open AND fill a form in one call."""
    union = {}
    for kind in ("customer", "payment", "cheque"):
        union.update(FORM_FIELDS[kind])
    t = fields_tool(union, "Open a NEW form and pre-fill it. form = customer | payment (money received from a customer) | cheque.")
    t["name"] = "open_form"
    t["parameters"]["properties"] = {"form": {"type": "string", "enum": ["customer", "payment", "cheque"]}, **t["parameters"]["properties"]}
    t["parameters"]["required"] = ["form"]
    return t


TOOL_ENTRY = {"name": "start_item_entry", "description": "Start guided row-by-row entry: the assistant asks 'row N – product name?' repeatedly, adds each spoken product, suggests the closest known product when unsure (the user answers yes/no) and asks the price when unknown, until the user says 'تمام'. Use it when the user says things like 'start from the first row, I will say the items one by one'.",
              "parameters": _obj({})}

TOOLS_MAIN = [
    *TOOLS_BASE,
    {"name": "open_invoice", "description": "Open a NEW sales invoice (or proforma) window, optionally filled in. The user then continues in that window.",
     "parameters": _obj({"customer_name": {"type": "string", "description": "customer exactly as spoken; omit for the default (cash) customer"},
                         "proforma": {"type": "boolean"}, "kind": _KIND,
                         "items": {"type": "array", "items": _ITEM}, "discount": {"type": "number"}, "due_days": {"type": "integer"}})},
    {"name": "customer_balance", "description": "Show the account balance of a customer on screen.", "parameters": _obj({"customer_name": {"type": "string"}}, ["customer_name"])},
    {"name": "open_statement", "description": "Open the account statement of a customer.", "parameters": _obj({"customer_name": {"type": "string"}}, ["customer_name"])},
    {"name": "create_customer", "description": "Create a new customer. Only after the user agreed.", "parameters": _obj({"name": {"type": "string"}}, ["name"])},
    {"name": "go_to_tab", "description": "Switch the main window to a section.",
     "parameters": _obj({"tab": {"type": "string", "enum": ["invoices", "proformas", "customers", "payments", "cheques", "reports", "settings"]}}, ["tab"])},
    {"name": "show_overdue", "description": "Show the overdue invoices list on screen.", "parameters": _obj({})},
    open_form_tool(),
    {"name": "search_invoices", "description": "Filter the invoices list by text (number or customer) and/or Jalali year/month/day.",
     "parameters": _obj({"text": {"type": "string"}, "year": {"type": "integer"}, "month": {"type": "integer"}, "day": {"type": "integer"}})},
    {"name": "open_invoice_by_number", "description": "Do something with an existing invoice or proforma by its number.",
     "parameters": _obj({"number": {"type": "string"}, "action": {"type": "string", "enum": ["view", "print", "receipt", "share", "pdf"]}}, ["number", "action"])},
    {"name": "quick_info", "description": "Show a quick fact on screen (not sent to you).",
     "parameters": _obj({"what": {"type": "string", "enum": ["overdue", "total_debts", "cheques_due_soon", "today_sales", "month_sales"]}}, ["what"])},
    {"name": "backup_now", "description": "Make a database backup right now.", "parameters": _obj({})},
]
TOOL_REPORT_NAME = "run_report"

TOOLS_INVOICE = [
    *TOOLS_BASE, TOOL_ENTRY,
    {"name": "add_items", "description": "Add one or more rows to the invoice that is open.", "parameters": _obj({"items": {"type": "array", "items": _ITEM}}, ["items"])},
    {"name": "set_customer", "description": "Choose the customer of the open invoice.", "parameters": _obj({"customer_name": {"type": "string"}}, ["customer_name"])},
    {"name": "create_customer", "description": "Create a new customer and select it. Only after the user agreed.", "parameters": _obj({"name": {"type": "string"}}, ["name"])},
    {"name": "set_discount", "description": "Set the discount amount in toman.", "parameters": _obj({"amount": {"type": "number"}}, ["amount"])},
    {"name": "set_kind", "description": "Set payment kind of the invoice.", "parameters": _obj({"kind": _KIND}, ["kind"])},
    {"name": "set_due_days", "description": "Set the due date to N days from today.", "parameters": _obj({"days": {"type": "integer"}}, ["days"])},
    {"name": "remove_last_item", "description": "Remove the last row.", "parameters": _obj({})},
    {"name": "save_invoice", "description": "Save the invoice (the app asks the user to confirm by voice).", "parameters": _obj({"print_after": {"type": "boolean"}})},
]

TOOLS_FORM = [
    *TOOLS_BASE,
    {"name": "save_form", "description": "Save the open form (the app asks the user to confirm by voice).", "parameters": _obj({})},
    {"name": "cancel_form", "description": "Close the open form without saving.", "parameters": _obj({})},
]


def report_tool(reports):
    """reports: [(key, label)] -> run_report tool listing every report."""
    return {"name": "run_report", "description": "Show a report in the Reports tab. Reports: " + "; ".join(f"{k} = {l}" for k, l in reports),
            "parameters": _obj({"report": {"type": "string", "enum": [k for k, _ in reports]}, "customer_name": {"type": "string"},
                                "from_date": {"type": "string", "description": "Jalali YYYY/MM/DD"}, "to_date": {"type": "string", "description": "Jalali YYYY/MM/DD"}}, ["report"])}


SYSTEM_PROMPT = """You are the voice assistant inside FactorDesk, a Persian (Farsi) invoicing app for a small shop.
Today is {today} (Jalali {today_j}). Amounts are in toman. The user speaks Persian; you reply in short, polite Persian.
Context: {context}
Rules:
- Use the tools to act. Never invent customers, products or prices. If something needed is missing or ambiguous, call ask_user with one short question.
- Convert spoken numbers to digits: "هشتصد و پنجاه هزار" = 850000. A price under 1000 means thousands of toman ("هشتصد" = 800000).
- When a tool returns an error (not_found / ambiguous), ask the user how to continue (candidates are listed in the result).
- If you are not sure what the user means, do NOT guess silently: propose your best interpretation with confirm_user (a yes/no question). On "no", ask what they meant.
- Forms and invoices have many fields: use set_fields for all of them (dates as Jalali YYYY/MM/DD digits, money as digits in toman).
- If the user wants to enter products one by one, call start_item_entry. If the user teaches you something ("from now on…", "when I say X I mean Y") confirm it and call teach_rule / teach_alias.
- Do several actions in one go when the user asked for several. Do not ask for confirmation of things the user already said.
- After the work is done answer with ONE short Persian sentence saying what was done (no markdown).
{notes}"""


def build_system_prompt(context, today, today_j, notes=""):
    n = ("\nThings the owner taught you (follow them):\n" + notes.strip()[:2000]) if notes and notes.strip() else ""
    return SYSTEM_PROMPT.format(context=context, today=today, today_j=today_j, notes=n)


# ───────────────────────── wake word engine (Vosk) ─────────────────────────
class WakeEngine:
    """Feeds 16 kHz mono 16-bit PCM chunks to Vosk recognisers restricted to a tiny vocabulary:
    idle  -> listens only for the wake word (everything else is discarded, nothing leaves the PC)
    confirm -> after the wake word, listens for yes / no.
    feed() returns a list of events: 'wake', 'yes', 'no', 'timeout'.
    The audio since the wake word is kept (audio_since_wake) so the speaker check can use it."""

    def __init__(self, make_wake, make_confirm, wake_words, yes_words, no_words, confirm_timeout=8.0, ring_seconds=4.0):
        self.make_wake, self.make_confirm = make_wake, make_confirm
        self.wake_words = [norm(w) for w in wake_words if norm(w)]
        self.yes_words = [norm(w) for w in yes_words]
        self.no_words = [norm(w) for w in no_words]
        self.confirm_timeout = confirm_timeout
        self.ring_bytes = int(ring_seconds * SAMPLE_RATE * 2)
        self.wake_rec = make_wake()
        self.confirm_rec = None
        self.state = "idle"
        self.ring = bytearray()
        self.since_wake = bytearray()
        self.t_confirm = 0.0

    @staticmethod
    def _heard(rec, pcm):
        if rec.AcceptWaveform(pcm):
            txt = json.loads(rec.Result()).get("text", "")
        else:
            txt = json.loads(rec.PartialResult()).get("partial", "")
        return norm(txt)

    def reset(self):
        self.state = "idle"
        self.wake_rec = self.make_wake()
        self.confirm_rec = None
        self.ring = bytearray()
        self.since_wake = bytearray()

    def audio_since_wake(self):
        return bytes(self.since_wake)

    def feed(self, pcm):
        events = []
        if not pcm:
            return events
        dur = len(pcm) / (2.0 * SAMPLE_RATE)
        if self.state == "idle":
            self.ring += pcm
            if len(self.ring) > self.ring_bytes:
                del self.ring[: len(self.ring) - self.ring_bytes]
            toks = self._heard(self.wake_rec, pcm).split()
            if any(w in toks for w in self.wake_words):
                events.append("wake")
                self.state = "confirm"
                self.since_wake = bytearray(self.ring)
                self.t_confirm = 0.0
                self.confirm_rec = self.make_confirm()
                self.wake_rec = self.make_wake()
        else:
            self.since_wake += pcm
            self.t_confirm += dur
            toks = self._heard(self.confirm_rec, pcm).split()
            if any(w in toks for w in self.yes_words):
                events.append("yes"); self.state = "idle"
            elif any(w in toks for w in self.no_words):
                events.append("no"); self.state = "idle"
            elif self.t_confirm > self.confirm_timeout:
                events.append("timeout"); self.state = "idle"
        return events


# ───────────────────────── speaker check ─────────────────────────
def cosine_distance(a, b):
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 1.0
    return 1.0 - sum(x * y for x, y in zip(a, b)) / (na * nb)


def average_vectors(vectors):
    vectors = [v for v in vectors if v]
    if not vectors:
        return None
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


def speaker_vector(model, spk_model, pcm, recognizer_cls):
    """Voice-print (x-vector) of a PCM clip using Vosk's speaker model; None when the clip is too short."""
    if len(pcm) < SAMPLE_RATE * 2 * 1.2:
        return None
    rec = recognizer_cls(model, SAMPLE_RATE)
    rec.SetSpkModel(spk_model)
    for i in range(0, len(pcm), 8000):
        rec.AcceptWaveform(pcm[i:i + 8000])
    return json.loads(rec.FinalResult()).get("spk")


def speaker_ok(vector, enrolled, threshold):
    """Returns (ok, distance)."""
    if not vector or not enrolled:
        return False, 1.0
    d = cosine_distance(vector, enrolled)
    return d <= threshold, d


def phrase_hash(text, salt):
    return hashlib.sha256((salt + norm(text)).encode("utf-8")).hexdigest()


def check_phrase(text, salt, stored_hash):
    t = norm(text)
    if not t or not stored_hash:
        return False
    if phrase_hash(t, salt) == stored_hash:
        return True
    # the recogniser may add a short filler word around the secret; try every contiguous word window
    w = t.split()
    return any(phrase_hash(" ".join(w[i:j]), salt) == stored_hash for i in range(len(w)) for j in range(i + 1, len(w) + 1))


# ───────────────────────── Vosk model helpers ─────────────────────────
def find_model_dir(base, prefix):
    """Folder inside base whose name starts with prefix and looks like a Vosk model."""
    if not os.path.isdir(base):
        return ""
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if name.startswith(prefix) and os.path.isdir(p) and (os.path.isdir(os.path.join(p, "am")) or os.path.isdir(os.path.join(p, "conf")) or os.path.isfile(os.path.join(p, "mfcc.conf"))):
            return p
    return ""


def download_and_extract(url, dest_dir, progress=None, timeout=60):
    os.makedirs(dest_dir, exist_ok=True)
    tmp = os.path.join(dest_dir, "download.tmp")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk); done += len(chunk)
                if progress: progress(done, total)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest_dir)
    finally:
        try: os.remove(tmp)
        except OSError: pass


def wake_word_in_vocab(model_dir, word):
    """Is the wake word known to the Persian model? (Vosk silently drops words it does not know.)"""
    path = os.path.join(model_dir, "graph", "words.txt")
    if not os.path.isfile(path):
        return None   # cannot tell
    target = norm(word)
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if norm(line.split(" ")[0]) == target:
                return True
    return False
