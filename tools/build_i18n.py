#!/usr/bin/env python3
"""Generate i18n.js from i18n_source.json (single source of truth for UI strings).

i18n_source.json maps each key -> { lt, lv, et, en, uk, ru }. This emits i18n.js
holding LANGS (flag + abbreviation per language), STRINGS (flat per-language maps),
the active-language resolver, and t() with {placeholder} interpolation.
Run after editing translations:  python tools/build_i18n.py
"""
import json
import os

LANGS = [
    ("lt", "\U0001F1F1\U0001F1F9", "LT"),
    ("lv", "\U0001F1F1\U0001F1FB", "LV"),
    ("et", "\U0001F1EA\U0001F1EA", "EE"),
    ("en", "\U0001F1EC\U0001F1E7", "EN"),
    ("uk", "\U0001F1FA\U0001F1E6", "UA"),
    ("ru", "\U0001F1F7\U0001F1FA", "RU"),
]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    src = json.load(open(os.path.join(ROOT, "i18n_source.json"), encoding="utf-8"))
    strings = {code: {} for code, _, _ in LANGS}
    missing = []
    for key, vals in src.items():
        for code, _, _ in LANGS:
            if code in vals and vals[code] != "":
                strings[code][key] = vals[code]
            elif code not in ("lt", "en"):
                missing.append(f"{code}:{key}")
    langs_js = json.dumps([{"code": c, "flag": f, "abbr": a} for c, f, a in LANGS], ensure_ascii=False)
    strings_js = json.dumps(strings, ensure_ascii=False, indent=2)
    out = f"""// AUTO-GENERATED from i18n_source.json by tools/build_i18n.py — DO NOT edit by hand.
const LANGS = {langs_js};
const STRINGS = {strings_js};
let lang = (function () {{ try {{ return localStorage.getItem("kk_lang") || ""; }} catch (e) {{ return ""; }} }})();
if (!LANGS.some(l => l.code === lang)) lang = "lt";
function t(key, vars) {{
    let s = STRINGS[lang] && STRINGS[lang][key];
    if (s == null) s = STRINGS.en && STRINGS.en[key];
    if (s == null) s = STRINGS.lt && STRINGS.lt[key];
    if (s == null) s = key;
    if (vars) for (const k in vars) s = s.split("{{" + k + "}}").join(vars[k]);
    return s;
}}
"""
    open(os.path.join(ROOT, "i18n.js"), "w", encoding="utf-8").write(out)
    n = len(src)
    print(f"[ok] i18n.js: {n} keys x {len(LANGS)} languages")
    if missing:
        print(f"[warn] {len(missing)} missing translations: {missing[:8]}{'…' if len(missing) > 8 else ''}")


if __name__ == "__main__":
    main()
