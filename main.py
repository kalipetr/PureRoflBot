import os
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import tempfile
import mimetypes
import pathlib
from contextlib import ExitStack

# --- Новое: yt-dlp для VK ---
import yt_dlp

# === ENV ===
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")
DEFAULT_CHAT_ID = int(os.getenv("DEFAULT_CHAT_ID", "-1002824956071"))
BOT_FILE_LIMIT = int(os.getenv("BOT_FILE_LIMIT_MB", "45")) * 1024 * 1024  # лимит отправки файла ботом

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# === STATE ===
# анкета
FORM_STATE = {}  # user_id -> {progress, answers, origin_chat_id, user_obj}
# cookies для VK: user_id -> path к cookies.txt (в формате Netscape)
USER_COOKIES = {}

QUESTIONS = [
    "1) Как тебя зовут?",
    "2) Сколько тебе лет?",
    "3) Рост?",
    "4) Из какого ты города? Если из Москвы, то из какого района?",
    "5) (а вот тут очень важно ответить честно...) Гетеро?"
]

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
    user_obj = state.get("user_obj")
    user_mention = mention(user_obj) if user_obj else "Участник"
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

# ----------------- VK DOWNLOADER (только в ЛС с ботом) -----------------

def _ydl_opts(tmpdir: str, cookies_path: str | None):
    opts = {
        "outtmpl": os.path.join(tmpdir, "%(title).200s.%(ext)s"),
        "restrictfilenames": True,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/best/bestaudio/bestvideo",  # лучшее доступное, без перекодирования
        "merge_output_format": None,
        "noplaylist": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
    }
    if cookies_path:
        opts["cookiefile"] = cookies_path  # для приватных ссылок
    return opts

def ensure_private_chat(message: types.Message) -> bool:
    """Возвращает True, если это ЛС. Если это группа — даёт инструкцию и возвращает False."""
    if message.chat.type == "private":
        return True
    bot.reply_to(
        message,
        "Скачивание VK доступно только в личке с ботом.\n"
        f"Открой меня: {build_deeplink('form')}\n"
        "Дальше: пришли ссылку VK сюда или используй команду /vk <ссылка>."
    )
    return False

def handle_vk_download(dm_chat_id: int, reply_to_message_id: int | None, url: str, user_id: int):
    cookies_path = USER_COOKIES.get(user_id)
    with tempfile.TemporaryDirectory(prefix="vkdl_") as tmpdir, ExitStack():
        ydl_opts = _ydl_opts(tmpdir, cookies_path)
        info = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception:
            # попробуем без скачивания — достать прямой URL
            try:
                with yt_dlp.YoutubeDL({**ydl_opts, "skip_download": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                bot.send_message(dm_chat_id, "Не удалось скачать с VK. Проверь ссылку/доступ или обнови cookies.", reply_to_message_id=reply_to_message_id)
                return

        files = []
        if info and "entries" in info and info["entries"]:
            for entry in info["entries"]:
                if entry and "requested_downloads" in entry:
                    for rd in entry["requested_downloads"]:
                        if "filepath" in rd:
                            files.append(rd["filepath"])
        else:
            rds = (info or {}).get("requested_downloads") or []
            for rd in rds:
                if "filepath" in rd:
                    files.append(rd["filepath"])

            if not files:
                # дать прямую ссылку, если файл нельзя отправить
                direct = None
                if info and "url" in info:
                    direct = info["url"]
                else:
                    fmts = (info or {}).get("formats") or []
                    for f in reversed(fmts):
                        if f.get("url"):
                            direct = f["url"]
                            break
                if direct:
                    bot.send_message(dm_chat_id, f"Не могу отправить файл напрямую. Вот ссылка на скачивание:\n{direct}", reply_to_message_id=reply_to_message_id, disable_web_page_preview=True)
                else:
                    bot.send_message(dm_chat_id, "Не удалось получить ссылку на файл VK.", reply_to_message_id=reply_to_message_id)
                return

        if not files:
            bot.send_message(dm_chat_id, "Файлы VK не получены (возможно, нужен cookies.txt).", reply_to_message_id=reply_to_message_id)
            return

        for fpath in files:
            p = pathlib.Path(fpath)
            if not p.exists():
                continue
            size = p.stat().st_size
            title = p.stem.replace("_", " ")
            ext = p.suffix.lower()

            if size > BOT_FILE_LIMIT:
                # слишком большой — попытаемся дать прямой URL с cookies
                try:
                    with yt_dlp.YoutubeDL({**_ydl_opts(tmpdir, cookies_path), "skip_download": True}) as ydl:
                        i2 = ydl.extract_info(url, download=False)
                        direct = None
                        if i2 and "url" in i2:
                            direct = i2["url"]
                        else:
                            fmts = (i2 or {}).get("formats") or []
                            for f in reversed(fmts):
                                if f.get("url"):
                                    direct = f["url"]
                                    break
                except Exception:
                    direct = None
                msg = "Файл больше лимита для отправки ботом."
                if direct:
                    msg += f"\nСсылка на скачивание:\n{direct}"
                bot.send_message(dm_chat_id, msg, reply_to_message_id=reply_to_message_id, disable_web_page_preview=True)
                continue

            mime, _ = mimetypes.guess_type(str(p))
            try:
                with open(p, "rb") as fh:
                    if ext in (".mp4", ".mkv", ".webm", ".mov") or (mime and mime.startswith("video/")):
                        bot.send_video(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
                    elif ext in (".mp3", ".m4a", ".ogg", ".opus", ".webm") or (mime and mime.startswith("audio/")):
                        bot.send_audio(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
                    else:
                        bot.send_document(dm_chat_id, fh, caption=title[:900], reply_to_message_id=reply_to_message_id)
            except Exception:
                bot.send_message(dm_chat_id, f"Не удалось отправить файл ({p.name}).", reply_to_message_id=reply_to_message_id)

# ----------------- ХЭНДЛЕРЫ -----------------

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
        # В группах — только приветствие / анкета
        bot.reply_to(message, "Погнали! Отвечай коротко, по пунктам. Можно написать «стоп» для отмены.")
    else:
        # В ЛС — подскажем про VK функционал
        bot.reply_to(
            message,
            "Погнали! Отвечай коротко, по пунктам. Напиши «стоп» для отмены.\n\n"
            "💿 Для загрузки VK:\n"
            "• Пришли сюда ссылку VK или используй /vk <ссылка>\n"
            "• Для приватных ссылок сначала пришли файл cookies.txt (Netscape формат)"
        )
    ask_next_question(message.from_user.id)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(
        message,
        "Как это работает:\n"
        "• АНКЕТА — по кнопке в группе, отвечаешь в ЛС, результат вернётся в чат\n"
        f"• Текущий чат публикации анкет: <code>{DEFAULT_CHAT_ID}</code>\n\n"
        "VK в ЛИЧКЕ:\n"
        "• /vk <ссылка> — скачать видео/аудио из VK (публичные и приватные при наличии cookies)\n"
        "• Отправь cookies.txt (Netscape) в ЛС — бот привяжет к твоему аккаунту"
    )

# --- Приём cookies.txt в ЛС ---
@bot.message_handler(content_types=['document'])
def on_document(message: types.Message):
    if message.chat.type != "private":
        return  # принимаем cookies только в ЛС
    doc = message.document
    fname = (doc.file_name or "").lower()
    if "cookie" not in fname:
        bot.reply_to(message, "Если хочешь настроить приватный VK, пришли файл cookies.txt (Netscape формат).")
        return

    # Сохраняем cookies в /tmp для этого user_id
    try:
        file_info = bot.get_file(doc.file_id)
        file_data = bot.download_file(file_info.file_path)
        user_path = f"/tmp/cookies_{message.from_user.id}.txt"
        with open(user_path, "wb") as f:
            f.write(file_data)
        USER_COOKIES[message.from_user.id] = user_path
        bot.reply_to(message, "Файл cookies принят ✅\nТеперь присылай приватную VK-ссылку — попробую скачать.")
    except Exception:
        bot.reply_to(message, "Не удалось сохранить cookies. Попробуй ещё раз.")

# --- Команда /vk (Только в ЛС) ---
@bot.message_handler(commands=['vk'])
def cmd_vk(message: types.Message):
    if not ensure_private_chat(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Пришли так: <code>/vk https://vk.com/video...</code>")
        return
    url = parts[1].strip()
    if "vk.com" not in url:
        bot.reply_to(message, "Это не похоже на ссылку VK. Нужен URL вида https://vk.com/...")
        return
    bot.reply_to(message, "Секунду, качаю из VK…")
    handle_vk_download(message.chat.id, message.message_id, url, message.from_user.id)

# --- Автоопределение ссылок VK (Только в ЛС) ---
@bot.message_handler(func=lambda m: m.chat.type == "private" and bool(m.text) and ("vk.com/" in m.text))
def auto_vk(message: types.Message):
    words = message.text.split()
    url = next((w for w in words if "vk.com/" in w), None)
    if not url:
        return
    bot.reply_to(message, "Ловлю VK — качаю…")
    handle_vk_download(message.chat.id, message.message_id, url, message.from_user.id)

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
def cancel_form(message):
    user_id = message.from_user.id
    if user_id in FORM_STATE:
        FORM_STATE.pop(user_id, None)
        bot.reply_to(message, "Окей, анкету отменил. Хочешь — начнём заново по кнопке «АНКЕТА».")
    else:
        bot.reply_to(message, "Сейчас анкета не запущена. Можешь нажать «АНКЕТА» в меню.")

@bot.message_handler(func=lambda m: m.chat.type == "private")
def private_flow(message):
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
    # На всякий: удаляем вебхук, чтобы точно был polling
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
