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
    WAIT_REMOVE_GROUP,
) = range(11)

user_data_store = {}
broadcast_tasks = {}


MAX_GROUPS = 15


def init_user(user_id):
    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "api_id": None,
            "api_hash": None,
            "session_string": None,
            "interval": None,
            "message": None,
            "groups": [],
            "photo_file_id": None,
        }


def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("⏱ الوقت بين الرسائل", callback_data="set_interval")],
        [InlineKeyboardButton("📝 الكليشة (نص الرسالة)", callback_data="set_message")],
        [InlineKeyboardButton("🖼 إضافة صورة", callback_data="set_photo")],
        [InlineKeyboardButton("👥 اختيار المجموعات", callback_data="choose_group")],
        [InlineKeyboardButton("🗑 إزالة مجموعة", callback_data="remove_group")],
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
    photo = "✅ مضافة" if d.get("photo_file_id") else "❌"
    groups = d.get("groups", [])
    if groups:
        groups_text = f"✅ {len(groups)} مجموعة"
    else:
        groups_text = "❌"
    return (
        f"📊 *الحالة الحالية:*\n"
        f"🔑 الجلسة: {session}\n"
        f"⏱ الوقت: {interval}\n"
        f"📝 الكليشة: {message}\n"
        f"🖼 الصورة: {photo}\n"
        f"👥 المجموعات: {groups_text}"
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


# ─── زر اختيار المجموعات (حتى 15) ───────────────────────────────────────────
def get_groups_menu():
    keyboard = [[InlineKeyboardButton("✅ انتهيت من الإضافة", callback_data="done_groups")]]
    return InlineKeyboardMarkup(keyboard)


def groups_list_text(groups):
    if not groups:
        return ""
    lines = "\n".join(f"  {i+1}. {g['name']}" for i, g in enumerate(groups))
    return f"\n\n📋 *المجموعات المضافة حتى الآن ({len(groups)}/{MAX_GROUPS}):*\n{lines}"


async def choose_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not user_data_store.get(user_id, {}).get("session_string"):
        await query.message.reply_text("❌ يجب إضافة جلسة أولاً من زر (إضافة جلسة).")
        return ConversationHandler.END

    groups = user_data_store[user_id].get("groups", [])
    existing = groups_list_text(groups)
    remaining = MAX_GROUPS - len(groups)

    text = (
        f"👥 أرسل يوزرنيم المجموعة التي تريد إضافتها:\n\n"
        f"مثال: `@mygroup` أو `mygroup`\n\n"
        f"⚠️ يجب أن تكون عضواً في المجموعة.\n"
        f"📌 يمكنك إضافة حتى *{remaining}* مجموعة إضافية."
        + existing
    )

    markup = get_groups_menu() if groups else None
    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    return WAIT_GROUP_USERNAME


async def receive_group_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.message.text.strip().lstrip("@")
    data = user_data_store[user_id]
    groups = data.get("groups", [])

    if len(groups) >= MAX_GROUPS:
        await update.message.reply_text(
            f"⚠️ وصلت للحد الأقصى ({MAX_GROUPS} مجموعة).",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    already = [g for g in groups if g["username"] == username]
    if already:
        await update.message.reply_text(
            f"⚠️ المجموعة `@{username}` مضافة مسبقاً. أرسل يوزرنيم آخر:",
            parse_mode="Markdown",
            reply_markup=get_groups_menu(),
        )
        return WAIT_GROUP_USERNAME

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
        await client.disconnect()

        groups.append({"username": username, "name": group_name})
        user_data_store[user_id]["groups"] = groups

        remaining = MAX_GROUPS - len(groups)

        if remaining == 0:
            await update.message.reply_text(
                f"✅ تمت إضافة *{group_name}*\n\n"
                f"🎯 وصلت للحد الأقصى ({MAX_GROUPS} مجموعة). سيبدأ النشر في جميعها."
                + groups_list_text(groups),
                parse_mode="Markdown",
                reply_markup=get_main_menu(),
            )
            return ConversationHandler.END

        await update.message.reply_text(
            f"✅ تمت إضافة *{group_name}*\n\n"
            f"📌 يمكنك إضافة {remaining} مجموعة أخرى، أو اضغط *انتهيت*."
            + groups_list_text(groups),
            parse_mode="Markdown",
            reply_markup=get_groups_menu(),
        )
        return WAIT_GROUP_USERNAME

    except Exception as e:
        await update.message.reply_text(
            f"❌ تعذر الوصول للمجموعة: {str(e)}\n\n"
            "تأكد أنك عضو فيها وأن اليوزرنيم صحيح، ثم أرسله مجدداً:",
            reply_markup=get_groups_menu() if groups else None,
        )
        return WAIT_GROUP_USERNAME


async def done_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    groups = user_data_store[user_id].get("groups", [])

    if not groups:
        await query.message.reply_text("❌ لم تضف أي مجموعة بعد. أرسل يوزرنيم مجموعة:")
        return WAIT_GROUP_USERNAME

    await query.message.reply_text(
        f"✅ تم حفظ *{len(groups)}* مجموعة للنشر."
        + groups_list_text(groups),
        parse_mode="Markdown",
        reply_markup=get_main_menu(),
    )
    return ConversationHandler.END


# ─── زر إزالة مجموعة ─────────────────────────────────────────────────────────
async def remove_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    groups = user_data_store.get(user_id, {}).get("groups", [])

    if not groups:
        await query.message.reply_text("ℹ️ لا توجد مجموعات مضافة حتى الآن.")
        return ConversationHandler.END

    list_text = "\n".join(f"  {i+1}. {g['name']} (@{g['username']})" for i, g in enumerate(groups))
    await query.message.reply_text(
        f"🗑 *إزالة مجموعة*\n\n"
        f"📋 المجموعات الحالية:\n{list_text}\n\n"
        f"أرسل يوزرنيم المجموعة التي تريد إزالتها:\n"
        f"مثال: `@mygroup` أو `mygroup`",
        parse_mode="Markdown",
    )
    return WAIT_REMOVE_GROUP


async def receive_remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.message.text.strip().lstrip("@")
    groups = user_data_store[user_id].get("groups", [])

    found = [g for g in groups if g["username"] == username]
    if not found:
        list_text = "\n".join(f"  {i+1}. @{g['username']}" for i, g in enumerate(groups))
        await update.message.reply_text(
            f"❌ المجموعة `@{username}` غير موجودة في قائمتك.\n\n"
            f"📋 المجموعات المتاحة:\n{list_text}\n\n"
            f"أرسل يوزرنيم صحيح:",
            parse_mode="Markdown",
        )
        return WAIT_REMOVE_GROUP

    new_groups = [g for g in groups if g["username"] != username]
    user_data_store[user_id]["groups"] = new_groups

    if new_groups:
        remaining_text = "\n".join(f"  {i+1}. {g['name']}" for i, g in enumerate(new_groups))
        msg = (
            f"✅ تم إزالة `@{username}` من قائمة النشر.\n\n"
            f"📋 المجموعات المتبقية ({len(new_groups)}):\n{remaining_text}"
        )
    else:
        msg = f"✅ تم إزالة `@{username}`.\n\nلا توجد مجموعات مضافة الآن."

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu())
    return ConversationHandler.END


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
    if not data.get("groups"):
        missing.append("المجموعات")

    if missing:
        await query.message.reply_text(
            "❌ يجب إكمال الإعدادات التالية أولاً:\n"
            + "\n".join(f"• {m}" for m in missing)
        )
        return ConversationHandler.END

    if user_id in broadcast_tasks and not broadcast_tasks[user_id].done():
        await query.message.reply_text("⚠️ النشر يعمل بالفعل!")
        return ConversationHandler.END

    groups = data["groups"]
    groups_names = "\n".join(f"  • {g['name']}" for g in groups)
    photo_info = "مع صورة 🖼" if data.get("photo_file_id") else "بدون صورة"
    await query.message.reply_text(
        f"🚀 بدأ النشر!\n\n"
        f"📍 المجموعات ({len(groups)}):\n{groups_names}\n"
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
            groups,
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
async def broadcast_loop(user_id, api_id, api_hash, session, groups, message, interval_minutes, photo_file_id, context):
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
            success_count = 0
            fail_count = 0

            for group in groups:
                try:
                    if photo_file_id:
                        tg_file = await bot.get_file(photo_file_id)
                        photo_bytes = await tg_file.download_as_bytearray()
                        photo_io = io.BytesIO(bytes(photo_bytes))
                        photo_io.name = "photo.jpg"
                        await client.send_file(group["username"], photo_io, caption=message)
                    else:
                        await client.send_message(group["username"], message)
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    await bot.send_message(user_id, f"❌ فشل الإرسال لـ {group['name']}: {str(e)}")

            await bot.send_message(
                user_id,
                f"📬 جولة نشر مكتملة:\n✅ نجح: {success_count} | ❌ فشل: {fail_count}"
            )
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
            CallbackQueryHandler(remove_group_start, pattern="^remove_group$"),
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_username),
                CallbackQueryHandler(done_groups, pattern="^done_groups$"),
            ],
            WAIT_REMOVE_GROUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_remove_group),
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
            CallbackQueryHandler(remove_group_start, pattern="^remove_group$"),
            CallbackQueryHandler(done_groups, pattern="^done_groups$"),
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
