import os
import logging
import json
import csv
import io
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
import requests

# ==================== КОНСТАНТЫ ====================
MANAGERS = ["Дима б", "Лера", "Алина", "Дима ч", "Антон", "Артур", "Александр"]
CATEGORIES = ["Ресторан", "Бар", "Клуб", "Лаундж"]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не задан в переменных окружения")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "11nvKladxDSIc1zqJtOQ0_M-KD_su-bR2a1bBQfsN894")

# ==================== ЛОГИ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== GOOGLE SHEETS ====================
def get_spreadsheet():
    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def get_all_records():
    spreadsheet = get_spreadsheet()
    all_records = []
    for ws in spreadsheet.worksheets():
        try:
            records = ws.get_all_records()
            for r in records:
                r["_sheet"] = ws.title
                all_records.append(r)
        except Exception as e:
            logger.error(f"Ошибка чтения листа {ws.title}: {e}")
            continue
    return all_records

def get_sheet():
    return get_spreadsheet().sheet1

def geocode_address(address: str):
    YANDEX_KEY = os.getenv("YANDEX_GEOCODER_KEY", "YOUR_YANDEX_KEY")
    if YANDEX_KEY and YANDEX_KEY != "YOUR_YANDEX_KEY":
        try:
            url = "https://geocode-maps.yandex.ru/1.x/"
            params = {
                "apikey": YANDEX_KEY,
                "geocode": f"{address}, Москва",
                "format": "json",
                "results": 1
            }
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            pos = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]["Point"]["pos"]
            lng, lat = map(float, pos.split())
            text = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]["metaDataProperty"]["GeocoderMetaData"]["text"]
            return lat, lng, text
        except Exception as e:
            logger.error(f"Yandex geocode error: {e}")

    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{address}, Москва, Россия",
            "format": "json",
            "limit": 1
        }
        headers = {"User-Agent": "KartaBot/1.0"}
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
    if t in ["неизвестно", "unknown", "other", "не известно"]:
        return "unknown"
    return "unknown"

# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("➕ Добавить"), KeyboardButton("🔄 Статус")],
        [KeyboardButton("🔍 Найти"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("🗑 Удалить"), KeyboardButton("📋 Последние 10")],
        [KeyboardButton("🔥 Горячие"), KeyboardButton("📝 Заметка")],
        [KeyboardButton("🗺 Карта"), KeyboardButton("📍 Рядом")],
        [KeyboardButton("👤 Менеджер"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("📥 Экспорт CSV"), KeyboardButton("⏳ Просроченные")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    text = "Привет! Я бот для управления картой клиентов.\n\nКарта: https://immmka.github.io/khm/\n\nВыбери действие кнопками ниже:"
    await update.message.reply_text(text, reply_markup=reply_markup)

async def map_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🗺 Открыть интерактивную карту клиентов:\nhttps://immmka.github.io/khm/")

async def hot_places(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        records = get_all_records()
        hot = [r for r in records if str(r.get("Status") or "").lower() in ["potential", "working", "потенциал", "работаю"]]
        if not hot:
            await update.message.reply_text("Горячих точек нет.")
            return
        text = f"Горячие точки ({len(hot)}):\n\n"
        for r in hot[:20]:
            text += f"• {r.get('Название заведения', '—')} — {r.get('Status', '—')}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def quick_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if " — " not in text and " - " not in text:
        await update.message.reply_text("Формат: Название — текст заметки\nПример: Rizz — договорились на пятницу")
        return
    sep = " — " if " — " in text else " - "
    name, note = text.split(sep, 1)
    name = name.strip()
    note = note.strip()
    try:
        spreadsheet = get_spreadsheet()
        for ws in spreadsheet.worksheets():
            records = ws.get_all_records()
            headers = ws.row_values(1)
            name_col = next((i for i, h in enumerate(headers) if "название" in h.lower()), None)
            notes_col = next((i for i, h in enumerate(headers) if "notes" in h.lower() or "заметк" in h.lower()), None)
            if name_col is None:
                continue
            for i, row in enumerate(records, start=2):
                if name.lower() in str(row.get(headers[name_col]) or "").lower():
                    old = str(row.get(headers[notes_col]) or "") if notes_col is not None else ""
                    new_note = (old + "\n" + note).strip() if old else note
                    if notes_col is not None:
                        ws.update_cell(i, notes_col + 1, new_note)
                    await update.message.reply_text(f"✅ Заметка добавлена к «{row.get(headers[name_col])}»")
                    return
        await update.message.reply_text("Заведение не найдено.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def near_places(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь свою геолокацию (скрепка → Геопозиция), и я покажу ближайшие заведения.")
    context.user_data["awaiting_location"] = True

async def manager_places(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str = None):
    if not query and not context.args:
        await update.message.reply_text("Использование: /manager Имя\nПример: /manager Лера")
        return
    name = " ".join(context.args).lower() if not query else query.lower()
    try:
        records = get_all_records()
        results = [r for r in records if name in str(r.get("Менеджер") or r.get("менеджер") or "").lower()]
        if not results:
            await update.message.reply_text("Ничего не найдено.")
            return
        text = f"Заведения менеджера ({len(results)}):\n\n"
        for r in results[:15]:
            text += f"• {r.get('Название заведения', '—')} ({r.get('Status', '—')})\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_location"):
        return
    context.user_data["awaiting_location"] = False
    loc = update.message.location
    try:
        records = get_all_records()
        nearby = []
        for r in records:
            try:
                lat = float(r.get("lat") or 0)
                lng = float(r.get("lng") or 0)
                if not lat or not lng:
                    continue
                dist = ((lat - loc.latitude)**2 + (lng - loc.longitude)**2)**0.5
                if dist < 0.02:
                    nearby.append((dist, r))
            except:
                continue
        nearby.sort(key=lambda x: x[0])
        if not nearby:
            await update.message.reply_text("Рядом ничего не найдено.")
            return
        text = "Ближайшие заведения:\n\n"
        for dist, r in nearby[:8]:
            text += f"• {r.get('Название заведения', '—')} ({r.get('Status', '—')}) ~{int(dist * 111)} км\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_add"):
        await update.message.reply_text("Сначала нажми «➕ Добавить» и отправь данные, потом фото.")
        return
    photo = update.message.photo[-1]
    context.user_data["pending_photo"] = photo.file_id
    await update.message.reply_text("Фото получено. Теперь отправь текст с данными заведения.")

async def add_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text.startswith("/add"):
        text = text[4:].strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 2:
        await update.message.reply_text("Отправь данные в формате:\n\nНазвание\nАдрес\nМенеджер\nКатегория\nТелефон\nЗаметки\n\nМожно сразу приложить фото.")
        return

    name = lines[0]
    address = lines[1] if len(lines) > 1 else ""
    manager = lines[2].strip() if len(lines) > 2 else context.user_data.pop("add_manager", "")
    category = lines[3].strip() if len(lines) > 3 else context.user_data.pop("add_category", "Бар")
    phone = lines[4].strip() if len(lines) > 4 else ""
    notes = lines[5].strip() if len(lines) > 5 else ""
    photo_id = context.user_data.pop("pending_photo", "")

    await update.message.reply_text("Ищу координаты по адресу...")
    lat, lng, full_address = geocode_address(address) if address else (None, None, None)

    try:
        sheet = get_sheet()
        headers = sheet.row_values(1)
        row = [""] * len(headers)
        def set_col(name_variants, value):
            for i, h in enumerate(headers):
                if any(v.lower() in h.lower() for v in name_variants):
                    row[i] = value
                    return

        set_col(["название заведения", "название"], name)
        set_col(["менеджер"], manager)
        set_col(["категория", "category"], category)
        set_col(["status"], "potential")
        set_col(["phone", "телефон", "номер"], phone)
        set_col(["notes", "заметки"], notes)
        set_col(["address", "адрес"], full_address or address)
        set_col(["lat", "широта"], str(lat) if lat else "")
        set_col(["lng", "долгота"], str(lng) if lng else "")
        set_col(["фото", "photo", "image"], photo_id)
        set_col(["update"], datetime.now().strftime("%Y-%m-%d %H:%M"))
        sheet.append_row(row)
        
        reply = f"✅ Добавлено: *{name}*\n"
        if category: reply += f"Категория: {category}\n"
        if manager: reply += f"Менеджер: {manager}\n"
        if lat and lng: reply += f"Адрес: {full_address}\nКоординаты: {lat:.5f}, {lng:.5f}\n"
        else: reply += "Координаты не найдены.\n"
        reply += f"Фото: {'да' if photo_id else 'нет'}"
        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Add error: {e}")
        await update.message.reply_text(f"Ошибка при добавлении: {e}")

async def change_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /status Название заведения статус\n\nСтатусы: работаю / не работаю / потенциал / неизвестно")
        return
    status_raw = context.args[-1]
    name_query = " ".join(context.args[:-1]).lower()
    new_status = normalize_status(status_raw)
    try:
        spreadsheet = get_spreadsheet()
        found = False
        for ws in spreadsheet.worksheets():
            records = ws.get_all_records()
            headers = ws.row_values(1)
            name_col = next((i for i, h in enumerate(headers) if "название" in h.lower()), None)
            status_col = next((i for i, h in enumerate(headers) if h.lower() in ["status", "статус"]), None)
            update_col = next((i for i, h in enumerate(headers) if h.lower() == "update"), None)
            if name_col is None:
                continue
            for i, row in enumerate(records, start=2):
                row_name = str(row.get(headers[name_col]) or "").lower()
                if name_query in row_name:
                    if status_col is not None:
                        ws.update_cell(i, status_col + 1, new_status)
                    if update_col is not None:
                        ws.update_cell(i, update_col + 1, datetime.now().strftime("%Y-%m-%d %H:%M"))
                    await update.message.reply_text(f"Статус обновлён:\n*{row.get(headers[name_col])}* → {new_status}", parse_mode="Markdown")
                    found = True
                    break
            if found:
                break
        if not found:
            await update.message.reply_text("Заведение не найдено.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def find_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).lower().strip() if context.args else ""
    if not query:
        await update.message.reply_text("Напиши часть названия для поиска.")
        return
    try:
        records = get_all_records()
        results = []
        for r in records:
            name = str(r.get("Название заведения") or r.get("название") or r.get("Название") or "").lower()
            if query in name:
                results.append(r)
        if not results:
            await update.message.reply_text("Ничего не найдено.")
            return
        text = ""
        for r in results[:5]:
            name = r.get("Название заведения") or r.get("название") or "—"
            status = r.get("Status") or r.get("status") or "—"
            manager = r.get("Менеджер") or r.get("менеджер") or "—"
            address = r.get("address") or r.get("Адрес") or r.get("адрес") or "—"
            sheet_name = r.get("_sheet", "—")
            text += f"*{name}*\nСтатус: {status}\nМенеджер: {manager}\nАдрес: {address}\nЛист: {sheet_name}\n\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Find error: {e}")
        await update.message.reply_text(f"Ошибка поиска: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        records = get_all_records()
        total = len(records)
        working = sum(1 for r in records if str(r.get("Status", "")).lower() in ["working", "работаю"])
        not_working = sum(1 for r in records if str(r.get("Status", "")).lower() in ["not", "не работаю"])
        potential = sum(1 for r in records if str(r.get("Status", "")).lower() in ["potential", "потенциал"])
        with_coords = sum(1 for r in records if r.get("lat") and r.get("lng"))
        text = f"📊 Статистика:\nВсего заведений: {total}\nНа карте (с коорд.): {with_coords}\nРаботаю: {working}\nНе работаю: {not_working}\nПотенциал: {potential}"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def list_places(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        records = get_all_records()
        last = records[-10:][::-1]
        text = "Последние записи:\n\n"
        for r in last:
            text += f"• {r.get('Название заведения', '—')} ({r.get('Status', '—')}) [Лист: {r.get('_sheet', '—')}]\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def delete_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /delete Название заведения")
        return
    name_query = " ".join(context.args).lower()
    try:
        spreadsheet = get_spreadsheet()
        found_row = None
        found_sheet = None
        found_name = None
        for ws in spreadsheet.worksheets():
            records = ws.get_all_records()
            headers = ws.row_values(1)
            name_col = next((i for i, h in enumerate(headers) if "название" in h.lower()), None)
            if name_col is None:
                continue
            for i, row in enumerate(records, start=2):
                row_name = str(row.get(headers[name_col]) or "").lower()
                if name_query in row_name:
                    found_row = i
                    found_sheet = ws
                    found_name = row.get(headers[name_col])
                    break
            if found_row:
                break
        if not found_row:
            await update.message.reply_text("Заведение не найдено.")
            return
        await update.message.reply_text(f"Точно удалить *{found_name}*?\n\nНапиши «да» для подтверждения.", parse_mode="Markdown")
        context.user_data["pending_delete"] = found_row
        context.user_data["pending_name"] = found_name
        context.user_data["pending_sheet"] = found_sheet.title
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Генерирую CSV файл, подожди...")
        spreadsheet = get_spreadsheet()
        output = io.StringIO()
        writer = csv.writer(output)
        for ws in spreadsheet.worksheets():
            records = ws.get_all_records()
            if not records:
                continue
            writer.writerow([f"=== ЛИСТ: {ws.title} ==="])
            writer.writerow(records[0].keys())
            for r in records:
                writer.writerow(r.values())
            writer.writerow([])
        output.seek(0)
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        await update.message.reply_document(
            document=io.BytesIO(output.getvalue().encode('utf-8-sig')),
            filename=filename
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка экспорта: {e}")

async def stale_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Ищу просроченные записи...")
        records = get_all_records()
        stale = []
        now = datetime.now()
        for r in records:
            update_str = str(r.get("update") or r.get("Update") or "")
            if not update_str:
                continue
            try:
                update_date = datetime.strptime(update_str, "%Y-%m-%d %H:%M")
                days = (now - update_date).days
                if days > 30:
                    stale.append((days, r))
            except ValueError:
                continue
        stale.sort(key=lambda x: x[0], reverse=True)
        if not stale:
            await update.message.reply_text("✅ Нет просроченных записей (все обновлены за последние 30 дней).")
            return
        text = f"⏳ Просроченные записи ({len(stale)}):\n\n"
        for days, r in stale[:15]:
            name = r.get("Название заведения") or r.get("название") or "—"
            manager = r.get("Менеджер") or r.get("менеджер") or "—"
            sheet = r.get("_sheet", "—")
            text += f"• {name} ({manager}) — {days} дн. [Лист: {sheet}]\n"
        if len(stale) > 15:
            text += f"\n...и ещё {len(stale) - 15} записей."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if context.user_data.get("pending_delete") and text.lower() in ["да", "yes", "удалить"]:
        try:
            spreadsheet = get_spreadsheet()
            sheet_name = context.user_data.get("pending_sheet")
            sheet = spreadsheet.worksheet(sheet_name) if sheet_name else spreadsheet.sheet1
            row_idx = context.user_data["pending_delete"]
            name = context.user_data["pending_name"]
            sheet.delete_rows(row_idx)
            await update.message.reply_text(f"🗑 Удалено: {name}")
        except Exception as e:
            await update.message.reply_text(f"Ошибка при удалении: {e}")
        finally:
            context.user_data.pop("pending_delete", None)
            context.user_data.pop("pending_name", None)
            context.user_data.pop("pending_sheet", None)
        return

    if text == "➕ Добавить":
        keyboard = [[KeyboardButton(m)] for m in MANAGERS]
        keyboard.append([KeyboardButton("Пропустить")])
        await update.message.reply_text("Выбери менеджера:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
        context.user_data["awaiting_manager"] = True
        return
    if text == "🔄 Статус":
        await update.message.reply_text("Напиши: Название заведения статус\n\nПример: Rizz работаю")
        context.user_data["awaiting_status"] = True
        return
    if text == "🔍 Найти":
        await update.message.reply_text("Напиши часть названия для поиска.")
        context.user_data["awaiting_find"] = True
        return
    if text == "📊 Статистика":
        await stats(update, context)
        return
    if text == "🗑 Удалить":
        await update.message.reply_text("Напиши название заведения, которое хочешь удалить.")
        context.user_data["awaiting_delete"] = True
        return
    if text == "📋 Последние 10":
        await list_places(update, context)
        return
    if text == "🔥 Горячие":
        await hot_places(update, context)
        return
    if text == "📝 Заметка":
        await update.message.reply_text("Напиши: Название — текст заметки\nПример: Rizz — договорились на пятницу")
        context.user_data["awaiting_note"] = True
        return
    if text == "📍 Рядом":
        await near_places(update, context)
        return
    if text == "👤 Менеджер":
        await update.message.reply_text("Напиши имя менеджера.\nПример: Лера")
        context.user_data["awaiting_manager_search"] = True
        return
    if text == "🗺 Карта":
        await map_link(update, context)
        return
    if text == "📥 Экспорт CSV":
        await export_csv(update, context)
        return
    if text == "⏳ Просроченные":
        await stale_records(update, context)
        return
    if text == "❓ Помощь":
        await start(update, context)
        return

    if context.user_data.get("awaiting_note"):
        context.user_data["awaiting_note"] = False
        await quick_note(update, context)
        return
    if context.user_data.get("awaiting_manager_search"):
        context.user_data["awaiting_manager_search"] = False
        await manager_places(update, context, query=text)
        return
    if context.user_data.get("awaiting_manager"):
        context.user_data["awaiting_manager"] = False
        context.user_data["add_manager"] = text if text != "Пропустить" else ""
        keyboard = [[KeyboardButton(c)] for c in CATEGORIES]
        await update.message.reply_text("Выбери категорию:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
        context.user_data["awaiting_category"] = True
        return
    if context.user_data.get("awaiting_category"):
        context.user_data["awaiting_category"] = False
        context.user_data["add_category"] = text
        await update.message.reply_text("Теперь отправь данные:\n\nНазвание\nАдрес\nТелефон (необязательно)\nЗаметки (необязательно)\n\nМожно приложить фото.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Отмена")]], resize_keyboard=True))
        context.user_data["awaiting_add"] = True
        return
    if context.user_data.get("awaiting_add"):
        if text.lower() in ["отмена", "cancel", "отменить"]:
            context.user_data["awaiting_add"] = False
            context.user_data.pop("add_manager", None)
            context.user_data.pop("add_category", None)
            await update.message.reply_text("Добавление отменено.")
            return
        context.user_data["awaiting_add"] = False
        update.message.text = "/add\n" + text
        await add_place(update, context)
        return
    if context.user_data.get("awaiting_status"):
        context.user_data["awaiting_status"] = False
        parts = text.split()
        if len(parts) >= 2:
            context.args = parts
            await change_status(update, context)
        else:
            await update.message.reply_text("Нужно название и статус.")
        return
    if context.user_data.get("awaiting_find"):
        context.user_data["awaiting_find"] = False
        context.args = [text]
        await find_place(update, context)
        return
    if context.user_data.get("awaiting_delete"):
        context.user_data["awaiting_delete"] = False
        context.args = [text]
        await delete_place(update, context)
        return

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_place))
    application.add_handler(CommandHandler("status", change_status))
    application.add_handler(CommandHandler("find", find_place))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("list", list_places))
    application.add_handler(CommandHandler("map", map_link))
    application.add_handler(CommandHandler("delete", delete_place))
    application.add_handler(CommandHandler("hot", hot_places))
    application.add_handler(CommandHandler("note", quick_note))
    application.add_handler(CommandHandler("near", near_places))
    application.add_handler(CommandHandler("manager", manager_places))
    application.add_handler(CommandHandler("export", export_csv))
    application.add_handler(CommandHandler("stale", stale_records))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
