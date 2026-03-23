import asyncio
import logging
import threading
import os
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
import io

# ─── Flask Server لـ UptimeRobot ────────────────────────────────────────────
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
APP_NAME = "MediaSyncApp"

# States
(
    ASK_API_ID,
    ASK_API_HASH,
    ASK_PHONE,
    ASK_CODE,
    ASK_2FA,
    WAIT_SESSION_INPUT,
    WAIT_INTERVAL,
    WAIT_MESSAGE,
    WAIT_GROUP_CHOICE,
    BROADCASTING,
) = range(10)

user_data_store = {}
broadcast_tasks = {}


def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("⏱ الوقت بين الرسائل", callback_data="set_interval")],
        [InlineKeyboardButton("📝 الكليشة (نص الرسالة)", callback_data="set_message")],
        [InlineKeyboardButton("👥 اختيار المجموعة", callback_data="choose_group")],
        [InlineKeyboardButton("🔑 إضافة جلسة (Session)", callback_data="add_session")],
        [InlineKeyboardButton("🚀 بدء النشر", callback_data="start_broadcast")],
        [InlineKeyboardButton("⛔ إيقاف النشر", callback_data="stop_broadcast")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "api_id": None,
            "api_hash": None,
            "session_string": None,
            "interval": None,
            "message": None,
            "group": None,
            "group_id": None,
        }

    if user_data_store[user_id].get("session_string"):
        await update.message.reply_text(
            "✅ مرحباً! لديك جلسة نشطة.\n\nاختر من القائمة:",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "👋 مرحباً بك في بوت النشر التلقائي!\n\n"
            "لبدء الاستخدام، نحتاج إلى ربط حسابك بتلغرام.\n\n"
            "📌 *الخطوة 1:* أرسل لي الـ API ID الخاص بك.\n"
            "يمكنك الحصول عليه من: my.telegram.org",
            parse_mode="Markdown",
        )
        return ASK_API_ID


async def ask_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("❌ API ID يجب أن يكون رقماً فقط. أعد الإرسال:")
        return ASK_API_ID

    user_data_store[user_id]["api_id"] = int(text)
    await update.message.reply_text(
        "✅ تم حفظ الـ API ID.\n\n"
        "📌 *الخطوة 2:* أرسل لي الـ API Hash الخاص بك:",
        parse_mode="Markdown",
    )
    return ASK_API_HASH


async def ask_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    user_data_store[user_id]["api_hash"] = text
    await update.message.reply_text(
        "✅ تم حفظ الـ API Hash.\n\n"
        "📌 *الخطوة 3:* أرسل رقم هاتفك مع رمز الدولة\n"
        "مثال: `+966XXXXXXXXX`",
        parse_mode="Markdown",
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    data = user_data_store[user_id]

    await update.message.reply_text("⏳ جاري إرسال كود التحقق...")

    try:
        client = TelegramClient(
            StringSession(), data["api_id"], data["api_hash"],
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()
        result = await client.send_code_request(phone)
        data["phone"] = phone
        data["phone_code_hash"] = result.phone_code_hash
        data["client"] = client
        context.user_data["client"] = client

        await update.message.reply_text(
            "📩 تم إرسال كود التحقق إلى رقمك.\n\n"
            "⚠️ أرسل الكود بهذا الشكل (ضع شرطة بين كل رقم):\n"
            "مثال: `5-6-6-1-4`\n\n"
            "هذا يحمي حسابك من الحظر.",
            parse_mode="Markdown",
        )
        return ASK_CODE

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}\n\nأعد إرسال رقم الهاتف:")
        return ASK_PHONE


async def ask_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = update.message.text.strip()
    # إزالة الشرطات والمسافات قبل إرسال الكود لتلغرام
    code = raw.replace("-", "").replace(" ", "").replace(".", "")
    data = user_data_store[user_id]
    client = data.get("client")

    try:
        await client.sign_in(
            data["phone"], code, phone_code_hash=data["phone_code_hash"]
        )
        session_string = client.session.save()
        data["session_string"] = session_string
        await client.disconnect()

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح!\n\n"
            "🔐 هذا هو الـ Session String الخاص بك (احتفظ به في مكان آمن):\n\n"
            f"`{session_string}`\n\n"
            "يمكنك استخدامه لاحقاً بضغط زر (إضافة جلسة) بدلاً من إعادة تسجيل الدخول.",
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            "🎉 أنت الآن جاهز للنشر! اختر من القائمة:",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔒 حسابك محمي بكلمة مرور ثنائية.\n\nأرسل كلمة المرور:"
        )
        return ASK_2FA

    except Exception as e:
        await update.message.reply_text(f"❌ كود خاطئ أو منتهي الصلاحية: {str(e)}\n\nأرسل الكود مرة أخرى:")
        return ASK_CODE


async def ask_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    data = user_data_store[user_id]
    client = data.get("client")

    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        data["session_string"] = session_string
        await client.disconnect()

        await update.message.reply_text(
            "✅ تم تسجيل الدخول بنجاح!\n\n"
            "🔐 هذا هو الـ Session String الخاص بك:\n\n"
            f"`{session_string}`\n\n"
            "احتفظ به في مكان آمن!",
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            "🎉 أنت الآن جاهز للنشر! اختر من القائمة:",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(f"❌ كلمة المرور خاطئة: {str(e)}\n\nأعد الإدخال:")
        return ASK_2FA


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data_store.get(user_id, {})

    if query.data == "set_interval":
        await query.message.reply_text(
            "⏱ أرسل الوقت بين كل رسالة بالدقائق (مثال: 1 أو 5 أو 10):"
        )
        context.user_data["waiting_for"] = "interval"
        return WAIT_INTERVAL

    elif query.data == "set_message":
        await query.message.reply_text(
            "📝 أرسل نص الرسالة التي تريد نشرها:"
        )
        context.user_data["waiting_for"] = "message"
        return WAIT_MESSAGE

    elif query.data == "choose_group":
        session = data.get("session_string")
        api_id = data.get("api_id")
        api_hash = data.get("api_hash")

        if not session:
            await query.message.reply_text("❌ يجب إضافة جلسة أولاً من زر (إضافة جلسة).")
            return ConversationHandler.END

        if not api_id or not api_hash:
            await query.message.reply_text("❌ يجب إدخال API ID و API Hash أولاً.")
            return ConversationHandler.END

        await query.message.reply_text("⏳ جاري جلب قائمة المجموعات...")

        try:
            client = TelegramClient(
                StringSession(session), api_id, api_hash,
                device_model="Desktop",
                system_version="Windows 10",
                app_version="4.8.1",
                lang_code="ar",
            )
            await client.connect()
            groups = []
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    groups.append((dialog.name, dialog.id))
            await client.disconnect()

            if not groups:
                await query.message.reply_text("❌ لا توجد مجموعات في حسابك.")
                return ConversationHandler.END

            keyboard = []
            for name, gid in groups[:20]:
                keyboard.append([
                    InlineKeyboardButton(name[:40], callback_data=f"group_{gid}_{name[:20]}")
                ])

            await query.message.reply_text(
                "👥 اختر المجموعة التي تريد النشر فيها:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return WAIT_GROUP_CHOICE

        except Exception as e:
            await query.message.reply_text(f"❌ حدث خطأ أثناء جلب المجموعات: {str(e)}")
            return ConversationHandler.END

    elif query.data == "add_session":
        await query.message.reply_text(
            "🔑 أرسل الـ Session String الخاص بك:\n\n"
            "⚠️ تأكد أنك حصلت عليه من هذا البوت أو من مصدر موثوق."
        )
        context.user_data["waiting_for"] = "session"
        return WAIT_SESSION_INPUT

    elif query.data == "start_broadcast":
        session = data.get("session_string")
        api_id = data.get("api_id")
        api_hash = data.get("api_hash")
        interval = data.get("interval")
        message = data.get("message")
        group_id = data.get("group_id")
        group_name = data.get("group")

        missing = []
        if not session:
            missing.append("الجلسة (Session)")
        if not interval:
            missing.append("الوقت بين الرسائل")
        if not message:
            missing.append("نص الرسالة (الكليشة)")
        if not group_id:
            missing.append("المجموعة")

        if missing:
            await query.message.reply_text(
                "❌ يجب إكمال الإعدادات التالية أولاً:\n" + "\n".join(f"• {m}" for m in missing)
            )
            return ConversationHandler.END

        if user_id in broadcast_tasks and not broadcast_tasks[user_id].done():
            await query.message.reply_text("⚠️ النشر يعمل بالفعل!")
            return ConversationHandler.END

        await query.message.reply_text(
            f"🚀 بدأ النشر!\n\n"
            f"📍 المجموعة: {group_name}\n"
            f"⏱ كل: {interval} دقيقة\n"
            f"📝 الرسالة: {message[:50]}..."
        )

        task = asyncio.create_task(
            broadcast_loop(user_id, api_id, api_hash, session, group_id, message, interval, context)
        )
        broadcast_tasks[user_id] = task
        return ConversationHandler.END

    elif query.data == "stop_broadcast":
        if user_id in broadcast_tasks and not broadcast_tasks[user_id].done():
            broadcast_tasks[user_id].cancel()
            await query.message.reply_text("⛔ تم إيقاف النشر.")
        else:
            await query.message.reply_text("ℹ️ لا يوجد نشر نشط حالياً.")
        return ConversationHandler.END

    elif query.data.startswith("group_"):
        parts = query.data.split("_", 2)
        group_id = int(parts[1])
        group_name = parts[2] if len(parts) > 2 else "مجموعة"
        user_data_store[user_id]["group_id"] = group_id
        user_data_store[user_id]["group"] = group_name

        await query.message.reply_text(
            f"✅ تم اختيار المجموعة: {group_name}",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    return ConversationHandler.END


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting_for")

    if waiting == "interval":
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

    elif waiting == "message":
        user_data_store[user_id]["message"] = text
        await update.message.reply_text(
            f"✅ تم حفظ الكليشة:\n{text[:100]}...\n\nاختر من القائمة:",
            reply_markup=get_main_menu(),
        )
        context.user_data.pop("waiting_for", None)
        return ConversationHandler.END

    elif waiting == "session":
        user_data_store[user_id]["session_string"] = text

        api_id = user_data_store[user_id].get("api_id")
        api_hash = user_data_store[user_id].get("api_hash")

        if not api_id or not api_hash:
            await update.message.reply_text(
                "⚠️ تم حفظ الجلسة، لكن تحتاج أيضاً إلى إدخال API ID و API Hash.\n"
                "أرسل /start للبدء من جديد وإدخالهما."
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "✅ تم حفظ الجلسة بنجاح! يمكنك الآن استخدام البوت.",
            reply_markup=get_main_menu(),
        )
        context.user_data.pop("waiting_for", None)
        return ConversationHandler.END

    return ConversationHandler.END


async def broadcast_loop(user_id, api_id, api_hash, session, group_id, message, interval_minutes, context):
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
                await client.send_message(group_id, message)
                await bot.send_message(user_id, f"✅ تم إرسال رسالة إلى المجموعة.")
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


def main():
    # تشغيل Flask في خيط منفصل حتى يعمل UptimeRobot
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Flask server started for UptimeRobot")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_id)],
            ASK_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_api_hash)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_code)],
            ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_2fa)],
            WAIT_SESSION_INPUT: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
            ],
            WAIT_INTERVAL: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
            ],
            WAIT_MESSAGE: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
            ],
            WAIT_GROUP_CHOICE: [
                CallbackQueryHandler(button_handler),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(button_handler),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
