"""
AMAL BOT - Telegram Email & OTP Service
Run: python amal.py
Install: pip install python-telegram-bot requests
"""

import asyncio
import logging
import random
import re
import string
import time
import threading

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ── CONFIG ────────────────────────────────────────────────────────────────────

BOT_TOKEN    = "8762002906:AAGD0-ZBTtRHrFHso9qec6iPvQOhtDNkqoc"
SUPABASE_URL = "https://hjzwndttkhhicvlookhb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhqenduZHR0a2hoaWN2bG9va2hiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg3NDY1NzgsImV4cCI6MjA4NDMyMjU3OH0.5SUDbHahV1NEKe9NAWe4Al4lGBjEwXJzixQY9SEWfew"

BASE_EMBUX  = "https://embux.io"
BASE_MAILTM = "https://api.mail.tm"
SESSION_TTL = 600  # 10 minutes

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── SUPABASE ──────────────────────────────────────────────────────────────────

SB_HEADERS = {
    "apikey"       : SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type" : "application/json",
    "Prefer"       : "return=representation"
}

def db_get_user(chat_id: int):
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?chat_id=eq.{chat_id}",
            headers=SB_HEADERS, timeout=10
        )
        data = r.json()
        return data[0] if data else None
    except Exception as e:
        log.error(f"db_get_user: {e}")
        return None

def db_save_user(chat_id: int, username: str, password: str):
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "chat_id"       : chat_id,
                "embux_username": username,
                "embux_password": password
            }, timeout=10
        )
        return True
    except Exception as e:
        log.error(f"db_save_user: {e}")
        return False

# ── MAIL.TM ───────────────────────────────────────────────────────────────────

def mailtm_create():
    try:
        dr     = requests.get(f"{BASE_MAILTM}/domains", timeout=10).json()
        domain = dr["hydra:member"][0]["domain"]
        rand   = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        addr   = f"{rand}@{domain}"
        pwd    = "TempPass@" + rand

        requests.post(f"{BASE_MAILTM}/accounts",
            json={"address": addr, "password": pwd}, timeout=10)

        tr = requests.post(f"{BASE_MAILTM}/token",
            json={"address": addr, "password": pwd}, timeout=10).json()

        return addr, tr["token"]
    except Exception as e:
        log.error(f"mailtm_create: {e}")
        return None, None

def mailtm_get_verify_link(mail_token: str) -> str:
    headers = {"Authorization": f"Bearer {mail_token}"}
    for attempt in range(15):
        try:
            msgs = requests.get(f"{BASE_MAILTM}/messages",
                headers=headers, timeout=10).json()
            for msg in msgs.get("hydra:member", []):
                full = requests.get(f"{BASE_MAILTM}/messages/{msg['id']}",
                    headers=headers, timeout=10).json()
                html = full.get("html", "")
                if isinstance(html, list): html = " ".join(html)
                text = full.get("text", "") + html
                match = re.search(
                    r'https?://(?:www\.)?embux\.io/accounts/verify/[^\s\]\'"<>]+',
                    text
                )
                if match:
                    return match.group()
        except Exception as e:
            log.error(f"mailtm poll {attempt+1}: {e}")
        time.sleep(4)
    return None

# ── CF EMAIL DECODER ──────────────────────────────────────────────────────────

def decode_cf_email(encoded: str) -> str:
    key = int(encoded[:2], 16)
    return ''.join(
        chr(int(encoded[i:i+2], 16) ^ key)
        for i in range(2, len(encoded), 2)
    )

# ── EMBUX ─────────────────────────────────────────────────────────────────────

def embux_create_account(progress_cb=None) -> dict:
    """Create embux account with optional progress callback"""

    def progress(msg):
        if progress_cb:
            progress_cb(msg)
        log.info(msg)

    progress("📧 টেম্পোরারি ইমেইল তৈরি হচ্ছে...")
    addr, mail_token = mailtm_create()
    if not addr:
        raise Exception("Failed to create temp email")

    progress("🔧 Embux একাউন্ট রেজিস্ট্রেশন হচ্ছে...")
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    pg   = sess.get(f"{BASE_EMBUX}/accounts/register/", timeout=15)
    csrf = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', pg.text)
    if not csrf:
        raise Exception("No CSRF on register page")

    user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    pwd  = "Ax@" + ''.join(random.choices(string.ascii_letters + string.digits, k=12))

    sess.post(f"{BASE_EMBUX}/accounts/register/", data={
        "csrfmiddlewaretoken"    : csrf.group(1),
        "username"               : user,
        "email"                  : addr,
        "password1"              : pwd,
        "password2"              : pwd,
        "subscribe_to_newsletter": "on",
    }, headers={
        "Referer"     : f"{BASE_EMBUX}/accounts/register/",
        "Origin"      : BASE_EMBUX,
        "Content-Type": "application/x-www-form-urlencoded",
    }, timeout=15)

    progress("📬 ভেরিফিকেশন ইমেইলের জন্য অপেক্ষা করা হচ্ছে...")
    link = mailtm_get_verify_link(mail_token)
    if not link:
        raise Exception("Verification email never arrived")

    progress("✅ একাউন্ট ভেরিফাই হচ্ছে...")
    sess.get(link, timeout=15)
    return {"username": user, "password": pwd}

def embux_login(username: str, password: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    pg   = sess.get(f"{BASE_EMBUX}/accounts/login/", timeout=15)
    csrf = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', pg.text)
    if not csrf:
        raise Exception("No CSRF on login page")

    time.sleep(0.7)

    sess.post(f"{BASE_EMBUX}/accounts/login/", data={
        "csrfmiddlewaretoken": csrf.group(1),
        "username"           : username,
        "password"           : password,
    }, headers={
        "Referer"     : f"{BASE_EMBUX}/accounts/login/",
        "Origin"      : BASE_EMBUX,
        "Content-Type": "application/x-www-form-urlencoded",
    }, allow_redirects=True, timeout=15)

    if "sessionid" not in sess.cookies:
        raise Exception("Login failed - no sessionid")

    return sess

def embux_start_task(sess: requests.Session) -> dict:
    home  = sess.get(f"{BASE_EMBUX}/home/", timeout=15)
    uuids = list(set(re.findall(
        r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        home.text
    )))
    if not uuids:
        raise Exception("No tasks on home page")

    tid = uuids[0]
    sess.get(f"{BASE_EMBUX}/task/{tid}/", timeout=15)
    time.sleep(0.7)

    csrf = sess.cookies.get("csrftoken", "")
    sess.post(f"{BASE_EMBUX}/task/{tid}/execute/", data={
        "csrfmiddlewaretoken": csrf,
        "start_task"         : "",
    }, headers={
        "Referer"     : f"{BASE_EMBUX}/task/{tid}/",
        "Origin"      : BASE_EMBUX,
        "Content-Type": "application/x-www-form-urlencoded",
    }, allow_redirects=False, timeout=15)

    time.sleep(1)

    exec_pg = sess.get(f"{BASE_EMBUX}/task/{tid}/execute/", timeout=15)
    email   = embux_extract_email(exec_pg.text)
    return {"task_id": tid, "email": email}

def embux_extract_email(html: str) -> str:
    m = re.search(r'data-cfemail="([a-f0-9]+)"', html, re.I)
    if m:
        return decode_cf_email(m.group(1))
    m = re.search(r'/cdn-cgi/l/email-protection#([a-f0-9]+)', html, re.I)
    if m:
        return decode_cf_email(m.group(1))
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    m = re.search(r'\bEmail\s+(\S+@\S+)', text, re.I)
    if m:
        return m.group(1)
    return None

def embux_scan_otp(sess: requests.Session, task_id: str) -> str:
    csrf = sess.cookies.get("csrftoken", "")
    resp = sess.post(
        f"{BASE_EMBUX}/ajax/task/{task_id}/check_code/",
        headers={
            "Referer"       : f"{BASE_EMBUX}/task/{task_id}/execute/",
            "Origin"        : BASE_EMBUX,
            "X-CSRFToken"   : csrf,
            "Accept"        : "*/*",
            "Content-Length": "0",
        }, timeout=15
    )
    try:
        d    = resp.json()
        code = (d.get("code") or d.get("otp") or
                d.get("verification_code") or
                (d.get("data") or {}).get("code"))
        if code and re.match(r'^\d{4,8}$', str(code)):
            return str(code)
    except Exception:
        t = resp.text.strip()
        if re.match(r'^\d{4,8}$', t):
            return t
    return None

# ── SESSION MANAGER ───────────────────────────────────────────────────────────

active_sessions = {}
sessions_lock   = threading.Lock()

# Track users currently getting email (prevent double sessions)
getting_email = set()
getting_lock  = threading.Lock()

def session_close(chat_id: int, bot_app=None, reason: str = "expired"):
    with sessions_lock:
        if chat_id in active_sessions:
            data = active_sessions.pop(chat_id)
            if data.get("timer"):
                data["timer"].cancel()
            log.info(f"Session closed chat_id={chat_id} reason={reason}")

    if bot_app and reason == "expired":
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⏰ *ইমেইলের মেয়াদ শেষ!*\n\n"
                    "নিচে বাটনে চাপ দিয়ে নতুন ইমেইল নিন 👇"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📧 নতুন ইমেইল", callback_data="get_email")
                ]])
            ),
            bot_app.loop
        )

def session_create(chat_id: int, sess, task_id: str, email: str, bot_app=None):
    with sessions_lock:
        # Cancel any existing session first
        if chat_id in active_sessions:
            old = active_sessions[chat_id]
            if old.get("timer"):
                old["timer"].cancel()

        timer = threading.Timer(SESSION_TTL, session_close,
            args=[chat_id, bot_app, "expired"])
        timer.daemon = True
        timer.start()

        active_sessions[chat_id] = {
            "session": sess,
            "task_id": task_id,
            "email"  : email,
            "created": time.time(),
            "timer"  : timer,
        }
    log.info(f"Session created chat_id={chat_id} email={email}")

def session_get(chat_id: int):
    with sessions_lock:
        return active_sessions.get(chat_id)

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def kb_main():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📧 ইমেইল নিন", callback_data="get_email")
    ]])

def kb_otp():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 OTP স্ক্যান",  callback_data="scan_otp"),
        InlineKeyboardButton("📧 নতুন ইমেইল", callback_data="new_email")
    ]])

def kb_new():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📧 নতুন ইমেইল নিন", callback_data="get_email")
    ]])

# ── BOT HANDLERS ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ আপনার একাউন্ট চেক করা হচ্ছে...")

    user = db_get_user(chat_id)

    if not user or not user.get("embux_username"):
        # Show setup progress messages
        msg = await update.message.reply_text(
            "🆕 *প্রথমবার ব্যবহার!*\n\n"
            "⚙️ আপনার জন্য একটি ইমেইল সিস্টেম তৈরি হচ্ছে...\n"
            "⏳ এটি ৩০-৬০ সেকেন্ড সময় নেবে, অপেক্ষা করুন।",
            parse_mode="Markdown"
        )

        # Progress updater
        progress_msgs = []
        def on_progress(text):
            progress_msgs.append(text)
            asyncio.run_coroutine_threadsafe(
                msg.edit_text(
                    "🆕 *প্রথমবার ব্যবহার!*\n\n" +
                    "\n".join(progress_msgs[-4:]) +
                    "\n\n⏳ অপেক্ষা করুন...",
                    parse_mode="Markdown"
                ),
                ctx.application.loop
            )

        try:
            creds = await asyncio.get_event_loop().run_in_executor(
                None, lambda: embux_create_account(on_progress)
            )
            db_save_user(chat_id, creds["username"], creds["password"])
            await msg.edit_text(
                "✅ *সেটআপ সম্পন্ন!*\n\n"
                "🎉 আপনার ইমেইল সিস্টেম প্রস্তুত!\n\n"
                "📌 *কিভাবে ব্যবহার করবেন:*\n"
                "১. নিচের বাটনে চাপ দিয়ে ইমেইল নিন\n"
                "২. ইমেইলটি শুধুমাত্র *Instagram* এর জন্য ব্যবহার করুন\n"
                "৩. Instagram এ ইমেইল দেওয়ার পর OTP স্ক্যান করুন\n\n"
                "⚠️ *মনে রাখবেন:* প্রতিটি ইমেইল মাত্র *১০ মিনিট* বৈধ থাকে!\n\n"
                "👇 শুরু করতে বাটনে চাপ দিন",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
        except Exception as e:
            log.error(f"Setup failed chat_id={chat_id}: {e}")
            await msg.edit_text(
                f"❌ সেটআপ ব্যর্থ হয়েছে!\n\n"
                f"কারণ: `{e}`\n\n"
                f"/start আবার চেষ্টা করুন।",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "👋 *স্বাগতম!*\n\n"
            "📌 *কিভাবে ব্যবহার করবেন:*\n"
            "১. নিচের বাটনে চাপ দিয়ে ইমেইল নিন\n"
            "২. ইমেইলটি শুধুমাত্র *Instagram* এ দিন\n"
            "৩. Instagram OTP পাঠালে OTP স্ক্যান করুন\n\n"
            "⚠️ প্রতিটি ইমেইল মাত্র *১০ মিনিট* বৈধ!\n"
            "⚠️ একটি ইমেইল দিয়ে একটিই একাউন্ট খোলা যাবে!\n\n"
            "👇 শুরু করুন",
            parse_mode="Markdown",
            reply_markup=kb_main()
        )

async def do_get_email(chat_id: int, edit_func, ctx: ContextTypes.DEFAULT_TYPE):
    # ── Prevent multiple simultaneous sessions ──
    with getting_lock:
        if chat_id in getting_email:
            await edit_func(
                "⏳ *আপনার ইমেইল তৈরি হচ্ছে...*\n\n"
                "একটু অপেক্ষা করুন, ইতিমধ্যে প্রক্রিয়া চলছে!",
                parse_mode="Markdown"
            )
            return
        getting_email.add(chat_id)

    try:
        user = db_get_user(chat_id)
        if not user or not user.get("embux_username"):
            await edit_func("❌ একাউন্ট নেই। /start পাঠান।")
            return

        await edit_func("⏳ লগইন হচ্ছে এবং ইমেইল প্রস্তুত হচ্ছে...")

        try:
            def do_task():
                sess = embux_login(user["embux_username"], user["embux_password"])
                task = embux_start_task(sess)
                return sess, task

            sess, task = await asyncio.get_event_loop().run_in_executor(None, do_task)

            if not task.get("email"):
                await edit_func(
                    "⚠️ ইমেইল পাওয়া যায়নি। আবার চেষ্টা করুন।",
                    reply_markup=kb_main()
                )
                return

            session_create(chat_id, sess, task["task_id"], task["email"], ctx.application)

            remaining = SESSION_TTL
            mins = remaining // 60
            secs = remaining % 60

            await edit_func(
                f"✅ *আপনার ইমেইল প্রস্তুত!*\n\n"
                f"📧 ইমেইল:\n`{task['email']}`\n\n"
                f"⏱ মেয়াদ: *{mins} মিনিট {secs} সেকেন্ড*\n\n"
                f"📌 *এখন কী করবেন:*\n"
                f"১. উপরের ইমেইলটি কপি করুন\n"
                f"২. Instagram এ নতুন একাউন্ট খুলতে এই ইমেইল দিন\n"
                f"৩. Instagram OTP পাঠালে নিচের *OTP স্ক্যান* বাটনে চাপুন\n\n"
                f"⚠️ *সতর্কতা:*\n"
                f"• এই ইমেইল শুধু Instagram এর জন্য\n"
                f"• একটি ইমেইল দিয়ে একটিই একাউন্ট\n"
                f"• {mins} মিনিটের মধ্যে OTP নিন!",
                parse_mode="Markdown",
                reply_markup=kb_otp()
            )

        except Exception as e:
            log.error(f"do_get_email chat_id={chat_id}: {e}")
            await edit_func(
                f"❌ সমস্যা হয়েছে!\n\n`{e}`\n\nআবার চেষ্টা করুন 👇",
                parse_mode="Markdown",
                reply_markup=kb_main()
            )
    finally:
        with getting_lock:
            getting_email.discard(chat_id)

async def handle_get_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id

    async def edit(text, **kw):
        await q.edit_message_text(text, **kw)

    await do_get_email(chat_id, edit, ctx)

async def handle_new_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id

    session_close(chat_id, reason="new_email_requested")

    async def edit(text, **kw):
        await q.edit_message_text(text, **kw)

    await do_get_email(chat_id, edit, ctx)

async def handle_scan_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id

    sd = session_get(chat_id)
    if not sd:
        await q.edit_message_text(
            "❌ কোনো সক্রিয় সেশন নেই।\nআগে ইমেইল নিন 👇",
            reply_markup=kb_new()
        )
        return

    elapsed   = int(time.time() - sd["created"])
    remaining = max(0, SESSION_TTL - elapsed)
    mins = remaining // 60
    secs = remaining % 60

    await q.edit_message_text(
        f"🔍 *OTP খোঁজা হচ্ছে...*\n\n"
        f"📧 ইমেইল: `{sd['email']}`\n"
        f"⏱ বাকি সময়: *{mins}m {secs}s*",
        parse_mode="Markdown"
    )

    try:
        otp = await asyncio.get_event_loop().run_in_executor(
            None, embux_scan_otp, sd["session"], sd["task_id"]
        )

        if otp:
            email = sd['email']
            session_close(chat_id, reason="otp_received")

            # Delete old message and send NEW message with OTP
            try:
                await q.delete_message()
            except:
                pass

            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 *OTP পাওয়া গেছে!*\n\n"
                    f"📧 ইমেইল:\n`{email}`\n\n"
                    f"🔑 OTP কোড:\n`{otp}`\n\n"
                    f"✅ এই কোডটি Instagram এ দিন!\n\n"
                    f"নতুন একাউন্ট খুলতে নিচের বাটনে চাপুন 👇"
                ),
                parse_mode="Markdown",
                reply_markup=kb_new()
            )
        else:
            await q.edit_message_text(
                f"⏳ *এখনো OTP আসেনি*\n\n"
                f"📧 ইমেইল: `{sd['email']}`\n"
                f"⏱ বাকি সময়: *{mins}m {secs}s*\n\n"
                f"Instagram এ ইমেইল দিয়েছেন?\n"
                f"কয়েক সেকেন্ড পর আবার স্ক্যান করুন 👇",
                parse_mode="Markdown",
                reply_markup=kb_otp()
            )
    except Exception as e:
        log.error(f"scan_otp chat_id={chat_id}: {e}")
        await q.edit_message_text(
            f"❌ স্ক্যান সমস্যা: `{e}`",
            parse_mode="Markdown",
            reply_markup=kb_otp()
        )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if   data == "get_email": await handle_get_email(update, ctx)
    elif data == "scan_otp" : await handle_scan_otp(update, ctx)
    elif data == "new_email" : await handle_new_email(update, ctx)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Starting AMAL bot...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info("Bot running!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
