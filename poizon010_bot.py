import os
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
import aiosqlite
from aiohttp import web

BOT_TOKEN = "8816444631:AAEj2JoljUfk2T2pg_VcxFRzlMMDaUINQnU"
ADMIN_ID = 1681547868  # Твой числовой Telegram ID (узнать в @userinfobot)
CHANNEL_URL = "https://t.me/poizonstore010"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
DB_FILE = "poizon010.db"


# --- FSM СОСТОЯНИЯ ---
class CalcState(StatesGroup):
    waiting_for_price = State()
    waiting_for_weight = State()


class AdminState(StatesGroup):
    waiting_for_rate = State()
    waiting_for_delivery_price = State()
    waiting_for_custom_percent = State()


class OrderState(StatesGroup):
    waiting_for_link = State()
    waiting_for_size = State()


# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                city TEXT,
                registered_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                cny_rate REAL,
                delivery_per_kg REAL,
                margin_mode TEXT,
                custom_percent REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TEXT
            )
        """)
        cursor = await db.execute("SELECT id FROM settings WHERE id = 1")
        if not await cursor.fetchone():
            await db.execute("""
                INSERT INTO settings (id, cny_rate, delivery_per_kg, margin_mode, custom_percent)
                VALUES (1, 13.8, 1100.0, 'dumping', 0.0)
            """)
        await db.commit()


async def log_action(user_id: int, action: str, details: str = ""):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO logs (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()


async def get_settings():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT cny_rate, delivery_per_kg, margin_mode, custom_percent FROM settings WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            return {
                "cny_rate": row[0],
                "delivery_per_kg": row[1],
                "margin_mode": row[2],
                "custom_percent": row[3]
            }


async def update_setting(key: str, value):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(f"UPDATE settings SET {key} = ? WHERE id = 1", (value,))
        await db.commit()


# --- ГЛАВНЫЕ КЛАВИАТУРЫ ---
def get_main_kb(user_id: int):
    buttons = [
        [KeyboardButton(text="🧮 Рассчитать стоимость"), KeyboardButton(text="📦 Оформить заказ")],
        [KeyboardButton(text="📏 Таблица размеров"), KeyboardButton(text="⚖️ Примерный вес вещей")],
        [KeyboardButton(text="📍 Сменить город"), KeyboardButton(text="📢 Наш канал")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_city_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Ижевск (Самовывоз / Без СДЭК)", callback_data="set_city_izhevsk")],
        [InlineKeyboardButton(text="🚚 Другой город РФ (Доставка СДЭК)", callback_data="set_city_other")]
    ])


def get_sizes_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👟 Nike / Jordan", callback_data="size_nike"),
         InlineKeyboardButton(text="👟 Adidas / Yeezy", callback_data="size_adidas")],
        [InlineKeyboardButton(text="👟 New Balance", callback_data="size_nb"),
         InlineKeyboardButton(text="👕 Одежда (Верх/Низ)", callback_data="size_clothes")]
    ])


def get_admin_kb(settings):
    mode_names = {
        "dumping": "0% (Демпинг)",
        "low": "5% / 3% (Сарафан)",
        "standard": "10% / 5% (Стандарт)",
        "custom": f"Свой ({settings['custom_percent']}%)"
    }
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💱 Курс юаня: {settings['cny_rate']} ₽", callback_data="adm_rate")],
        [InlineKeyboardButton(text=f"✈️ Доставка/кг: {settings['delivery_per_kg']} ₽", callback_data="adm_deliv")],
        [InlineKeyboardButton(text=f"📊 Наценка: {mode_names.get(settings['margin_mode'])}", callback_data="adm_modes")],
        [InlineKeyboardButton(text="📈 Логи (посл. 10)", callback_data="adm_logs"),
         InlineKeyboardButton(text="👥 Пользователи", callback_data="adm_users_count")]
    ])


def get_margin_modes_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. Демпинг: 0% (Себестоимость)", callback_data="set_mode_dumping")],
        [InlineKeyboardButton(text="2. Сарафан: 5% (<10k) / 3% (>10k)", callback_data="set_mode_low")],
        [InlineKeyboardButton(text="3. Стандарт: 10% (<10k) / 5% (>10k)", callback_data="set_mode_standard")],
        [InlineKeyboardButton(text="4. Свой фиксированный %", callback_data="set_mode_custom")],
        [InlineKeyboardButton(text="« Назад в админку", callback_data="adm_back")]
    ])


# --- СТАРТ И ГОРОД ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT city FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        if not user:
            await db.execute(
                "INSERT INTO users (user_id, username, full_name, city, registered_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, message.from_user.username or "", message.from_user.full_name, "Не выбран", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()

    await log_action(user_id, "start", "Старт бота")

    welcome_text = (
        "👋 **Добро пожаловать в POIZON 010!**\n\n"
        "Прямая доставка оригинальных товаров с Poizon / Dewu / Taobao по минимальной себестоимости без наценок посредников.\n\n"
        f"📢 Официальный канал: [t.me/poizonstore010]({CHANNEL_URL})\n\n"
        "🔻 **Укажите ваш город для настройки тарифов доставки:**"
    )
    await message.answer(welcome_text, reply_markup=get_city_kb(), parse_mode="Markdown", disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("set_city_"))
async def cb_set_city(callback: CallbackQuery):
    city_type = callback.data.split("_")[-1]
    city_name = "Ижевск" if city_type == "izhevsk" else "Другой город РФ"

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET city = ? WHERE user_id = ?", (city_name, callback.from_user.id))
        await db.commit()

    await log_action(callback.from_user.id, "set_city", city_name)

    if city_name == "Ижевск":
        city_info = "🟢 **Город: Ижевск**\nДоставка внутри города — **0 ₽** (Самовывоз / Встреча)."
    else:
        city_info = "🚚 **Город: Регионы РФ**\nДоставка из Ижевска до вашего ПВЗ СДЭК оплачивается отдельно при получении."

    await callback.message.edit_text(city_info, parse_mode="Markdown")
    await callback.message.answer("Воспользуйтесь меню ниже для заказа и расчетов 👇", reply_markup=get_main_kb(callback.from_user.id))
    await callback.answer()


@dp.message(F.text == "📍 Сменить город")
async def change_city(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите ваш город:", reply_markup=get_city_kb())


# --- КАЛЬКУЛЯТОР СТОИМОСТИ ---
@dp.message(F.text == "🧮 Рассчитать стоимость")
async def start_calc(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CalcState.waiting_for_price)
    await message.answer(
        "💱 **Шаг 1 из 2:**\n\n"
        "Введите **стоимость товара в юанях (¥)** со страницы Poizon:\n"
        "*(Например: `450` или `1280`)*",
        parse_mode="Markdown"
    )


@dp.message(CalcState.waiting_for_price)
async def process_calc_price(message: Message, state: FSMContext):
    try:
        price_cny = float(message.text.replace(",", ".").strip())
        if price_cny <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную цену цифрами (например: `520`):")
        return

    await state.update_data(price_cny=price_cny)
    await state.set_state(CalcState.waiting_for_weight)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👟 Кроссовки (~1.3 кг)", callback_data="weight_1.3"),
         InlineKeyboardButton(text="👕 Футболка (~0.4 кг)", callback_data="weight_0.4")],
        [InlineKeyboardButton(text="🧥 Худи / Свитшот (~0.8 кг)", callback_data="weight_0.8"),
         InlineKeyboardButton(text="🧥 Куртка / Пуховик (~1.8 кг)", callback_data="weight_1.8")]
    ])
    await message.answer(
        "⚖️ **Шаг 2 из 2:**\n\n"
        "Выберите стандартную категорию или отправьте **точный вес в кг вручную** (например: `0.6` или `1.4`):",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@dp.callback_query(CalcState.waiting_for_weight, F.data.startswith("weight_"))
async def process_calc_weight_cb(callback: CallbackQuery, state: FSMContext):
    weight = float(callback.data.split("_")[1])
    await callback.answer()
    await finalize_calc(callback.message, state, weight, callback.from_user.id)


@dp.message(CalcState.waiting_for_weight)
async def process_calc_weight_msg(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", ".").strip())
        if weight <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите вес числом (например: `1.2`):")
        return
    await finalize_calc(message, state, weight, message.from_user.id)


async def finalize_calc(message: Message, state: FSMContext, weight: float, user_id: int):
    data = await state.get_data()
    price_cny = data["price_cny"]
    await state.clear()

    settings = await get_settings()
    rate, deliv_rate, mode = settings["cny_rate"], settings["delivery_per_kg"], settings["margin_mode"]
    base_rub = price_cny * rate

    percent = 0.0
    if mode == "dumping":
        percent = 0.0
    elif mode == "low":
        percent = 0.03 if base_rub >= 10000 else 0.05
    elif mode == "standard":
        percent = 0.05 if base_rub >= 10000 else 0.10
    elif mode == "custom":
        percent = settings["custom_percent"] / 100.0

    margin_rub = base_rub * percent
    delivery_rub = weight * deliv_rate
    total_rub = round(base_rub + margin_rub + delivery_rub)

    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT city FROM users WHERE user_id = ?", (user_id,))
        res = await cursor.fetchone()
        city = res[0] if res else "Ижевск"

    sdek_badge = (
        "🟢 **Доставка по Ижевску:** 0 ₽ (Личная встреча / Самовывоз)"
        if city == "Ижевск" else
        "🚚 **Доставка СДЭК:** оплачивается отдельно при заборе посылки"
    )

    result = (
        f"📋 **Детализация стоимости:**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• Цена товара: **{price_cny} ¥** (~{round(base_rub)} ₽ по курсу {rate})\n"
        f"• Расчетный вес: **{weight} кг**\n"
        f"• Доставка в РФ: **{round(delivery_rub)} ₽** ({int(deliv_rate)} ₽/кг)\n"
        f"• Наша комиссия: **{round(margin_rub)} ₽** ({int(percent * 100)}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 **ИТОГО: {total_rub} ₽**\n\n"
        f"{sdek_badge}\n\n"
        f"Чтобы зафиксировать заказ по этой цене, нажмите **«📦 Оформить заказ»**."
    )
    await log_action(user_id, "calc", f"{price_cny}Y/{weight}kg -> {total_rub}RUB")
    await message.answer(result, parse_mode="Markdown")


# --- ОФОРМЛЕНИЕ ЗАКАЗА ---
@dp.message(F.text == "📦 Оформить заказ")
async def start_order(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderState.waiting_for_link)
    await message.answer(
        "📝 **Оформление заказа:**\n\n"
        "Отправьте **ссылку** на товар из Poizon/Dewu или название модели/скриншот:",
        parse_mode="Markdown"
    )


@dp.message(OrderState.waiting_for_link)
async def process_order_link(message: Message, state: FSMContext):
    await state.update_data(link=message.text or "Скриншот / фото")
    await state.set_state(OrderState.waiting_for_size)
    await message.answer(
        "Укажите **размер** (в EUR / US или мм) и желаемый цвет:",
        parse_mode="Markdown"
    )


@dp.message(OrderState.waiting_for_size)
async def process_order_size(message: Message, state: FSMContext):
    size = message.text
    data = await state.get_data()
    link = data.get("link", "Не указана")
    await state.clear()

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id: {user.id}"

    order_notification = (
        f"🔥 **НОВАЯ ЗАЯВКА POIZON 010!**\n\n"
        f"👤 Клиент: {user.full_name} ({username})\n"
        f"🆔 ID: `{user.id}`\n"
        f"🔗 Товар / Ссылка: {link}\n"
        f"📏 Размер: {size}\n"
        f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await bot.send_message(ADMIN_ID, order_notification, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Сбой отправки заявки админу: {e}")

    await log_action(user.id, "order", f"Заказ: {link[:25]} | {size}")
    await message.answer(
        "✅ **Заявка успешно принята!**\n\n"
        "Мы проверим актуальную цену и наличие размера на Poizon, после чего свяжемся с вами в Telegram для подтверждения.",
        parse_mode="Markdown"
    )


# --- ТАБЛИЦА РАЗМЕРОВ ---
@dp.message(F.text == "📏 Таблица размеров")
async def sizes_menu(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "📏 **Интерактивная таблица размеров:**\n\n"
        "На Poizon европейские и американские размеры разных брендов часто не совпадают.\n"
        "Самый точный ориентир — **длина стопы в сантиметрах (CM) или миллиметрах (MM)**.\n\n"
        "Выберите интересующий бренд или категорию одежды 👇"
    )
    await message.answer(text, reply_markup=get_sizes_kb(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("size_"))
async def cb_sizes_detail(callback: CallbackQuery):
    cat = callback.data.replace("size_", "")
    if cat == "nike":
        text = (
            "👟 **Размерная сетка Nike / Jordan (Мужская):**\n"
            "```text\n"
            "EUR   | US    | Стелька (CM)\n"
            "40    | 7.0   | 25.0 см\n"
            "41    | 8.0   | 26.0 см\n"
            "42    | 8.5   | 26.5 см\n"
            "42.5  | 9.0   | 27.0 см\n"
            "43    | 9.5   | 27.5 см\n"
            "44    | 10.0  | 28.0 см\n"
            "44.5  | 10.5  | 28.5 см\n"
            "45    | 11.0  | 29.0 см\n"
            "```\n"
            "💡 *Nike обычно идут размер в размер (TTS).* Достаньте свою стельку и замерьте её рулеткой."
        )
    elif cat == "adidas":
        text = (
            "👟 **Размерная сетка Adidas / Yeezy:**\n"
            "```text\n"
            "EUR   | US    | Стелька (CM)\n"
            "40    | 7.0   | 24.5 см\n"
            "40.7  | 7.5   | 25.0 см\n"
            "41.3  | 8.0   | 25.5 см\n"
            "42    | 8.5   | 26.0 см\n"
            "42.7  | 9.0   | 26.5 см\n"
            "43.3  | 9.5   | 27.0 см\n"
            "44    | 10.0  | 27.5 см\n"
            "44.7  | 10.5  | 28.0 см\n"
            "```\n"
            "💡 *Yeezy 350 / 700 / Slide почти всегда маломерят — берите на 0.5–1 размер больше!*"
        )
    elif cat == "nb":
        text = (
            "👟 **Размерная сетка New Balance:**\n"
            "```text\n"
            "EUR   | US    | Стелька (CM)\n"
            "40    | 7.0   | 25.0 см\n"
            "40.5  | 7.5   | 25.5 см\n"
            "41.5  | 8.0   | 26.0 см\n"
            "42    | 8.5   | 26.5 см\n"
            "42.5  | 9.0   | 27.0 см\n"
            "43    | 9.5   | 27.5 см\n"
            "44    | 10.0  | 28.0 см\n"
            "44.5  | 10.5  | 28.5 см\n"
            "```\n"
            "💡 *Модели 1906R, 2002R, 530 идут строго по сетке в сантиметрах.*"
        )
    else:
        text = (
            "👕 **Одежда (Футболки, Худи, Брюки):**\n"
            "```text\n"
            "Размер | Рост (см)  | Вес (кг)\n"
            "S      | 165 - 172  | 55 - 65\n"
            "M      | 170 - 178  | 65 - 75\n"
            "L      | 175 - 183  | 75 - 85\n"
            "XL     | 180 - 188  | 85 - 95\n"
            "XXL    | 185 - 195  | 95 - 110\n"
            "```\n"
            "💡 *Азиатские бренды могут маломерить на размер. Всегда уточняйте длину по спине и ширину груди.*"
        )

    await callback.message.edit_text(text, reply_markup=get_sizes_kb(), parse_mode="Markdown")
    await callback.answer()


# --- ПРИМЕРНЫЙ ВЕС ТОВАРОВ ---
@dp.message(F.text == "⚖️ Примерный вес вещей")
async def weights_info(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "⚖️ **Средний вес товаров (с учетом фирменной коробки Poizon):**\n\n"
        "👟 **Обувь:**\n"
        "• Легкие кеды / Слипоны / Тапки: **~0.8 – 1.0 кг**\n"
        "• Низкие кроссовки (Dunk, Air Force, Samba): **~1.2 – 1.4 кг**\n"
        "• Массивные / Высокие кроссовки (Jordan 4, Bape): **~1.5 – 1.8 кг**\n"
        "• Ботинки / Зимняя обувь: **~1.8 – 2.3 кг**\n\n"
        "👕 **Одежда:**\n"
        "• Футболки, шорты: **~0.3 – 0.5 кг**\n"
        "• Лонгсливы, рубашки, тонкие свитшоты: **~0.5 – 0.7 кг**\n"
        "• Плотные худи (Оверсайз / 450+ gsm), джинсы: **~0.8 – 1.1 кг**\n"
        "• Демисезонные куртки, ветровки: **~1.0 – 1.4 кг**\n"
        "• Зимние пуховики: **~1.6 – 2.4 кг**\n\n"
        "🎒 **Аксессуары:**\n"
        "• Кепки, шапки, ремни, носки: **~0.2 – 0.4 кг**\n"
        "• Рюкзаки, спортивные сумки: **~0.8 – 1.3 кг**\n\n"
        "💡 *Вес уже учитывает сертификационную коробку и пластиковые пломбы Poizon.*"
    )
    await message.answer(text, parse_mode="Markdown")


# --- КАНАЛ ---
@dp.message(F.text == "📢 Наш канал")
async def channel_info(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Подписывайтесь на наш канал с подборками и отзывами:\n👉 [POIZON 010]({CHANNEL_URL})",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


# --- ПОЛНОЦЕННОЕ УПРАВЛЕНИЕ АДМИНОМ ---
@dp.message(F.text == "👑 Админ-панель")
@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    settings = await get_settings()
    await message.answer("👑 **Панель управления POIZON 010**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")


@dp.callback_query(F.data == "adm_rate")
async def adm_change_rate(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_rate)
    await callback.message.answer("Введите новый **курс юаня** (например: `13.95`):", parse_mode="Markdown")
    await callback.answer()


@dp.message(AdminState.waiting_for_rate)
async def adm_save_rate(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        val = float(message.text.replace(",", "."))
        await update_setting("cny_rate", val)
        await state.clear()
        settings = await get_settings()
        await message.answer(f"✅ Курс юаня обновлен: **{val} ₽**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Введите число цифрами:")


@dp.callback_query(F.data == "adm_deliv")
async def adm_change_deliv(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_delivery_price)
    await callback.message.answer("Введите **стоимость доставки за 1 кг в рублях** (например: `1150`):", parse_mode="Markdown")
    await callback.answer()


@dp.message(AdminState.waiting_for_delivery_price)
async def adm_save_deliv(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        val = float(message.text.replace(",", "."))
        await update_setting("delivery_per_kg", val)
        await state.clear()
        settings = await get_settings()
        await message.answer(f"✅ Доставка обновлена: **{val} ₽/кг**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Введите число цифрами:")


@dp.callback_query(F.data == "adm_modes")
async def adm_modes(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("⚙️ **Выберите схему наценки бота:**", reply_markup=get_margin_modes_kb(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("set_mode_"))
async def adm_set_mode(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    mode = callback.data.replace("set_mode_", "")
    if mode == "custom":
        await state.set_state(AdminState.waiting_for_custom_percent)
        await callback.message.answer("Введите **фиксированный процент наценки** (например: `6`):", parse_mode="Markdown")
        await callback.answer()
        return

    await update_setting("margin_mode", mode)
    settings = await get_settings()
    await callback.message.edit_text("✅ Схема наценки обновлена!", reply_markup=get_admin_kb(settings))
    await callback.answer()


@dp.message(AdminState.waiting_for_custom_percent)
async def adm_save_custom_percent(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        val = float(message.text.replace(",", "."))
        await update_setting("margin_mode", "custom")
        await update_setting("custom_percent", val)
        await state.clear()
        settings = await get_settings()
        await message.answer(f"✅ Установлен персональный тариф: **{val}%**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Введите число:")


@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    settings = await get_settings()
    await callback.message.edit_text("👑 **Панель управления POIZON 010**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data == "adm_users_count")
async def adm_users_count(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT COUNT(*), city FROM users GROUP BY city")
        rows = await cursor.fetchall()
        total_cursor = await db.execute("SELECT COUNT(*) FROM users")
        total = (await total_cursor.fetchone())[0]

    report = f"👥 Всего пользователей в базе: **{total}**\n\nРаспределение по городам:\n"
    for cnt, city in rows:
        report += f"• {city}: **{cnt} чел.**\n"

    await callback.message.answer(report, parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data == "adm_logs")
async def adm_show_logs(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT user_id, action, details, created_at FROM logs ORDER BY id DESC LIMIT 10")
        logs = await cursor.fetchall()

    if not logs:
        await callback.message.answer("Логов пока нет.")
        await callback.answer()
        return

    text = "📋 **Последние 10 действий пользователей:**\n\n"
    for u, a, d, dt in logs:
        text += f"• `[{dt[11:16]}]` ID `{u}`: **{a}** ({d})\n"

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


# --- ВЕБ-СЕРВЕР И ЗАПУСК ---
async def handle_ping(request):
    return web.Response(text="Poizon 010 Bot is running!")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    while True:
        await asyncio.sleep(3600)


async def main():
    await init_db()
    logging.info("База данных готова. Запуск сервиса...")
    asyncio.create_task(run_web_server())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
