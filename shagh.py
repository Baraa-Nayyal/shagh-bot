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
ADMIN_IDS = {7861055850, 6621235954, 5956304322}
TOKEN = os.getenv("BOT_TOKEN")

pending_resets = {}  # group_id -> datetime when /reset_group was requested
RESET_CONFIRM_WINDOW_SECONDS = 60

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
    if user_id in ADMIN_IDS:
        return True

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM extra_admins WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None


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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups_data (
                group_id INTEGER PRIMARY KEY,
                title TEXT,
                registered_at TEXT NOT NULL
            )
            """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_id INTEGER PRIMARY KEY,
                paused INTEGER NOT NULL DEFAULT 0
            )
            """)

        cur.execute("""
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
            """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_done (
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            done_date TEXT NOT NULL,
            message TEXT NOT NULL,
            done_time TEXT NOT NULL,
            done_time_iso TEXT NOT NULL,
            PRIMARY KEY (user_id, group_id, activity_type, done_date)
            )
        """)

        cur.execute("""
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
            """)

        cur.execute("""
             CREATE TABLE IF NOT EXISTS payments (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                paid INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE
             )
            """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
             )
            """)

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_group_active ON users(group_id, active)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_group_username ON users(group_id, username)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_done_group_date ON daily_done(group_id, done_date)"
        )

        cur.execute("""
         CREATE TABLE IF NOT EXISTS projects (
            project_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            link TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
         CREATE TABLE IF NOT EXISTS extra_admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL
            )
        """)

        cur.execute("""
         CREATE TABLE IF NOT EXISTS teams (
            team_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user1_id INTEGER NOT NULL,
            user1_name TEXT NOT NULL,
            user2_id INTEGER NOT NULL,
            user2_name TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)

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


async def resolve_user_arg(context: ContextTypes.DEFAULT_TYPE, group_id: int, arg: str):
    arg = arg.strip()

    if arg.lstrip("-").isdigit():
        user_id = int(arg)
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM users WHERE user_id = ? AND group_id = ?",
                (user_id, group_id),
            )
            row = cur.fetchone()
        return user_id, (row["name"] if row else str(user_id))

    if arg.startswith("@"):
        username = arg[1:]
        try:
            resolved = await context.bot.get_chat(f"@{username}")
            return resolved.id, (resolved.full_name or resolved.first_name or username)
        except Exception:
            pass

        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
             """
                SELECT user_id, name
                FROM users
                WHERE group_id = ? AND LOWER(username) = LOWER(?)
             """,
                (group_id, username),
            )
            row = cur.fetchone()
        if row:
            return row["user_id"], row["name"]

    return None, None

async def pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id

    if len(context.args) < 2:
        await reply_same_place(update, "استخدم: /pair @user1 @user2")
        return

    uid1, name1 = await resolve_user_arg(context, group_id, context.args[0])
    uid2, name2 = await resolve_user_arg(context, group_id, context.args[1])

    if uid1 is None or uid2 is None:
        await reply_same_place(update, "ما قدرت ألقى أحد المستخدمين.")
        return

    if uid1 == uid2:
        await reply_same_place(update, "لازم يكونوا مستخدمين مختلفين.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT team_id FROM teams
            WHERE group_id = ? AND (user1_id IN (?, ?) OR user2_id IN (?, ?))
            """,
            (group_id, uid1, uid2, uid1, uid2),
        )
        if cur.fetchone():
            await reply_same_place(update, "أحد المستخدمين موجود بفريق بالفعل.")
            return

        now_iso = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO teams (group_id, user1_id, user1_name, user2_id, user2_name, points, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (group_id, uid1, name1, uid2, name2, now_iso),
        )
        conn.commit()

    await reply_same_place(
        update,
        f"تم تكوين فريق: {mention_html(uid1, name1)} & {mention_html(uid2, name2)} 🤝",
    )
async def unpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id

    if len(context.args) < 1:
        await reply_same_place(update, "استخدام: /unpair @user1")
        return

    uid1, name1 = await resolve_user_arg(context, group_id, context.args[0])

    if uid1 is None:
        await reply_same_place(update, "ما قدرت القى المستخدم.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        
        # البحث عن الفريق الذي ينتمي إليه المستخدم
        cur.execute("""
            SELECT team_id FROM teams
            WHERE group_id = ? AND (user1_id = ? OR user2_id = ?)
        """, (group_id, uid1, uid1))
        
        row = cur.fetchone()
        
        if not row:
            await reply_same_place(update, "المستخدم ليس في أي فريق حالياً.")
            return

        # حذف الفريق وفك الربط نهائياً
        cur.execute("DELETE FROM teams WHERE team_id = ?", (row["team_id"],))
        conn.commit()

    await reply_same_place(update, f"تم فك الفريق وإلغاء الربط للمستخدم {mention_html(uid1, name1)} بنجاح 💔.")
    
def add_team_points(conn, group_id: int, user_id: int, pts: int):
    cur = conn.cursor()
    cur.execute(
        "SELECT team_id FROM teams WHERE group_id = ? AND (user1_id = ? OR user2_id = ?)",
        (group_id, user_id, user_id),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE teams SET points = points + ? WHERE team_id = ?",
            (pts, row["team_id"]),
        )

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


def is_paused(group_id: int) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT paused FROM group_settings WHERE group_id = ?",
            (group_id,),
        )
        row = cur.fetchone()
        return bool(row["paused"]) if row else False


def set_paused(group_id: int, paused: bool):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO group_settings (group_id, paused)
            VALUES (?, ?)
            ON CONFLICT(group_id) DO UPDATE SET paused = excluded.paused
            """,
            (group_id, int(paused)),
        )
        conn.commit()


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

    if is_paused(group_id):
        await reply_same_place(update, "تسجيل الإنجازات متوقف لهذه الدفعة ⏸️")
        return

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

    done_replies = [
    "ولك شو هيدااا! 😭 مين سمحلك تخلّص قبل الموعد؟!",
    "يدلييي، خلصنا المهمة! حدا يرنّ عالتلفزيون، عنا خبر عييجييل 😂📢",
    "يا عيني عالآدمي، قال DONE قال! نحنا بعدنا عم نفكير نبليش 💀",
    "لك هيدا أنجز ولا عم يجرّب يستفزّنا بنشاطه؟ 😭",
    "خلصت؟ طيّب استنّى شوي خلّينا نستوعب الصدمة 😂",
    "ولك شيلوا كلمة «بعدين» من قاموس هالإنسان، ما عاد إلها لزوم 😭👏",
    "يا لطيف! في حدن عم يطبيق اللي بيقول إنو رح يعملو ؟ شو هالتطوّر هيدا",
    "هلأ منصديق إنك كنت عم تشتغيل، مش بس فاتح اللابتوب للديكور 💻😭",
    "تمّ الإنجاز يا جماعة، يرجى عدم الاقتراب من البطل لنستوعيب العظمة",
    "ولك خلصت المهمة قبل ما ازهق من السؤال عنها؟ هيدي جديدة 😂",
    "نقطة إلك ووسام صبر إلنا وكاسة متّي عحسابنا 🧉",
    "يا عيني! لو التسويف بيحكي، كان هللأ عم يبكي شو عمولت انتا ياخطير💀",
    "مش معقول يكونوا الأدمن مهددينك ؟ 😂",
    "خلصت المهمة ؟ إي هيك، مش كل شي بدّو اجتماع لجنة تحضيرية 😭",
    "ولك هيدا DONE ولا إعلان استقلال عن الكسل؟",
    "يدلي عالإنجاز! بس ما بدي شوفك بعد شوي عم تقول ما عمولت شي اليوم 😌",
    "ولك شو هالسرعة هيدي؟ عامل اشتراك Premium بالإنجازات؟ 💀",
    "DONE وصلت لعندنا ومعها ريحة طالب قرّر ياخد مستقبله بجدّية 😭",
    "هيدا الإنجاز بدّو زفّة، بس الميزانية حالياً نقطة وحدة 😂🎺",
    "يا حرام التسويف، كان مفكّر إنو بعد عندو فرصة معك 💔",
    "خلصت المهمة؟ روح اشرب مي، مشان ما ينشف مخّك من كتر الإنتاجية 😭",
    "شغوف شهد، شغوف سجّل، وشغوف عم يتساءل إذا انتا نفس الشخص اللي كان بيأجّل مبارح",
    "يا عيني عالإنجازات اللي بتجي بلا ضجّي بس بتعمل فرق كبير",
    "ولك كل هيدا من دون ما نبعتلك «وين وصلت؟» تطوّر تاريخي 😭",
    "المهمة خلصت بنجاح، أمّا الاحتفال فمعلّق لحدّيت ما نخليص باقي المهام 💀",
    "خلصت؟ لك روح خبير رفقاتك، يمكن العدوى الإيجابية تنتشر شوي 🤏🏻",
    "يا سلام! هيكي منحبّ نشوف ال DONE مش DONE كلام بس 😭💙",
    "لك دخيلك خلّصنا، بعد في مهام نلحقها قبل ما يصير بكرا 😭",
    "ولك لك لك، روّق علينا شوي، مش هيك الإنجازات دفعة وحدة 😂",
    "يا خيي ريّحنا شوي، عم تخلّينا نحسّ إنو نحنا اللي مقصّرين 😂"
]

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

        add_team_points(conn, group_id, user.id, 1)

        conn.commit()

        if already_done:
            await reply_same_place(update, "تم تحديث الإنجاز بنجاح ✅")
        else:
            await reply_same_place(update, random.choice(done_replies))


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

    target = None

    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user

    elif context.args:
        username = context.args[0].replace("@", "")

        members = await update.effective_chat.get_member_by_username(username)
        if members:
            target = members.user

    if not target:
        await update.message.reply_text(
            "رد على رسالة المستخدم أو استخدم /promote @username"
        )
        return

    username = f"@{target.username}" if target.username else target.first_name

    text = f"""
     إشادة برمجية مستحقة

    يسرّنا منح المستخدم {username} لقب
    💻 سيد الأكواد وحلّال المسائل 💻

    وذلك تقديراً لـ:
    • مشاركته المميزة في المسابقة البرمجية 🚀
    • تحليله الذكي للمسائل وإيجاد الحلول بكفاءة 🧠
    • كتابته أكواد نظيفة ومنظمة تحت ضغط الوقت ⚡

    هذا اللقب يليق بالشخص الذي يرى كل مسألة
    كتحدٍّ يستحق أن يُهزم، وكل خطأ
    خطوة جديدة نحو الحل الصحيح 😎

    نفتخر بسرعة تفكيرك،
    وإبداعك في البرمجة،
    وروح المنافسة التي أظهرتها طوال المسابقة 👏

    استمر في صقل مهاراتك،
    فكل مسألة تحلها اليوم
    تقربك من إنجازات أكبر غداً 🌟

    ⚠️ ملاحظة: هذا اللقب رمزي،
    لكن مستواك البرمجي يستحق كل التقدير. 🔥
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


ACTIVITY_CONFIG = {
    "study": {
        "points": 1,
        "label": "دراسة",
        "no_message": "اكتب وش درست بعد الأمر.",
        "already_done": "تم تحديث نشاط الدراسة بنجاح ✅",
        "replies": [
        "ولك فتحت الكتاب بإرادتك؟ 😭 مين انتا وشو عمولت بالطالب الأصلي؟",
        "يدلي عالدراسة! صار فينا نطمّن عالجيل الصاعد شوي 😂📚",
        "يا لطيف، الطالب عم يدرس قبل ما ينزنيق بالامتحان هيدي بدها توثيق تاريخي 💀",
        "شو هيدا؟ جلسة دراسة بلا مفاوضات مع النفس؟ 😭",
        "ولك الكتاب ما عضّك صح؟ خبرنا كيف كانت التجربة 😂",
        "يا عاايني اليوم درست وبكرا بتفهم، وبعده بتصير تشرح لغيراك وتقولن سهلة كتير 😂",
        "درست؟ إي هيك، خلّي مخّك يشتغل شوبينا ولاو بدل ما يضلّ فاتح عشرين تبويب بلا فايدة 😭",
        "ياعيني عليك ، المعلومات فاتت مخك بس انت مأكد انها بقيت فيو ؟",
        "مين الي قلك مين ؟ مين الي قلك مين انو الدراسة بدها مزاج ؟ لو ناطرين المزاج كان بعمرنا ما عملنا شي💀",
        "نقطة دراسة جديدي شغوف ما بيطلب منك تصير اينشتاين بيوم، بس ضلّك عم تتقدّم 💙",
        "ولك شو هالتركيز هيدا ؟ حتى إشعارات الموبايل صارت تحسّ بالذنب 📵😭",
        "يا عيني عالطالب اللي بيحارب النعس والمعادلات بنفس الوقت 😭",
        "شغوف بيحب يخبرك : ما بدنا بطولات يوم واحد، بدنا طالب ما بيختفي بعد أول محاضرة 😂",
        "هيدا الحكي! شوي شوي بتتراكم المعلومات، مو متل الغسيل اللي بيتراكم فجأة 😭😔",
        "لو درست مدة قليلة ممتاز، في ناس صارلن نص ساعة عم يختاروا أي موسيقى يدرسوا عليها 😂",
        "اليوم انتا والكتاب أصحاب بكرا منشوف إذا بتضلّ العلاقة مستئرّة وقت الامتحان 😭",
        "ولك لا تدرس لتخلّص وبس، ادرس لتفهم… إي بعرف، صارت حكمة فجأة 😌",
        "يا عيني، عم تدرس حتى لو ما عندك نفس. هيدي العادة اللي بتعمل فرق عنجد 💙",
        "الدراسة ماشية، والنقاط عم تزيد، وبقي بس نخفّف شوي من علاقتنا السامّة مع التأجيل 💀",
        "ولك استمرّ! بدنا نشوفك عم تتطوّر، مش بس عم تجمع نقاط وتنافس حالك عالترتيب 😂🏆",
        "ولك عَ مهلك علينا، شو هالهمّة 😭",
        "لك دخيلك خلّصنا، بعد في مهام نلحقها قبل ما يصير بكرا 😭"
    ],
    },
    "meeting": {
        "points": 3,
        "label": "اجتماع",
        "no_message": "اكتب تفاصيل الاجتماع بعد الأمر.",
        "already_done": "تم تحديث الاجتماع بنجاح ✅",
        "replies": [
        "ولك حضرت الاجتماع؟ 😭 مين انتاا وشو عملت باللي كان يقول «بشوف التسجيل بعدين»؟",
        "يا لطيف! الكل فات عالاجتماع؟ شكله اليوم في شي تاريخي رح يصير 😂",
        "الاجتماع خلص؟ ولك بعدني ما لحقت قول «مسا الخير» 😂",
        "يا ويلي حدن سأل سؤال بالاجتماع؟ سجّلوا التاريخ، في تفاعل بالدفعة 😭",
        "ولك هيدا اجتماع ولا حلقة من مسلسل؟",
        "حضرت الاجتماع وسكّرت؟ طيب خبرنا، فهمت شي ولا بدنا نعيد الحلقة من الأول؟",
        "يا عيني عالاجتماع المرتّب، ناقصنا بس نوزّع منائيش بعده 🥙",
        "يا دلي عالحضور، حتى الإنترنت قرّر يتعاون معنا اليوم 🤝😂",
        "يا لطيف ! حدن دوّن الملاحظات؟ دخيلكن ابعتولنا ياها قبل ما تصير آثار تاريخية 💀📜",
        "ولك هيدا الحماس بالاجتماع بدّو متابعة، ما بدنا الحماس يفلّ مع زرّ ال Leave 😭",
        "وين كنت ؟ الكوتش صار بالموضوع التالت وانتا بعدك عم تجرب تفوت عاللينك 😭",
        "هيدا انتا الي فتت وفلّيت ورجعت فتت عم تحضر الاجتماع ولا عم تعمول زيارة تفقدية؟ 😂",
        "يا عيني عالطالب اللي دوّن الملاحظات، هلّأ صار المرجع الرسمي للدفعة 😭📜"
    ],
    },
    "project": {
        "points": 5,
        "label": "مشروع",
        "no_message": "اكتب تفاصيل المشروع بعد الأمر.",
        "already_done": "تم تحديث المشروع بنجاح ✅",
        "replies": [
        "ولك شو هيدااا! 😭 المشروع خلص؟ حدا يقرصني، شكلي عم بحلم!",
        "يا لطيف يا لطيف! خمس نقاط دفعة وحدة؟ شغوف بدّو يفلّس هيك 😂💸",
        "ولك دخيل هالإبداع! شو هالمشروع اللي طالع من تحت إيديك؟ 👀",
        "يا عيني عالشغل الحلو ، بلّش بفكرة وخلص بمشروع! نحنا بعدنا عم نختار اسم الفولدر 💀",
        "ولك المشروع اشتغل من أوّل مرّة؟ أكيد في شي مخبّى 😭",
        "يا ويلي عال bugs، شكلهن اليوم رح يناموا زعلانين 😂🐛",
        "خلص المشروع؟ طب شو هيدا، ما بدّك تعملنا شي درامي وتحكيلنا عن معاناتك مع الكود؟ 😭",
        "دخيلك خبرنا، كم مرّة كتبت الكود ومسحتو ورجعت كتبتو؟ بدنا الحقيقة 😂",
        "صار عنا مشروع جديد يدلي حدا يتّصل بالإعلام، شغوف عم يخرّج مبرمجين 😭📢",
        "المشروع اشتغل؟ إي خلص اليوم مننام وضميرنا مرتاح، بكرا منكتشف ال bugs الجديدة 💀",
        "يا لطيف! خمس نقاط عالحساب، ناقصنا بس نحطّلن فايدة بالبنك 😂",
        "شو هالهمّة هيدي ؟ كنت مفكّرك عم تتفرّج عالكود، طلعت عم تبني المشروع فعلا 😭",
        "رفعت المشروع عال GitHub؟ يعني صار عندك دليل إنك ما كنت عم تكبّس عالفاضي",
        "خلص المشروع؟ إي هلقأ فيك تقول «كان سهل» بعد ما قضيت عليه تلات ليالي 😂",
        "والله وصرت تعمول مشاريع؟ طيب من هلأ إذا خرب شي بالبوت منعرف مين ننادي 😂",
        "يا ويلي! المشروع خلص قبل ما يخلص صبرنا نحنا 😭",
        "هيدا الشغل ولا بلاش! بس بالله عليك ما تسمّيه project2-final-final 😂",
        "المشروع صار حقيقة، مش بس فكرة مكتوبة بالنوتس من شهر آب 💀",
        "ولك هال bugs وين راحوا؟ لا يكونوا عم يخطّطوا لانتقام جماعي 😭🐛",
        "يا خيي شو هالإنجاز! صرت تخلّص شغلات قبل ما يصير عندك ضمير يعذبك؟",
        "لااااا، مش معقول! هيدا خلّص المهمّة قبل ما الأدمن يبعثله تهديد مبطّن 😭",
        "يا ويلي خلصت المشروع طيب مين رح يضلّ يعطينا أعذار هلأ؟ 😂",
        "خيي إذا بتضلّ هيك، رح نبلّش نشكّ إنك عامل بوت ينجز عنك 🤨😭",
        "يا خيي، المشروع إلك، والتعب إلك، وال bug اللي رح يطلع بعد التسليم إلك كمان 😂",
        "يا لطيف الطالب أنجز! حدا يخبّي الخبر عن رفقاتو ما بدنا نعمول أزمة ثقة بالدفعة 😂"
    ],
    },
}


async def log_activity(
    update: Update, context: ContextTypes.DEFAULT_TYPE, activity_type: str
):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    group_id = chat.id

    if is_paused(group_id):
        await reply_same_place(update, "تسجيل الإنجازات متوقف لهذه الدفعة ⏸️")
        return

    config = ACTIVITY_CONFIG[activity_type]

    if not get_user_active(group_id, user.id):
        await reply_same_place(update, "استخدم /register أولاً داخل هذه المجموعة.")
        return

    msg = " ".join(context.args).strip()
    if not msg:
        await reply_same_place(update, config["no_message"])
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
            FROM activity_done
            WHERE user_id = ? AND group_id = ? AND activity_type = ? AND done_date = ?
            """,
            (user.id, group_id, activity_type, today),
        )

        already_done = cur.fetchone() is not None

        cur.execute(
            """
            INSERT INTO activity_done (user_id, group_id, activity_type, done_date, message, done_time, done_time_iso)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_id, activity_type, done_date) DO UPDATE SET
                message = excluded.message,
                done_time = excluded.done_time,
                done_time_iso = excluded.done_time_iso
            """,
            (user.id, group_id, activity_type, today, msg, time_str, now_iso),
        )

        if not already_done:
            cur.execute(
                "UPDATE users SET points = points + ? WHERE user_id = ? AND group_id = ?",
                (config["points"], user.id, group_id),
            )
            add_team_points(conn, group_id, user.id, config["points"])

        conn.commit()

    if already_done:
        await reply_same_place(update, config["already_done"])
    else:
        await reply_same_place(update, random.choice(config["replies"]))


async def study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_activity(update, context, "study")


async def meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_activity(update, context, "meeting")


async def project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await log_activity(update, context, "project")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id
    currently_paused = is_paused(group_id)
    set_paused(group_id, not currently_paused)

    if currently_paused:
        await reply_same_place(update, "تم استئناف تسجيل الإنجازات ✅")
    else:
        await reply_same_place(update, "تم إيقاف تسجيل الإنجازات ⏸️")


async def reset_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id
    pending_resets[group_id] = datetime.now()

    await reply_same_place(
        update,
        "⚠️ هذا سيحذف جميع بيانات المجموعة نهائيًا (المستخدمين، النقاط، سجل الإنجازات، الإعدادات).\n"
        f"للتأكيد اكتب /confirm_reset خلال {RESET_CONFIRM_WINDOW_SECONDS} ثانية.",
    )


async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id
    requested_at = pending_resets.get(group_id)

    if (
        not requested_at
        or (datetime.now() - requested_at).total_seconds()
        > RESET_CONFIRM_WINDOW_SECONDS
    ):
        await reply_same_place(
            update, "لا يوجد طلب حذف نشط. استخدم /reset_group أولاً."
        )
        return

    del pending_resets[group_id]

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE group_id = ?", (group_id,))
        cur.execute("DELETE FROM daily_done WHERE group_id = ?", (group_id,))
        cur.execute("DELETE FROM activity_done WHERE group_id = ?", (group_id,))
        cur.execute("DELETE FROM group_settings WHERE group_id = ?", (group_id,))
        cur.execute("DELETE FROM groups_data WHERE group_id = ?", (group_id,))
        conn.commit()

    await reply_same_place(update, "تم حذف جميع بيانات المجموعة بنجاح 🗑️")


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    # Only works in DM with the bot now, not inside a group test test test
    if chat.type != "private":
        await reply_same_place(update, "استخدم /review بالخاص مع البوت وليس داخل المجموعة.")
        return

    if len(context.args) < 2:
        await reply_same_place(
            update,
            "استخدم: /review group_id ملاحظتك\nمثال:\n/review -1001234567890 هذه ملاحظتي وتجربتي",
        )
        return

    group_arg = context.args[0]
    if not group_arg.lstrip("-").isdigit():
        await reply_same_place(update, "رقم المجموعة غير صحيح.")
        return

    group_id = int(group_arg)
    msg = " ".join(context.args[1:]).strip()

    if not msg:
        await reply_same_place(update, "اكتب ملاحظتك بعد رقم المجموعة.")
        return

    # Confirm the user is actually registered in that group before accepting the review
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM users WHERE user_id = ? AND group_id = ? AND active = 1",
            (user.id, group_id),
        )
        row = cur.fetchone()

    if not row:
        await reply_same_place(update, "ما أنت مسجل بهذه المجموعة.")
        return

    name = row["name"]
    now_iso = datetime.now().isoformat(timespec="seconds")

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO reviews (user_id, group_id, name, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user.id, group_id, name, msg, now_iso),
        )
        conn.commit()

    await reply_same_place(update, "تم استلام ملاحظتك، شكرًا لك 🙏")

async def show_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    chat = update.effective_chat
    if not chat:
        return

    group_id = chat.id

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT message FROM reviews WHERE group_id = ? ORDER BY created_at ASC",
            (group_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await reply_same_place(update, "📭 ما في ملاحظات لهذه المجموعة بعد.")
        return

    text = "📝 آراء وملاحظات المجموعة\n━━━━━━━━━━━━━━\n\n"
    for i, row in enumerate(rows, start=1):
        text += f"🔸 {i}. {html.escape(row['message'])}\n\n"

    await send_in_same_topic(update, context, text)



async def reveal_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, name, message, created_at
            FROM reviews
            WHERE group_id = ?
            ORDER BY created_at ASC
            """,
            (group_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await reply_same_place(update, "📭 ما في ملاحظات لهذه المجموعة بعد.")
        return

    text = "🔍 الملاحظات (مع الأسماء) — للأدمن فقط\n━━━━━━━━━━━━━━\n\n"
    for i, row in enumerate(rows, start=1):
        text += f"{i}. {mention_html(row['user_id'], row['name'])}\n{html.escape(row['message'])}\n\n"

    await send_in_same_topic(update, context, text)

async def clear_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_group(update):
            return

        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat or not is_admin(user.id):
            await reply_same_place(update, "للأدمن فقط.")
            return

        group_id = chat.id

        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM reviews WHERE group_id = ?", (group_id,))
            deleted_count = cur.rowcount
            conn.commit()

        if deleted_count:
            await reply_same_place(update, f"تم حذف {deleted_count} ملاحظة 🗑️")
        else:
            await reply_same_place(update, "ما في ملاحظات لحذفها.")

async def project_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if not user or not chat or not message or not message.text:
        return

    group_id = chat.id

    if not get_user_active(group_id, user.id):
        await reply_same_place(update, "استخدم /register أولاً داخل هذه المجموعة.")
        return

    # Strip the command itself, keep everything after it (including newlines)
    raw = message.text.split(None, 1)
    body = raw[1] if len(raw) > 1 else ""

    lines = [line.strip() for line in body.splitlines() if line.strip()]

    if not lines:
        await reply_same_place(
            update,
            "أرسل كل مشروع بسطر منفصل، رابط ثم وصف، مثال:\n"
            "/project_link\n"
            "https://github.com/me/app1 وصف المشروع الأول\n"
            "https://github.com/me/app2 وصف المشروع الثاني",
        )
        return

    now_iso = datetime.now().isoformat(timespec="seconds")
    name = user.full_name or user.first_name or "مستخدم"

    rows_to_insert = []
    skipped = 0

    for line in lines:
        parts = line.split(None, 1)
        if len(parts) < 2:
            skipped += 1
            continue
        link, description = parts[0], parts[1].strip()
        if not description:
            skipped += 1
            continue
        rows_to_insert.append((user.id, group_id, name, link, description, now_iso))

    if not rows_to_insert:
        await reply_same_place(update, "ما قدرت أفهم أي سطر، تأكد من كتابة الرابط ثم الوصف بكل سطر.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO projects (user_id, group_id, name, link, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        conn.commit()

    reply = f"تم استلام {len(rows_to_insert)} مشروع بنجاح ✅"
    if skipped:
        reply += f"\n(تم تجاهل {skipped} سطر لعدم وضوح الرابط أو الوصف)"

    await reply_same_place(update, reply)

async def show_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT user_id, name, link, description, created_at
            FROM projects
            WHERE group_id = ?
            ORDER BY name COLLATE NOCASE, created_at ASC
            """,
            (group_id,),
        )
        rows = cur.fetchall()

    if not rows:
        await reply_same_place(update, "📭 ما في مشاريع مسجلة لهذه المجموعة بعد.")
        return

    grouped = {}
    order = []
    for row in rows:
        key = row["user_id"]
        if key not in grouped:
            grouped[key] = {"name": row["name"], "items": []}
            order.append(key)
        grouped[key]["items"].append((row["link"], row["description"]))

    text = "💻 مشاريع الأعضاء\n━━━━━━━━━━━━━━\n\n"
    for uid in order:
        entry = grouped[uid]
        text += f"👤 {mention_html(uid, entry['name'])}\n"
        for link, description in entry["items"]:
            text += f"🔗 {html.escape(link)}\n📄 {html.escape(description)}\n\n"
        text += "\n"

    await send_in_same_topic(update, context, text)

async def clear_user_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    group_id = chat.id

    if not context.args:
        await reply_same_place(update, "استخدم: /clear_user_projects @username أو user_id")
        return

    target = context.args[0].strip()
    target_user_id = None
    target_name = None

    if target.lstrip("-").isdigit():
        target_user_id = int(target)
    elif target.startswith("@"):
        username = target[1:]
        try:
            resolved = await context.bot.get_chat(f"@{username}")
            target_user_id = resolved.id
        except Exception:
            target_user_id = None

        # Fallback: match by stored name if Telegram couldn't resolve the username
        if target_user_id is None:
            with db_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT DISTINCT user_id, name FROM projects
                    WHERE group_id = ? AND name LIKE ?
                    """,
                    (group_id, f"%{username}%"),
                )
                match = cur.fetchone()
            if match:
                target_user_id = match["user_id"]
                target_name = match["name"]
    else:
        await reply_same_place(update, "صيغة غير صحيحة. استخدم @username أو user_id.")
        return

    if target_user_id is None:
        await reply_same_place(update, "ما قدرت ألقى هذا المستخدم.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM projects WHERE group_id = ? AND user_id = ?",
            (group_id, target_user_id),
        )
        deleted_count = cur.rowcount
        conn.commit()

    if deleted_count:
        who = target_name or f"user_id {target_user_id}"
        await reply_same_place(update, f"تم حذف {deleted_count} مشروع لـ {who} 🗑️")
    else:
        await reply_same_place(update, "ما في مشاريع مسجلة لهذا المستخدم.")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    if not context.args or not context.args[0].isdigit():
        await reply_same_place(update, "استخدم: /add_admin user_id")
        return

    target_id = int(context.args[0])

    if is_admin(target_id):
        await reply_same_place(update, "هذا المستخدم أدمن بالفعل.")
        return

    now_iso = datetime.now().isoformat(timespec="seconds")

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO extra_admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
            (target_id, user.id, now_iso),
        )
        conn.commit()

    await reply_same_place(update, f"تمت إضافة {target_id} كأدمن ✅")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_same_place(update, "للأدمن فقط.")
        return

    if not context.args or not context.args[0].isdigit():
        await reply_same_place(update, "استخدم: /remove_admin user_id")
        return

    target_id = int(context.args[0])

    if target_id in ADMIN_IDS:
        await reply_same_place(update, "ما تقدر تحذف أدمن أساسي.")
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM extra_admins WHERE user_id = ?", (target_id,))
        deleted_count = cur.rowcount
        conn.commit()

    if deleted_count:
        await reply_same_place(update, f"تمت إزالة {target_id} من الأدمن ✅")
    else:
        await reply_same_place(update, "هذا المستخدم مو أدمن إضافي.")

async def checkout_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    chat = update.effective_chat
    if not chat:
        return

    group_id = chat.id
    detailed = bool(context.args) and context.args[0] == "1"

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT team_id, user1_id, user1_name, user2_id, user2_name, points
            FROM teams
            WHERE group_id = ?
            ORDER BY points DESC
            """,
            (group_id,),
        )
        teams = cur.fetchall()

        user_points = {}
        if detailed and teams:
            ids = {t["user1_id"] for t in teams} | {t["user2_id"] for t in teams}
            placeholders = ",".join("?" for _ in ids)
            cur.execute(
                f"SELECT user_id, points FROM users WHERE group_id = ? AND user_id IN ({placeholders})",
                (group_id, *ids),
            )
            user_points = {row["user_id"]: row["points"] for row in cur.fetchall()}

    if not teams:
        await reply_same_place(update, "ما في فرق مكوّنة بهذه المجموعة بعد.")
        return

    text = "👥 نقاط الفرق\n━━━━━━━━━━━━━━\n\n"
    for i, t in enumerate(teams, start=1):
        text += (
            f"{i}. {mention_html(t['user1_id'], t['user1_name'])} & "
            f"{mention_html(t['user2_id'], t['user2_name'])} — {t['points']} نقطة فريق\n"
        )
        if detailed:
            p1 = user_points.get(t["user1_id"], 0)
            p2 = user_points.get(t["user2_id"], 0)
            text += f"    • {html.escape(t['user1_name'])}: {p1} نقطة فردية\n"
            text += f"    • {html.escape(t['user2_name'])}: {p2} نقطة فردية\n"
        text += "\n"

    await send_in_same_topic(update, context, text)

  #----------#non baraa code:-----------#
async def add_team_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_group(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_admin(user.id):
        await reply_same_place(update, ".للأدمن فقط")
        return

    # التأكد من إدخال يوزرين مع عدد النقاط
    if len(context.args) != 3:
        await reply_same_place(update, "استخدم:\n/addteampoints @username1 @username2 5")
        return

    u1 = context.args[0].lstrip("@").strip()
    u2 = context.args[1].lstrip("@").strip()

    try:
        pts = int(context.args[2])
    except ValueError:
        await reply_same_place(update, ".القيمة لازم تكون رقم")
        return

    with db_conn() as conn:
        cur = conn.cursor()

        # جلب بيانات العضو الأول
        cur.execute("SELECT user_id FROM users WHERE group_id = ? AND username = ? AND active = 1", (chat.id, u1))
        r1 = cur.fetchone()

        # جلب بيانات العضو الثاني
        cur.execute("SELECT user_id FROM users WHERE group_id = ? AND username = ? AND active = 1", (chat.id, u2))
        r2 = cur.fetchone()

        if not r1:
            await reply_same_place(update, f".المستخدم @{u1} غير موجود في هذه المجموعة")
            return
        if not r2:
            await reply_same_place(update, f".المستخدم @{u2} غير موجود في هذه المجموعة")
            return

        uid1 = r1["user_id"]
        uid2 = r2["user_id"]

        # التحقق من أن العضوين يشكلان ثنائي في جدول teams
        cur.execute("""
            SELECT team_id FROM teams 
            WHERE group_id = ? AND ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?))
        """, (chat.id, uid1, uid2, uid2, uid1))
        t_row = cur.fetchone()

        if not t_row:
            await reply_same_place(update, f".المستخدمان @{u1} و @{u2} ليسا في فريق واحد")
            return

        t_id = t_row["team_id"]

        # 1. إضافة النقاط لرصيد الفريق
        cur.execute("UPDATE teams SET points = points + ? WHERE team_id = ?", (pts, t_id))

        # 2. إضافة النقاط لكل شخص في جدول users
        cur.execute("UPDATE users SET points = points + ? WHERE group_id = ? AND user_id = ?", (pts, chat.id, uid1))
        cur.execute("UPDATE users SET points = points + ? WHERE group_id = ? AND user_id = ?", (pts, chat.id, uid2))

        conn.commit()

        p1_mention = mention_html(uid1, u1)
        p2_mention = mention_html(uid2, u2)

        await reply_same_place(update, f"🟢 تم إضافة {pts} نقطة للفريق ({p1_mention} و {p2_mention}) بنجاح ✨")

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
    app.add_handler(CommandHandler("addteampoints", add_team_points_cmd))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("listUsers", list_users))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("paid", paid))
    app.add_handler(CommandHandler("listPay", list_pay))
    app.add_handler(CommandHandler("welcome", welcome_cmd))
    app.add_handler(CommandHandler("study", study))
    app.add_handler(CommandHandler("meeting", meeting))
    app.add_handler(CommandHandler("project", project))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("reset_group", reset_group))
    app.add_handler(CommandHandler("confirm_reset", confirm_reset))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("show_reviews", show_reviews))
    app.add_handler(CommandHandler("reveal_reviews", reveal_reviews))   
    app.add_handler(CommandHandler("clear_reviews", clear_reviews))
    app.add_handler(CommandHandler("project_link", project_link))
    app.add_handler(CommandHandler("show_projects", show_projects))
    app.add_handler(CommandHandler("clear_user_projects", clear_user_projects))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("pair", pair))
    app.add_handler(CommandHandler("checkout_teams", checkout_teams))
    app.add_handler(CommandHandler("unpair", unpair))
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
