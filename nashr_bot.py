import asyncio
import logging
import threading
import os
import io
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# ─── Flask Server لـ UptimeRobot ─────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ البوت يعمل!", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8611830167:AAF6lR4rJ-_CiDmo68NfdOiQkLjiUg4OwCc"
DEVELOPER_USERNAME = "c9aac"

# States
(
    ASK_PHONE,
    ASK_CODE,
    ASK_2FA,
    ASK_API_ID_SESSION,
    ASK_API_HASH_SESSION,
    WAIT_SESSION_STRING,
    WAIT_INTERVAL,
    WAIT_MESSAGE,
    WAIT_GROUP_USERNAME,
    WAIT_PHOTO,
) = range(10)

user_data_store = {}
broadcast_tasks = {}


def init_user(user_id):
    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "api_id": None,
            "api_hash": None,
            "session_string": None,
            "interval": None,
            "message": None,
            "group": None,
            "group_id": None,
            "photo_file_id": None,
        }


def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("⏱ الوقت بين الرسائل", callback_data="set_interval")],
        [InlineKeyboardButton("📝 الكليشة (نص الرسالة)", callback_data="set_message")],
        [InlineKeyboardButton("🖼 إضافة صورة", callback_data="set_photo")],
        [InlineKeyboardButton("👥 اختيار المجموعة", callback_data="choose_group")],
        [InlineKeyboardButton("🔑 إضافة جلسة (Session)", callback_data="add_session")],
        [InlineKeyboardButton("🚀 بدء النشر", callback_data="start_broadcast")],
        [InlineKeyboardButton("⛔ إيقاف النشر", callback_data="stop_broadcast")],
        [InlineKeyboardButton("👨‍💻 المطور", callback_data="developer")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_status(user_id):
    d = user_data_store.get(user_id, {})
    session = "✅" if d.get("session_string") else "❌"
    interval = f"✅ {d.get('interval')} دقيقة" if d.get("interval") else "❌"
    message = "✅ محفوظة" if d.get("message") else "❌"
    group = f"✅ {d.get('group')}" if d.get("group") else "❌"
    photo = "✅ مضافة" if d.get("photo_file_id") else "❌"
    return (
        f"📊 *الحالة الحالية:*\n"
        f"🔑 الجلسة: {session}\n"
        f"⏱ الوقت: {interval}\n"
        f"📝 الكليشة: {message}\n"
        f"🖼 الصورة: {photo}\n"
        f"👥 المجموعة: {group}"
    )


# ─── /start ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    init_user(user_id)
    context.user_data.clear()

    await update.message.reply_text(
        "👋 مرحباً بك في بوت النشر التلقائي!\n\n"
        + get_status(user_id)
        + "\n\nاختر من القائمة:",
        parse_mode="Markdown",
        reply_markup=get_main_menu(),
    )
    return ConversationHandler.END


# ─── زر إضافة الجلسة ─────────────────────────────────────────────────────────
async def add_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    init_user(user_id)

    if user_data_store[user_id].get("api_id") and user_data_store[user_id].get("api_hash"):
        await query.message.reply_text(
            "🔑 أرسل الـ Session String الخاص بك:"
        )
        context.user_data["waiting_for"] = "session_string"
        return WAIT_SESSION_STRING
    else:
        await query.message.reply_text(
            "📌 لإضافة الجلسة نحتاج أولاً الـ API ID\n\n"
            "احصل عليه من: my.telegram.org\n\n"
            "أرسل الـ API ID:"
        )
        return ASK_API_ID_SESSION


async def ask_api_id_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("❌ API ID يجب أن يكون رقماً. أعد الإرسال:")
        return ASK_API_ID_SESSION

    user_data_store[user_id]["api_id"] = int(text)
    await update.message.reply_text("✅ تم.\n\nأرسل الآن الـ API Hash:")
    return ASK_API_HASH_SESSION


async def ask_api_hash_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store[user_id]["api_hash"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ تم.\n\nأرسل الآن الـ Session String الخاص بك:"
    )
    context.user_data["waiting_for"] = "session_string"
    return WAIT_SESSION_STRING


async def receive_session_string(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = update.message.text.strip()
    data = user_data_store[user_id]

    await update.message.reply_text("⏳ جاري التحقق من الجلسة...")

    try:
        client = TelegramClient(
            StringSession(session),
            data["api_id"],
            data["api_hash"],
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()
        me = await client.get_me()
        await client.disconnect()

        user_data_store[user_id]["session_string"] = session

        await update.message.reply_text(
            f"✅ تم التحقق من الجلسة بنجاح!\n"
            f"👤 الحساب: {me.first_name} (@{me.username})\n\n"
            + get_status(user_id),
            parse_mode="Markdown",
            reply_markup=get_main_menu(),
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ الجلسة غير صالحة أو منتهية: {str(e)}\n\nأرسل جلسة صحيحة:",
        )
        return WAIT_SESSION_STRING

    context.user_data.pop("waiting_for", None)
    return ConversationHandler.END


# ─── زر الوقت ────────────────────────────────────────────────────────────────
async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "⏱ أرسل الوقت بين كل رسالة بالدقائق (مثال: 1 أو 5 أو 10):"
    )
    context.user_data["waiting_for"] = "interval"
    return WAIT_INTERVAL


async def receive_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً أكبر من 0:")
        return WAIT_INTERVAL

    user_data_store[user_id]["interval"] = int(text)
    await update.message.reply_text(
        f"✅ تم تحديد الوقت: {text} دقيقة بين كل رسالة.",
        reply_markup=get_main_menu(),
    )
    context.user_data.pop("waiting_for", None)
    return ConversationHandler.END


# ─── زر الكليشة ──────────────────────────────────────────────────────────────
async def set_message_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 أرسل نص الرسالة التي تريد نشرها:")
    context.user_data["waiting_for"] = "message"
    return WAIT_MESSAGE


async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store[user_id]["message"] = update.message.text.strip()
    await update.message.reply_text(
        "✅ تم حفظ الكليشة.",
        reply_markup=get_main_menu(),
    )
    context.user_data.pop("waiting_for", None)
    return ConversationHandler.END


# ─── زر إضافة الصورة ─────────────────────────────────────────────────────────
async def set_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🖼 أرسل الصورة التي تريد إرفاقها مع الكليشة في رسالة النشر:\n\n"
        "ℹ️ سيتم إرسال الصورة والنص في رسالة واحدة."
    )
    context.user_data["waiting_for"] = "photo"
    return WAIT_PHOTO


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not update.message.photo:
        await update.message.reply_text("❌ يرجى إرسال صورة وليس ملفاً آخر. أرسل الصورة مجدداً:")
        return WAIT_PHOTO

    photo = update.message.photo[-1]
    user_data_store[user_id]["photo_file_id"] = photo.file_id

    await update.message.reply_text(
        "✅ تم حفظ الصورة. ستُرسل مع الكليشة في رسالة واحدة.",
        reply_markup=get_main_menu(),
    )
    context.user_data.pop("waiting_for", None)
    return ConversationHandler.END


# ─── زر المطور ───────────────────────────────────────────────────────────────
async def developer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("📩 تواصل مع المطور", url="https://t.me/c9aac")]]
    await query.message.reply_text(
        "👨‍💻 *المطور*\n\n"
        "تم تطوير هذا البوت بواسطة:\n"
        "🔗 @c9aac\n\n"
        "للتواصل أو الاستفسار اضغط الزر أدناه:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END


# ─── أمر /info للمطور فقط ────────────────────────────────────────────────────
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username != DEVELOPER_USERNAME:
        await update.message.reply_text("❌ هذا الأمر للمطور فقط.")
        return

    total_users = len(user_data_store)
    active_broadcasts = sum(
        1 for task in broadcast_tasks.values() if not task.done()
    )
    configured_users = sum(
        1 for d in user_data_store.values()
        if d.get("session_string") and d.get("message") and d.get("group")
    )

    await update.message.reply_text(
        "📊 *إحصائيات البوت*\n\n"
        f"👥 إجمالي المستخدمين: `{total_users}`\n"
        f"⚙️ مستخدمون مكتملو الإعداد: `{configured_users}`\n"
        f"🚀 جلسات نشر نشطة الآن: `{active_broadcasts}`",
        parse_mode="Markdown",
    )


# ─── زر اختيار المجموعة ──────────────────────────────────────────────────────
async def choose_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not user_data_store.get(user_id, {}).get("session_string"):
        await query.message.reply_text("❌ يجب إضافة جلسة أولاً من زر (إضافة جلسة).")
        return ConversationHandler.END

    await query.message.reply_text(
        "👥 أرسل يوزرنيم المجموعة التي تريد النشر فيها:\n\n"
        "مثال: `@mygroup` أو `mygroup`\n\n"
        "⚠️ يجب أن تكون عضواً في المجموعة.",
        parse_mode="Markdown",
    )
    return WAIT_GROUP_USERNAME


async def receive_group_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.message.text.strip().lstrip("@")
    data = user_data_store[user_id]

    await update.message.reply_text("⏳ جاري التحقق من المجموعة...")

    try:
        client = TelegramClient(
            StringSession(data["session_string"]),
            data["api_id"],
            data["api_hash"],
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()
        entity = await client.get_entity(username)
        group_name = getattr(entity, "title", username)
        group_id = entity.id
        await client.disconnect()

        user_data_store[user_id]["group"] = group_name
        user_data_store[user_id]["group_id"] = group_id
        user_data_store[user_id]["group_username"] = username

        await update.message.reply_text(
            f"✅ تم تحديد المجموعة: *{group_name}*",
            parse_mode="Markdown",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(
            f"❌ تعذر الوصول للمجموعة: {str(e)}\n\n"
            "تأكد أنك عضو فيها وأن اليوزرنيم صحيح، ثم أرسله مجدداً:"
        )
        return WAIT_GROUP_USERNAME


# ─── بدء النشر ───────────────────────────────────────────────────────────────
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data_store.get(user_id, {})

    missing = []
    if not data.get("session_string"):
        missing.append("الجلسة (Session)")
    if not data.get("api_id") or not data.get("api_hash"):
        missing.append("API ID و API Hash")
    if not data.get("interval"):
        missing.append("الوقت بين الرسائل")
    if not data.get("message"):
        missing.append("نص الرسالة (الكليشة)")
    if not data.get("group_id"):
        missing.append("المجموعة")

    if missing:
        await query.message.reply_text(
            "❌ يجب إكمال الإعدادات التالية أولاً:\n"
            + "\n".join(f"• {m}" for m in missing)
        )
        return ConversationHandler.END

    if user_id in broadcast_tasks and not broadcast_tasks[user_id].done():
        await query.message.reply_text("⚠️ النشر يعمل بالفعل!")
        return ConversationHandler.END

    photo_info = "مع صورة 🖼" if data.get("photo_file_id") else "بدون صورة"
    await query.message.reply_text(
        f"🚀 بدأ النشر!\n\n"
        f"📍 المجموعة: {data['group']}\n"
        f"⏱ كل: {data['interval']} دقيقة\n"
        f"📝 الرسالة: {str(data['message'])[:50]}\n"
        f"🖼 الصورة: {photo_info}"
    )

    task = asyncio.create_task(
        broadcast_loop(
            user_id,
            data["api_id"],
            data["api_hash"],
            data["session_string"],
            data["group_username"],
            data["message"],
            data["interval"],
            data.get("photo_file_id"),
            context,
        )
    )
    broadcast_tasks[user_id] = task
    return ConversationHandler.END


# ─── إيقاف النشر ─────────────────────────────────────────────────────────────
async def stop_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id in broadcast_tasks and not broadcast_tasks[user_id].done():
        broadcast_tasks[user_id].cancel()
        await query.message.reply_text("⛔ تم إيقاف النشر.")
    else:
        await query.message.reply_text("ℹ️ لا يوجد نشر نشط حالياً.")
    return ConversationHandler.END


# ─── حلقة النشر ──────────────────────────────────────────────────────────────
async def broadcast_loop(user_id, api_id, api_hash, session, group_username, message, interval_minutes, photo_file_id, context):
    bot = context.application.bot
    interval_seconds = interval_minutes * 60

    try:
        client = TelegramClient(
            StringSession(session), api_id, api_hash,
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()

        while True:
            try:
                if photo_file_id:
                    tg_file = await bot.get_file(photo_file_id)
                    photo_bytes = await tg_file.download_as_bytearray()
                    photo_io = io.BytesIO(bytes(photo_bytes))
                    photo_io.name = "photo.jpg"
                    await client.send_file(
                        group_username,
                        photo_io,
                        caption=message,
                    )
                else:
                    await client.send_message(group_username, message)

                await bot.send_message(user_id, "✅ تم إرسال رسالة.")
            except Exception as e:
                await bot.send_message(user_id, f"❌ فشل الإرسال: {str(e)}")

            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        try:
            await client.disconnect()
        except:
            pass
        await bot.send_message(user_id, "⛔ تم إيقاف النشر التلقائي.")

    except Exception as e:
        await bot.send_message(user_id, f"❌ خطأ في النشر: {str(e)}")


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask server started for UptimeRobot")

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(add_session_start, pattern="^add_session$"),
            CallbackQueryHandler(set_interval_start, pattern="^set_interval$"),
            CallbackQueryHandler(set_message_start, pattern="^set_message$"),
            CallbackQueryHandler(set_photo_start, pattern="^set_photo$"),
            CallbackQueryHandler(choose_group_start, pattern="^choose_group$"),
            CallbackQueryHandler(start_broadcast, pattern="^start_broadcast$"),
            CallbackQueryHandler(stop_broadcast, pattern="^stop_broadcast$"),
            CallbackQueryHandler(developer_info, pattern="^developer$"),
        ],
        states={
            ASK_API_ID_SESSION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_id_session)
            ],
            ASK_API_HASH_SESSION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_hash_session)
            ],
            WAIT_SESSION_STRING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_session_string)
            ],
            WAIT_INTERVAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_interval)
            ],
            WAIT_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_message)
            ],
            WAIT_GROUP_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_username)
            ],
            WAIT_PHOTO: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_photo),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(add_session_start, pattern="^add_session$"),
            CallbackQueryHandler(set_interval_start, pattern="^set_interval$"),
            CallbackQueryHandler(set_message_start, pattern="^set_message$"),
            CallbackQueryHandler(set_photo_start, pattern="^set_photo$"),
            CallbackQueryHandler(choose_group_start, pattern="^choose_group$"),
            CallbackQueryHandler(start_broadcast, pattern="^start_broadcast$"),
            CallbackQueryHandler(stop_broadcast, pattern="^stop_broadcast$"),
            CallbackQueryHandler(developer_info, pattern="^developer$"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("info", info_command))

    print("✅ البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
