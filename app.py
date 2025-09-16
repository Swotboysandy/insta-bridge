import os, secrets, time, requests
from urllib.parse import urlencode
from flask import Flask, request, redirect, jsonify, render_template_string

APP_SECRET = os.getenv("APP_SECRET", "dev-secret")
FB_APP_ID = os.getenv("FB_APP_ID")
FB_APP_SECRET = os.getenv("FB_APP_SECRET")
BASE = os.getenv("BRIDGE_BASE_URL")  # e.g. https://your-bridge.onrender.com

app = Flask(__name__)
app.secret_key = APP_SECRET
STORE = {}  # device_code -> {"ready":bool,"token":...,"igid":...,"ts":...}

OAUTH_REDIRECT = f"{BASE}/oauth/callback"
SCOPES = ["instagram_basic", "pages_show_list", "instagram_content_publish"]

@app.get("/health")
def health(): return "OK", 200

@app.get("/oauth/start")
def oauth_start():
    device_code = request.args.get("device_code")
    if not device_code: return "device_code missing", 400
    state = secrets.token_urlsafe(24) + "::" + device_code
    params = {"client_id": FB_APP_ID, "redirect_uri": OAUTH_REDIRECT,
              "response_type": "code", "scope": ",".join(SCOPES), "state": state}
    return redirect("https://www.facebook.com/v19.0/dialog/oauth?" + urlencode(params), 302)

@app.get("/oauth/callback")
def oauth_callback():
    code = request.args.get("code"); state = request.args.get("state","")
    if not code or "::" not in state: return "Bad request", 400
    _, device_code = state.split("::",1)
    # short-lived
    r = requests.get("https://graph.facebook.com/v19.0/oauth/access_token",
        params={"client_id":FB_APP_ID,"client_secret":FB_APP_SECRET,"redirect_uri":OAUTH_REDIRECT,"code":code}, timeout=20)
    r.raise_for_status(); short = r.json()["access_token"]
    # long-lived
    r2 = requests.get("https://graph.facebook.com/v19.0/oauth/access_token",
        params={"grant_type":"fb_exchange_token","client_id":FB_APP_ID,"client_secret":FB_APP_SECRET,"fb_exchange_token":short}, timeout=20)
    r2.raise_for_status(); long = r2.json()["access_token"]
    # page list
    r3 = requests.get("https://graph.facebook.com/v19.0/me/accounts", params={"access_token": long}, timeout=20)
    r3.raise_for_status(); pages = r3.json().get("data",[])
    igid=None
    for p in pages:
        r4 = requests.get(f"https://graph.facebook.com/v19.0/{p['id']}",
            params={"fields":"connected_instagram_account","access_token": long}, timeout=20)
        r4.raise_for_status(); igacct=(r4.json().get("connected_instagram_account") or {}).get("id")
        if igacct: igid=igacct; break
    if not igid: return "No connected Instagram account found.", 400
    STORE[device_code] = {"ready": True, "token": long, "igid": igid, "ts": int(time.time())}
    return render_template_string("<h2>✅ Connected</h2><p>You can close this tab.</p>")

@app.get("/exchange")
def exchange():
    code = request.args.get("device_code")
    data = STORE.get(code)
    if not data: return jsonify({"ready": False})
    STORE.pop(code, None)
    return jsonify({"ready": True, "access_token": data["token"], "ig_user_id": data["igid"]})
    