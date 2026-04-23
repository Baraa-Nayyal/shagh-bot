import os
import html
import sqlite3
from datetime import date, datetime
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.ext import MessageHandler, filters
import random
from dotenv import load_dotenv
import os

load_dotenv()

DB_PATH = "bot.db"
ADMIN_ID = 7861055850
TOKEN = os.getenv("BOT_TOKEN")

WARNING_MESSAGE = """
⚠️ تنبيه مهم بخصوص المتابعة

تم إزالة بعض المستخدمين من نظام المتابعة بسبب عدم وجود أي نشاط أو نقاط منذ بداية التحدي.

فكرة هذا الإشتراك أساساً قائمة على الالتزام اليومي والمتابعة الحقيقية للإنجاز. وجود أشخاص مسجلين بدون أي مشاركة أو إنجاز يخلق شعور عام بالتقصير ويؤثر على جو الالتزام عند باقي المشاركين الذين يحاولون فعلاً الاستمرار.

نُقدر ظروف الجميع، لكن في نفس الوقت الهدف من الاشتراك في التحدي ليس فقط الوجود في المجموعة، بل المشاركة الفعلية والعمل اليومي حتى لو كان الإنجاز بسيط.

إذا كان لديك ظرف أو سبب معين منعك من المشاركة خلال الفترة الماضية، يمكنك إرسال تبرير أو توضيح على الخاص حتى نراجع الخطة للفترة القادمة، وبعدها يمكنك التسجيل مرة أخرى.

الهدف ليس الإقصاء، بل الحفاظ على جدية التحدي وعدم التأثير على الأشخاص الملتزمين فعلاً والذين يحاولون الاستمرار يومياً.

بالتوفيق للجميع
""".strip()


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP})


def get_group_id(update: Update) -> int | None:
    chat = update.effective_chat
    return chat.id if chat else None


def get_thread_id(update: Update) -> int | None:
    message = update.effective_message
    return message.message_thread_id if message else None


def mention_html(user_id: int, name: str) -> str:
    safe_name = html.escape(name or "User")
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


async def reply_same_place(update: Update, text: str):
    message = update.effective_message
    if not message:
        return
    await message.reply_text(text, parse_mode=ParseMode.HTML)


async def send_in_same_topic(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
):
    chat = update.effective_chat
    if not chat:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        message_thread_id=get_thread_id(update),
    )


async def require_group(update: Update) -> bool:
    if is_group_chat(update):
        return True
    await reply_same_place(update, "هذا الأمر يعمل داخل المجموعة فقط.")
    return False


def init_db():
    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS groups_data (
                group_id INTEGER PRIMARY KEY,
                title TEXT,
                registered_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                username TEXT,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                registered_at TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (group_id) REFERENCES groups_data(group_id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_done (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                done_date TEXT NOT NULL,
                message TEXT NOT NULL,
                done_time TEXT NOT NULL,
                done_time_iso TEXT NOT NULL,
                PRIMARY KEY (user_id, group_id, done_date),
                FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
             CREATE TABLE IF NOT EXISTS payments (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                paid INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE
             )
            """
        )

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_group_active ON users(group_id, active)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_group_username ON users(group_id, username)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_done_group_date ON daily_done(group_id, done_date)"
        )

        conn.commit()


def mark_paid(group_id: int, user_id: int):
    now_iso = datetime.now().isoformat(timespec="seconds")

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO payments (user_id, group_id, paid, paid_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                paid = 1,
                paid_at = excluded.paid_at
            """,
            (user_id, group_id, now_iso),
        )
        conn.commit()


# async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     message = update.effective_message
#     chat = update.effective_chat

#     if not message or not chat:
#         return

#     group_id = chat.id

#     for user in message.new_chat_members:
#         ensure_group_registered(group_id, chat.title or "")

#         upsert_user(
#             group_id,
#             user.id,
#             user.username or "",
#             user.first_name or "User",
#         )

#         texts = [
#             f"🚨 انضم إلينا {mention_html(user.id, user.first_name)} رسميًا 😎🔥\n"
#             "تم إدخاله مباشرة إلى بيئة عالية الإنتاجية بدون تدريب مسبق 👀\n"
#             "هون ما في تمهيد… الشغل بيبدأ فورًا 💼\n"
#             "⚠️ تحذير: إشعاعات الإنجاز مرتفعة جدًا ☢️\n"
#             "الدفعة الأولى قاسية… وفي ناس ما استوعبت شو صار فيها 😅\n"
#             "استعد… لأن النسخة القديمة منك على وشك الاختفاء 😏",
#             f"🎯 تم رصد انضمام {mention_html(user.id, user.first_name)} للتحدي 😎🔥\n"
#             "الدخول كان سلس… لكن القادم مش رح يكون هيك 👀\n"
#             "من الآن، كل خطوة محسوبة وكل يوم عليه رقابة 🔍\n"
#             "⚠️ انتبه من إشعاعات الإنجاز ☢️\n"
#             "الدفعة الأولى تضرب بدون سابق إنذار 😅\n"
#             "وإذا نجوت منها… خلاص دخلت الجد 😏",
#             f"🔥 تم تسجيل {mention_html(user.id, user.first_name)} ضمن قائمة المقاتلين 😎\n"
#             "لا يوجد زر رجوع… ولا وضع راحة 👀\n"
#             "البرنامج يبدأ فورًا بدون تسخين 🧠\n"
#             "⚠️ إشعاعات الإنجاز في أعلى مستوياتها ☢️\n"
#             "الدفعة الأولى كفيلة تغيّر نظام حياتك 😅\n"
#             "تابع بحذر… أو لا تتابع أصلاً 😏",
#             f"🚀 دخول رسمي للمستخدم {mention_html(user.id, user.first_name)} 😎🔥\n"
#             "تم نقله إلى بيئة لا تعترف بالتأجيل 👀\n"
#             "كل يوم = اختبار جديد 💥\n"
#             "⚠️ تحذير: إشعاعات الإنجاز نشطة ☢️\n"
#             "الدفعة الأولى تضرب بقوة وما بترحم 😅\n"
#             "إذا صمدت… أنت مش طبيعي 😏",
#             f"👀 تم إدخال {mention_html(user.id, user.first_name)} إلى النظام 😎🔥\n"
#             "تم تفعيل وضع الإنجاز التلقائي بدون إذن 😅\n"
#             "الراحة أصبحت خيار غير متاح حاليًا 💼\n"
#             "⚠️ إشعاعات الإنجاز عالية جدًا ☢️\n"
#             "الدفعة الأولى ممكن تسبب إدمان إنتاج 👀\n"
#             "لا تقلق… هذا طبيعي هون 😏",
#             f"⚡ انضم {mention_html(user.id, user.first_name)} رسميًا 😎🔥\n"
#             "الدخول سهل… لكن الاستمرار هو التحدي الحقيقي 👀\n"
#             "من الآن، كل يوم فيه تقدم إجباري 📈\n"
#             "⚠️ انتبه من إشعاعات الإنجاز ☢️\n"
#             "الدفعة الأولى بتغيّر كل قواعدك 😅\n"
#             "وما رح ترجع زي قبل أبدًا 😏",
#             f"🎖️ تم ضم {mention_html(user.id, user.first_name)} للنظام 😎🔥\n"
#             "تم تفعيل وضع الضغط العالي مباشرة 👀\n"
#             "التأجيل صار ممنوع رسميًا 🚫\n"
#             "⚠️ إشعاعات الإنجاز شغالة بكامل طاقتها ☢️\n"
#             "الدفعة الأولى ما بترحم أي حدا 😅\n"
#             "استعد… لأنك داخل مرحلة جديدة 😏",
#             f"🔥 انضمام جديد: {mention_html(user.id, user.first_name)} 😎🔥\n"
#             "تم إدخاله إلى منطقة لا تعرف الكسل 👀\n"
#             "الإنجاز هون أسلوب حياة مش خيار 💼\n"
#             "⚠️ إشعاعات الإنجاز مرتفعة ☢️\n"
#             "الدفعة الأولى صدمة إيجابية قوية 😅\n"
#             "رح تفهم لاحقًا ليش 😏",
#             f"🚨 تم استقبال {mention_html(user.id, user.first_name)} في التحدي 😎🔥\n"
#             "تم تفعيل نمط الأداء العالي مباشرة 👀\n"
#             "لا يوجد وقت للتفكير… فقط تنفيذ 💥\n"
#             "⚠️ تحذير: إشعاعات الإنجاز فعالة ☢️\n"
#             "الدفعة الأولى تضرب بسرعة وقوة 😅\n"
#             "أهلاً بك في الواقع الجديد 😏",
#             f"🎯 تم إدخال {mention_html(user.id, user.first_name)} إلى ساحة الإنجاز 😎🔥\n"
#             "تم إلغاء خيار الكسل تلقائيًا 👀\n"
#             "كل يوم لازم يكون فيه تقدم واضح 📈\n"
#             "⚠️ إشعاعات الإنجاز مرتفعة جدًا ☢️\n"
#             "الدفعة الأولى كفيلة تعيد تشكيلك 😅\n"
#             "من الآن… الأمور جد 😏",
#         ]
#         text = random.choice(texts)

#         await message.reply_text(text, parse_mode=ParseMode.HTML)


def get_payment_status(group_id: int):
    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT u.user_id, u.name,
                   COALESCE(p.paid, 0) as paid
            FROM users u
            LEFT JOIN payments p
              ON u.user_id = p.user_id AND u.group_id = p.group_id
            WHERE u.group_id = ? AND u.active = 1
            ORDER BY u.name COLLATE NOCASE
            """,
            (group_id,),
        )

        return cur.fetchall()


def ensure_group_registered(group_id: int, title: str):
    now_iso = datetime.now().isoformat(timespec="seconds")
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO groups_data (group_id, title, registered_at)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET title = excluded.title
            """,
            (group_id, title or "", now_iso),
        )
        conn.commit()


def upsert_user(group_id: int, user_id: int, username: str, name: str):
    now_iso = datetime.now().isoformat(timespec="seconds")
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (user_id, group_id, username, name, active, registered_at, points)
            VALUES (?, ?, ?, ?, 1, ?, 0)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                username = excluded.username,
                name = excluded.name,
                active = 1
            """,
            (user_id, group_id, username or "", name or "User", now_iso),
        )
        conn.commit()


def get_user_active(group_id: int, user_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT active FROM users WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        row = cur.fetchone()
        return bool(row and row["active"] == 1)


def remove_user_by_username(group_id: int, username: str) -> bool:
    username = username.lstrip("@").strip()
    if not username:
        return False

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE group_id = ? AND username = ? AND active = 1
            """,
            (group_id, username),
        )
        row = cur.fetchone()
        if not row:
            return False

        user_id = row["user_id"]

        cur.execute(
            "UPDATE users SET active = 0 WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        cur.execute(
            "DELETE FROM daily_done WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        conn.commit()
        return True


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    group_id = chat.id
    ensure_group_registered(group_id, chat.title or "")
    upsert_user(group_id, user.id, user.username or "", user.first_name or "User")
    await reply_same_place(update, "✅ تم تسجيلك في هذه المجموعة.")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    group_id = chat.id

    if not get_user_active(group_id, user.id):
        await reply_same_place(update, "استخدم /register أولاً داخل هذه المجموعة.")
        return

    msg = " ".join(context.args).strip()
    if not msg:
        await reply_same_place(update, "اكتب إنجازك بعد الأمر.")
        return

    today = str(date.today())
    now = datetime.now()
    time_str = now.strftime("%I:%M %p").lstrip("0")
    now_iso = now.isoformat(timespec="seconds")

    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT 1
            FROM daily_done
            WHERE user_id = ? AND group_id = ? AND done_date = ?
            """,
            (user.id, group_id, today),
        )
        already_done = cur.fetchone() is not None

        cur.execute(
            """
            INSERT INTO daily_done (user_id, group_id, done_date, message, done_time, done_time_iso)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_id, done_date) DO UPDATE SET
                message = excluded.message,
                done_time = excluded.done_time,
                done_time_iso = excluded.done_time_iso
            """,
            (user.id, group_id, today, msg, time_str, now_iso),
        )

        if not already_done:
            cur.execute(
                "UPDATE users SET points = points + 1 WHERE user_id = ? AND group_id = ?",
                (user.id, group_id),
            )

        conn.commit()

    if already_done:
        await reply_same_place(update, f"✏️ تم تحديث إنجازك اليوم الساعة {time_str}")
    else:
        await reply_same_place(
            update, f"🔥 تم تسجيل إنجازك الساعة {time_str} (+1 نقطة)"
        )


async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id
    today = str(date.today())

    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT user_id, name
            FROM users
            WHERE group_id = ? AND active = 1
            ORDER BY name COLLATE NOCASE
            """,
            (group_id,),
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT user_id
            FROM daily_done
            WHERE group_id = ? AND done_date = ?
            """,
            (group_id, today),
        )
        done_ids = {row["user_id"] for row in cur.fetchall()}

    missing = [
        (row["user_id"], row["name"]) for row in users if row["user_id"] not in done_ids
    ]

    if not missing:
        await send_in_same_topic(update, context, "🎉 الكل سجل اليوم.")
        return

    tags = "\n".join(f"- {mention_html(uid, name)}" for uid, name in missing)
    await send_in_same_topic(update, context, f"⏰ وينكم؟\n{tags}")


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id
    today = str(date.today())

    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT user_id, name
            FROM users
            WHERE group_id = ? AND active = 1
            ORDER BY name COLLATE NOCASE
            """,
            (group_id,),
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT u.user_id, u.name, d.message, d.done_time
            FROM daily_done d
            JOIN users u
            ON u.user_id = d.user_id AND u.group_id = d.group_id
            WHERE d.group_id = ? AND d.done_date = ? AND u.active = 1
            ORDER BY d.done_time_iso ASC
            """,
            (group_id, today),
        )
        done_rows = cur.fetchall()

        done_ids = {row["user_id"] for row in done_rows}
        missing = [
            (row["user_id"], row["name"])
            for row in users
            if row["user_id"] not in done_ids
        ]

        done_text = "✅ إنجازات اليوم:\n"
        if done_rows:
            done_text += "\n".join(
                f"- {mention_html(row['user_id'], row['name'])} ({html.escape(row['done_time'])}): {html.escape(row['message'])}"
                for row in done_rows
            )
        else:
            done_text += "ما في إنجازات."

        missing_text = "\n\n❌ لم يسجلوا اليوم:\n"
        if missing:
            missing_text += "\n".join(
                f"- {mention_html(uid, name)}" for uid, name in missing
            )
        else:
            missing_text += "ولا أحد 🎉"

        cur.execute(
            "DELETE FROM daily_done WHERE group_id = ? AND done_date = ?",
            (group_id, today),
        )
        conn.commit()

    await send_in_same_topic(update, context, done_text + missing_text)


async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        return

    group_id = chat.id

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, points
            FROM users
            WHERE group_id = ? AND active = 1
            ORDER BY points DESC, name COLLATE NOCASE ASC
            """,
            (group_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await reply_same_place(update, "لا يوجد مستخدمين.")
        return

    groups = {}
    for row in rows:
        groups.setdefault(row["points"], []).append(row["name"])

    sorted_points = sorted(groups.keys(), reverse=True)

    text = "🏆 لوحة الصدارة\n\n"

    rank_titles = [
        ("🥇 المراكز الأولى", 0),
        ("🥈 المراكز الثانية", 1),
        ("🥉 المراكز الثالثة", 2),
    ]

    used_points = set()

    for title, idx in rank_titles:
        if idx >= len(sorted_points):
            continue

        pts = sorted_points[idx]
        used_points.add(pts)
        text += f"{title}\n"

        for name in groups[pts]:
            text += f"{html.escape(name)} — {pts} نقطة\n"

        text += "\n"

    others = []
    for pts in sorted_points:
        if pts in used_points:
            continue
        for name in groups[pts]:
            others.append(f"{html.escape(name)} — {pts}")

    if others:
        text += "━━━━━━━━━━━━━━\n\n📊 باقي الترتيب\n\n"
        text += "\n".join(others)

    await reply_same_place(update, text)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    if not context.args:
        await reply_same_place(update, "اكتب /remove @username")
        return

    ok = remove_user_by_username(chat.id, context.args[0])
    await reply_same_place(
        update, "✅ تم." if ok else "المستخدم غير موجود في هذه المجموعة."
    )


async def update_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    if len(context.args) != 2:
        await reply_same_place(update, "استخدم:\n/updatePoints @username 7")
        return

    username = context.args[0].lstrip("@").strip()

    try:
        new_points = int(context.args[1])
    except ValueError:
        await reply_same_place(update, "النقاط لازم تكون رقم.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE group_id = ? AND username = ? AND active = 1
            """,
            (chat.id, username),
        )
        row = cur.fetchone()

        if not row:
            await reply_same_place(update, "المستخدم غير موجود في هذه المجموعة.")
            return

        cur.execute(
            """
            UPDATE users
            SET points = ?
            WHERE user_id = ? AND group_id = ?
            """,
            (new_points, row["user_id"], chat.id),
        )
        conn.commit()

    await reply_same_place(
        update, f"✅ تم تحديث نقاط @{html.escape(username)} إلى {new_points}"
    )


async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    if len(context.args) != 2:
        await reply_same_place(update, "استخدم:\n/addPoints @username 2")
        return

    username = context.args[0].lstrip("@").strip()

    try:
        points_to_add = int(context.args[1])
    except ValueError:
        await reply_same_place(update, "القيمة لازم تكون رقم.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, points
            FROM users
            WHERE group_id = ? AND username = ? AND active = 1
            """,
            (chat.id, username),
        )
        row = cur.fetchone()

        if not row:
            await reply_same_place(update, "المستخدم غير موجود في هذه المجموعة.")
            return

        new_total = row["points"] + points_to_add

        cur.execute(
            """
            UPDATE users
            SET points = ?
            WHERE user_id = ? AND group_id = ?
            """,
            (new_total, row["user_id"], chat.id),
        )
        conn.commit()

    await reply_same_place(
        update,
        f"✅ تم إضافة {points_to_add} نقطة لـ @{html.escape(username)}\nالمجموع الجديد: {new_total}",
    )


async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE group_id = ? AND active = 1 AND points = 0
            """,
            (chat.id,),
        )
        users = cur.fetchall()

        if not users:
            await reply_same_place(update, "لا يوجد مستخدمين بنقاط 0.")
            return

        cur.execute(
            """
            UPDATE users
            SET active = 0
            WHERE group_id = ? AND active = 1 AND points = 0
            """,
            (chat.id,),
        )

        cur.execute(
            """
            DELETE FROM daily_done
            WHERE group_id = ?
              AND user_id IN (
                  SELECT user_id
                  FROM users
                  WHERE group_id = ? AND active = 0 AND points = 0
              )
            """,
            (chat.id, chat.id),
        )

        conn.commit()

    await reply_same_place(update, WARNING_MESSAGE)


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, username, name, points
            FROM users
            WHERE group_id = ? AND active = 1
            ORDER BY name COLLATE NOCASE ASC
            """,
            (chat.id,),
        )
        rows = cur.fetchall()

    if not rows:
        await reply_same_place(update, "لا يوجد مستخدمين.")
        return

    text = "👥 المستخدمين المسجلين:\n\n"
    for row in rows:
        uname = f"@{html.escape(row['username'])}" if row["username"] else "بدون يوزر"
        text += f"{html.escape(row['name'])} | {uname} | {row['points']} نقطة\n"

    await reply_same_place(update, text)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.reply_to_message:
        return

    replied_user = message.reply_to_message.from_user
    if replied_user and replied_user.is_bot:
        await message.reply_text(
            "انت فهيم شي؟ 😏 بدك انفذ تبليغ على حالي؟ بتحلم 🤣🤣🔥🤡"
        )


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return

    group_id = chat.id

    if not get_user_active(group_id, user.id):
        await reply_same_place(update, "استخدم /register أولاً.")
        return

    mark_paid(group_id, user.id)

    await update.message.reply_text("✅ شكراً لإتمام عملية الدفع، تم تسجيلك بنجاح.")


async def list_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    rows = get_payment_status(chat.id)

    if not rows:
        await reply_same_place(update, "لا يوجد مستخدمين.")
        return

    paid = []
    not_paid = []

    for row in rows:
        name = html.escape(row["name"])
        if row["paid"] == 1:
            paid.append(name)
        else:
            not_paid.append(name)

    text = "💰 حالة الدفع\n\n"

    text += "✅ دفعوا:\n"
    text += "\n".join(f"- {n}" for n in paid) if paid else "لا أحد"

    text += "\n\n❌ لم يدفعوا:\n"
    text += "\n".join(f"- {n}" for n in not_paid) if not_paid else "لا أحد 🎉"

    await reply_same_place(update, text)


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("رد على رسالة المستخدم ثم استخدم /promot")
        return

    target = update.message.reply_to_message.from_user

    username = f"@{target.username}" if target.username else target.first_name

    text = f"""
        🎖️ قرار إداري عاجل

        تمت ترقية المستخدم {username} إلى رتبة
        🚀 متعدد المسارات بإصرار فولاذي 🚀

        ومن مهام هذا المنصب النبيل:
        • دخول أكثر من مجال وكأنه فاتح DLC للحياة 😎
        • التعلّم المستمر بدون ما يطفّي حتى لما الضغط عالي 🔥
        • ملاحقة أهدافه بإصرار يخلي الباقي يعيد حساباته 👀

        وغالباً يكون صاحب هذا المنصب
        الشخص اللي لما تسأله "شو عم تعمل هالفترة؟"
        يعطيك قائمة مشاريع مو جواب 😅

        نشكر التزامك العالي،
        وشغفك اللي ما بيهدأ،
        وقدرتك على التوازن بين أكثر من طريق بنفس الوقت 👏

        ويمكنك استلام وسام "ما بعرف يوقف"
        من تحت الساعة بعد إنهاء أقرب إنجاز جديد ⏰

        ⚠️ ملاحظة: هذا المنصب رمزي وفكاهي
        لكن سرعتك بالتطور صارت مقلقة شوي 😏
        """

    await update.message.reply_text(text)


async def welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    text = """
    👋 مرحبااا بالشغوفين واهلاً فيكن 🔥😎

    🤖 أنا مساعد شغوف مهمتي:
    تسجيل إنجازاتكم اليومية 📌
    وتوثيقها لحتى نأرشف التقدم خطوة بخطوة 🚀

    ━━━━━━━━━━━━━━

    🧠 كيف تستخدموني؟

    1️⃣ أول خطوة:
    استخدم /register
    حتى تسجل حالك بالنظام ✅

    2️⃣ كل يوم:
    روح على قناة الإنجاز 📍
    وابعت إنجازك باستخدام:
    👉 /done شو اشتغلت اليوم

    📌 مثال:
    /done خلصت تصميم الصفحة الرئيسية

    3️⃣ التوثيق:
    بيكون بنهاية اليوم ⏰
    يعني لا تأجل… خلص يومك وسجّل إنجازك 🔥

    ━━━━━━━━━━━━━━

    ⚠️ تذكير بسيط:
    الالتزام اليومي هو الفرق بين شخص عم يتطور…
    وشخص بس عم يحكي 😏

    يلا خلينا نشتغل 💪🔥
    """.strip()

    await reply_same_place(update, text)


def main():
    if not TOKEN:
        raise RuntimeError("Set TOKEN environment variable")

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("alert", alert))
    app.add_handler(CommandHandler("checkout", checkout))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("points", points))
    app.add_handler(CommandHandler("updatePoints", update_points))
    app.add_handler(CommandHandler("addPoints", add_points))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("listUsers", list_users))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("paid", paid))
    app.add_handler(CommandHandler("listPay", list_pay))
    app.add_handler(CommandHandler("welcome", welcome_cmd))
    # app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))

    print("Bot is running...")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    if not WEBHOOK_URL:
        app.run_polling()
    else:
        PORT = int(os.getenv("PORT"))
        SECRET_TOKEN = os.getenv("SECRET_TOKEN")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            secret_token=SECRET_TOKEN,
            webhook_url=WEBHOOK_URL,
            drop_pending_updates=True,
            url_path="shagh-bot",
        )


if __name__ == "__main__":
    main()
