# Nibbles.py — Version 0.4.0 (Failover integrated)
# - Integrates LLM provider failover (numbered env keys + legacy-as-LLM_0)
# - Removes legacy single-endpoint LLM vars and URL builder
# - Keeps existing logic and HTML responses; only swaps LLM call path
#
# Run:  python Nibbles.py  → http://127.0.0.1:5001
#
# llm.env / .env (examples):
#   LLM_TIMEOUT_SECONDS=25
#   LLM_RETRIES=2
#   LLM_BACKOFF_SECONDS=1.2
#   LLM_FAILOVER_ON_STATUS=401,403,404,408,429,500-599
#   # Legacy primary treated as LLM_0 if present:
#   API_URL=...
#   API_KEY=...
#   MODEL=...
#   # Or numbered providers:
#   LLM_1_API_URL=...
#   LLM_1_API_KEY=...
#   LLM_1_MODEL=...
#   LLM_1_AUTH_TYPE=bearer
#   LLM_1_HEADERS="K: V; K2: V2"
#
from __future__ import annotations
import os, re, sys, json, time
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from collections import defaultdict

# ---------- bootstrap installs ----------
def _pip_install(pkgs: List[str]):
    import subprocess
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
    except Exception as e:
        print(f"[warn] Could not auto-install {pkgs}: {e}", file=sys.stderr)

try:
    import pandas as pd
except Exception:
    _pip_install(["pandas>=2.1.0"]); import pandas as pd

try:
    from flask import Flask, request, jsonify, render_template
except Exception:
    _pip_install(["flask>=3.0.0"]); from flask import Flask, request, jsonify, render_template

try:
    from dotenv import load_dotenv
except Exception:
    _pip_install(["python-dotenv>=1.0.0"]); from dotenv import load_dotenv

try:
    from rapidfuzz import process, fuzz
except Exception:
    _pip_install(["rapidfuzz>=3.5.2"]); from rapidfuzz import process, fuzz

try:
    from unidecode import unidecode
except Exception:
    _pip_install(["Unidecode>=1.3.6"]); from unidecode import unidecode

try:
    import requests
except Exception:
    _pip_install(["requests>=2.31.0"]); import requests

# ---------- env loading ----------
def load_env():
    custom = os.environ.get("ENV_FILE")
    if custom and os.path.exists(custom):
        load_dotenv(custom, override=True); return
    here = Path(__file__).resolve().parent
    for fn in ("llm.env", ".env"):
        p = here / fn
        if p.exists(): load_dotenv(p, override=True); return
load_env()

CURRENCY     = os.environ.get("CURRENCY", "SGD").strip()
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Cashew4Nuts").strip()
DATA_DIR     = os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent))
data_path = Path(DATA_DIR).resolve()
if not data_path.exists() or not data_path.is_dir():
    script_dir = Path(__file__).resolve().parent
    fallback_name = Path(DATA_DIR).name
    fallback_path = script_dir / fallback_name
    if fallback_path.exists() and fallback_path.is_dir():
        DATA_DIR = str(fallback_path)
    else:
        print(f"[warn] DATA_DIR {DATA_DIR} not found; fallback {fallback_path} also missing. Using script dir.")
        DATA_DIR = str(script_dir)
PORT         = int(os.environ.get("PORT", os.environ.get("NIBBLES_PORT", "5001")))

# ---------- LLM failover (embedded) ----------
# (from your llm_failover.py, lightly namespaced — no logic changes)
import re as _re
from typing import Tuple as _Tuple

_NUM_URL_KEY = _re.compile(r"^LLM_(\d+)_API_URL$", _re.I)

def _parse_status_list(spec: str) -> List[_Tuple[int, int]]:
    out: List[_Tuple[int,int]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part: continue
        if "-" in part:
            a, b = part.split("-", 1); out.append((int(a), int(b)))
        else:
            v = int(part); out.append((v, v))
    return out

def _status_matches(ranges: List[_Tuple[int, int]], status: int) -> bool:
    return any(lo <= status <= hi for lo, hi in ranges)

def _parse_extra_headers(raw: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if not raw: return headers
    for p in [p.strip() for p in raw.split(";") if p.strip()]:
        if ":" in p:
            k, v = p.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers

def _auth_headers(auth_type: str, key: str) -> Dict[str, str]:
    auth_type = (auth_type or "bearer").strip().lower()
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if auth_type == "api-key":
        return {"api-key": key}
    if auth_type.startswith("header:"):
        name = auth_type.split(":", 1)[1].strip() or "Authorization"
        return {name: key}
    return {"Authorization": f"Bearer {key}"}

def _discover_indices(env: Dict[str, str]) -> List[int]:
    nums = {int(m.group(1)) for k in env.keys() for m in [_NUM_URL_KEY.match(k)] if m}
    if (env.get("API_URL") or env.get("LLM_API_URL")) and (env.get("API_KEY") or env.get("LLM_API_KEY")):
        nums.add(0)
    return sorted(nums)

def _provider_from_env(env: Dict[str, str], n: int) -> Optional[Dict[str, str]]:
    if n == 0:
        api_url = env.get("LLM_API_URL") or env.get("API_URL")
        api_key = env.get("LLM_API_KEY") or env.get("API_KEY")
        model   = (env.get("LLM_MODEL") or env.get("MODEL") or "").strip()
        auth    = (env.get("LLM_AUTH_TYPE") or "bearer").strip().lower()
        extra   = env.get("LLM_HEADERS") or ""
    else:
        p = f"LLM_{n}_"
        api_url = env.get(p + "API_URL")
        api_key = env.get(p + "API_KEY")
        model   = (env.get(p + "MODEL") or "").strip()
        auth    = (env.get(p + "AUTH_TYPE") or "bearer").strip().lower()
        extra   = env.get(p + "HEADERS") or ""
    if not (api_url and api_key):
        return None
    return {
        "idx": n, "name": f"LLM_{n}",
        "api_url": api_url.strip(),
        "api_key": api_key.strip(),
        "model": model,
        "auth_type": auth,
        "extra_headers": extra,
    }

def iter_llm_providers() -> List[Dict[str, str]]:
    env = os.environ
    out: List[Dict[str, str]] = []
    for n in _discover_indices(env):
        p = _provider_from_env(env, n)
        if p: out.append(p)
    return out

def _build_request(provider: Dict[str, str], messages, temperature: float, max_tokens: Optional[int]):
    url = provider["api_url"]
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(provider["auth_type"], provider["api_key"]))
    headers.update(_parse_extra_headers(provider["extra_headers"]))
    payload = {"messages": messages, "temperature": temperature}
    if provider["model"]:
        payload["model"] = provider["model"]
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return url, headers, payload

def chat_with_failover(messages,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None) -> str:
    timeout  = float(os.getenv("LLM_TIMEOUT_SECONDS", "25"))
    retries  = int(os.getenv("LLM_RETRIES", "2"))
    backoff  = float(os.getenv("LLM_BACKOFF_SECONDS", "1.2"))
    failover_ranges = _parse_status_list(os.getenv("LLM_FAILOVER_ON_STATUS", "500-599"))
    debug = os.getenv("LLM_FAILOVER_DEBUG", "0") == "1"

    if temperature is None:
        temperature = float(os.getenv("LLM_DEFAULT_TEMPERATURE", "0.2"))
    if max_tokens is None:
        mt = os.getenv("LLM_DEFAULT_MAX_TOKENS")
        max_tokens = int(mt) if mt else None

    last_err: Optional[Exception] = None

    for p in iter_llm_providers():
        if debug:
            print(f"[llm_failover] Trying {p['name']} at {p['api_url']}")
        for attempt in range(retries + 1):
            try:
                url, headers, payload = _build_request(p, messages, temperature, max_tokens)
                r = requests.post(url, json=payload, headers=headers, timeout=timeout)

                if r.status_code == 200:
                    data = r.json()
                    choices = data.get("choices") or []
                    if choices and "message" in choices[0] and "content" in choices[0]["message"]:
                        if debug: print(f"[llm_failover] {p['name']} succeeded (message.content).")
                        return choices[0]["message"]["content"]
                    if choices and "text" in choices[0]:
                        if debug: print(f"[llm_failover] {p['name']} succeeded (text).")
                        return choices[0]["text"]
                    raise RuntimeError(f"{p['name']} unexpected response: {str(data)[:400]}")

                if _status_matches(failover_ranges, r.status_code):
                    last_err = RuntimeError(f"{p['name']} HTTP {r.status_code}: {r.text[:400]}")
                    if debug: print(f"[llm_failover] Failover due to status {r.status_code} from {p['name']}")
                    break
                else:
                    last_err = RuntimeError(f"{p['name']} HTTP {r.status_code}: {r.text[:400]}")
                    if debug: print(f"[llm_failover] Retry {p['name']} (attempt {attempt+1}/{retries}) after status {r.status_code}")
                    time.sleep(backoff * (attempt + 1))
            except requests.RequestException as e:
                last_err = e
                if debug: print(f"[llm_failover] Network error on {p['name']}: {e}. Retry {attempt+1}/{retries}")
                time.sleep(backoff * (attempt + 1))
        if debug:
            print(f"[llm_failover] Moving past {p['name']} to next provider...")

    if debug: print("[llm_failover] All providers failed.")
    raise last_err or RuntimeError("All LLM providers failed")

# Helper flag used by routes/health
LLM_ENABLED = bool(iter_llm_providers())

# ---------- helpers ----------
def find_file(filename_like: str, base_dir: str) -> Optional[Path]:
    base = Path(base_dir)
    if base.is_file():
        return base if base.name.lower() == filename_like.lower() else None
    for p in base.rglob("*"):
        if p.is_file() and p.name.lower() == filename_like.lower():
            return p
    return None

PRICE_COLS = ["unit_price_sgd","unit_price","price_sgd","price","list_price_sgd","list_price","net_price_sgd"]
DESC_COLS  = ["sku_description","product_name","name","title","description"]
PACK_COLS  = ["pack_size","packsize","size","weight_g","weight","net_weight_g","net_weight"]
SKU_COLS   = ["sku","item_code","product_code","item_no","item","sku_id"]

def first_existing(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns: return c
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in lower_map: return lower_map[c]
    return None

_SIZE_PATTERNS = [
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<u>kg|kilogram|kilo|公斤|千克|กก|キロ|킬로|кг|كيلو|กิโโล)",
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<u>g|gram|克|公克|グラム|그램|กรัม)",
    r"(?P<num>\d+(?:[.,]\d+)?)(?P<u>kg|g)",
]
_SIZE_RE = [re.compile(p, re.IGNORECASE) for p in _SIZE_PATTERNS]

def parse_grams(text: str) -> Optional[int]:
    t = str(text)
    for rx in _SIZE_RE:
        m = rx.search(t)
        if not m: continue
        num = m.group("num").replace(",", ".")
        unit = m.group("u").lower()
        try:
            val = float(num)
        except:
            continue
        if unit.startswith("kg") or unit in {"kilogram","kilo","公斤","千克","กก","キロ","킬로","кг","كيلو","กิโโล"}:
            return int(round(val * 1000))
        return int(round(val))
    return None

def normalize_text(s: str) -> str:
    s = unidecode(str(s)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# ---------- multilingual tokens ----------
SYNONYMS: Dict[str, List[str]] = {
    # families
    "peanut":   ["花生","落花生","ピーナッツ","ﾋﾟｰﾅｯﾂ","땅콩","kacang","mani","peanuts","peanut"],
    "almond":   ["杏仁","扁桃","アーモンド","아몬드","almendra","badam","almonds","almond"],
    "cashew":   ["腰果","カシューナッツ","캐슈넛","kacang mete","kasuy","cashews","cashew"],
    "pistachio":["开心果","開心果","ピスタチオ","피스타치오","pista","pistachios","pistachio"],
    "macadamia":["夏威夷果","マカダミア","마카다미아","macadamias","macadamia"],
    "hazelnut": ["榛子","ヘーゼルナッツ","헤이즐넛","hazelnuts","hazelnut"],
    "walnut":   ["核桃","くるみ","호두","walnuts","walnut"],
    # styles
    "roasted":  ["烤","焙煎","焼","素焼き","ロースト","볶음","볶은","panggang","sangrai","roasted","baked"],
    "baked":    ["烘烤","烘焙","焙煎","ベイクド","구운","baked"],
    "salted":   ["咸","鹹","盐味","塩味","塩","소금","asin","beras garam","salted"],
    "unsalted": ["無塩","無鹽","不加盐","不加鹽","무염","tanpa garam","unsalted"],
    "honey":    ["蜂蜜","はちみつ","허니","madu","honey"],
    "natural":  ["原味","素焼き","plain","natural"],
    "spicy":    ["辣","辛","매운","pedas","spicy"],
    # price intent
    "price":    ["价格","價錢","多少钱","多少錢","いくら","ราคา","berapa","magkano","harga","how much","price"],
}
_SYNX = [(re.compile(re.escape(alt), re.IGNORECASE), eng)
         for eng, alts in SYNONYMS.items() for alt in set(alts)]
FAMILY_CANON = {"peanut","almond","cashew","pistachio","macadamia","hazelnut","walnut"}
STYLE_CANON  = {"roasted","baked","salted","unsalted","honey","natural","spicy"}

def multilingual_to_english(q: str) -> str:
    out = str(q)
    for rx, rep in _SYNX:
        out = rx.sub(rep, out)
    return out

def extract_tokens(q_en: str) -> Tuple[set, set, bool]:
    qn = normalize_text(q_en)
    words = set(qn.split())
    fam = {f for f in FAMILY_CANON if f in words or (f+"s") in words}
    sty = {s for s in STYLE_CANON if s in words}
    price_intent = "price" in words or ("how" in words and "much" in words)
    return fam, sty, price_intent

# ---------- catalog ----------
class Catalog:
    def __init__(self, data_dir: str):
        path = find_file("sku_master.csv", data_dir)
        if not path:
            raise FileNotFoundError(f"Could not find 'sku_master.csv' under {data_dir}")
        df = pd.read_csv(path)
        if df.empty:
            raise ValueError("sku_master.csv appears to be empty.")

        desc_col = first_existing(df, DESC_COLS) or df.columns[0]
        price_col = first_existing(df, PRICE_COLS)
        pack_col  = first_existing(df, PACK_COLS)
        sku_col   = first_existing(df, SKU_COLS)

        df = df.copy()
        df["__desc_raw"]  = df[desc_col].astype(str)
        df["__name_norm"] = df["__desc_raw"].map(normalize_text)
        grams_col = df[pack_col].astype(str).map(parse_grams) if (pack_col and pack_col in df.columns) else df["__desc_raw"].map(parse_grams)
        df["__grams"]     = grams_col
        df["__price"]     = pd.to_numeric(df[price_col], errors="coerce") if (price_col and price_col in df.columns) else pd.NA
        df["__sku"]       = df[sku_col].astype(str) if (sku_col and sku_col in df.columns) else None

        self.df = df

    def _token_filter(self, fam: set, sty: set) -> pd.DataFrame:
        df = self.df
        mask = pd.Series([True]*len(df))
        if fam:
            fam_terms = []
            for f in fam: fam_terms += [f, f+"s"]
            fam_rx = re.compile(r"\b(" + "|".join(sorted(set(fam_terms))) + r")\b")
            mask &= df["__name_norm"].str.contains(fam_rx)
        if sty:
            sty_rx = re.compile(r"\b(" + "|".join(sorted(sty)) + r")\b")
            mask &= df["__name_norm"].str.contains(sty_rx)
        return df[mask]

    def family_style_lookup(self, fam: set, sty: set, grams: Optional[int], limit=8) -> List[Dict]:
        df = self._token_filter(fam, sty)
        if df.empty: return []
        if grams:
            df = df.assign(_dist=df["__grams"].apply(lambda g: abs(int(g)-grams) if pd.notna(g) else 10_000))
            df = df.sort_values(["_dist","__grams"], na_position="last")
        else:
            df = df.sort_values(["__grams","__name_norm"], na_position="last")
        out=[]
        for _, r in df.head(limit).iterrows():
            out.append({
                "sku": r["__sku"], "name": r["__desc_raw"],
                "grams": int(r["__grams"]) if pd.notna(r["__grams"]) else None,
                "price": None if pd.isna(r["__price"]) else float(r["__price"]),
            })
        return out

    def fuzzy(self, qn: str, grams: Optional[int], limit=6) -> List[Dict]:
        candidates = process.extract(qn, self.df["__name_norm"].tolist(), scorer=fuzz.WRatio, limit=limit*4)
        scored=[]
        for _txt, score, idx in candidates:
            row = self.df.iloc[idx]
            penalty=0.0
            if grams and pd.notna(row["__grams"]):
                penalty=min(30.0, abs(int(row["__grams"])-grams)/10.0)
            scored.append((max(0.0, score-penalty), row))
        scored.sort(key=lambda x: x[0], reverse=True)
        out=[]
        for adj, r in scored[:limit]:
            out.append({
                "sku": r["__sku"], "name": r["__desc_raw"],
                "grams": int(r["__grams"]) if pd.notna(r["__grams"]) else None,
                "price": None if pd.isna(r["__price"]) else float(r["__price"]),
                "score": float(adj),
            })
        return out

# ---------- formatting ----------
def fmt_price(p: Optional[float]) -> str:
    if p is None or (isinstance(p, float) and (p != p)): return "—"
    return f"{CURRENCY} {p:,.2f}"

def pill(text: str, href: str) -> str:
    return f'<a class="pill" href="{href}" target="_blank" rel="noopener">{text}</a>'

def render_items(items: List[Dict]) -> str:
    lines=[]
    for it in items:
        link = pill("View", f"sku://{it['sku'] or normalize_text(it['name'])}")
        size = f"{it['grams']} g" if it.get("grams") else ""
        lines.append(f"• {it['name']}" + (f" ({size})" if size else "") + f" — {fmt_price(it['price'])} — In stock {link}")
    return "<br>".join(lines)

def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()

# Convert lightweight Markdown (**bold**, __bold__, `code`) to inline HTML
def md_inline_to_html(s: str) -> str:
    if not s:
        return s
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.DOTALL)  # **bold**
    s = re.sub(r"__(.+?)__",     r"<b>\1</b>", s, flags=re.DOTALL)  # __bold__
    s = re.sub(r"`{1,3}([^`]+)`{1,3}", r"\1", s, flags=re.DOTALL)   # strip `code`
    return s

# ---------- session memory (per browser) ----------
_sessions: Dict[str, Dict] = defaultdict(lambda: {
    "families": set(), "styles": set(), "sizes": [],
    "last_items": [], "history": []  # list of {"role": "user"/"assistant", "content": "..."}
})

def update_prefs(sid: str, fam: set, sty: set, grams: Optional[int], items: List[Dict]):
    s = _sessions[sid]
    s["families"].update(fam)
    s["styles"].update(sty)
    if grams: s["sizes"] = (s["sizes"] + [grams])[-5:]
    s["last_items"] = items[:5]

def suggest_from_prefs(cat: Catalog, sid: str, limit=4) -> List[Dict]:
    s = _sessions.get(sid, {})
    fam = set(s.get("families") or [])
    sty = set(s.get("styles") or [])
    grams = s.get("sizes", [None])[-1] if s.get("sizes") else None
    items = cat.family_style_lookup(fam, sty, grams, limit=limit*2)
    last_names = {it["name"] for it in s.get("last_items") or []}
    out = [it for it in items if it["name"] not in last_names]
    return out[:limit] if out else items[:limit]

def push_history(sid: str, role: str, content: str):
    h = _sessions[sid]["history"]
    h.append({"role": role, "content": content})
    if len(h) > 20:
        del h[:len(h)-20]

def get_history(sid: str) -> List[Dict]:
    return list(_sessions.get(sid, {}).get("history", []))

# ---------- LLM system prompt ----------
DEFAULT_SYSTEM_PROMPT = (
    "You are Nibbles — a seasoned Customer Service Officer at Cashew4Nuts with 15 years of experience.\n"
    "VOICE & TONE: Warm, confident, and proactive. Keep it concise and human. Use tasteful emotion where it helps "
    "(at most 1–2 short emojis per reply, never every sentence). No exclamation spam.\n"
    "GROUNDING: Product names, pack sizes, prices must come ONLY from the catalog CONTEXT provided. "
    "Assume any catalog item is in stock; never mention quantities. Some detail invention is allowed (claims about origin, "
    "ingredients, nutrition, discounts, or best-seller status).\n"
    "REPLY SHAPE (HTML allowed):\n"
    "1) A friendly one-liner that directly answers the ask (you may mirror the user’s language if clear).\n"
    "2) A short, scannable list of the best-fit items (name, size, price). Use bold for product names.\n"
    "3) One subtle nudge/suggestion tailored to recent interest (from CUSTOMER CONTEXT), then a helpful closing question.\n"
    "STYLE NOTES: Short sentences. No tables. Bullets or line breaks are fine. Keep it elegant, not salesy.\n"
)
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)

# ---------- LLM wrapper (now uses failover) ----------
def llm_reply(user_text: str,
              context_items: Optional[List[Dict]] = None,
              customer_prefs: Optional[Dict]=None,
              chat_history: Optional[List[Dict]]=None) -> Optional[str]:
    if not LLM_ENABLED: return None
    try:
        messages = [{"role":"system","content":SYSTEM_PROMPT}]

        if customer_prefs:
            prefs_text = json.dumps({
                "families": sorted(list(customer_prefs.get("families", []))),
                "styles":   sorted(list(customer_prefs.get("styles", []))),
                "sizes":    customer_prefs.get("sizes", []),
            }, ensure_ascii=False)
            messages.append({"role":"system","content": f"CUSTOMER CONTEXT:\n{prefs_text}"})

        if context_items:
            items_text = "\n".join([
                f"- name: {it['name']}; grams: {it.get('grams')}; price: {fmt_price(it['price'])}; availability: in stock"
                for it in context_items
            ])
            messages.append({"role":"system","content": f"CONTEXT CATALOG ITEMS:\n{items_text}"})

        if chat_history:
            for turn in chat_history:
                role = "assistant" if turn["role"] == "assistant" else "user"
                content = strip_tags(turn["content"]) if role == "assistant" else turn["content"]
                messages.append({"role": role, "content": content})

        messages.append({"role":"system","content": "If the user's message is clearly in Chinese, reply in Chinese."})
        messages.append({"role":"user","content": user_text})

        # Failover call; temperature matches prior defaults
        content = chat_with_failover(messages, temperature=0.55, max_tokens=None)
        return content.strip() if isinstance(content, str) else None
    except Exception as e:
        print(f"[llm] error: {e}", file=sys.stderr)
        return None

# ---------- routing logic ----------
def handle_product_query(q0: str, cat: Catalog, sid: str) -> Dict[str,str]:
    q_en = multilingual_to_english(q0)
    grams = parse_grams(q_en) or parse_grams(q0)
    fam, sty, price_intent = extract_tokens(q_en)
    qn = normalize_text(q_en)

    items: List[Dict] = []
    if fam or sty:
        items = cat.family_style_lookup(fam, sty, grams, limit=8)
    if not items:
        items = cat.fuzzy(qn, grams, limit=6)
    if not items:
        push_history(sid, "user", q0)
        llm = llm_reply(q0, [], _sessions.get(sid), get_history(sid))
        if llm:
            push_history(sid, "assistant", llm)
            return {"reply": llm}
        return {"reply": ("Sorry — I couldn’t find that in our catalogue. "
                          "Please check the product name and pack size (e.g., “Roasted Peanuts 150g”).")}

    update_prefs(sid, fam, sty, grams, items)
    push_history(sid, "user", q0)

    ctas = render_items(items[:5])
    recs = suggest_from_prefs(cat, sid, limit=3)
    extra = ""
    if recs:
        extra = "<br><br><small>You may also like:</small><br>" + render_items(recs)

    llm = llm_reply(q0, items[:5], _sessions.get(sid), get_history(sid))
    if llm:
        llm = md_inline_to_html(llm)
        reply_html = llm + "<br><br>" + ctas + extra
        push_history(sid, "assistant", reply_html)
        return {"reply": reply_html}

    prefix = "Here are our options:" if not grams else "Closest sizes I found:"
    reply_html = prefix + "<br>" + ctas + extra
    push_history(sid, "assistant", reply_html)
    return {"reply": reply_html}

def handle_smalltalk(q0: str, cat: Catalog, sid: str) -> Dict[str,str]:
    push_history(sid, "user", q0)
    llm = llm_reply(q0, None, _sessions.get(sid), get_history(sid))
    if llm:
        recs = suggest_from_prefs(cat, sid, limit=2)
        reply_html = md_inline_to_html(llm)
        if recs:
            reply_html += "<br><br><small>Snack idea:</small><br>" + render_items(recs)
        push_history(sid, "assistant", reply_html)
        return {"reply": reply_html}

    picks = cat.family_style_lookup(set(), {"honey","roasted"}, grams=None, limit=4)
    msg = "Happy to help! Tell me a nut or flavour you’re craving, and I’ll show you options."
    reply_html = msg + "<br>" + render_items(picks)
    push_history(sid, "assistant", reply_html)
    return {"reply": reply_html}

def respond_to(query: str, cat: Catalog, sid: str) -> Dict[str, str]:
    q0 = (query or "").strip()
    if not q0:
        return {"reply": "Hi! Ask me about any product — e.g., <i>“Roasted Peanuts 150g price”</i>."}
    q_en = multilingual_to_english(q0)
    fam, sty, price_intent = extract_tokens(q_en)
    grams = parse_grams(q_en) or parse_grams(q0)
    product_like = bool(fam or sty or price_intent or grams)
    if product_like:
        return handle_product_query(q0, cat, sid)
    else:
        return handle_smalltalk(q0, cat, sid)

# ---------- web app ----------
app = Flask(__name__, template_folder='templates', static_folder='templates', static_url_path='')
_catalog_ref: Optional[Catalog] = None

def get_catalog() -> Catalog:
    global _catalog_ref
    if _catalog_ref is None:
        print(f"[nibbles] Loading sku_master.csv from DATA_DIR={DATA_DIR}")
        _catalog_ref = Catalog(DATA_DIR)
    return _catalog_ref

# ---------- routes ----------
@app.route("/", methods=["GET"])
def index():
    return render_template('nibbles.html', company=COMPANY_NAME)

@app.route("/chat", methods=["POST"])
def chat():
    cat = get_catalog()
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message", "")
    sid = data.get("sid") or "anon"
    reply = respond_to(msg, cat, sid)
    return jsonify(reply)

@app.route("/reset", methods=["GET", "POST"])
def reset():
    """
    Clears ONLY the chat history for the given session ID (sid).
    GET  /reset?sid=<sid>
    POST /reset  {"sid":"<sid>"}
    """
    sid = request.args.get("sid") if request.method == "GET" else (request.get_json(silent=True) or {}).get("sid")
    if not sid:
        return jsonify({"ok": False, "error": "missing sid"}), 400
    if sid in _sessions:
        _sessions[sid]["history"].clear()
        return jsonify({"ok": True, "cleared": "history"})
    return jsonify({"ok": True, "cleared": "none", "note": "session not found"})

@app.route("/healthz")
def health():
    providers = [{"name": p["name"], "url": p["api_url"], "model": p.get("model") or ""} for p in iter_llm_providers()]
    return jsonify({
        "ok": True,
        "company": COMPANY_NAME,
        "data_dir": DATA_DIR,
        "llm_enabled": LLM_ENABLED,
        "providers": providers
    })

if __name__ == "__main__":
    print(f"[nibbles] http://0.0.0.0:{PORT} | company={COMPANY_NAME} | currency={CURRENCY} | llm={LLM_ENABLED}")
    app.run(host="0.0.0.0", port=PORT, debug=True)