"""
app.py — EcoGreenPower Assistant Backend
Routing: DeepSeek πρώτα → Claude αν απάντηση ανεπαρκής
Session-based Sheets logging: μία γραμμή ανά συνομιλία, ενημερώνεται συνεχώς
"""

import os
import re
import base64
import json
import threading
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import requests
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

app = Flask(__name__)

CORS(
    app,
    origins=[
        "https://ecogreenpower-frontend.onrender.com",
        "https://ecogreenpower.gr",
        "https://www.ecogreenpower.gr",
        "https://ecogreenpower-new.pages.dev",
        "https://*.ecogreenpower-new.pages.dev",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SHEETS_ID = "1_kZ9D3WDmBukioBs9Tn9VGA5iKlMV7oxiITxtHVxVYY"
DEEPSEEK_MIN_CHARS = 80

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"
session_row_cache: dict[str, int] = {}


# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────

def get_sheet():
    try:
        creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json:
            return None
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEETS_ID)
        try:
            ws = sh.worksheet("Συνομιλίες")
        except:
            ws = sh.add_worksheet(title="Συνομιλίες", rows=2000, cols=8)
            ws.append_row(["Session ID", "Ημερομηνία", "Ώρα έναρξης", "Ώρα τελευταίου", "Ερωτήσεις", "Transcript", "Μοντέλα", "Τύπος"])
        return ws
    except Exception as e:
        print(f"[Sheets ERROR] {e}")
        return None


def _upsert_session_bg(session_id, history, model_used, chat_type):
    try:
        ws = get_sheet()
        if not ws:
            return
        now = datetime.now()
        lines = []
        model_counts: dict[str, int] = {}
        user_messages = [m for m in history if m["role"] == "user"]
        for m in history:
            if m["role"] == "user":
                lines.append(f"👤 Πελάτης: {m['content']}")
            else:
                mdl = m.get("model", model_used)
                model_counts[mdl] = model_counts.get(mdl, 0) + 1
                lines.append(f"🤖 Στέλιος [{mdl}]: {m['content']}")
        transcript = "\n".join(lines)
        models_summary = ", ".join([f"{k}({v})" for k, v in model_counts.items() if v > 0])
        user_count = len(user_messages)
        if session_id in session_row_cache:
            row = session_row_cache[session_id]
            ws.update(f"D{row}", [[now.strftime("%H:%M")]])
            ws.update(f"E{row}", [[user_count]])
            ws.update(f"F{row}", [[transcript]])
            ws.update(f"G{row}", [[models_summary]])
            print(f"[Sheets] ✓ Updated row {row} — {session_id[:20]} ({user_count} ερωτήσεις)")
        else:
            ws.append_row([session_id, now.strftime("%d/%m/%Y"), now.strftime("%H:%M"), now.strftime("%H:%M"), user_count, transcript, models_summary, chat_type])
            all_values = ws.col_values(1)
            row_num = len(all_values)
            session_row_cache[session_id] = row_num
            print(f"[Sheets] ✓ New row {row_num} — {session_id[:20]}")
    except Exception as e:
        print(f"[Sheets ERROR] {e}")


def log_session(session_id, history, model_used, chat_type="chat"):
    t = threading.Thread(target=_upsert_session_bg, args=(session_id, history, model_used, chat_type), daemon=True)
    t.start()


# ─── KNOWLEDGE ────────────────────────────────────────────────────────────────

def load_knowledge():
    if not KNOWLEDGE_FILE.exists():
        return "Δεν βρέθηκε knowledge base.", "Δεν βρέθηκε knowledge base."
    raw = KNOWLEDGE_FILE.read_text(encoding="utf-8")
    lines = [l for l in raw.splitlines() if not l.strip().startswith("#")]
    raw = "\n".join(lines)
    chat_version = re.sub(r'\[phone text="([^"]+)" speak="([^"]+)"\]', r"\1", raw)
    chat_version = re.sub(r'\[email text="([^"]+)" speak="([^"]+)"\]', r"\1", chat_version)
    voice_version = re.sub(r'\[phone text="([^"]+)" speak="([^"]+)"\]', r"\2", raw)
    voice_version = re.sub(r'\[email text="([^"]+)" speak="([^"]+)"\]', r"\2", voice_version)
    return chat_version.strip(), voice_version.strip()


KNOWLEDGE_CHAT, KNOWLEDGE_VOICE = load_knowledge()
print(f"[INFO] Knowledge Base φορτώθηκε ({len(KNOWLEDGE_CHAT)} χαρακτήρες)")

TTS_REPLACEMENTS = {
    "2310230078": "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "2310 230078": "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "stkaramesoutis@gmail.com": "stkaramesoutis παπάκι gmail τελεία com",
    "24-48 ωρών": "είκοσι τεσσάρων έως σαράντα οκτώ ωρών",
}

# Χάρτης ψηφίων → ελληνικά
DIGIT_WORDS = {
    "0": "μηδέν", "1": "ένα", "2": "δύο", "3": "τρία",
    "4": "τέσσερα", "5": "πέντε", "6": "έξι", "7": "επτά",
    "8": "οκτώ", "9": "εννέα"
}


def phone_to_words(match) -> str:
    """Μετατρέπει τηλέφωνο 10+ ψηφίων σε λεκτική μορφή ψηφίο-ψηφίο."""
    number = re.sub(r'\D', '', match.group(0))  # κρατάμε μόνο ψηφία
    return " ".join(DIGIT_WORDS[d] for d in number)


def prepare_for_tts(text: str) -> str:
    # Αφαίρεση markdown
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)

    # Γνωστές αντικαταστάσεις πρώτα (πριν το phone detection)
    for original, spoken in TTS_REPLACEMENTS.items():
        text = text.replace(original, spoken)

    # Τηλέφωνα 10+ ψηφίων (με ή χωρίς κενά/παύλες) → ψηφίο-ψηφίο
    # π.χ. 6948494524, 694 849 4524, 694-849-4524
    text = re.sub(r'\b[\d][\d\s\-]{8,}[\d]\b', phone_to_words, text)

    return text


VOICE_SETTINGS = {
    "stability": 0.35,
    "similarity_boost": 0.80,
    "style": 0.45,
    "use_speaker_boost": True,
    "speed": 0.95,
}


def text_to_speech(text: str) -> tuple[bytes | None, str]:
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        return None, ""
    tts_text = prepare_for_tts(text)
    print(f"[TTS] {tts_text[:100]}...")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream?output_format=mp3_44100_128"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {"text": tts_text, "model_id": "eleven_turbo_v2_5", "voice_settings": VOICE_SETTINGS}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.content, "audio/mpeg"
        return None, ""
    except Exception as e:
        print(f"[ElevenLabs ERROR] {e}")
        return None, ""


SYSTEM_BASE = """
Είσαι ο Στέλιος, ψηφιακός βοηθός της EcoGreenPower, εταιρείας ηλεκτρολογικών 
εγκαταστάσεων στη Θεσσαλονίκη. Μιλάς πάντα στα Ελληνικά, επαγγελματικά αλλά φιλικά.

ΣΤΥΛ:
- Κάνε ΜΙΑ ερώτηση κάθε φορά
- Σύντομες απαντήσεις, σαν τηλεφωνική συνομιλία
- Αν ρωτήσουν ΥΔΕ/ΔΕΗ → ρώτα «Κατοικία ή επαγγελματικός χώρος;»
- Αν ρωτήσουν βλάβη → ρώτα «Πού εντοπίζετε το πρόβλημα;»
- Μετά από 2-3 ανταλλαγές → πρότεινε ραντεβού
- ΜΗΝ παρουσιάζεις μεγάλες λίστες

ΜΟΡΦΟΠΟΙΗΣΗ:
- ΜΗΝ χρησιμοποιείς ποτέ markdown (**, *, #)
- Γράψε ΠΑΝΤΑ απλό κείμενο
- Το τηλέφωνο γράψε ως: 2310230078

ΚΑΝΟΝΕΣ:
- Απάντα ΜΟΝΟ βάσει του Knowledge Base
- Αν δεν ξέρεις → παρέπεμπε στο τηλέφωνο ή email
- Μην εφευρίσκεις τιμές ή δεδομένα

KNOWLEDGE BASE:
"""


def get_system_prompt() -> str:
    return SYSTEM_BASE + KNOWLEDGE_CHAT


# ─── AI ROUTING ───────────────────────────────────────────────────────────────

def ask_deepseek(question: str, history: list) -> str | None:
    if not DEEPSEEK_API_KEY:
        return None
    try:
        recent = history[-6:] if len(history) > 6 else history
        messages = [
            {"role": "system", "content": get_system_prompt()},
            *[{"role": m["role"], "content": m["content"]} for m in recent],
            {"role": "user", "content": question}
        ]
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": messages, "max_tokens": 512, "temperature": 0.7},
            timeout=15
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        print(f"[DeepSeek ERROR] status {r.status_code}")
        return None
    except Exception as e:
        print(f"[DeepSeek ERROR] {e}")
        return None


def is_sufficient(answer: str) -> bool:
    if not answer or len(answer.strip()) < DEEPSEEK_MIN_CHARS:
        return False
    uncertainty_phrases = [
        "δεν γνωρίζω", "δεν είμαι σίγουρος", "δεν έχω πληροφορίες",
        "δεν μπορώ να απαντήσω", "δεν ξέρω", "άγνωστο",
        "i don't know", "i'm not sure",
    ]
    if any(p in answer.lower() for p in uncertainty_phrases):
        return False
    return True


def ask_smart(question: str, history: list) -> tuple[str, str]:
    print(f"[Router] {question[:60]}...")
    ds_answer = ask_deepseek(question, history)
    if ds_answer and is_sufficient(ds_answer):
        print(f"[Router] ✓ DeepSeek ({len(ds_answer)} chars)")
        return ds_answer, "deepseek"
    print(f"[Router] → Claude")
    recent = history[-6:] if len(history) > 6 else history
    messages = [
        *[{"role": m["role"], "content": m["content"]} for m in recent],
        {"role": "user", "content": question}
    ]
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=get_system_prompt(),
        messages=messages,
    )
    return response.content[0].text, "claude"


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ecogreenpower-assistant"})


@app.route("/reload", methods=["POST"])
def reload_knowledge():
    global KNOWLEDGE_CHAT, KNOWLEDGE_VOICE
    KNOWLEDGE_CHAT, KNOWLEDGE_VOICE = load_knowledge()
    return jsonify({"status": "ok", "chars": len(KNOWLEDGE_CHAT)})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "Λείπει η ερώτηση"}), 400
    if len(data["question"]) > 500:
        return jsonify({"error": "Πολύ μεγάλη ερώτηση"}), 400
    try:
        question = data["question"].strip()
        history = data.get("history", [])
        session_id = data.get("session_id", f"unknown_{datetime.now().timestamp()}")
        answer, model_used = ask_smart(question, history)
        full_history = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer, "model": model_used}]
        log_session(session_id, full_history, model_used, "chat")
        return jsonify({"answer": answer, "model": model_used})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/voice", methods=["POST"])
def voice():
    data = request.get_json()
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "Λείπει η ερώτηση"}), 400
    try:
        question = data["question"].strip()
        history = data.get("history", [])
        session_id = data.get("session_id", f"unknown_{datetime.now().timestamp()}")
        answer, model_used = ask_smart(question, history)
        audio_bytes, mime_type = text_to_speech(answer)
        full_history = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer, "model": model_used}]
        log_session(session_id, full_history, model_used, "voice")
        result = {"answer": answer, "model": model_used}
        if audio_bytes:
            result["audio_base64"] = base64.b64encode(audio_bytes).decode("utf-8")
            result["audio_type"] = mime_type
        return jsonify(result)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
