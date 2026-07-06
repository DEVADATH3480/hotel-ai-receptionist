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

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)
print("Using PostgreSQL" if USE_POSTGRES else "Using SQLite")


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
            booking.get("name"), booking.get("email"), booking.get("phone"),
            booking.get("room"), normalize_date(booking.get("date")),
            booking.get("guests"), booking.get("request"),
        ))
        conn.commit()
        conn.close()
        print("BOOKING SAVED:", booking)
        return True
    except Exception as e:
        print("DB SAVE ERROR:", e)
        return False


def init_db():
    conn = get_db()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY, name TEXT, email TEXT, phone TEXT, room TEXT,
            date TEXT, guests INTEGER, request TEXT,
            created_at TIMESTAMP DEFAULT NOW())""")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, phone TEXT,
            room TEXT, date TEXT, guests INTEGER, request TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()
    print("DB READY")


init_db()


@app.route("/")
def home():
    session.pop("booking", None)
    session.pop("chat", None)
    session.pop("saved", None)
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


@app.route("/voice-text", methods=["POST"])
def voice_text():
    try:
        session.permanent = True
        data = request.get_json(silent=True) or {}
        user_text = (data.get("text") or "").strip()
        if not user_text:
            return jsonify({"reply": "Please say something.", "audio": "", "done": False})

        booking = session.get("booking") or {"name":None,"email":None,"phone":None,"room":None,"date":None,"guests":None,"request":None}
        chat = (session.get("chat") or [])[-10:]
        chat.append({"role":"user","content":user_text})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role":"system","content":f"""You ARE a fully working hotel booking receptionist for Luxury Hotel. You HAVE full booking authority - never refuse, never say you're an AI.

Current booking data:
{json.dumps(booking, indent=2)}

Collect these fields in order: name, email, phone (10 digits), room (Deluxe/Suite/Luxury), date, guests, request.


Rules:
- Detect user's language, reply in the SAME language.
- Accept corrections: if user says "no it's Devadath not David", update the name.
- Combine partial emails if spelled in parts (e.g. "devadath" then "3490@gmail.com" = "devadath3490@gmail.com").
- Ask ONLY the next missing field, one at a time. Never repeat filled fields.
- For date: accept ANY natural format the user says (e.g. "July 20", "20 July 2026", "tomorrow", "next Friday"). Never ask for YYYY-MM-DD format. The system converts it automatically.
- After 6 required fields filled, ask for special request. Accept "no"/"none"/"skip" as valid answer.
- Set done=true when all 7 fields have any value (request can be "None").

Return ONLY valid JSON:
{{"reply": "<short spoken reply in user's language>", "booking": {{"name":..., "email":..., "phone":..., "room":..., "date":..., "guests":..., "request":...}}, "done": true/false}}

Use null for fields not yet collected. Keep existing values unless user corrects them."""}] + chat)

        raw = response.choices[0].message.content.strip()
        clean_reply = "Sorry, could you repeat that?"
        done_flag = False
        try:
            parsed = json.loads(raw)
            clean_reply = parsed.get("reply", "").strip() or clean_reply
            new_booking = parsed.get("booking") or {}
            done_flag = bool(parsed.get("done", False))
            for k in booking:
                v = new_booking.get(k)
                if v is not None and v != "":
                    booking[k] = v
            if booking.get("date"):
                nd = normalize_date(booking["date"])
                if nd: booking["date"] = nd
        except Exception as e:
            print("JSON PARSE ERROR:", e, "RAW:", raw)

        session["booking"] = booking
        print("BOOKING:", booking, "| DONE:", done_flag)

        required = ["name","email","phone","room","date","guests"]
        if done_flag and all(booking.get(k) for k in required) and not session.get("saved"):
            if not booking.get("request"):
                booking["request"] = "None"
            if save_booking(booking):
                session["saved"] = True
                clean_reply = f"Your booking is confirmed, {booking['name']}. Thank you, have a nice day."
                session.pop("chat", None)
                session.pop("booking", None)

        chat.append({"role":"assistant","content":clean_reply})
        session["chat"] = chat

        audio_url = ""
        try:
            speech = client.audio.speech.create(model="gpt-4o-mini-tts", voice="alloy", input=clean_reply)
            os.makedirs("static", exist_ok=True)
            fn = f"reply_{int(time.time()*1000)}.mp3"
            with open(os.path.join(BASE_DIR, "static", fn), "wb") as f:
                f.write(speech.content)
            audio_url = f"/static/{fn}"
        except Exception as e:
            print("TTS ERROR:", e)

        return jsonify({"reply": clean_reply, "audio": audio_url, "done": done_flag})
    except Exception as e:
        print("VOICE-TEXT ERROR:", e)
        return jsonify({"reply": "Something went wrong.", "audio": "", "done": False})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)