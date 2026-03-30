"""
app.py — EcoGreenPower Assistant Backend
Flask API που χρησιμοποιεί Claude + ElevenLabs.
Διαβάζει το knowledge.txt αυτόματα.
"""

import os
import re
import base64
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import requests

load_dotenv()

app = Flask(__name__)

# CORS — επιτρέπει κλήσεις από το frontend και το WordPress
CORS(app, origins=[
    "https://ecogreenpower-frontend.onrender.com",
    "https://ecogreenpower.gr",
    "https://www.ecogreenpower.gr",
    "http://localhost:8080",
])

anthropic_client    = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

KNOWLEDGE_FILE = Path(__file__).parent / "knowledge.txt"

def load_knowledge():
    if not KNOWLEDGE_FILE.exists():
        return "Δεν βρέθηκε knowledge base.", "Δεν βρέθηκε knowledge base."
    raw = KNOWLEDGE_FILE.read_text(encoding="utf-8")
    lines = [l for l in raw.splitlines() if not l.strip().startswith("#")]
    raw = "\n".join(lines)
    chat_version = re.sub(r'\[phone text="([^"]+)" speak="([^"]+)"\]', r'\1', raw)
    chat_version = re.sub(r'\[email text="([^"]+)" speak="([^"]+)"\]', r'\1', chat_version)
    voice_version = re.sub(r'\[phone text="([^"]+)" speak="([^"]+)"\]', r'\2', raw)
    voice_version = re.sub(r'\[email text="([^"]+)" speak="([^"]+)"\]', r'\2', voice_version)
    return chat_version.strip(), voice_version.strip()

KNOWLEDGE_CHAT, KNOWLEDGE_VOICE = load_knowledge()
print(f"[INFO] Knowledge Base φορτώθηκε ({len(KNOWLEDGE_CHAT)} χαρακτήρες)")

TTS_REPLACEMENTS = {
    "2310230078":               "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "2310 230078":              "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "2310 23 0078":             "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "231 023 0078":             "δύο τρία ένα, μηδέν δύο τρία, μηδέν μηδέν επτά οκτώ",
    "stkaramesoutis@gmail.com": "stkaramesoutis παπάκι gmail τελεία com",
    "24-48 ωρών":               "είκοσι τεσσάρων έως σαράντα οκτώ ωρών",
}

def add_question_intonation(text: str) -> str:
    def insert_comma(match):
        sentence = match.group(0)
        result = re.sub(
            r'(\S+)(\s+)(\S+)(;)',
            lambda m: m.group(1) + m.group(2) + ', ' + m.group(3) + m.group(4),
            sentence, count=1, flags=re.UNICODE
        )
        return result
    text = re.sub(r'[^.!;]+;', insert_comma, text)
    return text

def prepare_for_tts(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    for original, spoken in TTS_REPLACEMENTS.items():
        text = text.replace(original, spoken)
    text = add_question_intonation(text)
    return text

VOICE_SETTINGS = {
    "stability":         0.55,
    "similarity_boost":  0.80,
    "style":             0.25,
    "use_speaker_boost": True,
    "speed":             0.85,
}

def text_to_speech(text: str) -> tuple[bytes | None, str]:
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        return None, ""
    tts_text = prepare_for_tts(text)
    print(f"[TTS] Κείμενο: {tts_text[:150]}...")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream?output_format=mp3_44100_128"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": tts_text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": VOICE_SETTINGS
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.content, "audio/mpeg"
        print(f"[ElevenLabs ERROR] {r.status_code}: {r.text[:200]}")
        return None, ""
    except Exception as e:
        print(f"[ElevenLabs ERROR] {e}")
        return None, ""

SYSTEM_BASE = """
Είσαι ο Βλάσης, ψηφιακός βοηθός της EcoGreenPower, εταιρείας ηλεκτρολογικών 
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
        messages=messages
    )
    return response.content[0].text

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
        answer = ask_claude(data["question"].strip(), data.get("history", []))
        return jsonify({"answer": answer})
    except Exception as e:
        import traceback; print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/voice", methods=["POST"])
def voice():
    data = request.get_json()
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "Λείπει η ερώτηση"}), 400
    try:
        answer = ask_claude(data["question"].strip(), data.get("history", []))
        audio_bytes, mime_type = text_to_speech(answer)
        result = {"answer": answer}
        if audio_bytes:
            result["audio_base64"] = base64.b64encode(audio_bytes).decode("utf-8")
            result["audio_type"]   = mime_type
        return jsonify(result)
    except Exception as e:
        import traceback; print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
