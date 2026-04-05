"""
app.py — EcoGreenPower Assistant Backend
Flask API που χρησιμοποιεί Claude + ElevenLabs.
Αποθηκεύει συνομιλίες σε Google Sheets + email notification.
"""

import os
import re
import base64
import smtplib
import json
from pathlib import Path
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
SHEETS_ID = "1_kZ9D3WDmBukioBs9Tn9VGA5iKlMV7oxiITxtHVxVYY"
NOTIFY_EMAIL = "vlasisrallis@gmail.com"

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"


# ─── GOOGLE SHEETS ────────────────────────────────────────────────────────────

def get_sheet():
    """Συνδέεται στο Google Sheets μέσω Service Account."""
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
            ws = sh.add_worksheet(title="Συνομιλίες", rows=1000, cols=6)
            ws.append_row(["Ημερομηνία", "Ώρα", "Μηνύματα", "Transcript", "Διάρκεια (μηνύματα)", "Τύπος"])
        return ws
    except Exception as e:
        print(f"[Sheets ERROR] {e}")
        return None


def log_to_sheets(history: list, chat_type: str = "chat"):
    """Αποθηκεύει τη συνομιλία στο Google Sheets."""
    try:
        ws = get_sheet()
        if not ws:
            return
        now = datetime.now()
        transcript = "\n".join([
            f"{'Πελάτης' if m['role'] == 'user' else 'Στέλιος'}: {m['content']}"
            for m in history
        ])
        ws.append_row([
            now.strftime("%d/%m/%Y"),
            now.strftime("%H:%M"),
            len(history),
            transcript,
            len([m for m in history if m["role"] == "user"]),
            chat_type,
        ])
        print(f"[Sheets] Αποθηκεύτηκε συνομιλία {len(history)} μηνυμάτων")
    except Exception as e:
        print(f"[Sheets ERROR] {e}")


# ─── EMAIL NOTIFICATION ───────────────────────────────────────────────────────

def send_email_notification(history: list, question: str, answer: str):
    """Στέλνει email notification για κάθε νέα ερώτηση πελάτη."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"💬 Νέο μήνυμα στον Στέλιο — EcoGreenPower"
        msg["From"] = GMAIL_USER
        msg["To"] = NOTIFY_EMAIL

        # Φτιάχνουμε το transcript
        transcript_html = ""
        for m in history:
            role = "🧑 Πελάτης" if m["role"] == "user" else "🤖 Στέλιος"
            color = "#1a56db" if m["role"] == "user" else "#374151"
            transcript_html += f'<p><strong style="color:{color}">{role}:</strong> {m["content"]}</p>'

        # Τελευταία ερώτηση + απάντηση
        transcript_html += f'<p><strong style="color:#1a56db">🧑 Πελάτης:</strong> {question}</p>'
        transcript_html += f'<p><strong style="color:#374151">🤖 Στέλιος:</strong> {answer}</p>'

        html = f"""
        <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
          <div style="background: #0a1628; padding: 20px; border-radius: 8px 8px 0 0;">
            <h2 style="color: white; margin: 0;">⚡ EcoGreenPower — Νέο μήνυμα</h2>
            <p style="color: #00b4d8; margin: 4px 0 0;">Ο Στέλιος έλαβε νέο μήνυμα</p>
          </div>
          <div style="background: #f8faff; padding: 20px; border: 1px solid #e2e8f0; border-radius: 0 0 8px 8px;">
            <h3 style="color: #0a1628;">Νέα ερώτηση:</h3>
            <div style="background: white; padding: 12px; border-left: 4px solid #1a56db; border-radius: 4px; margin-bottom: 16px;">
              <strong>{question}</strong>
            </div>
            <h3 style="color: #0a1628;">Απάντηση Στέλιου:</h3>
            <div style="background: white; padding: 12px; border-left: 4px solid #00b4d8; border-radius: 4px; margin-bottom: 16px;">
              {answer}
            </div>
            <hr style="border: 1px solid #e2e8f0;">
            <h3 style="color: #0a1628;">Ολόκληρη η συνομιλία:</h3>
            <div style="background: white; padding: 12px; border-radius: 4px;">
              {transcript_html}
            </div>
            <p style="color: #64748b; font-size: 12px; margin-top: 16px;">
              {datetime.now().strftime("%d/%m/%Y %H:%M")} — EcoGreenPower AI Assistant
            </p>
          </div>
        </body></html>
        """

        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"[Email] Notification στάλθηκε για: {question[:50]}")
    except Exception as e:
        print(f"[Email ERROR] {e}")


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


def prepare_for_tts(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    for original, spoken in TTS_REPLACEMENTS.items():
        text = text.replace(original, spoken)
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


def ask_claude(question: str, history: list = []) -> str:
    recent = history[-6:] if len(history) > 6 else history
    messages = [*recent, {"role": "user", "content": question}]
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=get_system_prompt(),
        messages=messages,
    )
    return response.content[0].text


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
        answer = ask_claude(question, history)

        # Log σε background (δεν καθυστερεί την απάντηση)
        full_history = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}]

        # Email notification για κάθε μήνυμα
        send_email_notification(history, question, answer)

        # Google Sheets — log κάθε 3 μηνύματα χρήστη
        user_msgs = len([m for m in full_history if m["role"] == "user"])
        if user_msgs % 3 == 0:
            log_to_sheets(full_history, "chat")

        return jsonify({"answer": answer})
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
        answer = ask_claude(question, history)
        audio_bytes, mime_type = text_to_speech(answer)

        full_history = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        send_email_notification(history, question, answer)

        result = {"answer": answer}
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
