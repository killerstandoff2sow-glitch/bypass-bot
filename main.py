import os, asyncio, sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Состояние ожидания ввода суммы
class Deposit(StatesGroup):
    wait_amount = State()

# --- БАЗА ДАННЫХ (ID, БАЛАНС, ПОДПИСКА) ---
def db_query(sql, params=(), fetchone=False):
    with sqlite3.connect('economy_bot.db') as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetchone: return cur.fetchone()
        conn.commit()

db_query("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, owner INTEGER DEFAULT 0)")

# --- КЛАВИАТУРА ГЛАВНОГО МЕНЮ ---
def main_kb(user_id, balance):
    kb = [
        [InlineKeyboardButton(text=f"💰 Баланс: {balance} ⭐", callback_data="check_bal")],
        [InlineKeyboardButton(text="➕ Пополнить баланс", callback_data="deposit")],
        [InlineKeyboardButton(text="🛒 Купить подписку", callback_data="shop")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ЛОГИКА ПОПОЛНЕНИЯ ---
@dp.callback_query(F.data == "deposit")
async def start_deposit(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(Deposit.wait_amount)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    await call.message.edit_text("Введите сумму звезд, которую хотите внести на баланс:", reply_markup=cancel_kb)

@dp.message(Deposit.wait_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите число (например, 131)")
    
    amount = int(message.text)
    if amount < 1:
        return await message.answer("Минимальная сумма — 1 звезда.")

    await state.clear()
    # Выставляем счет
    await message.answer_invoice(
        title="Пополнение баланса",
        description=f"Зачисление {amount} ⭐ на ваш внутренний счет",
        payload=f"dep_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=amount)],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Оплатить {amount} ⭐", pay=True)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ])
    )

# --- ПРОВЕРКА И ЗАЧИСЛЕНИЕ ---
@dp.pre_checkout_query()
async def pre_check(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def success_pay(message: types.Message):
    amount = int(message.successful_payment.invoice_payload.split("_")[1])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, message.from_user.id))
    await message.answer(f"✅ Баланс успешно пополнен на {amount} ⭐!")

# --- ОБЩИЕ КОМАНДЫ ---
@dp.message(Command("start"))
async def start(message: types.Message):
    user = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not user:
        # Первый пользователь становится владельцем
        owners_count = db_query("SELECT COUNT(*) FROM users WHERE owner = 1", fetchone=True)[0]
        is_owner = 1 if owners_count == 0 else 0
        db_query("INSERT INTO users (id, balance, owner) VALUES (?, 0, ?)", (message.from_user.id, is_owner))
        balance = 0
    else:
        balance = user[0]
    
    await message.answer("Добро пожаловать в бот обхода ссылок!", reply_markup=main_kb(message.from_user.id, balance))

@dp.callback_query(F.data == "cancel")
async def cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Действие отменено.", reply_markup=None)

# --- АДМИНКА (ВЫДАЧА БАЛАНСА) ---
@dp.message(Command("add_bal"))
async def admin_add_bal(message: types.Message):
    owner = db_query("SELECT owner FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not owner or owner[0] == 0: return

    try:
        _, uid, amount = message.text.split()
        db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (int(amount), int(uid)))
        await message.answer(f"✅ Пользователю {uid} начислено {amount} ⭐")
    except:
        await message.answer("Формат: `/add_bal ID СУММА`", parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
