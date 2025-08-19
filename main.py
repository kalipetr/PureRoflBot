import os
import re
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import tempfile
import mimetypes
import pathlib
from contextlib import ExitStack
from datetime import datetime

import yt_dlp
import redis

# === ENV ===
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не установлен")

RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")
DEFAULT_CHAT_ID = int(os.getenv("DEFAULT_CHAT_ID", "-1002824956071"))
BOT_FILE_LIMIT = int(os.getenv("BOT_FILE_LIMIT_MB", "45")) * 1024 * 1024

REDIS_URL = os.getenv("REDIS_URL")  # обязателен для постоянного хранения cookies
if not REDIS_URL:
    raise RuntimeError("REDIS_URL не установлен (подключи Redis-плагин на Railway)")

rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# === STATE (анкета в RAM, как и раньше) ===
FORM_STATE = {}  # user_id -> {progress, answers, origin_chat_id, user_obj}

QUESTIONS = [
    "1) Как тебя зовут?",
    "2) Сколько тебе лет?",
    "3) Рост?",
    "4) Из какого ты города? Если из Москвы, то из какого района?",
    "5) (а вот тут очень важно ответить честно...) Гетеро?"
]

# ---------- Утилиты ----------
def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    first = esc(getattr(user, "first_name", None) or "Участник")
    return f"<a href='tg://user?id={user.id}'>{first}</a>"

def build_deeplink(param: str = "form") -> str:
    return f"https://t.me/{bot.get_me().username}?start={param}"

def welcome_keyboard(chat_id: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    deeplink_param = f"chat_{chat_id}" if chat_id is not None else f"chat_{DEFAULT_CHAT_ID}"
    kb.add(InlineKeyboardButton("📝 АНКЕТА", url=build_deeplink(deeplink_param)))
    kb.add(InlineKeyboardButton("📎 Правила чата", url=RULES_LINK))
    return kb

# ---------- Анкета ----------
def start_form(user, origin_chat_id: int | None):
    FORM_STATE[user.id] = {
        "progress": 0,
        "answers": [],
        "origin_chat_id": origin_chat_id or DEFAULT_CHAT_ID,
        "user_obj": user
    }

def ask_next_question(user_id: int):
    state = FORM_STATE.get(user_id)
    if not state:
        return
    idx = state["progress"]
    if idx < len(QUESTIONS):
        bot.send_message(user_id, QUESTIONS[idx])
    else:
        publish_form_result(user_id)

def publish_form_result(user_id: int):
    state = FORM_STATE.get(user_id)
    if not state:
        return
    answers = state["answers"]
    origin_chat_id = state.get("origin_chat_id") or DEFAULT_CHAT_ID
    user_mention = mention(state.get("user_obj")) if state.get("user_obj") else "Участник"
    filled = (answers + ["—"] * len(QUESTIONS))[:len(QUESTIONS)]
    text = (
        "🧾 <b>Короткая анкета</b>\n"
        f"От: {user_mention}\n\n"
        f"<b>{esc(QUESTIONS[0])}</b>\n{esc(filled[0])}\n\n"
        f"<b>{esc(QUESTIONS[1])}</b>\n{esc(filled[1])}\n\n"
        f"<b>{esc(QUESTIONS[2])}</b>\n{esc(filled[2])}\n\n"
        f"<b>{esc(QUESTIONS[3])}</b>\n{esc(filled[3])}\n\n"
        f"<b>{esc(QUESTIONS[4])}</b>\n{esc(filled[4])}"
    )
    try:
        bot.send_message(int(origin_chat_id), text, disable_web_page_preview=True)
    except Exception:
        bot.send_message(user_id, "Не удалось опубликовать анкету в чат, отправляю тебе:", disable_web_page_preview=True)
        bot.send_message(user_id, text, disable_web_page_preview=True)
    FORM_STATE.pop(user_id, None)

# ---------- VK (cookies в Redis, работа ТОЛЬКО в ЛС) ----------
COOKIES_KEY = "vk:cookies:{uid}"
COOKIES_META = "vk:cookies:{uid}:meta"  # хранит {'updated_at': iso}

def save_cookies(user_id: int, text: str):
    rds.set(COOKIES_KEY.format(uid=user_id), text)
    rds.hset(COOKIES_META.format(uid=user_id), mapping={"updated_at": datetime.utcnow().isoformat()})

def load_cookies(user_id: int) -> str | None:
    return rds.get(COOKIES_KEY.format(uid=user_id))

def clear_cookies(user_id: int):
    rds.delete(COOKIES_KEY.format(uid=user_id))
    rds.delete(COOKIES_META.format(uid=user_id))

def cookies_status(user_id: int) -> str:
    txt = load_cookies(user_id)
    if not txt:
        return "❌ cookies не загружены"
    updated = rds.hget(COOKIES_META.format(uid=user_id), "updated_at") or "неизвестно"
    return f"✅ cookies загружены\nОбновлены: {updated}"

def _ydl_opts(tmpdir: str, cookies_text: str | None, for_audio_only: bool = False, prefer_ext: str | None = None):
    opts = {
        "outtmpl": os.path.join(tmpdir, "%(title).200s.%(ext)s"),
        "restrictfilenames": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
    }
    # Форматы:
    if for_audio_only:
        # Пытаемся достать чистое аудио без перекодирования
        opts["format"] = "bestaudio/best"
        # Если ffmpeg доступен — можно попросить конвертацию через постпроцессор:
        if shutil_which("ffmpeg"):
            ext = prefer_ext or "mp3"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext,
                "preferredquality": "0"
            }]
        # иначе отправим как есть (opus/webm/m4a и т.д.)
    else:
        opts["format"] = "bv*+ba/best/bestaudio/bestvideo"

    # cookies подсовываем через файл во временной директории
    if cookies_text:
        cpath = os.path.join(tmpdir, "cookies.txt")
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(cookies_text)
        opts["cookiefile"] = cpath
    return opts

def shutil_which(cmd: str) -> str | None:
    # простая проверка наличия бинарника в PATH
    for path in os.getenv("PATH", "").split(os.pathsep):
        candidate = os.path.join(path.strip('"'), cmd)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        if os.path.isfile(candidate + ".exe") and os.access(candidate + ".exe", os.X_OK):
            return candidate + ".exe"
    return None

def ensure_private_chat(message: types.Message) -> bool:
    if message.chat.type == "private":
        return True
    bot.reply_to(
        message,
        "Скачивание VK доступно только в личке с ботом.\n"
        f"Открой меня: {build_deeplink('form')}\n"
        "Дальше: пришли ссылку VK сюда или используй команду /vk <ссылка>."
    )
    return False

def extract_first_url(text: str) -> str | None:
    if not text:
        return None
    # простенький поиск ссылки
    m = re.search(r'(https?://[^\s]+)', text)
    return m.group(1) if m else None

def _collect_downloaded_files(info: dict) -> list[str]:
    files = []
    if not info:
        return files
    if "entries" in info and info["entries"]:
        for entry in info["entries"]:
            if entry and "requested_downloads" in entry:
                for rd in entry["requested_downloads"]:
                    if "filepath" in rd:
                        files.append(rd["filepath"])
    else:
        rds = info.get("requested_downloads") or []
        for rd in rds:
            if "filepath" in rd:
                files.append(rd["filepath"])
    return files

def _get_direct_url(info: dict) -> str | None:
    if not info:
        return None
    if "url" in info:
        return info["url"]
    fmts = info.get("formats") or []
    for f in reversed(fmts):
        if f.get("url"):
            return f["url"]
    return None

def handle_vk_download(dm_chat_id: int, reply_to_message_id: int | None, url: str, user_id: int, audio_only: bool = False):
    cookies_text = load_cookies(user_id)
    with tempfile.TemporaryDirectory(prefix="vkdl_") as tmpdir, ExitStack():
        ydl_opts = _ydl_opts(tmpdir, cookies_text, for_audio_only=audio_only)
        info = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception:
            # Попробуем без скачивания — хотя бы прямую ссылку
            try:
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                bot.send_message(dm_chat_id, "Не удалось скачать с VK. Проверь ссылку/доступ или обнови cookies.", reply_to_message_id=reply_to_message_id)
                return

        files = _collect_downloaded_files(info)
        if not files:
            direct = _get_direct_url(info)
            if direct:
                bot.send_message(dm_chat_id, f"Не могу отправить файл напрямую. Ссылка на скачивание:\n{direct}",
                                 reply_to_message_id=reply_to_message_id, disable_web_page_preview=True)
            else:
                bot.send_message(dm_chat_id, "Не удалось получить файл/ссылку VK.", reply_to_message_id=reply_to_message_id)
            return

        for fpath in files:
            p = pathlib.Path(fpath)
            if not p.exists():
                continue
            size = p.stat().st_size
            title = p.stem.replace("_", " ")
            ext = p.suffix.lower()
            if size > BOT_FILE_LIMIT:
                direct = _get_direct_url(info)
                msg = "Файл больше лимита отправки ботом."
                if direct:
                    msg += f"\nСсылка на скачивание:\n{direct}"
                bot.send_message(dm_chat_id, msg, reply_to_message_id=reply_to_message_id, disable_web_page_preview=True)
                continue
            mime, _ = mimetypes.guess_type(str(p))
            try:
                with open(p, "rb") as fh:
                    if audio_only:
                        # всегда как аудио
                        bot.send_audio(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
                    else:
                        if ext in (".mp4", ".mkv", ".webm", ".mov") or (mime and mime.startswith("video/")):
                            bot.send_video(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
                        elif ext in (".mp3", ".m4a", ".ogg", ".opus", ".webm") or (mime and mime.startswith("audio/")):
                            bot.send_audio(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
                        else:
                            bot.send_document(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
            except Exception:
                bot.send_message(dm_chat_id, f"Не удалось отправить файл ({p.name}).", reply_to_message_id=reply_to_message_id)

# ---------- Хэндлеры ----------

@bot.message_handler(commands=['start'])
def cmd_start(message: types.Message):
    payload = None
    if message.text and " " in message.text:
        payload = message.text.split(" ", 1)[1].strip()

    origin_chat_id = None
    if payload and payload.startswith("chat_"):
        try:
            origin_chat_id = int(payload[len("chat_"):])
        except ValueError:
            origin_chat_id = None
    if origin_chat_id is None:
        origin_chat_id = DEFAULT_CHAT_ID

    start_form(message.from_user, origin_chat_id)
    if message.chat.type != "private":
        bot.reply_to(message, "Погнали! Отвечай коротко, по пунктам. Можно написать «стоп» для отмены.")
    else:
        bot.reply_to(
            message,
            "Погнали! Отвечай коротко, по пунктам. Напиши «стоп» для отмены.\n\n"
            "💿 VK-загрузка (только здесь, в ЛС):\n"
            "• /vk <ссылка> — скачать видео/аудио\n"
            "• /vk_audio <ссылка> — только аудио (если доступен FFmpeg — конвертирую в mp3)\n"
            "• /cookies — статус cookies; /clearcookies — забыть cookies\n"
            "• Отправь файл cookies.txt (Netscape) — привяжу для приватных ссылок"
        )
    ask_next_question(message.from_user.id)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(
        message,
        "Как это работает:\n"
        "• АНКЕТА — по кнопке в группе, отвечаешь в ЛС, результат вернётся в чат\n"
        f"• Текущий чат публикации анкет: <code>{DEFAULT_CHAT_ID}</code>\n\n"
        "VK (только в ЛС):\n"
        "• /vk <ссылка> — скачать видео/аудио\n"
        "• /vk_audio <ссылка> — только аудио (лучше с FFmpeg)\n"
        "• Пришли cookies.txt (Netscape) — для приватных ссылок\n"
        "• /cookies — статус, /clearcookies — удалить"
    )

# --- Команды VK (только ЛС) ---
@bot.message_handler(commands=['vk'])
def cmd_vk(message: types.Message):
    if not ensure_private_chat(message):
        return
    url = extract_first_url(message.text)
    if not url or "vk.com" not in url:
        bot.reply_to(message, "Пришли так: <code>/vk https://vk.com/video...</code>")
        return
    bot.reply_to(message, "Секунду, качаю из VK…")
    handle_vk_download(message.chat.id, message.message_id, url, message.from_user.id, audio_only=False)

@bot.message_handler(commands=['vk_audio'])
def cmd_vk_audio(message: types.Message):
    if not ensure_private_chat(message):
        return
    url = extract_first_url(message.text)
    if not url or "vk.com" not in url:
        bot.reply_to(message, "Пришли так: <code>/vk_audio https://vk.com/video...</code>")
        return
    has_ffmpeg = bool(shutil_which("ffmpeg"))
    if not has_ffmpeg:
        bot.reply_to(message, "FFmpeg не обнаружен — пришлю лучшую аудиодорожку как есть (без конвертации).")
    else:
        bot.reply_to(message, "Пробую вытащить аудио (FFmpeg)…")
    handle_vk_download(message.chat.id, message.message_id, url, message.from_user.id, audio_only=True)

@bot.message_handler(commands=['cookies'])
def cmd_cookies_status(message: types.Message):
    if message.chat.type != "private":
        return
    bot.reply_to(message, cookies_status(message.from_user.id))

@bot.message_handler(commands=['clearcookies'])
def cmd_clear_cookies(message: types.Message):
    if message.chat.type != "private":
        return
    clear_cookies(message.from_user.id)
    bot.reply_to(message, "Готово. Cookies удалены.")

# --- Приём cookies.txt в ЛС ---
@bot.message_handler(content_types=['document'])
def on_document(message: types.Message):
    if message.chat.type != "private":
        return
    doc = message.document
    fname = (doc.file_name or "").lower()
    # принимаем любой .txt, где встречаются домены VK
    try_txt = fname.endswith(".txt")
    if not try_txt:
        bot.reply_to(message, "Если хочешь настроить приватный VK, пришли файл cookies.txt (Netscape формат).")
        return
    try:
        file_info = bot.get_file(doc.file_id)
        file_data = bot.download_file(file_info.file_path)
        text = file_data.decode("utf-8", errors="ignore")
        # лёгкая валидация: должна быть строка "Netscape" или домены .vk.com
        if "Netscape" not in text and ".vk.com" not in text and "vk.com" not in text:
            bot.reply_to(message, "Похоже, это не cookies.txt (Netscape). Проверь файл.")
            return
        save_cookies(message.from_user.id, text)
        bot.reply_to(message, "Cookies приняты ✅ Теперь пришли приватную VK‑ссылку.")
    except Exception:
        bot.reply_to(message, "Не удалось обработать файл cookies.txt. Попробуй ещё раз.")

# --- Приветствия в группе ---
@bot.message_handler(content_types=['new_chat_members'])
def greet_new_members(message: types.Message):
    for new_user in message.new_chat_members:
        kb = welcome_keyboard(chat_id=message.chat.id)
        nick = mention(new_user)
        extra = {}
        if getattr(message, "is_topic_message", False) and getattr(message, "message_thread_id", None):
            extra["message_thread_id"] = message.message_thread_id
        bot.send_message(
            message.chat.id,
            f"🥳 Добро пожаловать, {nick}! \n"
            "Здесь рофлы, мемы, флирты, лайтовое общение на взаимном уваждении и оффлайн-тусовки, если поймаешь наш вайб ❤️\n\n"
            "Начинай прямо сейчас и жми <b>АНКЕТА!</b> (После перейди в личку с ботом и ответь на вопросы)\n\n"
            "Пришли фото, если ты без него - Ноунеймам здесь не рады\n\n"
            "И жми кномпочку <b>ПРАВИЛА</b>, чтобы быть в курсе.\n\n"
            "По всем вопросам обращайся @nad_wild @zhurina71 @tsvetovaan 💋\n\n"
            "Приятного общения!",
            reply_markup=kb,
            disable_web_page_preview=True,
            **extra
        )

@bot.chat_member_handler(func=lambda u: True)
def on_chat_member_update(update: types.ChatMemberUpdated):
    try:
        old = update.old_chat_member.status
        new = update.new_chat_member.status
        user = update.new_chat_member.user
        chat_id = update.chat.id
        joined_now = (old in ("left", "kicked")) and (new in ("member", "restricted"))
        if not joined_now:
            return
        kb = welcome_keyboard(chat_id=chat_id)
        nick = mention(user)
        bot.send_message(
            chat_id,
            f"🥳 Добро пожаловать, {nick}! \n"
            "Здесь рофлы, мемы, флирты, лайтовое общение на взаимном уваждении и оффлайн-тусовки, если поймаешь наш вайб ❤️\n\n"
            "Начинай прямо сейчас и жми <b>АНКЕТА!</b> (После перейди в личку с ботом и ответь на вопросы)\n\n"
            "Пришли фото, если ты без него - Ноунеймам здесь не рады\n\n"
            "И жми кномпочку <b>ПРАВИЛА</b>, чтобы быть в курсе.\n\n"
            "По всем вопросам обращайся @nad_wild @zhurina71 @tsvetovaan 💋\n\n"
            "Приятного общения!",
            reply_markup=kb,
            disable_web_page_preview=True
        )
    except Exception as e:
        print("chat_member handler error:", e)

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ["стоп", "stop", "cancel"])
def cancel_form(message: types.Message):
    user_id = message.from_user.id
    if user_id in FORM_STATE:
        FORM_STATE.pop(user_id, None)
        bot.reply_to(message, "Окей, анкету отменил. Хочешь — начнём заново по кнопке «АНКЕТА».")
    else:
        bot.reply_to(message, "Сейчас анкета не запущена. Можешь нажать «АНКЕТА» в меню.")

@bot.message_handler(func=lambda m: m.chat.type == "private")
def private_flow(message: types.Message):
    user_id = message.from_user.id
    state = FORM_STATE.get(user_id)
    if not state:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📝 Начать анкету", url=build_deeplink(f"chat_{DEFAULT_CHAT_ID}")))
        bot.reply_to(message, "Хочешь заполнить короткую анкету?", reply_markup=kb)
        return
    state["answers"].append(message.text.strip())
    state["progress"] += 1
    if state["progress"] < len(QUESTIONS):
        ask_next_question(user_id)
    else:
        bot.send_message(user_id, "Спасибо! Публикую краткую карточку в чат ✨")
        publish_form_result(user_id)

# --- START POLLING ---
if __name__ == "__main__":
    # Удаляем вебхук на всякий случай
    try:
        info = bot.get_webhook_info()
        print("Current webhook url:", getattr(info, "url", ""))
        if info and info.url:
            bot.delete_webhook(drop_pending_updates=True)
            print("Webhook deleted (ok for polling).")
    except Exception as e:
        print("webhook check/delete error:", e)

    print(f"Bot is starting polling as @{bot.get_me().username} ...")
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        skip_pending=True,
        allowed_updates=["message", "chat_member", "my_chat_member"]
    )
