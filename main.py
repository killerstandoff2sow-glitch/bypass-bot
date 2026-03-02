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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.callback_data import CallbackData

# Токен бота
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Кнопки
menu_cb = CallbackData("menu", "action")

# Временное хранилище
user_data = {}

# Список рабочих API для обхода (несколько на случай если один не работает)
API_LIST = [
    "https://bypassunlock.com/api",
    "https://api.bypass.vip/",
    "https://bypass.bid/api",
    "https://bypass.pm/bypass"
]

async def bypass_link(url):
    """Пытается обойти ссылку через разные API"""
    for api_url in API_LIST:
        try:
            params = {"url": url}
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "bypassed_url" in data:
                            return True, data["bypassed_url"]
                        elif "url" in data:
                            return True, data["url"]
                        elif "result" in data:
                            return True, data["result"]
        except:
            continue
    
    # Если все API не сработали, пробуем прямой метод
    try:
        # Альтернативный метод через прямой запрос
        bypass_sites = [
            f"https://bypass.pm/bypass?url={url}",
            f"https://bypass.bid/api?url={url}"
        ]
        async with aiohttp.ClientSession() as session:
            for site in bypass_sites:
                try:
                    async with session.get(site, timeout=10) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            # Ищем ссылку в ответе
                            urls = re.findall(r'https?://[^\s"\'<>]+', text)
                            if urls:
                                return True, urls[0]
                except:
                    continue
    except:
        pass
    
    return False, "Не удалось обойти ссылку"

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    # Инициализация пользователя
    if user_id not in user_data:
        user_data[user_id] = {
            'bypass_count': 0,
            'last_bypass': None
        }
    
    text = (
        "🔗 **Bypass Bot**\n\n"
        "Отправь мне ссылку с Linkvertise, Lootlinks, Workink и я обойду её!\n\n"
        "Просто отправь ссылку и получи прямой линк."
    )
    
    await message.answer(text, parse_mode="Markdown")

@dp.message_handler()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Проверяем, похоже ли на ссылку
    url_pattern = re.compile(r'https?://[^\s]+')
    if not url_pattern.match(text):
        await message.answer("❌ Отправьте **ссылку** для обхода", parse_mode="Markdown")
        return
    
    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer("🔄 **Обрабатываю ссылку...**", parse_mode="Markdown")
    
    # Пробуем обойти
    success, result = await bypass_link(text)
    
    if success:
        await processing_msg.edit_text(
            f"✅ **Готово!**\n\n{result}",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await processing_msg.edit_text(
            f"❌ **Не удалось обойти**\n\n{result}",
            parse_mode="Markdown"
        )

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
    asyncio.run(main())cio.run(main())
