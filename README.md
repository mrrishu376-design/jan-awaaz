# 🏛️ Jan Awaaz — Citizen Voice to MP Action

**Build with AI: Code for Communities — Second Edition | Track 1: AI for Digital Public Infrastructure & Governance**
Team: Rishu Kumar (Team Leader), Ranjan Kumar Singh — BCA, Bihar National College

## Problem
Citizens' complaints and suggestions reach an MP's office through scattered channels (letters, calls, WhatsApp, in-person). There is no quick way to see what is most urgent, so serious issues get delayed.

## Solution
Jan Awaaz lets citizens submit a problem in Hindi, English or Hinglish (text + optional photo). Gemini AI then:
1. Classifies it (Roads, Water, Health, Education, Sanitation, Electricity, ...)
2. Scores urgency from 1 to 5
3. Writes a one-line English summary
4. Suggests an action for the MP's office

The MP dashboard shows category and ward-wise charts, top priority issues, and an AI-written briefing.

## Tech Stack
Python, Streamlit, Google Gemini API (google-genai), Pandas

## How to run
```
pip install -r requirements.txt
export GEMINI_API_KEY=your_key_here     # optional, or paste key in the app sidebar
streamlit run app.py
```
Without a key the app runs in offline demo mode (keyword-based) so it never breaks.

## Future scope
Voice input in more Indic languages, WhatsApp bot intake, map view of complaints, department-wise ticket tracking.
