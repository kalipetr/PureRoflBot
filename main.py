import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

# Ссылка на сообщение с правилами
RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")

# ФИКСИРОВАННЫЙ чат для публикации анкет (ваш ID супергруппы)
DEFAULT_CHAT_ID = -1002824956071

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Состояние анкеты: user_id -> {progress, answers, origin_chat_id}
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
    if getattr(user, "username", None):
        return f"@{user.username}"
    first = esc(getattr(user, "first_name", None) or "Участник")
    return f"<a href='tg://user?id={user.id}'>{first}</a>"

def build_deeplink(param: str = "form") -> str:
    # param — строка до 64 символов. Мы передаём chat_id в виде "chat_-100..."
    return f"https://t.me/{bot.get_me().username}?start={param}"

def welcome_keyboard(chat_id: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    deeplink_param = f"chat_{chat_id}" if chat_id is not None else f"chat_{DEFAULT_CHAT_ID}"
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
    try:
        bot.send_message(int(target_chat), text, disable_web_page_preview=True)
    except Exception as e:
        # Если не удалось в чат — отправим пользователю
        bot.send_message(user_id, "Не удалось опубликовать анкету в чат, отправляю тебе:", disable_web_page_preview=True)
        bot.send_message(user_id, text, disable_web_page_preview=True)

    FORM_STATE.pop(user_id, None)

# ----------------- Хэндлеры -----------------

@bot.message_handler(commands=['start'])
def cmd_start(message: telebot.types.Message):
    """
    Поддерживает deep-link /start <payload>.
    payload варианты:
      - "chat_<ID>"  -> начинаем анкету и публикуем результат в этот чат
      - любое другое или пусто -> начнём анкету и опубликуем в DEFAULT_CHAT_ID
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

    # Если не пришёл корректный chat_id — используем ваш DEFAULT_CHAT_ID
    if origin_chat_id is None:
        origin_chat_id = DEFAULT_CHAT_ID

    start_form(message.from_user.id, origin_chat_id)
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
def greet_new_members(message: telebot.types.Message):
    for new_user in message.new_chat_members:
        kb = welcome_keyboard(chat_id=message.chat.id)
        nick = f"@{new_user.username}" if new_user.username else (new_user.first_name or "гость")
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

if __name__ == "__main__":
    print(f"Bot is starting polling as @{bot.get_me().username} ...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
