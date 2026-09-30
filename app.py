"""OneGov x Jan Awaaz - citizen services + AI complaint triage that ranks development works for an MP.
Build with AI: Code for Communities (2nd Edition) - Track 1."""
import datetime as dt
import html
import json
import os
import re
import time

import pandas as pd
import streamlit as st

st.set_page_config(page_title="OneGov | Jan Awaaz", page_icon="🏛️", layout="wide")

# ================= PURE LOGIC (no UI) =================
CATEGORIES = ["Roads", "Water", "Health", "Education", "Sanitation",
              "Electricity", "Transport", "Safety", "Environment", "Other"]
ICON = {"Roads": "🛣️", "Water": "💧", "Health": "🏥", "Education": "🎓", "Sanitation": "🗑️",
        "Electricity": "⚡", "Transport": "🚌", "Safety": "🛡️", "Environment": "🌳", "Other": "📌"}
DEPT = {"Roads": "Public Works Dept (PWD)", "Water": "Water Supply & Sewerage Board",
        "Health": "Health Department", "Education": "Education Department",
        "Sanitation": "Municipal Corporation", "Electricity": "Electricity Board",
        "Transport": "Transport Department", "Safety": "Police Department",
        "Environment": "Forest & Environment Dept", "Other": "District Administration"}
WORK = {
    "Roads": "Repair and resurface damaged roads and bridges",
    "Water": "Restore water supply: fix pipelines, install hand pumps, set tanker schedule",
    "Health": "Strengthen the health centre: doctor attendance, medicine stock",
    "Education": "Repair school buildings and add learning facilities",
    "Sanitation": "Clear drains and start regular garbage collection",
    "Electricity": "Fix power lines and improve power supply hours",
    "Transport": "Build bus shelters and improve local transport",
    "Safety": "Install street lights and improve local safety",
    "Environment": "Plant trees and develop green public spaces",
    "Other": "Review and route to the right department",
}
STEPS = ["Submitted", "Under Review", "Assigned", "In Progress", "Resolved"]
SLA_DAYS = {5: 2, 4: 4, 3: 7, 2: 10, 1: 15}
FALLBACK_MODEL = "gemini-3.8-flash"
SKIP = ["lite", "image", "tts", "live", "audio", "embed", "native", "robotics"]
DEMO = {"citizen": ("ramesh.kumar@gov.in", "Citizen@2026"), "officer": ("officer@gov.in", "Officer@2026")}

PROMPT = """You are an assistant for an Indian Member of Parliament's office.
A citizen sent this message (it may be in Hindi, English, Hinglish or another Indian language, possibly with a photo):

\"\"\"{text}\"\"\"

Return ONLY a JSON object, no extra text, with keys:
"category": one of {cats},
"urgency": integer 1-5 (5 = danger to life or health, needs action now; suggestions for the future = 1),
"english_summary": one short clear English sentence,
"suggested_action": one short practical action for the MP's office.
"""

KEYWORDS = {
    "Sanitation": ["kooda", "naali", "gandaa", "gandagi", "garbage", "badbu", "drain"],
    "Education": ["school", "library", "college", "padhai", "computer"],
    "Roads": ["sadak", "road", "gadde", "pothole", "pul ", "bridge", "rasta"],
    "Water": ["pani", "paani", "water", "tanker", "hand pump", "handpump", "nal "],
    "Health": ["hospital", "doctor", "dawai", "health", "mareez", "aspatal"],
    "Electricity": ["bijli", "electric", "taar", "power"],
    "Transport": ["bus", "train", "auto"],
    "Safety": ["chori", "crime", "police", "darr", "street light", "light"],
    "Environment": ["ped ", "park", "pollution", "tree"],
}
HIGH = ["haadsa", "toot", "band hai", "khatam", "bimari", "latak", "danger", "accident", "gir "]
BAD = ["khrb", "kharab", "kharaab", "kami", "problem", "dikkat", "kharb"]


def fallback_classify(text):
    t = " " + text.lower() + " "
    cat = "Other"
    for c, words in KEYWORDS.items():
        if any(w in t for w in words):
            cat = c
            break
    urgency = 4 if any(w in t for w in HIGH) else (3 if any(w in t for w in BAD) else 2)
    if "suggestion" in t:
        urgency = 1
    return {"category": cat, "urgency": urgency, "english_summary": text.strip()[:110],
            "suggested_action": f"Forward to {DEPT[cat]} and follow up."}


def clean_result(d):
    if d.get("category") not in CATEGORIES:
        d["category"] = "Other"
    try:
        d["urgency"] = max(1, min(5, int(d.get("urgency", 2))))
    except Exception:  # noqa: BLE001
        d["urgency"] = 2
    d.setdefault("english_summary", "")
    d.setdefault("suggested_action", "Forward to the concerned department.")
    return d


def parse_json(raw):
    raw = re.sub(r"```json|```", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)


def make_row(n, text, ward, res, mine, source, has_photo=False, status=0, days_ago=0):
    submitted = dt.date.today() - dt.timedelta(days=days_ago)
    return {"id": f"JAW-{2800 + n}", "text": text, "ward": ward, "category": res["category"],
            "urgency": res["urgency"], "english_summary": res["english_summary"] or text[:90],
            "suggested_action": res["suggested_action"], "has_photo": has_photo, "source": source,
            "status": status, "submitted": submitted,
            "sla": submitted + dt.timedelta(days=SLA_DAYS[res["urgency"]]), "mine": mine}


def seed_rows(df):
    rows = []
    for i, r in df.iterrows():
        res = clean_result({"category": r["category"], "urgency": r["urgency"],
                            "english_summary": r["english_summary"],
                            "suggested_action": f"Forward to {DEPT.get(r['category'], 'District Administration')}."})
        rows.append(make_row(i, r["text"], r["ward"], res, mine=(i in (0, 1, 2)), source="Demo data",
                             status=[1, 0, 2, 3, 0, 1, 0, 4, 1, 0, 2, 0, 0, 0, 1][i % 15], days_ago=(i * 2) % 9))
    return rows


def add_priority(g):
    if g.empty:
        return g
    g = g.copy()
    g["raw"] = g["urgency_sum"] * (0.5 + g["infra_gap"]) * (g["population"] / 10000.0)
    g["score"] = (100 * g["raw"] / (g["raw"].max() or 1)).round(0).astype(int)
    return g


def with_ward_info(df, wards):
    out = df.merge(wards, on="ward", how="left")
    out["population"] = out["population"].fillna(10000)
    out["infra_gap"] = out["infra_gap"].fillna(0.5)
    out["lat"] = out["lat"].fillna(25.60)
    out["lon"] = out["lon"].fillna(85.14)
    return out


def ward_hotspots(df, wards):
    g = df.groupby("ward").agg(complaints=("urgency", "size"), urgency_sum=("urgency", "sum"),
                               avg_urgency=("urgency", "mean")).reset_index()
    return add_priority(with_ward_info(g, wards)).sort_values("score", ascending=False)


def ranked_works(df, wards, n=5):
    g = df.groupby(["ward", "category"]).agg(complaints=("urgency", "size"), urgency_sum=("urgency", "sum"),
                                             avg_urgency=("urgency", "mean")).reset_index()
    g = add_priority(with_ward_info(g, wards)).sort_values(["score", "complaints"], ascending=False).head(n)
    ev = []
    for _, r in g.iterrows():
        sub = df[(df.ward == r.ward) & (df.category == r.category)].sort_values("urgency", ascending=False)
        ev.append(sub.iloc[0]["english_summary"])
    g["evidence"] = ev
    return g


# ================= AI CALLS =================
def get_key():
    k = os.environ.get("GEMINI_API_KEY", "")
    if not k:
        try:
            k = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:  # noqa: BLE001
            k = ""
    return k


def gemini_client(api_key):
    from google import genai
    return genai.Client(api_key=api_key)


def pick_model(client):
    if st.session_state.get("model"):
        return st.session_state["model"]
    found = []
    try:
        for m in client.models.list():
            name = getattr(m, "name", "").replace("models/", "")
            actions = getattr(m, "supported_actions", None) or []
            if "flash" not in name or any(x in name for x in SKIP):
                continue
            if actions and "generateContent" not in actions:
                continue
            ver = tuple(int(n) for n in re.findall(r"\d+", name)[:3]) or (0,)
            found.append((ver, name))
    except Exception as e:  # noqa: BLE001
        st.session_state["last_error"] = "Model list error: " + str(e)
    names = [n for _, n in sorted(found, reverse=True)] or [FALLBACK_MODEL]
    st.session_state["model_list"] = names
    st.session_state["model"] = names[0]
    return names[0]


def generate(client, contents):
    """Gemini call with retry on 503/429 and fallback to the next available Flash model."""
    pick_model(client)
    names = st.session_state.get("model_list") or [FALLBACK_MODEL]
    last = None
    for name in names[:3]:
        for _ in range(2):
            try:
                out = client.models.generate_content(model=name, contents=contents)
                st.session_state["model"] = name
                return out
            except Exception as e:  # noqa: BLE001
                last = e
                msg = str(e)
                if "404" in msg or "NOT_FOUND" in msg:
                    break
                if any(x in msg for x in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    time.sleep(1.5)
                    continue
                raise
    raise last


def classify(text, api_key, image=None):
    if api_key:
        try:
            client = gemini_client(api_key)
            contents = [PROMPT.format(text=text, cats=CATEGORIES)]
            if image:
                from google.genai import types
                contents.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
            resp = generate(client, contents)
            return clean_result(parse_json(resp.text)), "Gemini AI"
        except Exception as e:  # noqa: BLE001
            st.session_state["last_error"] = str(e)
    return fallback_classify(text), "Offline demo mode"


def transcribe(audio_bytes, api_key):
    try:
        from google.genai import types
        client = gemini_client(api_key)
        resp = generate(
            client,
            ["Transcribe this audio exactly (Hindi or English). Reply with only the transcript.",
                      types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav")])
        return resp.text.strip()
    except Exception as e:  # noqa: BLE001
        st.session_state["last_error"] = "Voice error: " + str(e)
        return ""


# ================= UI =================
st.markdown("""
<style>
.block-container{max-width:1100px;padding-top:1.2rem}
.brand{display:flex;align-items:center;gap:10px;font-size:1.7rem;font-weight:800;color:#1a56db}
.logo{background:#1a56db;color:#fff;border-radius:12px;padding:6px 10px;font-size:1.3rem}
.hero{background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#fff;padding:24px 26px;border-radius:20px;margin:10px 0 6px}
.hero small{letter-spacing:.08em;font-weight:700;opacity:.9}
.hero h2{color:#fff;margin:6px 0 4px}
.hero p{opacity:.92;margin:0}
.card{border:1px solid rgba(128,128,128,.28);border-radius:16px;padding:14px 18px;margin-bottom:10px;background:rgba(128,128,128,.06)}
.svc{border:1px solid rgba(128,128,128,.28);border-radius:16px;padding:16px;text-align:center;background:rgba(128,128,128,.06)}
.svc b{display:block;margin-top:4px}.svc span{font-size:.8rem;opacity:.75}
.pill{display:inline-block;padding:2px 11px;border-radius:20px;font-size:.78rem;font-weight:700;margin-right:6px}
.s0{background:#e0e7ff;color:#3730a3}.s1{background:#fef3c7;color:#92400e}.s2{background:#dbeafe;color:#1e40af}
.s3{background:#ede9fe;color:#5b21b6}.s4{background:#d1fae5;color:#065f46}
.bar{height:8px;background:rgba(128,128,128,.25);border-radius:6px;overflow:hidden;margin:8px 0 4px}
.bar div{height:8px;background:#2563eb}.bar.done div{background:#10b981}
.rank{font-size:1.4rem;font-weight:800;color:#2563eb;margin-right:10px}
.urg5,.urg4{color:#dc2626;font-weight:700}.urg3{color:#d97706;font-weight:700}.urg2,.urg1{color:#059669;font-weight:700}
.small{font-size:.85rem;opacity:.8}
</style>""", unsafe_allow_html=True)

WARDS = pd.read_csv("ward_data.csv")
if "rows" not in st.session_state:
    st.session_state.rows = seed_rows(pd.read_csv("sample_complaints.csv"))
    st.session_state.last_error = ""
    st.session_state.model = ""
    st.session_state.role = None
    st.session_state.q = ""

api_key = get_key()
with st.sidebar:
    st.header("⚙️ Settings")
    if not api_key:
        api_key = st.text_input("Gemini API key", type="password")
    else:
        st.success("Gemini API key connected")
    st.caption("Key ke bina app offline demo mode mein chalega.")
    if st.session_state.get("model"):
        st.caption("AI model: " + st.session_state["model"])
    if st.session_state.last_error:
        st.warning("Note: " + st.session_state.last_error[:180])
    if st.session_state.role and st.button("Sign out"):
        st.session_state.role = None
        st.rerun()
    st.divider()
    st.caption("Ward data (population, infrastructure gap) and demo complaints are illustrative sample data. "
               "Real use: Census and local development plans.")

st.markdown('<div class="brand"><span class="logo">🏛️</span>OneGov <span style="font-size:.9rem;font-weight:600;'
            'opacity:.7">× Jan Awaaz</span></div>', unsafe_allow_html=True)

rows = st.session_state.rows


def pill(status):
    return f'<span class="pill s{status}">{STEPS[status]}</span>'


# ---------- LOGIN ----------
if not st.session_state.role:
    st.markdown("##### I AM A")
    role = st.radio("role", ["🏠 Citizen", "🏛️ Department Officer / MP Office"], horizontal=True,
                    label_visibility="collapsed")
    key = "citizen" if role.startswith("🏠") else "officer"
    st.subheader("Welcome back")
    st.caption("Sign in to access government services")
    with st.form("login"):
        email = st.text_input("Email address", placeholder="yourname@email.com")
        pw = st.text_input("Password", type="password")
        go = st.form_submit_button("Sign in", type="primary")
    if go:
        if (email.strip().lower(), pw) == DEMO[key]:
            st.session_state.role = key
            st.rerun()
        else:
            st.error("Email ya password galat hai. Neeche demo credentials use kijiye.")
    st.info(f"**DEMO CREDENTIALS — {key.upper()}**  \nEmail: `{DEMO[key][0]}`  \nPassword: `{DEMO[key][1]}`  \n"
            "Code for Communities — demo environment with simulated data.")
    st.stop()

# ---------- CITIZEN ----------
if st.session_state.role == "citizen":
    st.markdown("### Good morning, Ramesh 👋")
    st.markdown("""<div class="hero"><small>✨ AI-POWERED SERVICE ROUTING</small>
<h2>What do you need help with today?</h2>
<p>Apni problem apne shabdon mein likhiye (Hindi / English). OneGov AI sahi department dhoondh kar complaint darj karega.</p></div>""",
                unsafe_allow_html=True)

    def set_q(v):
        st.session_state.q = v

    q = st.text_input("Describe your problem", key="q", placeholder='e.g. "Mere area mein 4 din se pani nahi aa raha"')
    chips = ["pothole complaint", "water problem", "street light not working", "garbage not collected"]
    cc = st.columns(len(chips))
    for c, chip in zip(cc, chips):
        c.button(chip, on_click=set_q, args=(chip,))
    c1, c2, c3 = st.columns(3)
    ward = c1.selectbox("Ward / Area", list(WARDS["ward"]))
    photo = c2.file_uploader("📷 Photo (optional)", type=["jpg", "jpeg", "png"])
    audio = None
    if hasattr(st, "audio_input"):
        with c3:
            audio = st.audio_input("🎤 Voice (optional)")
    if st.button("🔎 Route & submit", type="primary"):
        msg = q.strip()
        if not msg and audio is not None and api_key:
            with st.spinner("Awaaz sun raha hoon..."):
                msg = transcribe(audio.getvalue(), api_key)
            if msg:
                st.info("Voice se samjha: " + msg)
        if not msg:
            st.error("Kuch likhiye ya voice bhejiye.")
        else:
            img = (photo.getvalue(), photo.type or "image/jpeg") if photo is not None else None
            with st.spinner("AI analyse kar raha hai..."):
                res, src = classify(msg, api_key, img)
            res["suggested_action"] = f"Forward to {DEPT[res['category']]} and follow up in {SLA_DAYS[res['urgency']]} days."
            row = make_row(len(rows), msg, ward, res, mine=True, source=src, has_photo=photo is not None)
            rows.append(row)
            st.success(f"Complaint darj ho gayi! Ticket {row['id']} ({src})")
            st.markdown(f"""<div class="card"><b>{ICON[res['category']]} Routed to: {html.escape(DEPT[res['category']])}</b><br>
<span class="urg{res['urgency']}">Urgency {res['urgency']}/5</span> • {html.escape(ward)} • SLA: {row['sla']:%d %b %Y}<br>
<span class="small">{html.escape(res['english_summary'])}</span></div>""", unsafe_allow_html=True)

    st.markdown("#### Quick Services")
    cats = ["Roads", "Water", "Electricity", "Sanitation", "Health", "Safety"]
    for start in (0, 3):
        cols = st.columns(3)
        for col, c in zip(cols, cats[start:start + 3]):
            col.markdown(f'<div class="svc">{ICON[c]}<b>{c}</b><span>{html.escape(DEPT[c])}</span></div>',
                         unsafe_allow_html=True)

    st.markdown("#### My Applications")
    mine = [r for r in rows if r["mine"]][::-1]
    if not mine:
        st.caption("Abhi koi application nahi.")
    for r in mine:
        pct = int((r["status"] + 1) / 5 * 100)
        st.markdown(f"""<div class="card"><span class="small">{r['id']}</span> &nbsp; {pill(r['status'])}<br>
<b>{ICON[r['category']]} {html.escape(r['english_summary'])}</b><br>
<span class="small">{html.escape(DEPT[r['category']])} • {html.escape(r['ward'])}</span>
<div class="bar {'done' if r['status'] == 4 else ''}"><div style="width:{pct}%"></div></div>
<span class="small">{r['status'] + 1}/5 steps complete • Submitted {r['submitted']:%d %b %Y} • SLA {r['sla']:%d %b %Y}</span></div>""",
                    unsafe_allow_html=True)

# ---------- OFFICER ----------
else:
    st.markdown("### 🏛️ MP Office — Officer Dashboard")
    df_all = pd.DataFrame(rows)
    df = df_all[df_all.status < 4]
    today = dt.date.today()
    overdue = df[df.sla < today]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total requests", len(df_all))
    k2.metric("Urgent (4-5)", int((df.urgency >= 4).sum()))
    k3.metric("SLA overdue", len(overdue))
    k4.metric("Resolved", f"{int((df_all.status == 4).mean() * 100)}%")

    t1, t2, t3, t4 = st.tabs(["🏆 Ranked works & map", "🤖 AI complaint groups", "📥 Request queue & SLA", "📊 Analytics"])

    with t1:
        if df.empty:
            st.info("Sab complaints resolve ho chuki hain.")
        else:
            hot = ward_hotspots(df, WARDS)
            works = ranked_works(df, WARDS, 5)
            st.subheader("Recommended development works (ranked)")
            st.caption("Rank = urgency × infrastructure gap × ward population (sample data), scaled 0-100.")
            for i, (_, w) in enumerate(works.iterrows(), start=1):
                st.markdown(f"""<div class="card"><span class="rank">#{i}</span>
<b>{ICON[w.category]} {html.escape(WORK[w.category])}</b> — {html.escape(w.ward)}<br>
<span class="small">{int(w.complaints)} complaints • avg urgency {w.avg_urgency:.1f}/5 • infra gap {w.infra_gap:.0%} •
population {int(w.population):,} • <b>Priority {int(w.score)}/100</b></span><br>
<span class="small">Evidence: {html.escape(str(w.evidence))}</span></div>""", unsafe_allow_html=True)
            st.subheader("Demand hotspot map")
            mp = hot.copy()
            mp["size"] = mp["score"].clip(lower=5) * 12
            st.map(mp, latitude="lat", longitude="lon", size="size")
            st.caption("Bada circle = zyada priority. Ward locations are illustrative.")
            if st.button("🤖 Generate AI briefing for MP"):
                summary = "\n".join(f"#{i} {w.ward} - {w.category}: {int(w.complaints)} complaints, "
                                    f"avg urgency {w.avg_urgency:.1f}, score {int(w.score)}"
                                    for i, (_, w) in enumerate(works.iterrows(), start=1))
                if api_key:
                    try:
                        client = gemini_client(api_key)
                        out = generate(
                            client,
                            "Write a short 5-line briefing (simple English) for an Indian MP about these "
                                     "ranked development priorities. Say what to do first and why:\n" + summary)
                        st.info(out.text)
                    except Exception as e:  # noqa: BLE001
                        st.error(str(e))
                else:
                    w0 = works.iloc[0]
                    st.info(f"Sabse pehle {w0.ward} mein {w0.category} par kaam kijiye ({int(w0.complaints)} complaints, "
                            f"priority {int(w0.score)}/100).")

    with t2:
        st.subheader("AI complaint groups")
        st.caption("Milti-julti complaints ek group mein, taaki MP ek problem ek baar dekhe.")
        if df.empty:
            st.info("Koi open complaint nahi.")
        else:
            grp = df.groupby(["category", "ward"]).agg(n=("urgency", "size"), avg=("urgency", "mean")).reset_index()
            for _, g in grp.sort_values(["n", "avg"], ascending=False).head(8).iterrows():
                sub = df[(df.category == g.category) & (df.ward == g.ward)].sort_values("urgency", ascending=False)
                st.markdown(f"""<div class="card"><b>{ICON[g.category]} {g.category} — {html.escape(g.ward)}</b>
&nbsp; <span class="pill s1">{int(g.n)} complaints</span> <span class="urg{int(round(g.avg))}">avg {g.avg:.1f}/5</span><br>
<span class="small">{html.escape(sub.iloc[0]['english_summary'])}<br>➡️ {html.escape(DEPT[g.category])}</span></div>""",
                            unsafe_allow_html=True)

    with t3:
        st.subheader("Request queue (highest urgency first)")
        queue = df.sort_values(["urgency", "sla"], ascending=[False, True]).head(8)
        for idx, r in queue.iterrows():
            late = "⚠️ SLA overdue" if r["sla"] < today else f"SLA {r['sla']:%d %b}"
            a, b = st.columns([5, 1])
            a.markdown(f"""<div class="card"><span class="small">{r['id']}</span> &nbsp; {pill(r['status'])}
<span class="urg{r['urgency']}">U{r['urgency']}</span><br>
<b>{ICON[r['category']]} {html.escape(r['english_summary'])}</b><br>
<span class="small">{html.escape(r['ward'])} • {html.escape(DEPT[r['category']])} • {late}</span></div>""",
                       unsafe_allow_html=True)
            if b.button("Advance ▶", key=f"adv{idx}"):
                rows[idx]["status"] = min(4, rows[idx]["status"] + 1)
                st.rerun()
        st.subheader("Priority SLA")
        st.write(f"**{len(overdue)}** requests SLA se late hain." if len(overdue) else "Sab requests SLA ke andar hain ✅")

    with t4:
        st.subheader("Analytics")
        a1, a2 = st.columns(2)
        a1.markdown("**Category-wise complaints**")
        a1.bar_chart(df_all.category.value_counts())
        if not df.empty:
            a2.markdown("**Ward-wise priority score**")
            a2.bar_chart(ward_hotspots(df, WARDS).set_index("ward")["score"])
        st.markdown("**Status pipeline**")
        st.bar_chart(df_all.status.map(lambda s: STEPS[s]).value_counts())
        st.download_button("Download CSV", df_all.drop(columns=["submitted", "sla"]).to_csv(index=False), "complaints.csv")
