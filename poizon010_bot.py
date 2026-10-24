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

BOT_TOKEN = "8816444631:AAEj2JoljUfk2T2pg_VcxFRzlMMDaUINQnU"
ADMIN_ID = 1681547868  # Твой числовой ID в Telegram (узнать можно в @userinfobot)
CHANNEL_URL = "https://t.me/poizon010"  # Ссылка на твой канал

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


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ---
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
        # Дефолтные настройки, если база пустая
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


# --- КЛАВИАТУРЫ ---
def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧮 Рассчитать стоимость"), KeyboardButton(text="📦 Оформить заказ")],
            [KeyboardButton(text="📏 Таблица размеров"), KeyboardButton(text="⚖️ Примерный вес вещей")],
            [KeyboardButton(text="📍 Сменить город"), KeyboardButton(text="📢 Наш канал")]
        ],
        resize_keyboard=True
    )


def get_city_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Ижевск (Самовывоз / Без СДЭК)", callback_data="set_city_izhevsk")],
        [InlineKeyboardButton(text="🚚 Другой город РФ (Доставка СДЭК)", callback_data="set_city_other")]
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
        [InlineKeyboardButton(text=f"📊 Режим наценки: {mode_names.get(settings['margin_mode'])}", callback_data="adm_modes")],
        [InlineKeyboardButton(text="📈 Логи активности (посл. 10)", callback_data="adm_logs")],
        [InlineKeyboardButton(text="👥 Кол-во пользователей", callback_data="adm_users_count")]
    ])


def get_margin_modes_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. Демпинг: 0% на всё (чистая цена)", callback_data="set_mode_dumping")],
        [InlineKeyboardButton(text="2. Сарафан: 5% (<10k) / 3% (>10k)", callback_data="set_mode_low")],
        [InlineKeyboardButton(text="3. Стандарт: 10% (<10k) / 5% (>10k)", callback_data="set_mode_standard")],
        [InlineKeyboardButton(text="4. Ввести свой фиксированный %", callback_data="set_mode_custom")],
        [InlineKeyboardButton(text="« Назад", callback_data="adm_back")]
    ])


# --- СТАРТ И ВЫБОР ГОРОДА ---
@dp.message(CommandStart(), state="*")
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

    await log_action(user_id, "start", "Запустил бота")

    welcome_text = (
        "👋 **Добро пожаловать в POIZON 010!**\n\n"
        "Мы возим оригинальные вещи с китайских маркетплейсов (Poizon, Dewu, Taobao) "
        "по самым открытым тарифам без скрытых переплат.\n\n"
        f"📢 Наш канал с отзывами и подборками: [POIZON 010]({CHANNEL_URL})\n\n"
        "👉 **Для точного расчета выберите ваш город:**"
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

    city_info = (
        "✅ Город установлен: **Ижевск**\nВам доступен самовывоз / прямая передача (СДЭК оплачивать не нужно)."
        if city_name == "Ижевск" else
        "✅ Город установлен: **Регионы РФ**\nДоставка из Ижевска до вашего города осуществляется СДЭКом (оплата по тарифу при получении)."
    )

    await callback.message.edit_text(city_info, parse_mode="Markdown")
    await callback.message.answer("Используйте меню внизу для расчета и заказа 👇", reply_markup=get_main_kb())
    await callback.answer()


@dp.message(F.text == "📍 Сменить город", state="*")
async def change_city(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите ваш город:", reply_markup=get_city_kb())


# --- КАЛЬКУЛЯТОР ---
@dp.message(F.text == "🧮 Рассчитать стоимость", state="*")
async def start_calc(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CalcState.waiting_for_price)
    await message.answer("Введите **стоимость товара в юанях (¥)**:\n*(Например: 450)*", parse_mode="Markdown")


@dp.message(CalcState.waiting_for_price)
async def process_calc_price(message: Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        price_cny = float(text)
        if price_cny <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например: 680):")
        return

    await state.update_data(price_cny=price_cny)
    await state.set_state(CalcState.waiting_for_weight)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👟 Кроссовки (~1.3 кг)", callback_data="weight_1.3")],
        [InlineKeyboardButton(text="👕 Футболка / Лонг (~0.4 кг)", callback_data="weight_0.4")],
        [InlineKeyboardButton(text="🧥 Худи / Свитшот (~0.8 кг)", callback_data="weight_0.8")],
        [InlineKeyboardButton(text="🧥 Куртка / Пуховик (~1.8 кг)", callback_data="weight_1.8")]
    ])
    await message.answer("Выберите тип вещи или введите **вес в кг вручную** (например: 1.2):", reply_markup=kb)


@dp.callback_query(CalcState.waiting_for_weight, F.data.startswith("weight_"))
async def process_calc_weight_cb(callback: CallbackQuery, state: FSMContext):
    weight = float(callback.data.split("_")[1])
    await callback.answer()
    await finalize_calc(callback.message, state, weight, callback.from_user.id)


@dp.message(CalcState.waiting_for_weight)
async def process_calc_weight_msg(message: Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        weight = float(text)
        if weight <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректный вес в кг (например: 1.2):")
        return

    await finalize_calc(message, state, weight, message.from_user.id)


async def finalize_calc(message: Message, state: FSMContext, weight: float, user_id: int):
    data = await state.get_data()
    price_cny = data["price_cny"]
    await state.clear()

    settings = await get_settings()
    rate = settings["cny_rate"]
    deliv_rate = settings["delivery_per_kg"]
    mode = settings["margin_mode"]

    base_rub = price_cny * rate

    # Расчет процента
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

    # Проверка города
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT city FROM users WHERE user_id = ?", (user_id,))
        res = await cursor.fetchone()
        city = res[0] if res else "Ижевск"

    sdek_info = (
        "🟢 **Доставка по Ижевску:** 0 ₽ (Самовывоз / Встреча)"
        if city == "Ижевск" else
        "🚚 **Доставка до вашего города:** СДЭК (оплата по чеку при заборе из ПВЗ)"
    )

    result_text = (
        f"📊 **Расчет стоимости заказа:**\n\n"
        f"• Цена товара: **{price_cny} ¥** (~{round(base_rub)} ₽ по курсу {rate})\n"
        f"• Вес посылки: **{weight} кг**\n"
        f"• Международная доставка: **{round(delivery_rub)} ₽** ({int(deliv_rate)} ₽/кг)\n"
        f"• Комиссия сервиса: **{round(margin_rub)} ₽** ({int(percent * 100)}%)\n"
        f"───────────────────\n"
        f"💰 **ИТОГО К ОПЛАТЕ: {total_rub} ₽**\n\n"
        f"{sdek_info}\n\n"
        f"⚡ Чтобы заказать этот товар, нажмите кнопку «📦 Оформить заказ»."
    )

    await log_action(user_id, "calc", f"{price_cny}Y / {weight}kg -> {total_rub}RUB")
    await message.answer(result_text, parse_mode="Markdown")


# --- ОФОРМЛЕНИЕ ЗАКАЗА ---
@dp.message(F.text == "📦 Оформить заказ", state="*")
async def start_order(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderState.waiting_for_link)
    await message.answer(
        "📝 **Оформление заказа:**\n\n"
        "1. Отправьте ссылку на товар из Poizon / Dewu или скриншот карточки товара с ценой.\n"
        "*(Если ссылки нет — просто напишите название модели)*",
        parse_mode="Markdown"
    )


@dp.message(OrderState.waiting_for_link)
async def process_order_link(message: Message, state: FSMContext):
    await state.update_data(link=message.text or "Скриншот / фото")
    await state.set_state(OrderState.waiting_for_size)
    await message.answer("Укажите **размер** (в EUR или мм) или нужный цвет/параметр:")


@dp.message(OrderState.waiting_for_size)
async def process_order_size(message: Message, state: FSMContext):
    size = message.text
    data = await state.get_data()
    link = data.get("link", "Не указана")
    await state.clear()

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id: {user.id}"

    # Отправка заявки админу
    order_notification = (
        f"🔥 **НОВАЯ ЗАЯВКА НА ЗАКАЗ!**\n\n"
        f"👤 Клиент: {user.full_name} ({username})\n"
        f"🆔 ID: `{user.id}`\n"
        f"🔗 Ссылка/Товар: {link}\n"
        f"📏 Размер: {size}\n"
        f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await bot.send_message(ADMIN_ID, order_notification, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")

    await log_action(user.id, "order", f"Заказ: {link[:30]} | {size}")

    await message.answer(
        "✅ **Заявка принята!**\n\n"
        "Мы уже получили ваш запрос, проверим наличие нужного размера и свяжемся с вами в личных сообщениях для подтверждения.",
        parse_mode="Markdown"
    )


# --- СПРАВОЧНЫЕ РАЗДЕЛЫ ---
@dp.message(F.text == "📏 Таблица размеров", state="*")
async def sizes_info(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "📏 **Как правильно выбрать размер на Poizon:**\n\n"
        "1. Всегда ориентируйтесь на **длину стопы в миллиметрах (MM / JP)**.\n"
        "2. Европейские (EUR) и американские (US) размеры у брендов часто отличаются (Nike, Adidas, New Balance маломерят или большемерят по-разному).\n"
        "3. Достаньте стельку из вашей самой удобной обуви и измерьте её рулеткой от края до края.\n\n"
        "При заказе напишите длину вашей стельки — мы подберем точный размер."
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "⚖️ Примерный вес вещей", state="*")
async def weights_info(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "⚖️ **Средний вес категорий с упаковкой:**\n\n"
        "• Футболки, майки, шорты: **0.3 – 0.5 кг**\n"
        "• Лонгсливы, рубашки: **0.4 – 0.6 кг**\n"
        "• Свитшоты, худи, штаны: **0.7 – 1.0 кг**\n"
        "• Кроссовки низкие (Dunk, Air Force): **1.1 – 1.4 кг**\n"
        "• Кроссовки высокие / Ботинки: **1.4 – 1.8 кг**\n"
        "• Пуховики и зимние куртки: **1.5 – 2.2 кг**\n\n"
        "💡 *Вес включает коробку и фирменную упаковку Poizon.*"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "📢 Наш канал", state="*")
async def channel_info(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Следите за скидками, курсом и подборками в нашем канале:\n👉 [Перейти в POIZON 010]({CHANNEL_URL})",
        parse_mode="Markdown"
    )


# --- АДМИН-ПАНЕЛЬ ---
@dp.message(Command("admin"), state="*")
async def admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    settings = await get_settings()
    await message.answer("⚙️ **Панель управления POIZON 010**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")


@dp.callback_query(F.data == "adm_rate")
async def adm_change_rate(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_rate)
    await callback.message.answer("Введите новый **курс юаня в рублях** (например: 13.95):", parse_mode="Markdown")
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
        await message.answer(f"✅ Курс обновлен: **{val} ₽**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Введите число:")


@dp.callback_query(F.data == "adm_deliv")
async def adm_change_deliv(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminState.waiting_for_delivery_price)
    await callback.message.answer("Введите **стоимость доставки за 1 кг в рублях** (например: 1200):", parse_mode="Markdown")
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
        await message.answer("❌ Введите число:")


@dp.callback_query(F.data == "adm_modes")
async def adm_modes(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text("Выберите тариф наценки для привлечения аудитории:", reply_markup=get_margin_modes_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("set_mode_"))
async def adm_set_mode(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    mode = callback.data.replace("set_mode_", "")

    if mode == "custom":
        await state.set_state(AdminState.waiting_for_custom_percent)
        await callback.message.answer("Введите **единый процент наценки** (например: 7):", parse_mode="Markdown")
        await callback.answer()
        return

    await update_setting("margin_mode", mode)
    settings = await get_settings()
    await callback.message.edit_text("✅ Режим обновлен!", reply_markup=get_admin_kb(settings))
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
        await message.answer(f"✅ Установлен свой процент: **{val}%**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Введите число:")


@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    settings = await get_settings()
    await callback.message.edit_text("⚙️ **Панель управления POIZON 010**", reply_markup=get_admin_kb(settings), parse_mode="Markdown")
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

    report = f"👥 Всего пользователей: **{total}**\n\nПо городам:\n"
    for count, city in rows:
        report += f"• {city}: **{count}**\n"

    await callback.message.answer(report, parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data == "adm_logs")
async def adm_show_logs(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT user_id, action, details, created_at FROM logs ORDER BY id DESC LIMIT 10"
        )
        logs = await cursor.fetchall()

    if not logs:
        await callback.message.answer("Логов пока нет.")
        await callback.answer()
        return

    text = "📋 **Последние 10 действий:**\n\n"
    for uid, action, details, dt in logs:
        text += f"• `[{dt[11:16]}]` ID `{uid}`: **{action}** ({details})\n"

    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


# --- ЗАПУСК ---
async def main():
    await init_db()
    logging.info("База данных инициализирована. Запуск поллинга...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
