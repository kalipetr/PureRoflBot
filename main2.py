import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

# Ссылка на сообщение с правилами
RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")

# Фиксированный чат для публикации анкеты, если не удалось определить origin_chat_id
# Пример: -1001234567890 (для супергруппы). Оставьте пустым, если не хотите использовать запасной вариант.
DEFAULT_CHAT_ID = os.getenv("DEFAULT_CHAT_ID")
if DEFAULT_CHAT_ID:
    try:
        DEFAULT_CHAT_ID = int(DEFAULT_CHAT_ID)
    except ValueError:
        DEFAULT_CHAT_ID = None

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Состояние анкеты: user_id -> {progress, answers, origin_chat_id}
FORM_STATE = {}

QUESTIONS = [
    "1) Как к тебе обращаться? (ник/имя)",
    "2) Чем занимаешься/интересуешься?",
    "3) О чём хочешь общаться в чате?",
    "4) Есть ссылки на портфолио/соцсети? (если нет — так и напиши)"
]

def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def mention(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    # кликабельное имя
    first = esc(getattr(user, "first_name", None) or "Участник")
    return f"<a href='tg://user?id={user.id}'>{first}</a>"

def build_deeplink(param: str = "form") -> str:
    # param — строка до 64 символов. Можно передавать chat_id в виде "chat_-100123..."
    return f"https://t.me/{bot.get_me().username}?start={param}"

def welcome_keyboard(chat_id: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    deeplink_param = f"chat_{chat_id}" if chat_id is not None else "form"
    kb.add(InlineKeyboardButton("📝 АНКЕТА", url=build_deeplink(deeplink_param)))
    kb.add(InlineKeyboardButton("📎 Правила чата", url=RULES_LINK))
    return kb

def start_form(user_id: int, origin_chat_id: int | None):
    FORM_STATE[user_id] = {"progress": 0, "answers": [], "origin_chat_id": origin_chat_id}

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
    origin_chat_id = state.get("origin_chat_id")
    filled = (answers + ["—"] * len(QUESTIONS))[:len(QUESTIONS)]

    text = (
        "🧾 <b>Короткая анкета</b>\n"
        f"От: {mention(telebot.types.User(id=user_id, is_bot=False, first_name='Участник'))}\n\n"
        f"<b>{esc(QUESTIONS[0])}</b>\n{esc(filled[0])}\n\n"
        f"<b>{esc(QUESTIONS[1])}</b>\n{esc(filled[1])}\n\n"
        f"<b>{esc(QUESTIONS[2])}</b>\n{esc(filled[2])}\n\n"
        f"<b>{esc(QUESTIONS[3])}</b>\n{esc(filled[3])}\n\n"
        f"<i>Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"
    )

    target_chat = origin_chat_id or DEFAULT_CHAT_ID
    if target_chat:
        try:
            bot.send_message(int(target_chat), text, disable_web_page_preview=True)
        except Exception as e:
            # Если не удалось в чат — отправим пользователю
            bot.send_message(user_id, "Не удалось опубликовать анкету в чат, отправляю тебе:", disable_web_page_preview=True)
            bot.send_message(user_id, text, disable_web_page_preview=True)
    else:
        bot.send_message(user_id, "Не удалось определить чат для публикации анкеты.", disable_web_page_preview=True)
        bot.send_message(user_id, text, disable_web_page_preview=True)

    FORM_STATE.pop(user_id, None)

# ----------------- Хэндлеры -----------------

@bot.message_handler(commands=['start'])
def cmd_start(message: telebot.types.Message):
    """
    Поддерживает deep-link /start <payload>.
    payload варианты:
      - "chat_<ID>"  -> начинаем анкету и публикуем результат в этот чат
      - "form"       -> начинаем анкету без origin; уйдёт в DEFAULT_CHAT_ID или пользователю
    """
    payload = None
    if message.text and " " in message.text:
        try:
            payload = message.text.split(" ", 1)[1].strip()
        except Exception:
            payload = None

    if payload and payload.startswith("chat_"):
        # Пробуем вытащить chat_id из payload
        chat_id_str = payload[len("chat_"):]
        try:
            origin_chat_id = int(chat_id_str)
        except ValueError:
            origin_chat_id = None
        start_form(message.from_user.id, origin_chat_id)
        bot.reply_to(message, "Погнали! Отвечай коротко, по пунктам. Можно написать «стоп» для отмены.")
        ask_next_question(message.from_user.id)
        return

    if payload == "form":
        start_form(message.from_user.id, origin_chat_id=None)
        bot.reply_to(message, "Погнали! Отвечай коротко, по пунктам. Можно написать «стоп» для отмены.")
        ask_next_question(message.from_user.id)
        return

    # Обычный /start без payload — покажем описание и кнопку
    kb = welcome_keyboard(chat_id=None)
    bot.reply_to(
        message,
        "Привет! Я — приветственный бот 🤖\n\n"
        "Добавь меня в группу: поздороваюсь с новичками по нику, "
        "дам ссылку на правила и предложу короткую анкету.\n\n"
        "Хочешь заполнить прямо сейчас? Жми «АНКЕТА».",
        reply_markup=kb
    )

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(
        message,
        "Как это работает:\n"
        "• Добавьте бота в группу и выключите privacy (/setprivacy у BotFather → OFF)\n"
        "• Кнопка АНКЕТА открывает ЛС с ботом (deep-link)\n"
        "• Анкета публикуется обратно в чат (через deep-link параметр) или в DEFAULT_CHAT_ID"
    )

@bot.message_handler(content_types=['new_chat_members'])
def greet_new_members(message: telebot.types.Message):
    for new_user in message.new_chat_members:
        kb = welcome_keyboard(chat_id=message.chat.id)
        nick = f"@{new_user.username}" if new_user.username else (new_user.first_name or "гость")
        bot.send_message(
            message.chat.id,
            f"🎉 Добро пожаловать, {esc(nick)}!\n"
            "У нас лампово, без токсичности и с мемами. "
            "Хочешь рассказать о себе? Жми «АНКЕТА». А вот и <b>правила</b> ⤵️",
            reply_markup=kb,
            disable_web_page_preview=True
        )

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
        kb.add(InlineKeyboardButton("📝 Начать анкету", url=build_deeplink("form")))
        bot.reply_to(message, "Хочешь заполнить короткую анкету?", reply_markup=kb)
        return

    state["answers"].append(message.text.strip())
    state["progress"] += 1
    if state["progress"] < len(QUESTIONS):
        ask_next_question(user_id)
    else:
        bot.send_message(user_id, "Спасибо! Публикую краткую карточку в чат ✨")
        publish_form_result(user_id)

if __name__ == "__main__":
    print(f"Bot is starting polling as @{bot.get_me().username} ...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
