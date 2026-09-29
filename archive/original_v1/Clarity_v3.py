from __future__ import annotations
import os, json, re
from pathlib import Path
from typing import Dict, List, Optional

# Auto-install
try:
    import importlib, subprocess
    def _ensure(pkgs):
        for p in pkgs:
            try: importlib.import_module(p)
            except: subprocess.check_call([sys.executable, "-m", "pip", "install", p])
    _ensure(["flask", "flask_cors", "pandas", "numpy", "python-dotenv", "requests"])
except: pass

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import pandas as pd
from dotenv import dotenv_values

# ========================= CONFIG =========================
APP_NAME = "Clarity_v3"
SECRET_KEY = os.environ.get("CLARITY_SECRET_KEY", os.urandom(24))

DEFAULT_TIMEOUT = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
DEFAULT_DATA_DIR = os.environ.get("DATA_DIR", "./Team_Cashew_Synthetic_Data")
# =========================================================

app = Flask(APP_NAME, template_folder='templates', static_folder='templates', static_url_path='')
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True)

# ====================== DATA MANAGER ======================
class DataManager:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.tables: Dict[str, pd.DataFrame] = {}
        self.load_errors: List[str] = []
        self._load_all()

    def _load_all(self):
        if not self.data_dir.exists():
            self.load_errors.append(f"Data dir not found: {self.data_dir}")
            return
        for f in sorted(self.data_dir.rglob("*")):
            if f.suffix.lower() not in {".csv", ".xlsx", ".xls"}: continue
            try:
                df = pd.read_csv(f) if f.suffix.lower() == ".csv" else pd.read_excel(f)
                key = re.sub(r"[^a-z0-9_]+", "_", f.stem.lower())
                self.tables[key] = df.copy()
            except Exception as e:
                self.load_errors.append(f"Failed to load {f}: {e}")

    def to_schema_prompt(self, max_cols: int = 30) -> str:
        chunks = ["You have access to the following in-memory tables:"]
        for name, df in self.tables.items():
            cols = ", ".join(map(str, df.columns[:max_cols]))
            chunks.append(f"• {name} [{len(df)} rows]: {cols}")
        if self.load_errors:
            chunks.append("Load notes: " + "; ".join(self.load_errors[:5]))
        return "\n".join(chunks)


# ====================== LLM v3 - FIXED for Azure Foundry ======================
def iter_llm_providers():
    env = dict(os.environ)
    providers = []
    for n in range(10):
        prefix = f"LLM_{n}_" if n > 0 else ""
        url = env.get(f"{prefix}API_URL") or env.get("API_URL")
        key = env.get(f"{prefix}API_KEY") or env.get("API_KEY")
        if not (url and key): continue
        providers.append({
            "name": f"LLM_{n}" if n > 0 else "Primary",
            "api_url": url.strip().rstrip("/"),
            "api_key": key.strip(),
            "model": env.get(f"{prefix}MODEL") or env.get("MODEL"),
            "api_version": env.get(f"{prefix}API_VERSION") or env.get("AZURE_API_VERSION"),
        })
    return providers


def _build_url(base: str, model: str, api_version: str) -> str:
    """Improved logic for Azure Foundry + standard endpoints"""
    base = base.strip()

    # If the URL already contains /chat/completions or /models/chat/completions → use as-is
    if "/chat/completions" in base.lower():
        return base

    # Azure Foundry style
    if "services.ai.azure.com" in base.lower():
        if "/models" not in base.lower():
            base = base.rstrip("/") + "/models/chat/completions"
        if api_version and "?" not in base:
            base += f"?api-version={api_version}"
        elif api_version:
            base += f"&api-version={api_version}"
        return base

    # Standard fallback
    if not base.endswith("/chat/completions"):
        base = base.rstrip("/") + "/chat/completions"
    return base


def _build_headers(p: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    # Azure Foundry uses api-key
    if "azure.com" in p["api_url"].lower() or "services.ai.azure.com" in p["api_url"].lower():
        headers["api-key"] = p["api_key"]
    else:
        headers["Authorization"] = f"Bearer {p['api_key']}"
    return headers


def chat_with_failover(messages: list) -> str:
    for p in iter_llm_providers():
        try:
            url = _build_url(p["api_url"], p.get("model"), p.get("api_version"))
            payload = {
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 4000,
            }
            if p["model"]:
                payload["model"] = p["model"]

            print(f"[Clarity_v3] 🔥 Trying {p['name']} → {url}")

            r = requests.post(url, json=payload, headers=_build_headers(p), timeout=DEFAULT_TIMEOUT)
            
            print(f"[Clarity_v3] {p['name']} returned status: {r.status_code}")

            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                print(f"[Clarity_v3] ✅ Success with {p['name']}")
                return content
            else:
                print(f"[Clarity_v3] ❌ {p['name']} failed: {r.status_code} - {r.text[:300]}")

        except Exception as e:
            print(f"[Clarity_v3] {p['name']} exception: {e}")
            continue

    return "[Error] All providers failed. Check console logs for details."


# ====================== FLASK ======================
_sessions: Dict[str, dict] = {}
DM: Optional[DataManager] = None

SYSTEM_PROMPT = (
    "You are a Management Consultant with 20+ years of experience in FMCG. "
    "You specialize in supply chain, sales, digital marketing, and retail operations. "
    "Use ONLY the provided in-memory tables. Be elaborate and verbose. "
    "Use markdown. At the end, add a professional-opinion section with 2–3 short paragraphs."
)

def get_session():
    sid = request.cookies.get("clarity_sid") or os.urandom(16).hex()
    if sid not in _sessions:
        _sessions[sid] = {"history": []}
    return sid, _sessions[sid]


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_msg = str(data.get("message", "")).strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    sid, session = get_session()
    history = session["history"]

    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + DM.to_schema_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    reply = chat_with_failover(messages)

    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 30:
        history[:] = history[-30:]

    return jsonify({"reply": reply})


@app.route("/")
def index():
    return render_template('clarity.html')


def bootstrap():
    global DM
    if DM: return
    env = dotenv_values("llm.env")
    os.environ.update({k: v for k, v in env.items() if v is not None})
    DM = DataManager(os.getenv("DATA_DIR", DEFAULT_DATA_DIR))
    print(f"[Clarity_v3] Loaded {len(DM.tables)} tables | Timeout: {DEFAULT_TIMEOUT}s")


@app.before_request
def ensure_boot(): bootstrap()


if __name__ == "__main__":
    bootstrap()
    port = int(os.environ.get("CLARITY_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
