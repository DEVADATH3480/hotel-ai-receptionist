
import os
import re
import json
import time
import sqlite3
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from openai import OpenAI
import dateutil.parser


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.permanent_session_lifetime = 60 * 60  


client=OpenAI(api_key=(""))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hotel.db")



def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


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
        cur.execute("""
            INSERT INTO bookings (name, email, phone, room, date, guests, request)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            phone TEXT,
            room TEXT,
            date TEXT,
            guests INTEGER,
            request TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("📂 DB READY AT:", DB_PATH)


init_db()



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
        "name":    data.get("name"),
        "email":   data.get("email"),
        "phone":   data.get("phone"),
        "room":    data.get("room"),
        "date":    data.get("date"),
        "guests":  data.get("guests"),
        "request": data.get("request"),
    })
    if not ok:
        return "Something went wrong saving your booking", 500
    return redirect(url_for("success", name=data.get("name")))



@app.route("/success")
def success():
    return render_template("success.html", name=request.args.get("name"))



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "admin" and request.form.get("password") == "1234":
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

    cur.execute("SELECT COUNT(*) FROM bookings")
    total = cur.fetchone()[0]

    today_date = datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) FROM bookings WHERE date = ?", (today_date,))
    today = cur.fetchone()[0]

    cur.execute("SELECT * FROM bookings ORDER BY id DESC")
    bookings = cur.fetchall()
    conn.close()

    return render_template("admin.html", bookings=bookings, total=total, today=today)


@app.route("/delete/<int:id>")
def delete(id):
    if not session.get("admin"):
        return redirect("/login")
    conn = get_db()
    conn.execute("DELETE FROM bookings WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/admin")



ROOM_KEYWORDS = ["deluxe", "suite", "luxury", "standard", "single", "double"]


def extract_fields(user_text, booking):
    """Best-effort extraction from a user's utterance."""
    text = user_text.strip()
    low  = text.lower()


    if not booking["email"]:
        m = re.search(r'[\w\.\-\+]+@[\w\.\-]+\.\w+', text)
        if m:
            booking["email"] = m.group().replace(" ", "")


    if not booking["phone"]:
        digits = re.sub(r'\D', '', text)
        if len(digits) >= 10:
            booking["phone"] = digits[-10:]


    if not booking["room"]:
        
        for kw in ROOM_KEYWORDS:
            if kw in low:
                booking["room"] = kw.capitalize() + " Room"
                break
      
        if not booking["room"]:
            last_bot_msg = ""
            for m in reversed(session.get("chat", [])):
                if m.get("role") == "assistant":
                    last_bot_msg = m.get("content", "").lower()
                    break
            if "room" in last_bot_msg and len(text.split()) <= 4 and not re.search(r'\d', text):
                booking["room"] = text.title()

    if not booking["date"]:
        parsed = normalize_date(text)
        if parsed:
            booking["date"] = parsed


    if not booking["guests"]:
        words = text.split()
        if len(words) <= 3 and not re.search(r'\d{4,}', text):
            m = re.search(r'\b(\d{1,2})\b', text)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 20:
                    booking["guests"] = n

  
    if not booking["name"]:
        
        m = re.search(r'(?:my name is|i am|i\'m|this is)\s+([a-zA-Z ]{2,40})', low)
        if m:
            booking["name"] = m.group(1).strip().title()
        else:
            
            cleaned = re.sub(r'[^\w\s]', '', low).strip()
            filler = {
                "hello", "hi", "hey", "yes", "no", "ok", "okay",
                "sure", "thanks", "thank you", "hmm", "yeah", "yep",
                "nope", "please", "good morning", "good evening", "good afternoon"
            }
            if (
                "@" not in text
                and not re.search(r'\d', text)
                and 1 <= len(cleaned.split()) <= 4
                and not any(k in low for k in ROOM_KEYWORDS)
                and cleaned not in filler
            ):
                booking["name"] = re.sub(r'[^\w\s]', '', text).strip().title()


    if not booking["request"] and any(k in low for k in [
        "view", "quiet", "balcony", "high floor", "bed",
        "smoking", "non-smoking", "extra", "early check", "late check"
    ]):
        booking["request"] = text

    return booking


@app.route("/voice-text", methods=["POST"])
def voice_text():
    try:
        session.permanent = True
        data = request.get_json(silent=True) or {}
        user_text = (data.get("text") or "").strip()

        if not user_text:
            return jsonify({"reply": "Please say something.", "audio": "", "done": False})

        booking = session.get("booking") or {
            "name": None, "email": None, "phone": None,
            "room": None, "date": None, "guests": None, "request": None,
        }
        booking = extract_fields(user_text, booking)
        session["booking"] = booking

        chat = (session.get("chat") or [])[-8:]
        chat.append({"role": "user", "content": user_text})

       
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"""
You are a luxury hotel receptionist.

Already collected details (DO NOT ask these again):
Name: {booking.get("name")}
Email: {booking.get("email")}
Phone: {booking.get("phone")}
Room: {booking.get("room")}
Date: {booking.get("date")}
Guests: {booking.get("guests")}
Request: {booking.get("request")}

STRICT FLOW ORDER — ask ONE missing field at a time:
1. Name  2. Email  3. Phone  4. Room  5. Date  6. Guests  7. Special request

Rules:
- Speak naturally, short answers, ONE question at a time.
- Ask fields in this EXACT order: Name → Email → Phone → Room → Date → Guests.
- If Room is None above, you MUST ask "Which room would you like — Deluxe, Suite, or Luxury?" and DO NOT ask about date/guests until Room is filled.
- Never repeat a question if that field is already filled above.
- Detect the user's language and reply in the same language.
- NEVER speak JSON aloud.
"""}
            ] + chat,
        )

        ai_reply = response.choices[0].message.content.strip()
        
        clean_reply = re.sub(r'\{[\s\S]*?\}', '', ai_reply).strip() or ai_reply

    
        required = ["name", "email", "phone", "room", "date", "guests"]
        all_required_done = all(booking.get(k) for k in required)

        
        asked_request = session.get("asked_request", False)

        
        low = user_text.lower().strip()
        if asked_request and not booking.get("request"):
           if any(w in low for w in ["no", "nothing", "none", "skip", "that's all", "thats all"]):
              booking["request"] = "None"
              session["booking"] = booking

        done = all_required_done and asked_request and (
        booking.get("request") is not None
       )

        print("📋 BOOKING SO FAR:", booking,
            "| all_req:", all_required_done,
            "| asked_request:", asked_request,
            "| DONE:", done)


        if all_required_done and not asked_request:
           session["asked_request"] = True
           clean_reply = (
               f"Thank you {booking['name']}. Do you have any special request "
               f"like a room with a view, high floor, or a quiet room? "
               f"You can also say 'no' to skip."
            )


        elif done and not session.get("saved"):
            if save_booking(booking):
               session["saved"] = True
               clean_reply = (
                   f"Your booking is confirmed, {booking['name']}. "
                   f"Thank you, have a nice day."
                )
               session.pop("chat", None)
               session.pop("booking", None)
               session.pop("asked_request", None)

        
        chat.append({"role": "assistant", "content": clean_reply})
        session["chat"] = chat

      
        audio_url = ""
        try:
            speak_text = "Your booking has been confirmed." if done else clean_reply
            speech = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice="alloy",
                input=speak_text,
            )
            os.makedirs("static", exist_ok=True)
            filename = f"reply_{int(time.time()*1000)}.mp3"
            filepath = os.path.join(BASE_DIR, "static", filename)
            with open(filepath, "wb") as f:
                f.write(speech.content)
            audio_url = f"/static/{filename}"
        except Exception as e:
            print("TTS ERROR:", e)

        return jsonify({"reply": clean_reply, "audio": audio_url, "done": done})

    except Exception as e:
        print("VOICE-TEXT ERROR:", e)
        return jsonify({"reply": "Something went wrong.", "audio": "", "done": False})



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
