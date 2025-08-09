import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

# Замените на реальную ссылку на сообщение с правилами в вашем чате
# Например: https://t.me/c/123456789/42  (или публичная: https://t.me/your_chat/42)
RULES_LINK = os.getenv("RULES_LINK", "https://t.me/your_chat/42")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Память состояний анкеты в RAM (для простоты).
# Ключ: user_id, значение: dict(progress, answers, origin_chat_id)
FORM_STATE = {}

QUESTIONS = [
    "1) Как к тебе обращаться? (ник/имя)",
    "2) Чем занимаешься/интересуешься в жизни?",
    "3) О чём хочешь общаться в чате?",
    "4) Есть ссылки на портфолио/соцсети? (если нет — так и напиши)"
]

def mention(user) -> str:
    # Приветствие по нику, если есть; иначе — кликабельное имя
    if user.username:
        return f"@{user.username}"
    return f"<a href='tg://user?id={user.id}'>{telebot.util.escape(user.first_name or 'гость')}</a>"

def welcome_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📝 АНКЕТА", callback_data="start_form"))
    kb.add(InlineKeyboardButton("📎 Правила чата", url=RULES_LINK))
    return kb

def start_form(user_id: int, origin_chat_id: int = None):
    FORM_STATE[user_id] = {
        "progress": 0,
        "answers": [],
        "origin_chat_id": origin_chat_id  # куда публиковать результат
    }

def ask_next_question(user_id: int):
    state = FORM_STATE.get(user_id)
    if not state:
        return
    progress = state["progress"]
    if progress < len(QUESTIONS):
        bot.send_message(user_id, QUESTIONS[progress])
    else:
        # Сформировать и отправить итог и очистить состояние
        publish_form_result(user_id)

def publish_form_result(user_id: int):
    state = FORM_STATE.get(user_id)
    if not state:
        return

    answers = state["answers"]
    origin_chat_id = state.get("origin_chat_id")

    # Заполним пропуски "—"
    filled = answers + ["—"] * (len(QUESTIONS) - len(answers))

    text = (
        "🧾 <b>Короткая анкета</b>\n"
        f"Автор: {mention(bot.get_chat_member(user_id, user_id).user) if False else ''}"
    )

    # В Telegram API выше строчка с get_chat_member не применима для лички.
    # Используем данные из last_message, поэтому сделаем красивый вывод иначе:
    text = (
        "🧾 <b>Короткая анкета</b>\n"
        f"От: {mention(telebot.types.User(id=user_id, is_bot=False, first_name='Участник'))}\n\n"
        f"<b>{QUESTIONS[0]}</b>\n{telebot.util.escape(filled[0])}\n\n"
        f"<b>{QUESTIONS[1]}</b>\n{telebot.util.escape(filled[1])}\n\n"
        f"<b>{QUESTIONS[2]}</b>\n{telebot.util.escape(filled[2])}\n\n"
        f"<b>{QUESTIONS[3]}</b>\n{telebot.util.escape(filled[3])}\n\n"
        f"<i>Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"
    )

    # Публикуем в исходный чат (если известен), иначе — в личку пользователю
    if origin_chat_id:
        try:
            bot.send_message(origin_chat_id, text, disable_web_page_preview=True)
        except Exception as e:
            # Если не удалось в чат — отправим пользователю
            bot.send_message(user_id, "Не удалось опубликовать анкету в чат, отправляю тебе:", disable_web_page_preview=True)
            bot.send_message(user_id, text, disable_web_page_preview=True)
    else:
        bot.send_message(user_id, text, disable_web_page_preview=True)

    # Чистим состояние
    FORM_STATE.pop(user_id, None)

# ----------------- Хэндлеры -----------------

@bot.message_handler(commands=['start'])
def cmd_start(message):
    kb = welcome_keyboard()
    bot.reply_to(
        message,
        "Привет! Я — ваш приветственный бот 🤖\n\n"
        "Добавь меня в группу и при появлении новичков я:\n"
        "• весело их встречу по нику;\n"
        "• предложу заполнить короткую анкету;\n"
        "• скину ссылку на правила.\n\n"
        "Хочешь опробовать анкету прямо сейчас? Жми кнопку ниже 👇",
        reply_markup=kb
    )

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(
        message,
        "Как это работает:\n"
        "1) Добавьте бота в группу и отключите privacy у BotFather (/setprivacy → OFF)\n"
        "2) Я приветствую новых участников по нику и даю кнопки АНКЕТА и Правила\n"
        "3) Если участник заполнит анкету, опубликую результат в чат"
    )

@bot.message_handler(content_types=['new_chat_members'])
def greet_new_members(message):
    for new_user in message.new_chat_members:
        kb = welcome_keyboard()
        nick = f"@{new_user.username}" if new_user.username else (new_user.first_name or "гость")
        bot.send_message(
            message.chat.id,
            f"🥳 Добро пожаловать, {nick}! \n"
            "Здесь рофлы, мемы, флирты, лайтовое общение на взаимном уважении и оффлайн-тусовки, если поймаешь наш вайб ❤️ \n \n"
            "Начинай прямо сейчас и жми <b>АНКЕТА!</b> (После перейди в личку с ботом и ответь на вопросы) \n \n",
            "Пришли фото, если ты без него - Ноунеймам здесь не рады \n \n",
            "И жми кномпочку <b>ПРАВИЛА</b>, чтобы быть в курсе. \n \n",
            "По всем вопросам обращайся @nad_wild @zhurina71 @tsvetovaan 💋 \n \n",
            "Приятного общения!",
            reply_markup=kb
        )

@bot.callback_query_handler(func=lambda c: c.data == "start_form")
def cb_start_form(call):
    user_id = call.from_user.id
    origin_chat_id = call.message.chat.id  # куда постить итог
    # Старт анкеты и переходим в ЛС
    start_form(user_id, origin_chat_id=origin_chat_id)
    try:
        bot.answer_callback_query(call.id, "Открыл анкету в ЛС с ботом ✉️")
    except:
        pass
    bot.send_message(
        user_id,
        "Погнали! Отвечай коротко, по пунктам. Если передумал — напиши «стоп».\n"
        "Можно редактировать потом — не парься 😉"
    )
    ask_next_question(user_id)

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ["стоп", "stop", "cancel"])
def cancel_form(message):
    user_id = message.from_user.id
    if user_id in FORM_STATE:
        FORM_STATE.pop(user_id, None)
        bot.reply_to(message, "Окей, анкету отменил. Если захочешь — нажми кнопку «АНКЕТА» ещё раз.")
    else:
        bot.reply_to(message, "Сейчас никакая анкета не запущена. Можешь нажать «АНКЕТА» в меню.")

@bot.message_handler(func=lambda m: m.chat.type == "private")
def private_flow(message):
    user_id = message.from_user.id
    state = FORM_STATE.get(user_id)
    if not state:
        # Нет активной анкеты — предложим начать
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📝 Начать анкету", callback_data="start_form"))
        bot.reply_to(message, "Хочешь заполнить короткую анкету?", reply_markup=kb)
        return

    # Запись ответа и переход к следующему вопросу
    state["answers"].append(message.text.strip())
    state["progress"] += 1
    if state["progress"] < len(QUESTIONS):
        ask_next_question(user_id)
    else:
        bot.send_message(user_id, "Спасибо! Публикую краткую карточку в чат ✨")
        publish_form_result(user_id)

# Запуск
if __name__ == "__main__":
    print("Bot is starting polling as @PureRoflGreetingBot ...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
