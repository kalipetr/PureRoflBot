import os
import telebot
from telebot import types
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

# --- ENV ---
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")
# ФИКСИРОВАННЫЙ чат для публикации анкет (ваш ID супергруппы)
DEFAULT_CHAT_ID = int(os.getenv("DEFAULT_CHAT_ID", "-1002824956071"))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- STATE ---
# user_id -> {progress, answers, origin_chat_id, user_obj}
FORM_STATE = {}

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
    # @username если есть, иначе кликабельное имя
    if getattr(user, "username", None):
        return f"@{user.username}"
    first = esc(getattr(user, "first_name", None) or "Участник")
    return f"<a href='tg://user?id={user.id}'>{first}</a>"

def build_deeplink(param: str = "form") -> str:
    # deep-link для открытия ЛС с ботом
    return f"https://t.me/{bot.get_me().username}?start={param}"

def welcome_keyboard(chat_id: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    # Всегда передаём chat_id, чтобы результат анкеты вернулся в этот чат
    deeplink_param = f"chat_{chat_id}" if chat_id is not None else f"chat_{DEFAULT_CHAT_ID}"
    kb.add(InlineKeyboardButton("📝 АНКЕТА", url=build_deeplink(deeplink_param)))
    kb.add(InlineKeyboardButton("📎 Правила чата", url=RULES_LINK))
    return kb

def start_form(user, origin_chat_id: int | None):
    FORM_STATE[user.id] = {
        "progress": 0,
        "answers": [],
        "origin_chat_id": origin_chat_id,
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
        f"<b>{esc(QUESTIONS[4])}</b>\n{esc(filled[4])}\n\n"
    )

    try:
        bot.send_message(int(origin_chat_id), text, disable_web_page_preview=True)
    except Exception:
        # Если не удалось в чат — отправим пользователю
        bot.send_message(user_id, "Не удалось опубликовать анкету в чат, отправляю тебе:", disable_web_page_preview=True)
        bot.send_message(user_id, text, disable_web_page_preview=True)

    FORM_STATE.pop(user_id, None)

# ----------------- Хэндлеры -----------------

@bot.message_handler(commands=['start'])
def cmd_start(message: types.Message):
    """
    Поддерживает deep-link /start <payload>.
    payload:
      - "chat_<ID>"  -> начинаем анкету и публикуем результат в этот чат
      - другое/пусто -> начнём анкету и опубликуем в DEFAULT_CHAT_ID
    """
    payload = None
    if message.text and " " in message.text:
        payload = message.text.split(" ", 1)[1].strip()

    origin_chat_id = None
    if payload and payload.startswith("chat_"):
        chat_id_str = payload[len("chat_"):]
        try:
            origin_chat_id = int(chat_id_str)
        except ValueError:
            origin_chat_id = None

    if origin_chat_id is None:
        origin_chat_id = DEFAULT_CHAT_ID

    start_form(message.from_user, origin_chat_id)
    bot.reply_to(message, "Погнали! Отвечай коротко, по пунктам. Можно написать «стоп» для отмены.")
    ask_next_question(message.from_user.id)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(
        message,
        "Как это работает:\n"
        "• Кнопка АНКЕТА открывает ЛС с ботом через deep‑link\n"
        "• По завершении анкеты публикую результат в заданный чат\n"
        f"• Текущий чат публикации: <code>{DEFAULT_CHAT_ID}</code>"
    )

@bot.message_handler(content_types=['new_chat_members'])
def greet_new_members(message: types.Message):
    # Классическое событие «новые участники»
    for new_user in message.new_chat_members:
        kb = welcome_keyboard(chat_id=message.chat.id)
        nick = mention(new_user)
        extra = {}
        # Если это форумы (темы) — отвечаем в тот же thread
        if getattr(message, "is_topic_message", False) and getattr(message, "message_thread_id", None):
            extra["message_thread_id"] = message.message_thread_id
        bot.send_message(
            message.chat.id,
            f"🥳 Добро пожаловать, {nick}! \n"
            "Здесь рофлы, мемы, флирты, лайтовое общение на взаимном уважении и оффлайн-тусовки, если поймаешь наш вайб ❤️\n\n"
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
    """
    Срабатывает, когда статус участника меняется.
    Ловим «вход в чат»: было left/kicked -> стало member/restricted.
    Работает даже если join-сообщения скрыты в настройках чата.
    """
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
            "Здесь рофлы, мемы, флирты, лайтовое общение на взаимном уважении и оффлайн-тусовки, если поймаешь наш вайб ❤️\n\n"
            "Начинай прямо сейчас и жми <b>АНКЕТА!</b> (После перейди в личку с ботом и ответь на вопросы)\n\n"
            "Пришли фото, если ты без него - Ноунеймам здесь не рады\n\n"
            "И жми кномпочку <b>ПРАВИЛА</b>, чтобы быть в курсе.\n\n"
            "По всем вопросам обращайся @nad_wild @zhurina71 @to0cha @tsvetovaan 💋\n\n"
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
    # На всякий случай: удаляем вебхук, чтобы точно был polling (иначе возможны конфликты)
    try:
        info = bot.get_webhook_info()
        print("Current webhook url:", getattr(info, "url", ""))
        if info and info.url:
            bot.delete_webhook(drop_pending_updates=True)
            print("Webhook deleted (ok for polling).")
    except Exception as e:
        print("webhook check/delete error:", e)

    print(f"Bot is starting polling as @{bot.get_me().username} ...")
    # Включаем поддержку обновлений статуса участников
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        skip_pending=True,
        allowed_updates=["message", "chat_member", "my_chat_member"]
    )
