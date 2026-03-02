import asyncio
import logging
import aiohttp
import re
import threading
from flask import Flask
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.callback_data import CallbackData

# Токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения! Добавь в Render.")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Callback данные для кнопок
menu_cb = CallbackData("menu", "action")
subscription_cb = CallbackData("sub", "days", "price")
balance_cb = CallbackData("balance", "amount")
confirm_cb = CallbackData("confirm", "type", "value")
payment_cb = CallbackData("pay", "amount")

# Классы состояний
class BalanceStates(StatesGroup):
    waiting_for_custom_amount = State()

class BypassStates(StatesGroup):
    waiting_for_link = State()

# Класс для хранения данных пользователя
class UserData:
    def __init__(self):
        self.users = {}
        self.last_bypass = {}
        self.pending_payments = {}  # {payload: (user_id, amount)}
    
    def get_user(self, user_id):
        if user_id not in self.users:
            self.users[user_id] = {
                'balance': 0,
                'subscription_end': None,
                'trial_used': False
            }
        return self.users[user_id]
    
    def update_balance(self, user_id, amount):
        if user_id in self.users:
            self.users[user_id]['balance'] += amount
            return True
        return False
    
    def set_subscription(self, user_id, days):
        if user_id in self.users:
            end_date = datetime.now() + timedelta(days=days)
            self.users[user_id]['subscription_end'] = end_date
            return True
        return False
    
    def get_subscription_status(self, user_id):
        user = self.get_user(user_id)
        if user['subscription_end'] and user['subscription_end'] > datetime.now():
            return True, user['subscription_end']
        return False, None
    
    def use_trial(self, user_id):
        user = self.get_user(user_id)
        if not user['trial_used']:
            user['trial_used'] = True
            end_date = datetime.now() + timedelta(days=7)
            user['subscription_end'] = end_date
            return True
        return False
    
    def can_bypass(self, user_id):
        last = self.last_bypass.get(user_id)
        if not last:
            return True, 0
        now = datetime.now()
        diff = (now - last).total_seconds()
        if diff >= 600:
            return True, 0
        wait = 600 - int(diff)
        return False, wait
    
    def update_bypass_time(self, user_id):
        self.last_bypass[user_id] = datetime.now()

# Инициализация хранилища пользователей
user_data = UserData()

def format_subscription_time(end_date):
    if not end_date:
        return "не активна"
    now = datetime.now()
    if end_date <= now:
        return "не активна"
    delta = end_date - now
    hours = int(delta.total_seconds() / 3600)
    return f"истекает через {hours} ч."

def get_main_menu_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    is_active, end_date = user_data.get_subscription_status(user_id)
    
    if is_active:
        keyboard.add(InlineKeyboardButton("✅ Подписка активна", callback_data=menu_cb.new(action="subscription_active")))
    else:
        user = user_data.get_user(user_id)
        if not user['trial_used']:
            keyboard.add(InlineKeyboardButton("🎁 Пробный период (7 дней)", callback_data=menu_cb.new(action="trial")))
        keyboard.add(InlineKeyboardButton("💸 Купить подписку", callback_data=menu_cb.new(action="buy_subscription")))
    
    keyboard.add(InlineKeyboardButton("🔥 Пополнить баланс", callback_data=menu_cb.new(action="add_balance")))
    return keyboard

def get_subscription_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("7 дней (5⭐)", callback_data=subscription_cb.new(days="7", price="5")),
        InlineKeyboardButton("14 дней (10⭐)", callback_data=subscription_cb.new(days="14", price="10")),
        InlineKeyboardButton("31 день (17⭐)", callback_data=subscription_cb.new(days="31", price="17")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

def get_balance_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("10⭐", callback_data=balance_cb.new(amount="10")),
        InlineKeyboardButton("30⭐", callback_data=balance_cb.new(amount="30")),
        InlineKeyboardButton("50⭐", callback_data=balance_cb.new(amount="50")),
        InlineKeyboardButton("100⭐", callback_data=balance_cb.new(amount="100")),
        InlineKeyboardButton("250⭐", callback_data=balance_cb.new(amount="250")),
        InlineKeyboardButton("500⭐", callback_data=balance_cb.new(amount="500")),
        InlineKeyboardButton("💸 Своя сумма", callback_data=balance_cb.new(amount="custom")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

async def bypass_link(url):
    api_url = "https://bypassunlock.com/api"
    params = {"url": url}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return True, data.get("bypassed_url", "Не удалось получить ссылку")
                else:
                    return False, f"Ошибка API: {response.status}"
    except asyncio.TimeoutError:
        return False, "Нет ответа от сервера"
    except Exception as e:
        return False, f"Ошибка: {str(e)}"

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = user_data.get_user(user_id)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
    
    keyboard = get_main_menu_keyboard(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    
    is_active, _ = user_data.get_subscription_status(user_id)
    if not is_active:
        await message.answer("🚫 **Нет активной подписки**\nКупите подписку или активируйте пробный период", parse_mode="Markdown")
        return
    
    can_bypass, wait_time = user_data.can_bypass(user_id)
    if not can_bypass:
        minutes = wait_time // 60
        seconds = wait_time % 60
        await message.answer(f"⏳ **Лимит 10 минут**\nПодождите ещё {minutes} мин {seconds} сек", parse_mode="Markdown")
        return
    
    url_pattern = re.compile(r'https?://[^\s]+')
    if url_pattern.match(text):
        processing_msg = await message.answer("⏳ Обрабатываю ссылку...")
        success, result = await bypass_link(text)
        
        if success:
            user_data.update_bypass_time(user_id)
            await processing_msg.edit_text(f"✅ **Готово:**\n{result}", parse_mode="Markdown")
        else:
            await processing_msg.edit_text(f"❌ **Ошибка:**\n{result}", parse_mode="Markdown")
    else:
        await message.answer("❌ Отправьте **ссылку** для обхода", parse_mode="Markdown")

@dp.callback_query_handler(menu_cb.filter())
async def process_menu_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    action = callback_data['action']
    
    if action == "main_menu":
        user = user_data.get_user(user_id)
        is_active, end_date = user_data.get_subscription_status(user_id)
        time_left = format_subscription_time(end_date)
        
        text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
        keyboard = get_main_menu_keyboard(user_id)
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "trial":
        if user_data.use_trial(user_id):
            await callback_query.answer("✅ Пробный период на 7 дней активирован!", show_alert=True)
            user = user_data.get_user(user_id)
            is_active, end_date = user_data.get_subscription_status(user_id)
            time_left = format_subscription_time(end_date)
            text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
            keyboard = get_main_menu_keyboard(user_id)
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await callback_query.answer("🚫 Пробный период уже был использован!", show_alert=True)
    
    elif action == "buy_subscription":
        is_active, _ = user_data.get_subscription_status(user_id)
        if is_active:
            await callback_query.answer("🚫 Подписка уже активна!", show_alert=True)
            return
        
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите подписку:**"
        keyboard = get_subscription_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "subscription_active":
        await callback_query.answer("✅ Подписка активна! Лимит 10 минут между обходами", show_alert=True)
    
    elif action == "add_balance":
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите сумму пополнения** (от 2⭐ до 1000⭐):"
        keyboard = get_balance_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

@dp.callback_query_handler(subscription_cb.filter())
async def process_subscription_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    days = callback_data['days']
    price = int(callback_data['price'])
    
    user = user_data.get_user(user_id)
    
    if user['balance'] < price:
        await callback_query.answer(f"❌ Недостаточно средств! Нужно {price}⭐", show_alert=True)
        return
    
    user['balance'] -= price
    user_data.set_subscription(user_id, int(days))
    
    await callback_query.answer(f"✅ Подписка на {days} дней активирована!", show_alert=True)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
    keyboard = get_main_menu_keyboard(user_id)
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query_handler(balance_cb.filter())
async def process_balance_callback(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    user_id = callback_query.from_user.id
    amount = callback_data['amount']
    
    if amount == "custom":
        text = f"💸 **Введите сумму** (от 2 до 1000):"
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu")))
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await BalanceStates.waiting_for_custom_amount.set()
    else:
        amount_int = int(amount)
        if amount_int < 2 or amount_int > 1000:
            await callback_query.answer("❌ Сумма от 2 до 1000⭐", show_alert=True)
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount_int}⭐", amount=amount_int)]
        payload = f"topup_{user_id}_{amount_int}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount_int)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount_int}⭐ для бота",
            payload=payload,
            provider_token="",  # Для звезд пустая строка
            currency="XTR",      # Специальная валюта для звезд
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await callback_query.message.delete()
    
    await callback_query.answer()

@dp.message_handler(state=BalanceStates.waiting_for_custom_amount)
async def process_custom_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        amount = int(message.text)
        
        if amount < 2 or amount > 1000:
            await message.answer("❌ **Неверное значение**\nСумма от 2 до 1000⭐", parse_mode="Markdown")
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount}⭐", amount=amount)]
        payload = f"topup_{user_id}_{amount}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount}⭐ для бота",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Введите **число**", parse_mode="Markdown")

# Обработчик предварительной проверки платежа
@dp.pre_checkout_query_handler()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# Обработчик успешного платежа
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    
    if payload in user_data.pending_payments:
        user_id, stars_amount = user_data.pending_payments[payload]
        user_data.update_balance(user_id, stars_amount)
        del user_data.pending_payments[payload]
        
        await message.answer(f"✅ **Баланс пополнен на {stars_amount}⭐!**", parse_mode="Markdown")
    else:
        await message.answer("❌ Ошибка платежа", parse_mode="Markdown")

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running", 200

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

async def main():
    logging.info("Бот запущен...")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())import asyncio
import logging
import aiohttp
import re
import threading
from flask import Flask
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.callback_data import CallbackData

# Токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения! Добавь в Render.")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Callback данные для кнопок
menu_cb = CallbackData("menu", "action")
subscription_cb = CallbackData("sub", "days", "price")
balance_cb = CallbackData("balance", "amount")
confirm_cb = CallbackData("confirm", "type", "value")
payment_cb = CallbackData("pay", "amount")

# Классы состояний
class BalanceStates(StatesGroup):
    waiting_for_custom_amount = State()

class BypassStates(StatesGroup):
    waiting_for_link = State()

# Класс для хранения данных пользователя
class UserData:
    def __init__(self):
        self.users = {}
        self.last_bypass = {}
        self.pending_payments = {}  # {payload: (user_id, amount)}
    
    def get_user(self, user_id):
        if user_id not in self.users:
            self.users[user_id] = {
                'balance': 0,
                'subscription_end': None,
                'trial_used': False
            }
        return self.users[user_id]
    
    def update_balance(self, user_id, amount):
        if user_id in self.users:
            self.users[user_id]['balance'] += amount
            return True
        return False
    
    def set_subscription(self, user_id, days):
        if user_id in self.users:
            end_date = datetime.now() + timedelta(days=days)
            self.users[user_id]['subscription_end'] = end_date
            return True
        return False
    
    def get_subscription_status(self, user_id):
        user = self.get_user(user_id)
        if user['subscription_end'] and user['subscription_end'] > datetime.now():
            return True, user['subscription_end']
        return False, None
    
    def use_trial(self, user_id):
        user = self.get_user(user_id)
        if not user['trial_used']:
            user['trial_used'] = True
            end_date = datetime.now() + timedelta(days=7)
            user['subscription_end'] = end_date
            return True
        return False
    
    def can_bypass(self, user_id):
        last = self.last_bypass.get(user_id)
        if not last:
            return True, 0
        now = datetime.now()
        diff = (now - last).total_seconds()
        if diff >= 600:
            return True, 0
        wait = 600 - int(diff)
        return False, wait
    
    def update_bypass_time(self, user_id):
        self.last_bypass[user_id] = datetime.now()

# Инициализация хранилища пользователей
user_data = UserData()

def format_subscription_time(end_date):
    if not end_date:
        return "не активна"
    now = datetime.now()
    if end_date <= now:
        return "не активна"
    delta = end_date - now
    hours = int(delta.total_seconds() / 3600)
    return f"истекает через {hours} ч."

def get_main_menu_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    is_active, end_date = user_data.get_subscription_status(user_id)
    
    if is_active:
        keyboard.add(InlineKeyboardButton("✅ Подписка активна", callback_data=menu_cb.new(action="subscription_active")))
    else:
        user = user_data.get_user(user_id)
        if not user['trial_used']:
            keyboard.add(InlineKeyboardButton("🎁 Пробный период (7 дней)", callback_data=menu_cb.new(action="trial")))
        keyboard.add(InlineKeyboardButton("💸 Купить подписку", callback_data=menu_cb.new(action="buy_subscription")))
    
    keyboard.add(InlineKeyboardButton("🔥 Пополнить баланс", callback_data=menu_cb.new(action="add_balance")))
    return keyboard

def get_subscription_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("7 дней (5⭐)", callback_data=subscription_cb.new(days="7", price="5")),
        InlineKeyboardButton("14 дней (10⭐)", callback_data=subscription_cb.new(days="14", price="10")),
        InlineKeyboardButton("31 день (17⭐)", callback_data=subscription_cb.new(days="31", price="17")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

def get_balance_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("10⭐", callback_data=balance_cb.new(amount="10")),
        InlineKeyboardButton("30⭐", callback_data=balance_cb.new(amount="30")),
        InlineKeyboardButton("50⭐", callback_data=balance_cb.new(amount="50")),
        InlineKeyboardButton("100⭐", callback_data=balance_cb.new(amount="100")),
        InlineKeyboardButton("250⭐", callback_data=balance_cb.new(amount="250")),
        InlineKeyboardButton("500⭐", callback_data=balance_cb.new(amount="500")),
        InlineKeyboardButton("💸 Своя сумма", callback_data=balance_cb.new(amount="custom")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

async def bypass_link(url):
    api_url = "https://bypassunlock.com/api"
    params = {"url": url}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return True, data.get("bypassed_url", "Не удалось получить ссылку")
                else:
                    return False, f"Ошибка API: {response.status}"
    except asyncio.TimeoutError:
        return False, "Нет ответа от сервера"
    except Exception as e:
        return False, f"Ошибка: {str(e)}"

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = user_data.get_user(user_id)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
    
    keyboard = get_main_menu_keyboard(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    
    is_active, _ = user_data.get_subscription_status(user_id)
    if not is_active:
        await message.answer("🚫 **Нет активной подписки**\nКупите подписку или активируйте пробный период", parse_mode="Markdown")
        return
    
    can_bypass, wait_time = user_data.can_bypass(user_id)
    if not can_bypass:
        minutes = wait_time // 60
        seconds = wait_time % 60
        await message.answer(f"⏳ **Лимит 10 минут**\nПодождите ещё {minutes} мин {seconds} сек", parse_mode="Markdown")
        return
    
    url_pattern = re.compile(r'https?://[^\s]+')
    if url_pattern.match(text):
        processing_msg = await message.answer("⏳ Обрабатываю ссылку...")
        success, result = await bypass_link(text)
        
        if success:
            user_data.update_bypass_time(user_id)
            await processing_msg.edit_text(f"✅ **Готово:**\n{result}", parse_mode="Markdown")
        else:
            await processing_msg.edit_text(f"❌ **Ошибка:**\n{result}", parse_mode="Markdown")
    else:
        await message.answer("❌ Отправьте **ссылку** для обхода", parse_mode="Markdown")

@dp.callback_query_handler(menu_cb.filter())
async def process_menu_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    action = callback_data['action']
    
    if action == "main_menu":
        user = user_data.get_user(user_id)
        is_active, end_date = user_data.get_subscription_status(user_id)
        time_left = format_subscription_time(end_date)
        
        text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
        keyboard = get_main_menu_keyboard(user_id)
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "trial":
        if user_data.use_trial(user_id):
            await callback_query.answer("✅ Пробный период на 7 дней активирован!", show_alert=True)
            user = user_data.get_user(user_id)
            is_active, end_date = user_data.get_subscription_status(user_id)
            time_left = format_subscription_time(end_date)
            text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
            keyboard = get_main_menu_keyboard(user_id)
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await callback_query.answer("🚫 Пробный период уже был использован!", show_alert=True)
    
    elif action == "buy_subscription":
        is_active, _ = user_data.get_subscription_status(user_id)
        if is_active:
            await callback_query.answer("🚫 Подписка уже активна!", show_alert=True)
            return
        
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите подписку:**"
        keyboard = get_subscription_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "subscription_active":
        await callback_query.answer("✅ Подписка активна! Лимит 10 минут между обходами", show_alert=True)
    
    elif action == "add_balance":
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите сумму пополнения** (от 2⭐ до 1000⭐):"
        keyboard = get_balance_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

@dp.callback_query_handler(subscription_cb.filter())
async def process_subscription_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    days = callback_data['days']
    price = int(callback_data['price'])
    
    user = user_data.get_user(user_id)
    
    if user['balance'] < price:
        await callback_query.answer(f"❌ Недостаточно средств! Нужно {price}⭐", show_alert=True)
        return
    
    user['balance'] -= price
    user_data.set_subscription(user_id, int(days))
    
    await callback_query.answer(f"✅ Подписка на {days} дней активирована!", show_alert=True)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
    keyboard = get_main_menu_keyboard(user_id)
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query_handler(balance_cb.filter())
async def process_balance_callback(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    user_id = callback_query.from_user.id
    amount = callback_data['amount']
    
    if amount == "custom":
        text = f"💸 **Введите сумму** (от 2 до 1000):"
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu")))
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await BalanceStates.waiting_for_custom_amount.set()
    else:
        amount_int = int(amount)
        if amount_int < 2 or amount_int > 1000:
            await callback_query.answer("❌ Сумма от 2 до 1000⭐", show_alert=True)
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount_int}⭐", amount=amount_int)]
        payload = f"topup_{user_id}_{amount_int}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount_int)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount_int}⭐ для бота",
            payload=payload,
            provider_token="",  # Для звезд пустая строка
            currency="XTR",      # Специальная валюта для звезд
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await callback_query.message.delete()
    
    await callback_query.answer()

@dp.message_handler(state=BalanceStates.waiting_for_custom_amount)
async def process_custom_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        amount = int(message.text)
        
        if amount < 2 or amount > 1000:
            await message.answer("❌ **Неверное значение**\nСумма от 2 до 1000⭐", parse_mode="Markdown")
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount}⭐", amount=amount)]
        payload = f"topup_{user_id}_{amount}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount}⭐ для бота",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Введите **число**", parse_mode="Markdown")

# Обработчик предварительной проверки платежа
@dp.pre_checkout_query_handler()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# Обработчик успешного платежа
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    
    if payload in user_data.pending_payments:
        user_id, stars_amount = user_data.pending_payments[payload]
        user_data.update_balance(user_id, stars_amount)
        del user_data.pending_payments[payload]
        
        await message.answer(f"✅ **Баланс пополнен на {stars_amount}⭐!**", parse_mode="Markdown")
    else:
        await message.answer("❌ Ошибка платежа", parse_mode="Markdown")

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running", 200

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

async def main():
    logging.info("Бот запущен...")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())import asyncio
import logging
import aiohttp
import re
import threading
from flask import Flask
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.callback_data import CallbackData

# Токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения! Добавь в Render.")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Callback данные для кнопок
menu_cb = CallbackData("menu", "action")
subscription_cb = CallbackData("sub", "days", "price")
balance_cb = CallbackData("balance", "amount")
confirm_cb = CallbackData("confirm", "type", "value")
payment_cb = CallbackData("pay", "amount")

# Классы состояний
class BalanceStates(StatesGroup):
    waiting_for_custom_amount = State()

class BypassStates(StatesGroup):
    waiting_for_link = State()

# Класс для хранения данных пользователя
class UserData:
    def __init__(self):
        self.users = {}
        self.last_bypass = {}
        self.pending_payments = {}  # {payload: (user_id, amount)}
    
    def get_user(self, user_id):
        if user_id not in self.users:
            self.users[user_id] = {
                'balance': 0,
                'subscription_end': None,
                'trial_used': False
            }
        return self.users[user_id]
    
    def update_balance(self, user_id, amount):
        if user_id in self.users:
            self.users[user_id]['balance'] += amount
            return True
        return False
    
    def set_subscription(self, user_id, days):
        if user_id in self.users:
            end_date = datetime.now() + timedelta(days=days)
            self.users[user_id]['subscription_end'] = end_date
            return True
        return False
    
    def get_subscription_status(self, user_id):
        user = self.get_user(user_id)
        if user['subscription_end'] and user['subscription_end'] > datetime.now():
            return True, user['subscription_end']
        return False, None
    
    def use_trial(self, user_id):
        user = self.get_user(user_id)
        if not user['trial_used']:
            user['trial_used'] = True
            end_date = datetime.now() + timedelta(days=7)
            user['subscription_end'] = end_date
            return True
        return False
    
    def can_bypass(self, user_id):
        last = self.last_bypass.get(user_id)
        if not last:
            return True, 0
        now = datetime.now()
        diff = (now - last).total_seconds()
        if diff >= 600:
            return True, 0
        wait = 600 - int(diff)
        return False, wait
    
    def update_bypass_time(self, user_id):
        self.last_bypass[user_id] = datetime.now()

# Инициализация хранилища пользователей
user_data = UserData()

def format_subscription_time(end_date):
    if not end_date:
        return "не активна"
    now = datetime.now()
    if end_date <= now:
        return "не активна"
    delta = end_date - now
    hours = int(delta.total_seconds() / 3600)
    return f"истекает через {hours} ч."

def get_main_menu_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    is_active, end_date = user_data.get_subscription_status(user_id)
    
    if is_active:
        keyboard.add(InlineKeyboardButton("✅ Подписка активна", callback_data=menu_cb.new(action="subscription_active")))
    else:
        user = user_data.get_user(user_id)
        if not user['trial_used']:
            keyboard.add(InlineKeyboardButton("🎁 Пробный период (7 дней)", callback_data=menu_cb.new(action="trial")))
        keyboard.add(InlineKeyboardButton("💸 Купить подписку", callback_data=menu_cb.new(action="buy_subscription")))
    
    keyboard.add(InlineKeyboardButton("🔥 Пополнить баланс", callback_data=menu_cb.new(action="add_balance")))
    return keyboard

def get_subscription_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("7 дней (5⭐)", callback_data=subscription_cb.new(days="7", price="5")),
        InlineKeyboardButton("14 дней (10⭐)", callback_data=subscription_cb.new(days="14", price="10")),
        InlineKeyboardButton("31 день (17⭐)", callback_data=subscription_cb.new(days="31", price="17")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

def get_balance_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("10⭐", callback_data=balance_cb.new(amount="10")),
        InlineKeyboardButton("30⭐", callback_data=balance_cb.new(amount="30")),
        InlineKeyboardButton("50⭐", callback_data=balance_cb.new(amount="50")),
        InlineKeyboardButton("100⭐", callback_data=balance_cb.new(amount="100")),
        InlineKeyboardButton("250⭐", callback_data=balance_cb.new(amount="250")),
        InlineKeyboardButton("500⭐", callback_data=balance_cb.new(amount="500")),
        InlineKeyboardButton("💸 Своя сумма", callback_data=balance_cb.new(amount="custom")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

async def bypass_link(url):
    api_url = "https://bypassunlock.com/api"
    params = {"url": url}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return True, data.get("bypassed_url", "Не удалось получить ссылку")
                else:
                    return False, f"Ошибка API: {response.status}"
    except asyncio.TimeoutError:
        return False, "Нет ответа от сервера"
    except Exception as e:
        return False, f"Ошибка: {str(e)}"

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = user_data.get_user(user_id)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
    
    keyboard = get_main_menu_keyboard(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    
    is_active, _ = user_data.get_subscription_status(user_id)
    if not is_active:
        await message.answer("🚫 **Нет активной подписки**\nКупите подписку или активируйте пробный период", parse_mode="Markdown")
        return
    
    can_bypass, wait_time = user_data.can_bypass(user_id)
    if not can_bypass:
        minutes = wait_time // 60
        seconds = wait_time % 60
        await message.answer(f"⏳ **Лимит 10 минут**\nПодождите ещё {minutes} мин {seconds} сек", parse_mode="Markdown")
        return
    
    url_pattern = re.compile(r'https?://[^\s]+')
    if url_pattern.match(text):
        processing_msg = await message.answer("⏳ Обрабатываю ссылку...")
        success, result = await bypass_link(text)
        
        if success:
            user_data.update_bypass_time(user_id)
            await processing_msg.edit_text(f"✅ **Готово:**\n{result}", parse_mode="Markdown")
        else:
            await processing_msg.edit_text(f"❌ **Ошибка:**\n{result}", parse_mode="Markdown")
    else:
        await message.answer("❌ Отправьте **ссылку** для обхода", parse_mode="Markdown")

@dp.callback_query_handler(menu_cb.filter())
async def process_menu_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    action = callback_data['action']
    
    if action == "main_menu":
        user = user_data.get_user(user_id)
        is_active, end_date = user_data.get_subscription_status(user_id)
        time_left = format_subscription_time(end_date)
        
        text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**\nЛимит: **10 минут** между обходами"
        keyboard = get_main_menu_keyboard(user_id)
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "trial":
        if user_data.use_trial(user_id):
            await callback_query.answer("✅ Пробный период на 7 дней активирован!", show_alert=True)
            user = user_data.get_user(user_id)
            is_active, end_date = user_data.get_subscription_status(user_id)
            time_left = format_subscription_time(end_date)
            text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
            keyboard = get_main_menu_keyboard(user_id)
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await callback_query.answer("🚫 Пробный период уже был использован!", show_alert=True)
    
    elif action == "buy_subscription":
        is_active, _ = user_data.get_subscription_status(user_id)
        if is_active:
            await callback_query.answer("🚫 Подписка уже активна!", show_alert=True)
            return
        
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите подписку:**"
        keyboard = get_subscription_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "subscription_active":
        await callback_query.answer("✅ Подписка активна! Лимит 10 минут между обходами", show_alert=True)
    
    elif action == "add_balance":
        user = user_data.get_user(user_id)
        text = f"💸 **Баланс:** {user['balance']}⭐\n**Выберите сумму пополнения** (от 2⭐ до 1000⭐):"
        keyboard = get_balance_keyboard()
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

@dp.callback_query_handler(subscription_cb.filter())
async def process_subscription_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    days = callback_data['days']
    price = int(callback_data['price'])
    
    user = user_data.get_user(user_id)
    
    if user['balance'] < price:
        await callback_query.answer(f"❌ Недостаточно средств! Нужно {price}⭐", show_alert=True)
        return
    
    user['balance'] -= price
    user_data.set_subscription(user_id, int(days))
    
    await callback_query.answer(f"✅ Подписка на {days} дней активирована!", show_alert=True)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
    keyboard = get_main_menu_keyboard(user_id)
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query_handler(balance_cb.filter())
async def process_balance_callback(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    user_id = callback_query.from_user.id
    amount = callback_data['amount']
    
    if amount == "custom":
        text = f"💸 **Введите сумму** (от 2 до 1000):"
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu")))
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await BalanceStates.waiting_for_custom_amount.set()
    else:
        amount_int = int(amount)
        if amount_int < 2 or amount_int > 1000:
            await callback_query.answer("❌ Сумма от 2 до 1000⭐", show_alert=True)
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount_int}⭐", amount=amount_int)]
        payload = f"topup_{user_id}_{amount_int}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount_int)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount_int}⭐ для бота",
            payload=payload,
            provider_token="",  # Для звезд пустая строка
            currency="XTR",      # Специальная валюта для звезд
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await callback_query.message.delete()
    
    await callback_query.answer()

@dp.message_handler(state=BalanceStates.waiting_for_custom_amount)
async def process_custom_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        amount = int(message.text)
        
        if amount < 2 or amount > 1000:
            await message.answer("❌ **Неверное значение**\nСумма от 2 до 1000⭐", parse_mode="Markdown")
            return
        
        # Создаем счет для оплаты звездами
        prices = [LabeledPrice(label=f"{amount}⭐", amount=amount)]
        payload = f"topup_{user_id}_{amount}_{datetime.now().timestamp()}"
        user_data.pending_payments[payload] = (user_id, amount)
        
        await bot.send_invoice(
            chat_id=user_id,
            title="Пополнение баланса",
            description=f"Покупка {amount}⭐ для бота",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="topup",
            need_email=False,
            need_phone_number=False,
            need_shipping_address=False,
            is_flexible=False
        )
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Введите **число**", parse_mode="Markdown")

# Обработчик предварительной проверки платежа
@dp.pre_checkout_query_handler()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# Обработчик успешного платежа
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    
    if payload in user_data.pending_payments:
        user_id, stars_amount = user_data.pending_payments[payload]
        user_data.update_balance(user_id, stars_amount)
        del user_data.pending_payments[payload]
        
        await message.answer(f"✅ **Баланс пополнен на {stars_amount}⭐!**", parse_mode="Markdown")
    else:
        await message.answer("❌ Ошибка платежа", parse_mode="Markdown")

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running", 200

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

async def main():
    logging.info("Бот запущен...")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
