import os, asyncio, datetime, sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup

# Данные из настроек
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect('users.db') as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetchone: return cur.fetchone()
        conn.commit()

db_query('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, expire TEXT, is_owner INTEGER DEFAULT 0)''')

# --- КРАСИВЫЙ ИНТЕРФЕЙС ---
def main_kb(user_id, balance, expire_date):
    # Проверка подписки для текста
    sub_status = "❌ Не активна"
    if expire_date:
        try:
            dt = datetime.datetime.fromisoformat(expire_date)
            if dt > datetime.datetime.now():
                sub_status = f"✅ До {dt.strftime('%d.%m.%Y')}"
        except: pass

    kb =,, # ЗАМЕНИ НА СВОЙ ТГ
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb), sub_status

# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    res = db_query("SELECT balance, expire, is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not res:
        owners = db_query("SELECT COUNT(*) FROM users WHERE is_owner = 1", fetchone=True)[0]
        is_owner = 1 if owners == 0 else 0
        db_query("INSERT INTO users (id, balance, is_owner) VALUES (?, 0, ?)", (message.from_user.id, is_owner))
        balance, expire, owner_status = 0, None, is_owner
    else:
        balance, expire, owner_status = res

    kb, sub_text = main_kb(message.from_user.id, balance, expire)
    
    profile_text = (
        f"<b>👋 Добро пожаловать в Links Bypass!</b>\n\n"
        f"👤 <b>Ваш профиль:</b>\n"
        f"├ 🆔 <code>{message.from_user.id}</code>\n"
        f"├ 💰 Баланс: <b>{balance} ⭐</b>\n"
        f"└ 👑 Статус: <b>{sub_text}</b>\n\n"
        f"<i>Пришлите ссылку, чтобы начать обход!</i>"
    )
    if owner_status == 1: profile_text += "\n\n🛠 <b>Вы — Создатель</b>"
    
    await message.answer(profile_text, reply_markup=kb, parse_mode="HTML")

# --- ПОПОЛНЕНИЕ (КНОПКИ СУММ) ---
@dp.callback_query(F.data == "deposit")
async def deposit_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,
    ])
    await call.message.edit_text("<b>💎 Пополнение баланса Stars</b>\n\nВыберите сумму или введите свою:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("dep_"))
async def fast_dep(call: types.CallbackQuery):
    amount = int(call.data.split("_")[1])
    await call.message.answer_invoice(
        title="Пополнение Stars",
        description=f"Зачисление {amount} звезд на баланс бота",
        payload=f"stars_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=amount)]
    )
    await call.answer()

# --- МАГАЗИН (ТАРИФЫ) ---
@dp.callback_query(F.data == "shop")
async def shop_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,,
    ])
    await call.message.edit_text("<b>⚡ Выберите тариф подписки:</b>\n\n<i>Оплата будет списана с вашего внутреннего баланса бота.</i>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(call: types.CallbackQuery):
    if call.data == "buy_back": return
    _, days, price = call.data.split("_")
    days, price = int(days), int(price)

    user = db_query("SELECT balance FROM users WHERE id = ?", (call.from_user.id,), fetchone=True)
    if user[0] >= price:
        new_expire = (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat()
        db_query("UPDATE users SET balance = balance - ?, expire = ? WHERE id = ?", (price, new_expire, call.from_user.id))
        await call.message.edit_text(f"<b>✅ Успешно!</b>\nПодписка активна на {days} дн.\nПриятного пользования! ✨", parse_mode="HTML")
    else:
        await call.answer("⚠️ Недостаточно звезд! Пополните баланс.", show_alert=True)

# --- ТЕХНИЧЕСКИЕ ЧАСТИ ---
@dp.callback_query(F.data == "back")
async def go_back(call: types.CallbackQuery):
    await start(call.message)
    await call.message.delete()

@dp.pre_checkout_query()
async def checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def pay_ok(message: types.Message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, message.from_user.id))
    await message.answer(f"<b>🌟 Баланс пополнен на {amt} ⭐!</b>\nВоспользуйтесь меню /start чтобы купить подписку.", parse_mode="HTML")

# Админ-команда выдачи баланса
@dp.message(Command("add_bal"))
async def adm_add(message: types.Message):
    owner = db_query("SELECT is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if owner and owner[0] == 1:
        try:
            _, uid, val = message.text.split()
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (int(val), int(uid)))
            await message.answer(f"✅ Начислено {val} ⭐ пользователю {uid}")
        except: pass

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
import os, asyncio, datetime, sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup

# Данные из настроек
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect('users.db') as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetchone: return cur.fetchone()
        conn.commit()

db_query('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, expire TEXT, is_owner INTEGER DEFAULT 0)''')

# --- КРАСИВЫЙ ИНТЕРФЕЙС ---
def main_kb(user_id, balance, expire_date):
    # Проверка подписки для текста
    sub_status = "❌ Не активна"
    if expire_date:
        try:
            dt = datetime.datetime.fromisoformat(expire_date)
            if dt > datetime.datetime.now():
                sub_status = f"✅ До {dt.strftime('%d.%m.%Y')}"
        except: pass

    kb =,, # ЗАМЕНИ НА СВОЙ ТГ
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb), sub_status

# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    res = db_query("SELECT balance, expire, is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not res:
        owners = db_query("SELECT COUNT(*) FROM users WHERE is_owner = 1", fetchone=True)[0]
        is_owner = 1 if owners == 0 else 0
        db_query("INSERT INTO users (id, balance, is_owner) VALUES (?, 0, ?)", (message.from_user.id, is_owner))
        balance, expire, owner_status = 0, None, is_owner
    else:
        balance, expire, owner_status = res

    kb, sub_text = main_kb(message.from_user.id, balance, expire)
    
    profile_text = (
        f"<b>👋 Добро пожаловать в Links Bypass!</b>\n\n"
        f"👤 <b>Ваш профиль:</b>\n"
        f"├ 🆔 <code>{message.from_user.id}</code>\n"
        f"├ 💰 Баланс: <b>{balance} ⭐</b>\n"
        f"└ 👑 Статус: <b>{sub_text}</b>\n\n"
        f"<i>Пришлите ссылку, чтобы начать обход!</i>"
    )
    if owner_status == 1: profile_text += "\n\n🛠 <b>Вы — Создатель</b>"
    
    await message.answer(profile_text, reply_markup=kb, parse_mode="HTML")

# --- ПОПОЛНЕНИЕ (КНОПКИ СУММ) ---
@dp.callback_query(F.data == "deposit")
async def deposit_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,
    ])
    await call.message.edit_text("<b>💎 Пополнение баланса Stars</b>\n\nВыберите сумму или введите свою:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("dep_"))
async def fast_dep(call: types.CallbackQuery):
    amount = int(call.data.split("_")[1])
    await call.message.answer_invoice(
        title="Пополнение Stars",
        description=f"Зачисление {amount} звезд на баланс бота",
        payload=f"stars_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=amount)]
    )
    await call.answer()

# --- МАГАЗИН (ТАРИФЫ) ---
@dp.callback_query(F.data == "shop")
async def shop_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,,
    ])
    await call.message.edit_text("<b>⚡ Выберите тариф подписки:</b>\n\n<i>Оплата будет списана с вашего внутреннего баланса бота.</i>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(call: types.CallbackQuery):
    if call.data == "buy_back": return
    _, days, price = call.data.split("_")
    days, price = int(days), int(price)

    user = db_query("SELECT balance FROM users WHERE id = ?", (call.from_user.id,), fetchone=True)
    if user[0] >= price:
        new_expire = (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat()
        db_query("UPDATE users SET balance = balance - ?, expire = ? WHERE id = ?", (price, new_expire, call.from_user.id))
        await call.message.edit_text(f"<b>✅ Успешно!</b>\nПодписка активна на {days} дн.\nПриятного пользования! ✨", parse_mode="HTML")
    else:
        await call.answer("⚠️ Недостаточно звезд! Пополните баланс.", show_alert=True)

# --- ТЕХНИЧЕСКИЕ ЧАСТИ ---
@dp.callback_query(F.data == "back")
async def go_back(call: types.CallbackQuery):
    await start(call.message)
    await call.message.delete()

@dp.pre_checkout_query()
async def checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def pay_ok(message: types.Message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, message.from_user.id))
    await message.answer(f"<b>🌟 Баланс пополнен на {amt} ⭐!</b>\nВоспользуйтесь меню /start чтобы купить подписку.", parse_mode="HTML")

# Админ-команда выдачи баланса
@dp.message(Command("add_bal"))
async def adm_add(message: types.Message):
    owner = db_query("SELECT is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if owner and owner[0] == 1:
        try:
            _, uid, val = message.text.split()
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (int(val), int(uid)))
            await message.answer(f"✅ Начислено {val} ⭐ пользователю {uid}")
        except: pass

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
import os, asyncio, datetime, sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup

# Данные из настроек
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect('users.db') as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetchone: return cur.fetchone()
        conn.commit()

db_query('''CREATE TABLE IF NOT EXISTS users 
            (id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, expire TEXT, is_owner INTEGER DEFAULT 0)''')

# --- КРАСИВЫЙ ИНТЕРФЕЙС ---
def main_kb(user_id, balance, expire_date):
    # Проверка подписки для текста
    sub_status = "❌ Не активна"
    if expire_date:
        try:
            dt = datetime.datetime.fromisoformat(expire_date)
            if dt > datetime.datetime.now():
                sub_status = f"✅ До {dt.strftime('%d.%m.%Y')}"
        except: pass

    kb =,, # ЗАМЕНИ НА СВОЙ ТГ
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb), sub_status

# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def start(message: types.Message):
    res = db_query("SELECT balance, expire, is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not res:
        owners = db_query("SELECT COUNT(*) FROM users WHERE is_owner = 1", fetchone=True)[0]
        is_owner = 1 if owners == 0 else 0
        db_query("INSERT INTO users (id, balance, is_owner) VALUES (?, 0, ?)", (message.from_user.id, is_owner))
        balance, expire, owner_status = 0, None, is_owner
    else:
        balance, expire, owner_status = res

    kb, sub_text = main_kb(message.from_user.id, balance, expire)
    
    profile_text = (
        f"<b>👋 Добро пожаловать в Links Bypass!</b>\n\n"
        f"👤 <b>Ваш профиль:</b>\n"
        f"├ 🆔 <code>{message.from_user.id}</code>\n"
        f"├ 💰 Баланс: <b>{balance} ⭐</b>\n"
        f"└ 👑 Статус: <b>{sub_text}</b>\n\n"
        f"<i>Пришлите ссылку, чтобы начать обход!</i>"
    )
    if owner_status == 1: profile_text += "\n\n🛠 <b>Вы — Создатель</b>"
    
    await message.answer(profile_text, reply_markup=kb, parse_mode="HTML")

# --- ПОПОЛНЕНИЕ (КНОПКИ СУММ) ---
@dp.callback_query(F.data == "deposit")
async def deposit_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,
    ])
    await call.message.edit_text("<b>💎 Пополнение баланса Stars</b>\n\nВыберите сумму или введите свою:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("dep_"))
async def fast_dep(call: types.CallbackQuery):
    amount = int(call.data.split("_")[1])
    await call.message.answer_invoice(
        title="Пополнение Stars",
        description=f"Зачисление {amount} звезд на баланс бота",
        payload=f"stars_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=amount)]
    )
    await call.answer()

# --- МАГАЗИН (ТАРИФЫ) ---
@dp.callback_query(F.data == "shop")
async def shop_menu(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=,,,,
    ])
    await call.message.edit_text("<b>⚡ Выберите тариф подписки:</b>\n\n<i>Оплата будет списана с вашего внутреннего баланса бота.</i>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(call: types.CallbackQuery):
    if call.data == "buy_back": return
    _, days, price = call.data.split("_")
    days, price = int(days), int(price)

    user = db_query("SELECT balance FROM users WHERE id = ?", (call.from_user.id,), fetchone=True)
    if user[0] >= price:
        new_expire = (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat()
        db_query("UPDATE users SET balance = balance - ?, expire = ? WHERE id = ?", (price, new_expire, call.from_user.id))
        await call.message.edit_text(f"<b>✅ Успешно!</b>\nПодписка активна на {days} дн.\nПриятного пользования! ✨", parse_mode="HTML")
    else:
        await call.answer("⚠️ Недостаточно звезд! Пополните баланс.", show_alert=True)

# --- ТЕХНИЧЕСКИЕ ЧАСТИ ---
@dp.callback_query(F.data == "back")
async def go_back(call: types.CallbackQuery):
    await start(call.message)
    await call.message.delete()

@dp.pre_checkout_query()
async def checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def pay_ok(message: types.Message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, message.from_user.id))
    await message.answer(f"<b>🌟 Баланс пополнен на {amt} ⭐!</b>\nВоспользуйтесь меню /start чтобы купить подписку.", parse_mode="HTML")

# Админ-команда выдачи баланса
@dp.message(Command("add_bal"))
async def adm_add(message: types.Message):
    owner = db_query("SELECT is_owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if owner and owner[0] == 1:
        try:
            _, uid, val = message.text.split()
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (int(val), int(uid)))
            await message.answer(f"✅ Начислено {val} ⭐ пользователю {uid}")
        except: pass

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
