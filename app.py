# app.py  — Instagram Bridge (complete file)

import os, secrets, time, requests
from urllib.parse import urlencode
from flask import Flask, request, redirect, jsonify, render_template_string

# --- load .env for local dev (Render will inject env vars) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

APP_SECRET = os.getenv("APP_SECRET", "dev-secret")
FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")
BASE = (os.getenv("BRIDGE_BASE_URL") or "").rstrip("/")  # e.g. https://your-bridge.onrender.com

# Basic sanity checks (prints helpfully to logs)
if not (FB_APP_ID and FB_APP_SECRET and BASE):
    print("⚠️  Missing env vars. Need FB_APP_ID, FB_APP_SECRET, BRIDGE_BASE_URL")

app = Flask(__name__)
app.secret_key = APP_SECRET

# In-memory exchange store: device_code -> token bundle (cleared on pickup)
STORE = {}

# OAuth config
OAUTH_REDIRECT = f"{BASE}/oauth/callback"
SCOPES = ["instagram_basic", "pages_show_list", "instagram_content_publish"]

# ---- helpers -------------------------------------------------

def _get(url, **params):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _post(url, **data):
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    return r.json()

# ---- routes --------------------------------------------------

@app.get("/health")
def health():
    return "OK", 200

@app.get("/oauth/start")
def oauth_start():
    device_code = request.args.get("device_code")
    if not device_code:
        return "device_code missing", 400

    state = f"{secrets.token_urlsafe(24)}::{device_code}"
    params = {
        "client_id": FB_APP_ID,
        "redirect_uri": OAUTH_REDIRECT,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
    }
    url = "https://www.facebook.com/v19.0/dialog/oauth?" + urlencode(params)
    return redirect(url, 302)

@app.get("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state", "")
    if not code or "::" not in state:
        return "Bad request", 400

    _state, device_code = state.split("::", 1)

    # 1) exchange CODE -> short-lived user token
    try:
        j = _get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            client_id=FB_APP_ID,
            client_secret=FB_APP_SECRET,
            redirect_uri=OAUTH_REDIRECT,
            code=code,
        )
        short_token = j["access_token"]
    except Exception as e:
        return f"Token exchange failed (short-lived): {e}", 400

    # 2) short-lived -> long-lived
    try:
        j2 = _get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            grant_type="fb_exchange_token",
            client_id=FB_APP_ID,
            client_secret=FB_APP_SECRET,
            fb_exchange_token=short_token,
        )
        long_token = j2["access_token"]
    except Exception as e:
        return f"Token exchange failed (long-lived): {e}", 400

    # 3) list pages granted to the user/app
    try:
        pages_resp = _get("https://graph.facebook.com/v19.0/me/accounts", access_token=long_token)
        pages = pages_resp.get("data", []) or []
    except Exception as e:
        return f"Failed to list pages: {e}", 400

    if not pages:
        # Most common issue: user clicked through without selecting the Page in the consent dialog
        return render_template_string("""
            <h2>❌ No Facebook Pages granted</h2>
            <p>Please remove the app from <b>Facebook Settings → Business Integrations</b>, then run Connect again and
            <b>expand “Choose what you allow”</b> and select your <b>Page</b> (and Instagram account) before continuing.</p>
        """), 400

    # 4) find a page that has a linked IG account (either field)
    ig_user_id = None
    for p in pages:
        pid = p.get("id")
        if not pid:
            continue
        try:
            page_info = _get(
                f"https://graph.facebook.com/v19.0/{pid}",
                fields="connected_instagram_account,instagram_business_account",
                access_token=long_token,
            )
        except Exception as e:
            # keep scanning other pages
            print(f"⚠️  Failed to fetch page {pid} details: {e}")
            continue

        igacct = (page_info.get("connected_instagram_account") or {}).get("id")
        if not igacct:
            igacct = (page_info.get("instagram_business_account") or {}).get("id")

        if igacct:
            ig_user_id = igacct
            break

    if not ig_user_id:
        # Helpful guidance for the two typical root causes
        return render_template_string("""
            <h2>❌ No connected Instagram account found</h2>
            <ol>
              <li>Ensure your Instagram is a <b>Business/Creator</b> account.</li>
              <li>Link it to a <b>Facebook Page</b> (Instagram app → Settings → Accounts Center → add your Page).</li>
              <li>Remove this app from <b>Facebook Settings → Business Integrations</b> and run Connect again.<br>
                  On the consent dialog, click <b>“Choose what you allow”</b> and select your <b>Page</b> and Instagram.</li>
              <li>Make sure you are an <b>Admin</b> on that Page with the FB account you used to log in.</li>
            </ol>
        """), 400

    # 5) success: stash for device_code and show success page
    STORE[device_code] = {
        "ready": True,
        "token": long_token,
        "igid": ig_user_id,
        "ts": int(time.time()),
    }

    return render_template_string("""
        <h2>✅ Connected</h2>
        <p>You can close this tab and return to the app.</p>
    """)

@app.get("/exchange")
def exchange():
    device_code = request.args.get("device_code")
    data = STORE.get(device_code)
    if not data:
        return jsonify({"ready": False})
    # one-time read
    STORE.pop(device_code, None)
    return jsonify({
        "ready": True,
        "access_token": data["token"],
        "ig_user_id": data["igid"],
    })
