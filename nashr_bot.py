import asyncio
import logging
import threading
import os
import io
import json
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
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

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
DATA_FILE = os.environ.get("BOT_DATA_FILE", "bot_data.json")


def load_user_data():
    global user_data_store
    if not os.path.exists(DATA_FILE):
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        user_data_store = {int(user_id): data for user_id, data in raw_data.items()}
        logger.info("Loaded saved bot settings for %s users", len(user_data_store))
    except Exception as e:
        logger.error("Failed to load saved bot settings: %s", e)
        user_data_store = {}


def save_user_data():
    try:
        temp_file = DATA_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(user_data_store, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, DATA_FILE)
    except Exception as e:
        logger.error("Failed to save bot settings: %s", e)


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
            "🔑 أرسل الـ Session String الخاص بك:\n\n"
            "ℹ️ إذا ما تعرف من وين تجيب الـ Session String، تقدر تجيبها من هذا البوت: @excuteerbot"
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
    save_user_data()
    await update.message.reply_text("✅ تم.\n\nأرسل الآن الـ API Hash:")
    return ASK_API_HASH_SESSION


async def ask_api_hash_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store[user_id]["api_hash"] = update.message.text.strip()
    save_user_data()
    await update.message.reply_text(
        "✅ تم.\n\nأرسل الآن الـ Session String الخاص بك:\n\n"
        "ℹ️ إذا ما تعرف من وين تجيب الـ Session String، تقدر تجيبها من هذا البوت: @excuteerbot"
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
        save_user_data()

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
    save_user_data()
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
    save_user_data()
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
    save_user_data()

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


def get_group_key(group):
    return str(group.get("target") or group.get("username", "")).lower()


def normalize_chat_id(value):
    try:
        chat_id = int(value)
    except (TypeError, ValueError):
        return value

    if str(chat_id).startswith("-100"):
        return int(str(chat_id)[4:])
    if chat_id < 0:
        return abs(chat_id)
    return chat_id


def group_display(group):
    username = group.get("username")
    if username and not str(username).startswith("id:"):
        return f"@{username}"
    return "مجموعة خاصة"


def groups_list_text(groups):
    if not groups:
        return ""
    lines = "\n".join(
        f"  {i+1}. {g['name']} ({group_display(g)})"
        for i, g in enumerate(groups)
    )
    return f"\n\n📋 *المجموعات المضافة حتى الآن ({len(groups)}/{MAX_GROUPS}):*\n{lines}"


def extract_invite_hash(text):
    text = text.strip()
    for marker in ("t.me/+", "telegram.me/+", "t.me/joinchat/", "telegram.me/joinchat/"):
        if marker in text:
            invite_hash = text.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
            return invite_hash.strip()
    return None


def extract_public_username(text):
    text = text.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/", "http://telegram.me/", "telegram.me/"):
        if text.startswith(prefix):
            text = text.split(prefix, 1)[1].split("?", 1)[0].split("/", 1)[0]
            break
    return text.lstrip("@")


async def find_entity_by_id(client, chat_id):
    normalized_id = normalize_chat_id(chat_id)
    try:
        return await client.get_entity(normalized_id)
    except Exception:
        pass

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        entity_ids = {getattr(entity, "id", None)}
        entity_ids.add(normalize_chat_id(getattr(entity, "id", None)))
        if normalized_id in entity_ids or chat_id in entity_ids:
            return entity

    raise ValueError("لم أستطع العثور على المجموعة الخاصة في جلسة تيليجرام. تأكد أن الحساب عضو فيها.")


async def resolve_group_entity(client, message):
    forwarded_chat = getattr(message, "forward_from_chat", None)
    if forwarded_chat:
        entity = await find_entity_by_id(client, forwarded_chat.id)
        return entity, f"id:{getattr(entity, 'id', forwarded_chat.id)}", getattr(forwarded_chat, "title", None)

    forward_origin = getattr(message, "forward_origin", None)
    origin_chat = getattr(forward_origin, "chat", None) if forward_origin else None
    if origin_chat:
        entity = await find_entity_by_id(client, origin_chat.id)
        return entity, f"id:{getattr(entity, 'id', origin_chat.id)}", getattr(origin_chat, "title", None)

    text = (message.text or "").strip()
    invite_hash = extract_invite_hash(text)
    if invite_hash:
        invite = await client(CheckChatInviteRequest(invite_hash))
        chat = getattr(invite, "chat", None)
        if chat is None:
            updates = await client(ImportChatInviteRequest(invite_hash))
            chats = getattr(updates, "chats", [])
            if not chats:
                raise ValueError("رابط الدعوة غير صالح أو لا يمكن الوصول له.")
            chat = chats[0]
        entity = await client.get_entity(chat)
        return entity, f"id:{getattr(entity, 'id', '')}", getattr(entity, "title", None)

    if text.lstrip("-").isdigit():
        entity = await find_entity_by_id(client, int(text))
        return entity, f"id:{getattr(entity, 'id', text)}", getattr(entity, "title", None)

    username = extract_public_username(text)
    entity = await client.get_entity(username)
    return entity, username, getattr(entity, "title", username)


async def resolve_saved_group(client, group):
    target = group.get("target") or group.get("username")
    if isinstance(target, str) and target.startswith("id:"):
        return await find_entity_by_id(client, int(target.split(":", 1)[1]))
    return await client.get_entity(target)


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
        f"👥 أرسل المجموعة التي تريد إضافتها:\n\n"
        f"• مجموعة عامة: `@mygroup` أو رابطها\n"
        f"• مجموعة خاصة: أرسل رابط الدعوة، أو آيدي المجموعة، أو حوّل رسالة من المجموعة هنا\n\n"
        f"⚠️ يجب أن يكون حساب الجلسة عضواً في المجموعة.\n"
        f"📌 يمكنك إضافة حتى *{remaining}* مجموعة إضافية."
        + existing
    )

    markup = get_groups_menu() if groups else None
    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    return WAIT_GROUP_USERNAME


async def receive_group_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data_store[user_id]
    groups = data.get("groups", [])

    if len(groups) >= MAX_GROUPS:
        await update.message.reply_text(
            f"⚠️ وصلت للحد الأقصى ({MAX_GROUPS} مجموعة).",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    await update.message.reply_text("⏳ جاري التحقق من المجموعة...")

    client = None
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
        entity, target, fallback_name = await resolve_group_entity(client, update.message)
        group_name = getattr(entity, "title", None) or fallback_name or str(target)
        username = getattr(entity, "username", None)
        stored_group = {
            "username": username or target,
            "target": username or target,
            "name": group_name,
        }

        already = [g for g in groups if get_group_key(g) == get_group_key(stored_group)]
        if already:
            await update.message.reply_text(
                f"⚠️ المجموعة *{group_name}* مضافة مسبقاً. أرسل مجموعة أخرى:",
                parse_mode="Markdown",
                reply_markup=get_groups_menu(),
            )
            return WAIT_GROUP_USERNAME

        groups.append(stored_group)
        user_data_store[user_id]["groups"] = groups

        remaining = MAX_GROUPS - len(groups)
        save_user_data()


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
            "للمجموعات العامة أرسل اليوزرنيم، وللخاصة أرسل رابط الدعوة أو آيدي المجموعة أو حوّل رسالة منها:",
            reply_markup=get_groups_menu() if groups else None,
        )
        return WAIT_GROUP_USERNAME
    finally:
        if client:
            await client.disconnect()


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
def get_remove_groups_menu(groups):
    keyboard = []
    for i, group in enumerate(groups):
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {i+1}. {group['name']}",
                callback_data=f"remove_group_idx:{i}",
            )
        ])
    keyboard.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)


async def remove_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    init_user(user_id)
    groups = user_data_store[user_id].get("groups", [])

    if not groups:
        await query.message.reply_text("ℹ️ لا توجد مجموعات مضافة حتى الآن.", reply_markup=get_main_menu())
        return ConversationHandler.END

    list_text = "\n".join(f"  {i+1}. {g['name']} ({group_display(g)})" for i, g in enumerate(groups))
    await query.message.reply_text(
        f"🗑 *إزالة مجموعة*\n\n"
        f"📋 المجموعات الحالية:\n{list_text}\n\n"
        f"اختر المجموعة التي تريد إزالتها من الأزرار أدناه، أو أرسل اليوزرنيم يدوياً:",
        parse_mode="Markdown",
        reply_markup=get_remove_groups_menu(groups),
    )
    return WAIT_REMOVE_GROUP


async def receive_remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.message.text.strip().lstrip("@")
    groups = user_data_store[user_id].get("groups", [])

    found = [g for g in groups if str(g.get("username", "")).lstrip("@").lower() == username.lower()]
    if not found:
        list_text = "\n".join(f"  {i+1}. {g['name']} ({group_display(g)})" for i, g in enumerate(groups))
        await update.message.reply_text(
            f"❌ المجموعة `@{username}` غير موجودة في قائمتك.\n\n"
            f"📋 المجموعات المتاحة:\n{list_text}\n\n"
            f"أرسل يوزرنيم صحيح:",
            parse_mode="Markdown",
        )
        return WAIT_REMOVE_GROUP

    new_groups = [g for g in groups if str(g.get("username", "")).lstrip("@").lower() != username.lower()]
    user_data_store[user_id]["groups"] = new_groups
    save_user_data()

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


async def remove_group_by_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    init_user(user_id)
    groups = user_data_store[user_id].get("groups", [])

    try:
        index = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.message.reply_text("❌ اختيار غير صالح.", reply_markup=get_main_menu())
        return ConversationHandler.END

    if index < 0 or index >= len(groups):
        await query.message.reply_text("❌ هذه المجموعة لم تعد موجودة في القائمة.", reply_markup=get_main_menu())
        return ConversationHandler.END

    removed = groups.pop(index)
    user_data_store[user_id]["groups"] = groups
    save_user_data()

    if groups:
        remaining_text = "\n".join(f"  {i+1}. {g['name']}" for i, g in enumerate(groups))
        msg = (
            f"✅ تم إزالة *{removed['name']}* من قائمة النشر.\n\n"
            f"📋 المجموعات المتبقية ({len(groups)}):\n{remaining_text}"
        )
    else:
        msg = f"✅ تم إزالة *{removed['name']}*.\n\nلا توجد مجموعات مضافة الآن."

    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_menu())
    return ConversationHandler.END


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    init_user(user_id)
    await query.message.reply_text(
        get_status(user_id) + "\n\nاختر من القائمة:",
        parse_mode="Markdown",
        reply_markup=get_main_menu(),
    )
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


def is_photo_forbidden_error(error):
    error_text = str(error).upper()
    return (
        "CHAT_SEND_PHOTOS_FORBIDDEN" in error_text
        or "SEND_PHOTOS_FORBIDDEN" in error_text
        or "SENDMEDIAREQUEST" in error_text
    )


def build_live_broadcast_text(round_number, total_groups, current_group, success_count, text_only_count, fail_count, status):
    current_group_text = current_group or "بانتظار بدء الجولة"
    return (
        f"📡 حالة النشر المباشر\n\n"
        f"🔁 الجولة: {round_number}\n"
        f"📍 المجموعة الحالية: {current_group_text}\n"
        f"👥 إجمالي المجموعات: {total_groups}\n\n"
        f"✅ نجح: {success_count}\n"
        f"⚠️ نص فقط: {text_only_count}\n"
        f"❌ فشل: {fail_count}\n\n"
        f"🟢 الحالة: {status}"
    )


async def update_live_broadcast_message(bot, user_id, live_message, text):
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=live_message.message_id,
            text=text,
        )
        return live_message
    except Exception:
        return live_message


# ─── حلقة النشر ──────────────────────────────────────────────────────────────
async def broadcast_loop(user_id, api_id, api_hash, session, groups, message, interval_minutes, photo_file_id, context):
    bot = context.application.bot
    interval_seconds = interval_minutes * 60
    client = None
    live_message = None
    round_number = 0

    try:
        client = TelegramClient(
            StringSession(session), api_id, api_hash,
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.8.1",
            lang_code="ar",
        )
        await client.connect()

        live_message = await bot.send_message(
            user_id,
            build_live_broadcast_text(0, len(groups), None, 0, 0, 0, "تم تشغيل النشر"),
        )

        while True:
            round_number += 1
            success_count = 0
            text_only_count = 0
            fail_count = 0
            photo_bytes = None

            await update_live_broadcast_message(
                bot,
                user_id,
                live_message,
                build_live_broadcast_text(round_number, len(groups), None, success_count, text_only_count, fail_count, "جاري تجهيز الجولة"),
            )

            if photo_file_id:
                tg_file = await bot.get_file(photo_file_id)
                photo_bytes = bytes(await tg_file.download_as_bytearray())

            for group in groups:
                group_name = group.get("name", "مجموعة")
                status = "جاري الإرسال"

                await update_live_broadcast_message(
                    bot,
                    user_id,
                    live_message,
                    build_live_broadcast_text(round_number, len(groups), group_name, success_count, text_only_count, fail_count, status),
                )

                try:
                    target_entity = await resolve_saved_group(client, group)

                    if photo_bytes:
                        photo_io = io.BytesIO(photo_bytes)
                        photo_io.name = "photo.jpg"
                        try:
                            await client.send_file(target_entity, photo_io, caption=message)
                            success_count += 1
                            status = "تم الإرسال مع الصورة"
                        except Exception as photo_error:
                            if is_photo_forbidden_error(photo_error):
                                await client.send_message(target_entity, message)
                                success_count += 1
                                text_only_count += 1
                                status = "المجموعة تمنع الصور، تم إرسال النص فقط"
                            else:
                                raise photo_error
                    else:
                        await client.send_message(target_entity, message)
                        success_count += 1
                        status = "تم إرسال النص"
                except Exception as e:
                    fail_count += 1
                    error_text = str(e)
                    if len(error_text) > 90:
                        error_text = error_text[:90] + "..."
                    status = f"فشل الإرسال: {error_text}"

                await update_live_broadcast_message(
                    bot,
                    user_id,
                    live_message,
                    build_live_broadcast_text(round_number, len(groups), group_name, success_count, text_only_count, fail_count, status),
                )

            summary_status = f"اكتملت الجولة، الجولة التالية بعد {interval_minutes} دقيقة"
            await update_live_broadcast_message(
                bot,
                user_id,
                live_message,
                build_live_broadcast_text(round_number, len(groups), "تمت كل المجموعات", success_count, text_only_count, fail_count, summary_status),
            )
            await asyncio.sleep(interval_seconds)

    except asyncio.CancelledError:
        try:
            if client:
                await client.disconnect()
        except:
            pass

        if live_message:
            await update_live_broadcast_message(
                bot,
                user_id,
                live_message,
                build_live_broadcast_text(round_number, len(groups), "متوقف", 0, 0, 0, "تم إيقاف النشر التلقائي"),
            )
        else:
            await bot.send_message(user_id, "⛔ تم إيقاف النشر التلقائي.")

    except Exception as e:
        if live_message:
            await update_live_broadcast_message(
                bot,
                user_id,
                live_message,
                build_live_broadcast_text(round_number, len(groups), "خطأ", 0, 0, 0, f"خطأ في النشر: {str(e)}"),
            )
        else:
            await bot.send_message(user_id, f"❌ خطأ في النشر: {str(e)}")

# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    load_user_data()

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
                MessageHandler((filters.TEXT | filters.FORWARDED) & ~filters.COMMAND, receive_group_username),
                CallbackQueryHandler(done_groups, pattern="^done_groups$"),
            ],
            WAIT_REMOVE_GROUP: [
                CallbackQueryHandler(remove_group_by_button, pattern="^remove_group_idx:[0-9]+$"),
                CallbackQueryHandler(back_to_main, pattern="^back_to_main$"),
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
            CallbackQueryHandler(remove_group_by_button, pattern="^remove_group_idx:[0-9]+$"),
            CallbackQueryHandler(back_to_main, pattern="^back_to_main$"),
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
