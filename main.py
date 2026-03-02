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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.callback_data import CallbackData

# Токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения! Добавь его в Render.")

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

# Классы состояний
class BalanceStates(StatesGroup):
    waiting_for_custom_amount = State()

class BypassStates(StatesGroup):
    waiting_for_link = State()

# Класс для хранения данных пользователя
class UserData:
    def __init__(self):
        self.users = {}
    
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
    
    def set_subscription(self, user_id, days):
        if user_id in self.users:
            end_date = datetime.now() + timedelta(days=days)
            self.users[user_id]['subscription_end'] = end_date
    
    def get_subscription_status(self, user_id):
        user = self.get_user(user_id)
        if user['subscription_end'] and user['subscription_end'] > datetime.now():
            return True, user['subscription_end']
        return False, None
    
    def use_trial(self, user_id):
        user = self.get_user(user_id)
        if not user['trial_used']:
            user['trial_used'] = True
            end_date = datetime.now() + timedelta(days=15)
            user['subscription_end'] = end_date
            return True
        return False

# Инициализация хранилища пользователей
user_data = UserData()

# Функция для форматирования оставшегося времени подписки
def format_subscription_time(end_date):
    if not end_date:
        return "не активна"
    
    now = datetime.now()
    if end_date <= now:
        return "не активна"
    
    delta = end_date - now
    hours = int(delta.total_seconds() / 3600)
    return f"истекает через {hours} ч."

# Функция для создания клавиатуры главного меню
def get_main_menu_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    
    if is_active:
        keyboard.add(
            InlineKeyboardButton("▶️ Подписка активна", callback_data=menu_cb.new(action="subscription_active"))
        )
    else:
        user = user_data.get_user(user_id)
        if not user['trial_used']:
            keyboard.add(
                InlineKeyboardButton("🆓 Пробный период", callback_data=menu_cb.new(action="trial"))
            )
        else:
            keyboard.add(
                InlineKeyboardButton("💸 Купить подписку", callback_data=menu_cb.new(action="buy_subscription"))
            )
    
    keyboard.add(
        InlineKeyboardButton("🔥 Пополнить баланс", callback_data=menu_cb.new(action="add_balance"))
    )
    
    return keyboard

# Функция для создания клавиатуры выбора подписки
def get_subscription_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("7 дней (5⭐)", callback_data=subscription_cb.new(days="7", price="5")),
        InlineKeyboardButton("14 дней (10⭐)", callback_data=subscription_cb.new(days="14", price="10")),
        InlineKeyboardButton("31 день (17⭐)", callback_data=subscription_cb.new(days="31", price="17")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

# Функция для создания клавиатуры пополнения баланса
def get_balance_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("10", callback_data=balance_cb.new(amount="10")),
        InlineKeyboardButton("30", callback_data=balance_cb.new(amount="30")),
        InlineKeyboardButton("50", callback_data=balance_cb.new(amount="50")),
        InlineKeyboardButton("💸 Своя сумма", callback_data=balance_cb.new(amount="custom")),
        InlineKeyboardButton("🏠 В меню", callback_data=menu_cb.new(action="main_menu"))
    )
    return keyboard

# Функция для создания клавиатуры подтверждения
def get_confirmation_keyboard(confirm_type, value):
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    if confirm_type == "subscription":
        keyboard.add(
            InlineKeyboardButton("✅ Купить", callback_data=confirm_cb.new(type="confirm_sub", value=value)),
            InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu"))
        )
    elif confirm_type == "balance":
        keyboard.add(
            InlineKeyboardButton(f"✅ {value}⭐", callback_data=confirm_cb.new(type="confirm_balance", value=value)),
            InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu"))
        )
    
    return keyboard

# Функция для обхода ссылок через API
async def bypass_link(url):
    api_url = "https://bypassunlock.com/api"
    
    params = {
        "url": url
    }
    
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

# Обработчик команды /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = user_data.get_user(user_id)
    
    is_active, end_date = user_data.get_subscription_status(user_id)
    time_left = format_subscription_time(end_date)
    
    text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
    
    keyboard = get_main_menu_keyboard(user_id)
    
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

# Обработчик текстовых сообщений
@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    
    is_active, _ = user_data.get_subscription_status(user_id)
    
    if not is_active:
        await message.answer("🚫 У вас нет активной подписки. Купите подписку или активируйте пробный период.")
        return
    
    url_pattern = re.compile(r'https?://[^\s]+')
    if url_pattern.match(text):
        processing_msg = await message.answer("⏳ Обрабатываю ссылку...")
        
        success, result = await bypass_link(text)
        
        if success:
            await processing_msg.edit_text(f"✅ Ответ от сервера: {result}")
        else:
            await processing_msg.edit_text(f"❌ {result}")
    else:
        await message.answer("Пожалуйста, отправьте ссылку для обхода.")

# Обработчик callback-запросов
@dp.callback_query_handler(menu_cb.filter())
async def process_menu_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    action = callback_data['action']
    
    if action == "main_menu":
        user = user_data.get_user(user_id)
        is_active, end_date = user_data.get_subscription_status(user_id)
        time_left = format_subscription_time(end_date)
        
        text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
        keyboard = get_main_menu_keyboard(user_id)
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "trial":
        if user_data.use_trial(user_id):
            await callback_query.answer("✅ Пробный период на 15 дней активирован!", show_alert=True)
            
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
            await callback_query.answer("🚫 Подписку нельзя купить когда она действует!", show_alert=True)
            return
        
        user = user_data.get_user(user_id)
        text = f"💸 Баланс: **{user['balance']}⭐**\nВыберите время подписки:"
        keyboard = get_subscription_keyboard()
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif action == "subscription_active":
        await callback_query.answer("Подписка активна! Отправьте ссылку для обхода.", show_alert=True)
    
    elif action == "add_balance":
        user = user_data.get_user(user_id)
        text = f"💸 Баланс: **{user['balance']}⭐**\nВыберите сколько звёзд вы хотите положить на аккаунт:\n__Комиссия на боте__"
        keyboard = get_balance_keyboard()
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

# Обработчик выбора подписки
@dp.callback_query_handler(subscription_cb.filter())
async def process_subscription_callback(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    days = callback_data['days']
    price = callback_data['price']
    
    user = user_data.get_user(user_id)
    
    text = f"💸 Баланс: **{user['balance']}⭐**\nВы выбрали: **{days} дней**\nЦена: **{price}⭐**\nПодтвердите покупку:"
    keyboard = get_confirmation_keyboard("subscription", f"{days},{price}")
    
    await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()

# Обработчик выбора баланса
@dp.callback_query_handler(balance_cb.filter())
async def process_balance_callback(callback_query: types.CallbackQuery, callback_data: dict, state: FSMContext):
    user_id = callback_query.from_user.id
    amount = callback_data['amount']
    
    user = user_data.get_user(user_id)
    
    if amount == "custom":
        text = f"💸 Баланс: **{user['balance']}⭐**\nВведите сумму (мин 10):\n__Комиссия на боте__"
        keyboard = InlineKeyboardMarkup().add(
            InlineKeyboardButton("🚫 Отмена", callback_data=menu_cb.new(action="main_menu"))
        )
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await BalanceStates.waiting_for_custom_amount.set()
    else:
        text = f"💸 Баланс: **{user['balance']}⭐**\nПодтвердите покупку:"
        keyboard = get_confirmation_keyboard("balance", amount)
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

# Обработчик ввода своей суммы
@dp.message_handler(state=BalanceStates.waiting_for_custom_amount)
async def process_custom_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        amount = int(message.text)
        
        if amount < 10:
            await message.answer("❌ Неверное значение, минимальная сумма – 10⭐")
            return
        
        user = user_data.get_user(user_id)
        text = f"💸 Баланс: **{user['balance']}⭐**\nПодтвердите покупку:"
        keyboard = get_confirmation_keyboard("balance", str(amount))
        
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число")

# Обработчик подтверждения
@dp.callback_query_handler(confirm_cb.filter())
async def process_confirmation(callback_query: types.CallbackQuery, callback_data: dict):
    user_id = callback_query.from_user.id
    confirm_type = callback_data['type']
    value = callback_data['value']
    
    user = user_data.get_user(user_id)
    
    if confirm_type == "confirm_sub":
        days, price = value.split(',')
        price = int(price)
        
        if user['balance'] >= price:
            user['balance'] -= price
            user_data.set_subscription(user_id, int(days))
            
            await callback_query.answer(f"✅ Подписка на {days} дней активирована!", show_alert=True)
            
            is_active, end_date = user_data.get_subscription_status(user_id)
            time_left = format_subscription_time(end_date)
            
            text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
            keyboard = get_main_menu_keyboard(user_id)
            
            await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await callback_query.answer("❌ Недостаточно средств на балансе!", show_alert=True)
    
    elif confirm_type == "confirm_balance":
        amount = int(value)
        user['balance'] += amount
        
        await callback_query.answer(f"✅ Баланс пополнен на {amount}⭐!", show_alert=True)
        
        is_active, end_date = user_data.get_subscription_status(user_id)
        time_left = format_subscription_time(end_date)
        
        text = f"⚡ **LinkBypass**\nПодписка: **{time_left}**\nБаланс: **{user['balance']}⭐**"
        keyboard = get_main_menu_keyboard(user_id)
        
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    await callback_query.answer()

# ВЕБ-СЕРВЕР ДЛЯ RENDER
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running", 200

def run_web():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# Запускаем веб-сервер в отдельном потоке
threading.Thread(target=run_web, daemon=True).start()

# Запуск бота
async def main():
    logging.info("Бот запущен...")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
