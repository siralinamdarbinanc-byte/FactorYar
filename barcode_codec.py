# -*- coding: utf-8 -*-
"""رمزگذاری بارکد بدون کتابخانه‌ی خارجی: EAN-13، Code 128 (خودکار B/C) و Code 39. خروجی: رشته‌ی ماژول‌ها ('1' = نوار مشکی، '0' = سفید)."""

_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PAR = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]

def ean13_check(d12):
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d12))
    return str((10 - s % 10) % 10)

def ean13_valid(code):
    return len(code) == 13 and code.isdigit() and ean13_check(code[:12]) == code[12]

def encode_ean13(code):
    if not ean13_valid(code): raise ValueError("EAN-13 باید ۱۳ رقم با رقم کنترلِ درست باشد.")
    par = _PAR[int(code[0])]
    left = "".join((_L if par[i] == "L" else _G)[int(c)] for i, c in enumerate(code[1:7]))
    return "101" + left + "01010" + "".join(_R[int(c)] for c in code[7:]) + "101"

_C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
         "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
         "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
         "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
         "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
         "114131 311141 411131 211412 211214 211232 2331112").split()

def _mods(widths):
    out = ""; bar = True
    for w in widths: out += ("1" if bar else "0") * int(w); bar = not bar
    return out

def code128_values(text):
    """مقدارهای نمادهای Code 128 (شروع + داده + کنترل). اعداد زوجِ پشت‌سرهم با Subset C فشرده می‌شوند (کوتاه‌تر و برای بارکدخوان سریع‌تر)."""
    if not text or any(not (32 <= ord(c) <= 126) for c in text): raise ValueError("Code 128 فقط حروف انگلیسی، عدد و علائم ساده را می‌پذیرد.")
    vals = []; i = 0; mode = None; n = len(text)
    def digits_run(j):
        k = j
        while k < n and text[k].isdigit(): k += 1
        return k - j
    while i < n:
        run = digits_run(i)
        if run >= 4 or (mode == "C" and run >= 2):
            if run % 2 and mode != "C":                                  # رقم فرد در ابتدای دنباله: یکی با B، بقیه با C
                if mode is None: vals.append(104); mode = "B"
                vals.append(ord(text[i]) - 32); i += 1; run -= 1
            if mode != "C": vals.append(99 if mode else 105); mode = "C"
            for _ in range(run // 2): vals.append(int(text[i:i + 2])); i += 2
            continue
        if mode == "C": vals.append(100); mode = "B"
        elif mode is None: vals.append(104); mode = "B"
        vals.append(ord(text[i]) - 32); i += 1
    return vals

def encode_code128(text):
    v = code128_values(text); chk = (v[0] + sum(x * i for i, x in enumerate(v[1:], 1))) % 103
    return "".join(_mods(_C128[x]) for x in v + [chk, 106])

_C39 = {"0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
        "8": "wnnwnnwnn", "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn", "F": "nnwnwwnnn",
        "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn", "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
        "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn", "U": "wwnnnnnnw", "V": "nwwnnnnnw",
        "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn"}

def encode_code39(text):
    t = text.upper()
    if not t or any(c not in _C39 or c == "*" for c in t): raise ValueError("Code 39 فقط حروف انگلیسی بزرگ، عدد و - . فاصله را می‌پذیرد.")
    out = []
    for ch in "*" + t + "*":
        out.append(_mods(["2" if c == "w" else "1" for c in _C39[ch]]))
    return "0".join(out)

TYPES = {"EAN-13": encode_ean13, "Code 128": encode_code128, "Code 39": encode_code39}

def encode(kind, code):
    return TYPES[kind](code)

def compatible(kind, code):
    try: encode(kind, code); return True
    except (ValueError, KeyError): return False

def new_internal_code(kind, seq):
    """بارکد داخلیِ فروشگاه برای کالای بدون بارکد. EAN-13 با پیشوند ۲۰۰ (محدوده‌ی استاندارد مخصوص استفاده‌ی داخلی)؛ بقیه عددیِ کوتاه‌تر."""
    if kind == "EAN-13":
        b = "200" + str(seq).zfill(9)[-9:]; return b + ean13_check(b)
    return "9" + str(seq).zfill(7)[-7:]
