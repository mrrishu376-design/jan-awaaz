"""Jan Awaaz - AI triage of citizen complaints for MPs (Code for Communities, Track 1)."""
import json
import os
import re

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Jan Awaaz", page_icon="🏛️", layout="wide")

CATEGORIES = ["Roads", "Water", "Health", "Education", "Sanitation",
              "Electricity", "Transport", "Safety", "Environment", "Other"]

MODEL = "gemini-3.8-flash"  # agar ye model na chale to Google AI Studio se available Flash model ka naam yahan daal do

PROMPT = """You are an assistant for an Indian Member of Parliament's office.
A citizen sent this message (it may be in Hindi, English, Hinglish or another Indian language):

\"\"\"{text}\"\"\"

Return ONLY a JSON object, no extra text, with keys:
"category": one of {cats},
"urgency": integer 1-5 (5 = danger to life or health, needs action now),
"english_summary": one short English sentence,
"suggested_action": one short practical action for the MP's office.
"""

# ---------- Offline fallback (keyword based) so the demo never breaks ----------
KEYWORDS = {
    "Roads": ["sadak", "road", "gadde", "pothole", "pul", "bridge"],
    "Water": ["pani", "paani", "water", "tanker", "hand pump", "nal"],
    "Health": ["hospital", "doctor", "dawai", "health", "mareez", "bimari"],
    "Education": ["school", "library", "college", "padhai", "computer"],
    "Sanitation": ["kooda", "naali", "gandaa", "garbage", "badbu", "drain"],
    "Electricity": ["bijli", "electric", "taar", "light", "power"],
    "Transport": ["bus", "train", "auto"],
    "Safety": ["chori", "crime", "police", "darr"],
    "Environment": ["ped", "park", "pollution", "tree"],
}
HIGH = ["haadsa", "toot", "band hai", "khatam", "bimari", "latak", "danger", "accident", "gir"]


def fallback_classify(text: str) -> dict:
    t = text.lower()
    cat = "Other"
    for c, words in KEYWORDS.items():
        if any(w in t for w in words):
            cat = c
            break
    urgency = 4 if any(w in t for w in HIGH) else 2
    if "suggestion" in t:
        urgency = 1
    return {
        "category": cat,
        "urgency": urgency,
        "english_summary": text[:110],
        "suggested_action": f"Forward to the {cat} department and follow up in 7 days.",
    }


def gemini_classify(text: str, api_key: str) -> dict:
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=MODEL,
        contents=PROMPT.format(text=text, cats=CATEGORIES),
    )
    raw = re.sub(r"```json|```", "", resp.text).strip()
    data = json.loads(raw)
    if data.get("category") not in CATEGORIES:
        data["category"] = "Other"
    data["urgency"] = max(1, min(5, int(data.get("urgency", 2))))
    return data


def classify(text: str, api_key: str):
    """Returns (result, source). Falls back to keywords on any error."""
    if api_key:
        try:
            return gemini_classify(text, api_key), "Gemini AI"
        except Exception as e:  # noqa: BLE001
            st.session_state["last_error"] = str(e)
    return fallback_classify(text), "Offline demo mode"


# ---------- State ----------
if "rows" not in st.session_state:
    st.session_state.rows = []
    st.session_state.last_error = ""

api_key = st.sidebar.text_input("Gemini API key", type="password",
                                value=os.environ.get("GEMINI_API_KEY", ""))
st.sidebar.caption("Key ke bina app offline demo mode mein chalega. "
                   "Free key: aistudio.google.com")
if st.session_state.last_error:
    st.sidebar.warning("Gemini error, fallback use hua: " + st.session_state.last_error[:150])

st.title("🏛️ Jan Awaaz — Citizen Voice to MP Action")
st.caption("Citizens Hindi/English mein problem bhejte hain. AI usse category, urgency aur action mein badal deta hai.")

tab1, tab2 = st.tabs(["📝 Citizen Portal", "📊 MP Dashboard"])

# ---------- Citizen portal ----------
with tab1:
    st.subheader("Apni samasya ya suggestion bhejiye")
    text = st.text_area("Aap kya batana chahte hain? (Hindi / English / Hinglish)", height=120)
    col1, col2 = st.columns(2)
    ward = col1.selectbox("Ward / Area", [f"Ward {i}" for i in range(1, 11)])
    photo = col2.file_uploader("Photo (optional)", type=["jpg", "jpeg", "png"])
    if st.button("Submit", type="primary") and text.strip():
        with st.spinner("AI analyse kar raha hai..."):
            res, src = classify(text, api_key)
        st.session_state.rows.append({"text": text, "ward": ward, "has_photo": photo is not None, **res})
        st.success(f"Submit ho gaya! ({src})")
        st.json(res)

# ---------- Dashboard ----------
with tab2:
    c1, c2 = st.columns([1, 3])
    if c1.button("Load 15 sample complaints"):
        df_s = pd.read_csv("sample_complaints.csv")
        prog = st.progress(0)
        for i, r in df_s.iterrows():
            res, _ = classify(r["text"], api_key)
            st.session_state.rows.append({"text": r["text"], "ward": r["ward"], "has_photo": False, **res})
            prog.progress((i + 1) / len(df_s))
        st.rerun()
    if c2.button("Clear all"):
        st.session_state.rows = []
        st.rerun()

    if not st.session_state.rows:
        st.info("Abhi koi data nahi. 'Load 15 sample complaints' dabaiye ya Citizen Portal se submit kariye.")
    else:
        df = pd.DataFrame(st.session_state.rows)
        m1, m2, m3 = st.columns(3)
        m1.metric("Total complaints", len(df))
        m2.metric("Urgent (4-5)", int((df.urgency >= 4).sum()))
        m3.metric("Top category", df.category.mode()[0])

        left, right = st.columns(2)
        left.subheader("Category ke hisaab se")
        left.bar_chart(df.category.value_counts())
        right.subheader("Ward ke hisaab se urgency")
        right.bar_chart(df.groupby("ward").urgency.mean())

        st.subheader("🔥 Top priorities")
        top = df.sort_values("urgency", ascending=False).head(5)
        for _, r in top.iterrows():
            st.markdown(f"**[{r.category} | Urgency {r.urgency}/5 | {r.ward}]** {r.english_summary}  \n➡️ {r.suggested_action}")

        if st.button("🤖 Generate AI briefing for MP"):
            summary_input = "\n".join(f"- {r.category}, urgency {r.urgency}, {r.ward}: {r.english_summary}"
                                      for _, r in df.iterrows())
            if api_key:
                try:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    out = client.models.generate_content(
                        model=MODEL,
                        contents="Write a 5-line briefing for an Indian MP about these citizen issues. "
                                 "Say what to fix first and why:\n" + summary_input)
                    st.write(out.text)
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
            else:
                st.write(f"Sabse zyada complaints **{df.category.mode()[0]}** ki hain. "
                         f"{int((df.urgency >= 4).sum())} complaints urgent hain, unpar pehle kaam kijiye.")

        st.subheader("All complaints")
        st.dataframe(df[["ward", "category", "urgency", "english_summary", "text"]], use_container_width=True)
        st.download_button("Download CSV", df.to_csv(index=False), "complaints.csv")
