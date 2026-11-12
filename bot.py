import os
import logging
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
import requests

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "9226822324:AAGlVD__eYZcPTrzEgSeNbHR2KoU5OTtA5k")
SPREADSHEET_ID = "11nvKladxDSIc1zqJtOQ0_M-KD_su-bR2a1bBQfsN894"

# ==================== ЛОГИ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== GOOGLE SHEETS ====================
def get_sheet():
    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.sheet1

def geocode_address(address: str):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{address}, Москва, Россия",
            "format": "json",
            "limit": 1
        }
        headers = {"User-Agent": "KartaBot/1.0 (personal use)"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", address)
        return None, None, None
    except Exception as e:
        logger.error(f"Geocoding error: {e}")
        return None, None, None

def normalize_status(text: str) -> str:
    t = text.lower().strip()
    if t in ["работаю", "working", "да", "yes", "work"]:
        return "working"
    if t in ["не работаю", "not", "нет", "no"]:
        return "not"
    if t in ["потенциал", "potential", "возможно"]:
        return "potential"
    return "other"

# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я бот для управления картой клиентов.\n\n"
        "Карта: https://immmka.github.io/khm/\n\n"
        "Команды:\n"
        "/add — добавить заведение\n"
        "/status — изменить статус\n"
        "/find — найти заведение\n"
        "/stats — статистика\n"
        "/list — последние 10 записей\n"
        "/map — ссылка на карту\n\n"
        "Пример добавления:\n"
        "/add Остерия Марио\n"
        "ул. Тверская, 12\n"
        "Хворостова"
    )
    await update.message.reply_text(text)

async def map_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Карта: https://immmka.github.io/khm/")

async def add_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.split("\n")
    name = lines[0].replace("/add", "").strip()
    address = lines[1].strip() if len(lines) > 1 else ""
    manager = lines[2].strip() if len(lines) > 2 else ""

    if not name:
        await update.message.reply_text(
            "Использование:\n/add Название заведения\nАдрес\nМенеджер\n\n"
            "Пример:\n/add Остерия Марио\nул. Тверская, 12\nХворостова"
        )
        return

    await update.message.reply_text("Ищу координаты по адресу...")

    lat, lng, full_address = None, None, address
    if address:
        lat, lng, full_address = geocode_address(address)

    try:
        sheet = get_sheet()
        row = [
            "",                     # id
            "",                     # ООО
            name,                   # Название заведения
            manager,                # Менеджер
            "potential",            # Status
            "",                     # Phone
            "",                     # Notes
            lat or "",              # lat
            lng or "",              # lng
            datetime.now().strftime("%Y-%m-%d %H:%M"),  # update
            address or ""           # address
        ]
        sheet.append_row(row)

        reply = f"Добавлено: *{name}*\n"
        if manager:
            reply += f"Менеджер: {manager}\n"
        if lat and lng:
            reply += f"Адрес: {full_address}\nКоординаты: {lat:.5f}, {lng:.5f}"
        else:
            reply += "Координаты не найдены. Добавь lat/lng вручную в таблице."
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"Ошибка при записи в таблицу:\n{e}")

async def change_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /status Название заведения статус\n\n"
            "Статусы: работаю / не работаю / потенциал"
        )
        return

    status_raw = context.args[-1]
    name_query = " ".join(context.args[:-1])
    new_status = normalize_status(status_raw)

    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        headers = sheet.row_values(1)

        status_col = headers.index("Status") + 1 if "Status" in headers else 5
        update_col = headers.index("update") + 1 if "update" in headers else 10

        found = False
        for i, row in enumerate(records, start=2):
            if name_query.lower() in str(row.get("Название заведения", "")).lower():
                sheet.update_cell(i, status_col, new_status)
                sheet.update_cell(i, update_col, datetime.now().strftime("%Y-%m-%d %H:%M"))
                await update.message.reply_text(
                    f"Статус обновлён:\n*{row.get('Название заведения')}* → {new_status}",
                    parse_mode="Markdown"
                )
                found = True
                break

        if not found:
            await update.message.reply_text("Заведение не найдено.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def find_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /find часть названия")
        return

    query = " ".join(context.args).lower()
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        results = [r for r in records if query in str(r.get("Название заведения", "")).lower()]

        if not results:
            await update.message.reply_text("Ничего не найдено.")
            return

        text = ""
        for r in results[:5]:
            text += (
                f"*{r.get('Название заведения', '—')}*\n"
                f"Статус: {r.get('Status', '—')}\n"
                f"Менеджер: {r.get('Менеджер', '—')}\n"
                f"Адрес: {r.get('address', '—')}\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        total = len(records)
        working = sum(1 for r in records if str(r.get("Status", "")).lower() == "working")
        not_working = sum(1 for r in records if str(r.get("Status", "")).lower() == "not")
        potential = sum(1 for r in records if str(r.get("Status", "")).lower() == "potential")
        with_coords = sum(1 for r in records if r.get("lat") and r.get("lng"))

        text = (
            f"Всего заведений: {total}\n"
            f"На карте: {with_coords}\n"
            f"Работаю: {working}\n"
            f"Не работаю: {not_working}\n"
            f"Потенциал: {potential}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def list_places(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        last = records[-10:][::-1]

        text = "Последние записи:\n\n"
        for r in last:
            text += f"• {r.get('Название заведения', '—')} ({r.get('Status', '—')})\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# ==================== ЗАПУСК ====================
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_place))
    application.add_handler(CommandHandler("status", change_status))
    application.add_handler(CommandHandler("find", find_place))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("list", list_places))
    application.add_handler(CommandHandler("map", map_link))

    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
