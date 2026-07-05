
import os
import re
import json
import time
import sqlite3
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from openai import OpenAI
import dateutil.parser
import psycopg


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.permanent_session_lifetime = 60 * 60  


client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hotel.db")

Let me give you the complete updated app.py — just copy-replace the whole file, push, and it should work.

📄 Complete new app.py — replace your entire file with this
import os
import re
import json
import time
import sqlite3
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from openai import OpenAI
import dateutil.parser
import psycopg

# =========================
# 🔧 APP CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.permanent_session_lifetime = 60 * 60

client = OpenAI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hotel.db")

# Detect environment
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)
print(f"🗄  Using {'PostgreSQL (Neon)' if USE_POSTGRES else 'SQLite (local)'}")


# =========================
# 🛠 HELPERS
# =========================
def get_db():
    if USE_POSTGRES:
        return psycopg.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def ph():
    return "%s" if USE_POSTGRES else "?"

def normalize_date(text):
    if not text:
        return None
    try:
        return dateutil.parser.parse(str(text), fuzzy=True).strftime("%Y-%m-%d")
    except Exception:
        return None

def save_booking(booking):
    try:
        conn = get_db()
        cur = conn.cursor()
        p = ph()
        cur.execute(f"""
            INSERT INTO bookings (name, email, phone, room, date, guests, request)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (
            booking.get("name"),
            booking.get("email"),
            booking.get("phone"),
            booking.get("room"),
            normalize_date(booking.get("date")),
            booking.get("guests"),
            booking.get("request"),
        ))
        conn.commit()
        conn.close()
        print("✅ BOOKING SAVED:", booking)
        return True
    except Exception as e:
        print("❌ DB SAVE ERROR:", e)
        return False


# =========================
# 🧠 INIT DATABASE
# =========================
def init_db():
    conn = get_db()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                name TEXT, email TEXT, phone TEXT, room TEXT,
                date TEXT, guests INTEGER, request TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, email TEXT, phone TEXT, room TEXT,
                date TEXT, guests INTEGER, request TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()
    print(f"📂 DB READY ({'Postgres' if USE_POSTGRES else 'SQLite'})")

init_db()


# =========================
# 🏠 ROUTES
# =========================
@app.route("/")
def home():
    session.pop("booking", None)
    session.pop("chat", None)
    session.pop("saved", None)
    session.pop("asked_request", None)
    return render_template("hai.html")


@app.route("/book", methods=["POST"])
def book_now():
    data = request.form
    ok = save_booking({
        "name": data.get("name"), "email": data.get("email"),
        "phone": data.get("phone"), "room": data.get("room"),
        "date": data.get("date"), "guests": data.get("guests"),
        "request": data.get("request"),
    })
    if not ok:
        return "Something went wrong", 500
    return redirect(url_for("success", name=data.get("name")))


@app.route("/success")
def success():
    return render_template("success.html", name=request.args.get("name"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("username") == os.environ.get("ADMIN_USER", "admin")
                and request.form.get("password") == os.environ.get("ADMIN_PASS", "1234")):
            session["admin"] = True
            return redirect("/admin")
        return render_template("login.html", error="Invalid login")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/login")
    conn = get_db()
    cur = conn.cursor()
    p = ph()

    cur.execute("SELECT COUNT(*) FROM bookings")
    total = cur.fetchone()[0]

    today_date = datetime.now().strftime("%Y-%m-%d")
    cur.execute(f"SELECT COUNT(*) FROM bookings WHERE DATE(created_at) = {p}", (today_date,))
    today = cur.fetchone()[0]

    cur.execute("SELECT id, name, email, phone, room, date, guests, request FROM bookings ORDER BY id DESC")
    bookings = cur.fetchall()
    conn.close()
    return render_template("admin.html", bookings=bookings, total=total, today=today)


@app.route("/delete/<int:id>")
def delete(id):
    if not session.get("admin"):
        return redirect("/login")
    conn = get_db()
    p = ph()
    conn.cursor().execute(f"DELETE FROM bookings WHERE id={p}", (id,))
    conn.commit()
    conn.close()
    return redirect("/admin")


# =========================
# 🎤 VOICE AI (unchanged from before)
# =========================
ROOM_KEYWORDS = ["deluxe", "suite", "luxury", "standard", "single", "double"]

def extract_fields(user_text, booking):
    text = user_text.strip()
    low = text.lower()
    if not booking["email"]:
        m = re.search(r'[\w\.\-\+]+@[\w\.\-]+\.\w+', text)
        if m: booking["email"] = m.group().replace(" ", "")
    if not booking["phone"]:
        digits = re.sub(r'\D', '', text)
        if len(digits) >= 10: booking["phone"] = digits[-10:]
    if not booking["room"]:
        for kw in ROOM_KEYWORDS:
            if kw in low:
                booking["room"] = kw.capitalize() + " Room"; break
        if not booking["room"]:
            last_bot = ""
            for m in reversed(session.get("chat", [])):
                if m.get("role") == "assistant":
                    last_bot = m.get("content", "").lower(); break
            if "room" in last_bot and len(text.split()) <= 4 and not re.search(r'\d', text):
                booking["room"] = text.title()
    if not booking["date"]:
        parsed = normalize_date(text)
        if parsed: booking["date"] = parsed
    if not booking["guests"]:
        words = text.split()
        if len(words) <= 3 and not re.search(r'\d{4,}', text):
            m = re.search(r'\b(\d{1,2})\b', text)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 20: booking["guests"] = n
    if not booking["name"]:
        m = re.search(r'(?:my name is|i am|i\'m|this is)\s+([a-zA-Z ]{2,40})', low)
        if m:
            booking["name"] = m.group(1).strip().title()
        else:
            cleaned = re.sub(r'[^\w\s]', '', low).strip()
            filler = {"hello","hi","hey","yes","no","ok","okay","sure","thanks","thank you","hmm","yeah","yep","nope","please","good morning","good evening","good afternoon"}
            if ("@" not in text and not re.search(r'\d', text)
                and 1 <= len(cleaned.split()) <= 4
                and not any(k in low for k in ROOM_KEYWORDS)
                and cleaned not in filler):
                booking["name"] = re.sub(r'[^\w\s]', '', text).strip().title()
    if not booking["request"] and any(k in low for k in ["view","quiet","balcony","high floor","bed","smoking","non-smoking","extra","early check","late check"]):
        booking["request"] = text
    return booking


@app.route("/voice-text", methods=["POST"])
def voice_text():
    try:
        session.permanent = True
        data = request.get_json(silent=True) or {}
        user_text = (data.get("text") or "").strip()
        if not user_text:
            return jsonify({"reply":"Please say something.","audio":"","done":False})

        booking = session.get("booking") or {"name":None,"email":None,"phone":None,"room":None,"date":None,"guests":None,"request":None}
        booking = extract_fields(user_text, booking)
        session["booking"] = booking

        chat = (session.get("chat") or [])[-8:]
        chat.append({"role":"user","content":user_text})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":f"""
You are a luxury hotel receptionist.
Already collected: Name:{booking.get("name")} Email:{booking.get("email")} Phone:{booking.get("phone")} Room:{booking.get("room")} Date:{booking.get("date")} Guests:{booking.get("guests")} Request:{booking.get("request")}
Ask fields in EXACT order: Name -> Email -> Phone -> Room -> Date -> Guests.
If Room is None you MUST ask "Which room - Deluxe, Suite, or Luxury?" and NOT move on.
Speak short, one question at a time, never repeat filled fields. Never speak JSON aloud.
"""}] + chat)
        ai_reply = response.choices[0].message.content.strip()
        clean_reply = re.sub(r'\{[\s\S]*?\}','',ai_reply).strip() or ai_reply

        required = ["name","email","phone","room","date","guests"]
        all_req = all(booking.get(k) for k in required)
        asked = session.get("asked_request", False)
        low = user_text.lower().strip()
        if asked and not booking.get("request"):
            if any(w in low for w in ["no","nothing","none","skip","that's all","thats all"]):
                booking["request"] = "None"; session["booking"] = booking
        done = all_req and asked and booking.get("request") is not None

        if all_req and not asked:
            session["asked_request"] = True
            clean_reply = f"Thank you {booking['name']}. Any special request like a view or quiet room? Say 'no' to skip."
        elif done and not session.get("saved"):
            if save_booking(booking):
                session["saved"] = True
                clean_reply = f"Your booking is confirmed, {booking['name']}. Thank you, have a nice day."
                session.pop("chat",None); session.pop("booking",None); session.pop("asked_request",None)

        chat.append({"role":"assistant","content":clean_reply})
        session["chat"] = chat

        audio_url = ""
        try:
            speak = "Your booking has been confirmed." if done else clean_reply
            speech = client.audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=speak)
            os.makedirs("static", exist_ok=True)
            fn = f"reply_{int(time.time()*1000)}.mp3"
            fp = os.path.join(BASE_DIR,"static",fn)
            with open(fp,"wb") as f: f.write(speech.content)
            audio_url = f"/static/{fn}"
        except Exception as e:
            print("TTS ERROR:",e)

        return jsonify({"reply":clean_reply,"audio":audio_url,"done":done})
    except Exception as e:
        print("VOICE-TEXT ERROR:",e)
        return jsonify({"reply":"Something went wrong.","audio":"","done":False})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)


