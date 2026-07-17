from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, quote
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from database import (
    database_enabled,
    get_acts_count,
    get_acts_page,
    get_all_acts_for_export,
    get_public_act_by_token,
    get_stats,
    get_top_users,
    init_database,
    record_sent_act,
    touch_user,
    track_app_open,
)
from pdf_generator import generate_act_pdf
from xlsx_export import build_acts_xlsx


GITHUB_PAGES_ORIGIN = "https://dubovetsdaryaa.github.io"
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://carcitypro-backend.onrender.com",
).rstrip("/")
MINI_APP_URL = "https://dubovetsdaryaa.github.io/CarcityPRO-app/?v=render-loader-v1"
STATIC_DIR = Path(__file__).resolve().parent / "static"
WEBHOOK_PATH = "/telegram/webhook"

ALMATY_TZ = ZoneInfo("Asia/Almaty")
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60
BYTES_IN_GB = 1024 ** 3


def load_database_storage_limit_gb() -> float:
    raw_value = os.environ.get(
        "DB_STORAGE_LIMIT_GB",
        "1",
    ).strip()

    try:
        value = float(raw_value)

        if value <= 0:
            raise ValueError

        return value
    except ValueError:
        print(
            "WARNING: DB_STORAGE_LIMIT_GB is invalid. "
            "Using 1 GB."
        )
        return 1.0


DATABASE_STORAGE_LIMIT_GB = load_database_storage_limit_gb()
DATABASE_STORAGE_LIMIT_BYTES = int(
    DATABASE_STORAGE_LIMIT_GB * BYTES_IN_GB
)


def load_bot_token() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не задана."
        )

    return token


def load_admin_id() -> int:
    raw_value = os.environ.get("ADMIN_TELEGRAM_ID", "").strip()

    if not raw_value:
        return 0

    try:
        return int(raw_value)
    except ValueError:
        print("WARNING: ADMIN_TELEGRAM_ID is not a valid integer.")
        return 0


def load_groq_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()


BOT_TOKEN = load_bot_token()
ADMIN_TELEGRAM_ID = load_admin_id()
GROQ_API_KEY = load_groq_api_key()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TRANSCRIPTION_MODEL = os.environ.get("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3").strip()
MAX_VOICE_AUDIO_BYTES = 15 * 1024 * 1024

WEBHOOK_SECRET = hashlib.sha256(
    BOT_TOKEN.encode("utf-8")
).hexdigest()


class ActItem(BaseModel):
    mode: str = Field(min_length=1, max_length=30)
    group: str = Field(min_length=1, max_length=200)
    item: str = Field(min_length=1, max_length=300)
    position: str | None = Field(default=None, max_length=200)
    quantity: str = Field(default="", max_length=30)
    price: str = Field(default="", max_length=50)


class AppOpenRequest(BaseModel):
    init_data: str = Field(min_length=1)


class VoiceTranscribeRequest(BaseModel):
    init_data: str = Field(min_length=1)
    audio_base64: str = Field(min_length=1)
    content_type: str = Field(default="audio/webm", max_length=120)


class GenerateActRequest(BaseModel):
    init_data: str = Field(min_length=1)
    sto: str = Field(default="", max_length=150)
    master: str = Field(default="", max_length=150)
    master_phone: str = Field(default="", max_length=50)
    client_phone: str = Field(default="", max_length=50)
    car: str = Field(default="", max_length=200)
    car_brand: str = Field(default="", max_length=80)
    car_model: str = Field(default="", max_length=120)
    car_year: str = Field(default="", max_length=4)
    mileage: str = Field(default="", max_length=20)
    comment: str = Field(default="", max_length=2000)
    items: list[ActItem] = Field(min_length=1, max_length=150)


async def telegram_api(
    method: str,
    payload: dict,
    *,
    timeout: float = 30.0,
) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{TELEGRAM_API_URL}/{method}",
            json=payload,
        )

    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(
            f"Telegram вернул некорректный ответ для {method}."
        ) from error

    if response.is_error or not result.get("ok"):
        description = result.get("description") or "Telegram Bot API error"
        raise RuntimeError(f"{method}: {description}")

    return result


async def transcribe_audio_with_groq(
    *,
    audio_bytes: bytes,
    content_type: str,
) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY is not configured in Render.",
        )

    content_type = content_type or "audio/webm"

    if "ogg" in content_type:
        filename = "voice.ogg"
    elif "wav" in content_type:
        filename = "voice.wav"
    elif "mpeg" in content_type or "mp3" in content_type:
        filename = "voice.mp3"
    elif "mp4" in content_type or "m4a" in content_type:
        filename = "voice.m4a"
    else:
        filename = "voice.webm"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    files = {
        "file": (
            filename,
            audio_bytes,
            content_type,
        ),
    }

    data = {
        "model": GROQ_TRANSCRIPTION_MODEL or "whisper-large-v3",
        "language": "ru",
        "response_format": "json",
        "temperature": "0",
        "prompt": (
            "Русская речь автомастера. "
            "Термины автозапчастей и услуг: диск сцепления, диск тормозной, "
            "стойка стабилизатора, стойка амортизатора, колодки, суппорт, "
            "рычаг, шаровая, фильтр, свечи, масло."
        ),
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=45.0,
        write=20.0,
        pool=10.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                GROQ_TRANSCRIPTIONS_URL,
                headers=headers,
                data=data,
                files=files,
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="Groq is taking too long. Please try again or enter the text manually.",
            )
        except httpx.HTTPError as error:
            print(f"ERROR: Groq request failed: {error}")
            raise HTTPException(
                status_code=502,
                detail="Groq is temporarily unavailable. Please try again.",
            )

    content_type_header = response.headers.get("content-type", "")

    if response.status_code >= 400:
        if "application/json" in content_type_header:
            try:
                error_data = response.json()
            except Exception:
                error_data = {"error": response.text[:500]}
        else:
            error_data = {
                "error": response.text[:500] or "Groq returned a non-JSON error response.",
                "status_code": response.status_code,
            }

        raise HTTPException(
            status_code=502,
            detail=f"Groq error {response.status_code}: {error_data}",
        )

    try:
        payload = response.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Groq returned a non-JSON response.",
        )

    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()

        if text:
            return text

    raise HTTPException(
        status_code=502,
        detail=f"Groq did not return recognized text: {payload}",
    )


async def send_text_message(
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if parse_mode:
        payload["parse_mode"] = parse_mode

    await telegram_api("sendMessage", payload)


async def configure_bot_commands() -> None:
    public_commands = [
        {
            "command": "start",
            "description": "Открыть CarcityPRO",
        },
        {
            "command": "help",
            "description": "Показать список команд",
        },
        {
            "command": "myid",
            "description": "Узнать свой Telegram ID",
        },
    ]

    admin_commands = public_commands + [
        {
            "command": "stats",
            "description": "Статистика приложения и базы",
        },
        {
            "command": "acts",
            "description": "Просмотр всех актов",
        },
        {
            "command": "users",
            "description": "Пользователи CarcityPRO",
        },
        {
            "command": "export_acts",
            "description": "Выгрузить все акты в Excel",
        },
    ]

    try:
        await telegram_api(
            "setMyCommands",
            {
                "commands": public_commands,
                "scope": {
                    "type": "default",
                },
            },
        )

        await telegram_api(
            "setChatMenuButton",
            {
                "menu_button": {
                    "type": "commands",
                },
            },
        )

        if ADMIN_TELEGRAM_ID:
            await telegram_api(
                "setMyCommands",
                {
                    "commands": admin_commands,
                    "scope": {
                        "type": "chat",
                        "chat_id": ADMIN_TELEGRAM_ID,
                    },
                },
            )

        print("Telegram bot commands configured.")
    except Exception as error:
        print(
            f"WARNING: Telegram bot commands setup failed: {error}"
        )


async def configure_telegram_webhook() -> None:
    webhook_url = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

    try:
        await telegram_api(
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        print(f"Telegram webhook configured: {webhook_url}")
    except Exception as error:
        print(f"WARNING: Telegram webhook setup failed: {error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    await configure_telegram_webhook()
    await configure_bot_commands()
    yield


app = FastAPI(
    title="CarcityPRO PDF API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # Telegram WebView can send different Origin values depending on the client.
    # We do not use cookies/credentials here, so wildcard CORS is safe for these API calls.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/app",
    StaticFiles(directory=STATIC_DIR, html=True),
    name="mini_app",
)


def validate_telegram_init_data(init_data: str) -> dict:
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Telegram hash не найден.",
        )

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=BOT_TOKEN.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=401,
            detail="Не удалось подтвердить данные Telegram.",
        )

    try:
        auth_date = int(values["auth_date"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Некорректный auth_date.",
        )

    age = int(time.time()) - auth_date

    if age < -60 or age > MAX_INIT_DATA_AGE_SECONDS:
        raise HTTPException(
            status_code=401,
            detail=(
                "Сессия Mini App устарела. "
                "Закройте и снова откройте приложение."
            ),
        )

    try:
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(
            status_code=401,
            detail="Не удалось определить Telegram-пользователя.",
        )

    return {
        "user_id": user_id,
        "user": user,
    }


def is_admin(user_id: int) -> bool:
    return bool(
        ADMIN_TELEGRAM_ID
        and user_id == ADMIN_TELEGRAM_ID
    )


def format_storage_size(size_bytes: int) -> str:
    size = max(int(size_bytes), 0)

    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.2f} GB"

    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.1f} MB"

    if size >= 1024:
        return f"{size / 1024:.1f} KB"

    return f"{size} B"


def format_storage_limit(limit_gb: float) -> str:
    if float(limit_gb).is_integer():
        return f"{int(limit_gb)} GB"

    return f"{limit_gb:g} GB"


def storage_progress(percent: float) -> str:
    safe_percent = min(max(percent, 0.0), 100.0)
    filled = min(10, int(safe_percent / 10))
    return "▓" * filled + "░" * (10 - filled)


def user_label(row: dict) -> str:
    full_name = " ".join(
        part
        for part in [
            str(row.get("first_name") or "").strip(),
            str(row.get("last_name") or "").strip(),
        ]
        if part
    )

    username = str(row.get("username") or "").strip()

    if username:
        username = f"@{username}"

    return " · ".join(
        value
        for value in [full_name, username]
        if value
    ) or f"ID {row.get('telegram_id')}"


def normalize_whatsapp_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())

    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]

    if len(digits) == 10:
        return "7" + digits

    return digits


def build_client_message(
    *,
    public_url: str,
    sto: str,
    master: str,
    master_phone: str,
    car: str,
    act_number: str,
) -> str:
    lines = [
        "Здравствуйте! По вашему автомобилю подготовлен акт дефектовки.",
    ]

    if car:
        lines[0] = f"Здравствуйте! По вашему автомобилю {car} подготовлен акт дефектовки."

    lines.extend(
        [
            "",
            "Посмотреть и скачать акт:",
            public_url,
            "",
        ]
    )

    if sto:
        lines.append(f"СТО: {sto}")

    if master:
        lines.append(f"Мастер: {master}")

    if master_phone:
        lines.append(f"Телефон мастера: {master_phone}")

    lines.append(f"Акт №{act_number}")

    return "\n".join(lines)


def build_whatsapp_url(
    *,
    client_phone: str,
    message: str,
) -> str:
    encoded_text = quote(message, safe="")
    whatsapp_phone = normalize_whatsapp_phone(client_phone)

    if whatsapp_phone:
        return f"https://wa.me/{whatsapp_phone}?text={encoded_text}"

    return f"https://wa.me/?text={encoded_text}"


def clean_public_token(token: str) -> str:
    return "".join(
        ch
        for ch in str(token or "")
        if ch.isalnum() or ch in {"-", "_"}
    )


def row_items(row: dict) -> list[dict]:
    items = row.get("items") or []

    if isinstance(items, str):
        try:
            loaded = json.loads(items)
            items = loaded
        except json.JSONDecodeError:
            items = []

    if not isinstance(items, list):
        return []

    return [
        item
        for item in items
        if isinstance(item, dict)
    ]


def build_act_info_from_row(row: dict) -> dict:
    return {
        "sto": str(row.get("sto") or ""),
        "master": str(row.get("master") or ""),
        "master_phone": str(row.get("master_phone") or ""),
        "car": str(row.get("car") or ""),
        "car_brand": str(row.get("car_brand") or ""),
        "car_model": str(row.get("car_model") or ""),
        "car_year": str(row.get("car_year") or ""),
        "mileage": str(row.get("mileage") or ""),
        "comment": str(row.get("comment") or ""),
    }


def build_public_act_html(row: dict) -> str:
    token = clean_public_token(str(row.get("public_token") or ""))
    act_number = str(row.get("act_number") or "")
    public_pdf_url = f"{PUBLIC_BASE_URL}/act/{token}/pdf"

    sto = str(row.get("sto") or "")
    master = str(row.get("master") or "")
    master_phone = str(row.get("master_phone") or "")
    car = str(row.get("car") or "")
    mileage = str(row.get("mileage") or "")
    comment = str(row.get("comment") or "")
    created_at = row.get("created_at")

    if hasattr(created_at, "astimezone"):
        created_text = created_at.astimezone(ALMATY_TZ).strftime("%d.%m.%Y %H:%M")
    else:
        created_text = ""

    details = []

    for label, value in [
        ("СТО", sto),
        ("Мастер", master),
        ("Телефон мастера", master_phone),
        ("Автомобиль", car),
        ("Пробег", f"{mileage} км" if mileage else ""),
        ("Дата", created_text),
    ]:
        if value:
            details.append(
                f"<div class='row'><span>{escape(label)}</span><b>{escape(value)}</b></div>"
            )

    item_cards = []

    for index, item in enumerate(row_items(row), start=1):
        title = str(item.get("item") or "")
        position = str(item.get("position") or "")
        group = str(item.get("group") or "")
        mode = "Автозапчасть" if item.get("mode") == "parts" else "Услуга СТО"
        quantity = str(item.get("quantity") or "")
        price = str(item.get("price") or "")

        subtitle_parts = [
            mode,
            group,
        ]

        if position:
            subtitle_parts.append(position)

        meta = " · ".join(part for part in subtitle_parts if part)

        extra = []

        if quantity:
            extra.append(f"Кол-во: {quantity}")

        if price:
            extra.append(f"Цена: {price} ₸")

        item_cards.append(
            "<div class='item'>"
            f"<strong>{index}. {escape(title)}</strong>"
            f"<span>{escape(meta)}</span>"
            + (
                f"<em>{escape(' · '.join(extra))}</em>"
                if extra
                else ""
            )
            + "</div>"
        )

    call_phone = ""

    if master_phone:
        digits = "".join(ch for ch in master_phone if ch.isdigit() or ch == "+")
        call_phone = (
            f"<a class='button secondary' href='tel:{escape(digits)}'>"
            "Позвонить мастеру"
            "</a>"
        )

    comment_block = ""

    if comment:
        comment_block = (
            "<div class='comment'>"
            "<span>Комментарий мастера</span>"
            f"<p>{escape(comment)}</p>"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light">
  <title>Акт дефектовки №{escape(act_number)}</title>
  <style>
    :root {{
      --green: #007C5A;
      --orange: #F9B041;
      --text: #101318;
      --muted: #697386;
      --line: #E7EBF1;
      --page: #F5F7FA;
      --card: #FFFFFF;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: var(--page);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }}

    .wrap {{
      max-width: 620px;
      margin: 0 auto;
      padding: 22px 16px 34px;
    }}

    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 8px 24px rgba(16, 24, 40, .06);
    }}

    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--green);
      font-weight: 800;
      margin-bottom: 14px;
    }}

    h1 {{
      margin: 0 0 8px;
      font-size: 25px;
      line-height: 1.2;
    }}

    .sub {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.45;
    }}

    .row {{
      display: grid;
      gap: 5px;
      padding: 12px 0;
      border-top: 1px solid var(--line);
    }}

    .row span,
    .comment span {{
      color: var(--muted);
      font-size: 13px;
    }}

    .row b {{
      font-size: 15px;
      font-weight: 680;
    }}

    .actions {{
      display: grid;
      gap: 10px;
      margin: 18px 0;
    }}

    .button {{
      min-height: 48px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 12px 14px;
      text-align: center;
      font-size: 15px;
      font-weight: 700;
      text-decoration: none;
    }}

    .primary {{
      background: var(--green);
      color: #fff;
    }}

    .secondary {{
      border: 1.5px solid var(--green);
      background: #fff;
      color: var(--green);
    }}

    .items-title {{
      margin: 20px 0 10px;
      font-size: 18px;
      font-weight: 800;
    }}

    .item {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 13px 14px;
      margin-top: 10px;
      background: #fff;
    }}

    .item strong,
    .item span,
    .item em {{
      display: block;
    }}

    .item strong {{
      font-size: 15px;
      margin-bottom: 5px;
    }}

    .item span {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}

    .item em {{
      margin-top: 6px;
      color: var(--text);
      font-style: normal;
      font-size: 13px;
      font-weight: 650;
    }}

    .comment {{
      margin-top: 18px;
      padding: 14px;
      border-radius: 14px;
      background: #F7FAF9;
      border: 1px solid #D7EEE7;
    }}

    .comment p {{
      margin: 6px 0 0;
      line-height: 1.45;
      font-size: 14px;
    }}

    .footer {{
      margin-top: 16px;
      color: var(--muted);
      text-align: center;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <div class="brand">🚗 CarcityPRO</div>
      <h1>Акт дефектовки №{escape(act_number)}</h1>
      <p class="sub">Документ подготовлен СТО. Его можно посмотреть или скачать в формате PDF.</p>

      {''.join(details)}

      <div class="actions">
        <a class="button primary" href="{escape(public_pdf_url)}" target="_blank" rel="noopener">Скачать PDF</a>
        {call_phone}
        <a class="button secondary" href="https://carcity.kz/category/avtozapcasti" target="_blank" rel="noopener">Открыть каталог запчастей</a>
      </div>

      <div class="items-title">Позиции</div>
      {''.join(item_cards) if item_cards else '<p class="sub">Позиции не указаны.</p>'}

      {comment_block}
    </section>

    <div class="footer">Сформировано в CarcityPRO</div>
  </main>
</body>
</html>"""


async def send_start_message(chat_id: int) -> None:
    text = (
        "👋 <b>Добро пожаловать в CarcityPRO!</b>\n\n"
        "CarcityPRO помогает быстро оформить акт дефектовки "
        "автомобиля прямо со смартфона.\n\n"
        "Выберите автозапчасть или услугу СТО, укажите "
        "расположение детали и добавьте выявленные позиции в акт.\n\n"
        "🚗 Чтобы начать, нажмите кнопку ниже."
    )

    await telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚗 Открыть CarcityPRO",
                            "web_app": {
                                "url": MINI_APP_URL,
                            },
                        }
                    ]
                ]
            },
        },
    )


async def send_commands_message(
    chat_id: int,
    user_id: int,
) -> None:
    text = (
        "🤖 <b>Команды CarcityPRO</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/start — открыть CarcityPRO\n"
        "/help — показать список команд\n"
        "/myid — узнать свой Telegram ID"
    )

    if is_admin(user_id):
        text += (
            "\n\n<b>Команды администратора:</b>\n"
            "/stats — статистика приложения и размер базы\n"
            "/acts — просмотр всех актов\n"
            "/users — пользователи CarcityPRO\n"
            "/export_acts — выгрузить все акты в Excel"
        )

    await send_text_message(chat_id, text)


async def send_admin_stats(chat_id: int) -> None:
    today_start = datetime.now(ALMATY_TZ).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    stats = await get_stats(today_start)

    database_size_bytes = stats["database_size_bytes"]
    storage_percent = (
        database_size_bytes
        / DATABASE_STORAGE_LIMIT_BYTES
        * 100
    )
    storage_percent = max(storage_percent, 0.0)

    storage_warning = ""

    if storage_percent >= 90:
        storage_warning = (
            "\n⚠️ <b>Критично: база заполнена более чем на 90%.</b>"
        )
    elif storage_percent >= 80:
        storage_warning = (
            "\n⚠️ База заполнена более чем на 80%."
        )

    text = (
        "📊 <b>CarcityPRO — статистика</b>\n\n"
        f"👤 Пользователей: <b>{stats['users']}</b>\n"
        f"🚀 Запусков приложения: <b>{stats['app_opens']}</b>\n"
        f"📄 Получено актов: <b>{stats['acts']}</b>\n\n"
        "<b>Сегодня:</b>\n"
        f"👤 Активных пользователей: <b>{stats['today_active_users']}</b>\n"
        f"🚀 Запусков: <b>{stats['today_app_opens']}</b>\n"
        f"📄 Актов: <b>{stats['today_acts']}</b>\n\n"
        "<b>Хранилище PostgreSQL:</b>\n"
        f"💾 Использовано: <b>{format_storage_size(database_size_bytes)}</b>"
        f" / {format_storage_limit(DATABASE_STORAGE_LIMIT_GB)}\n"
        f"{storage_progress(storage_percent)} "
        f"<b>{storage_percent:.2f}%</b>"
        f"{storage_warning}"
    )

    await send_text_message(chat_id, text)


def acts_keyboard(
    *,
    page: int,
    total_pages: int,
) -> dict | None:
    if total_pages <= 1:
        return None

    buttons = []

    if page > 0:
        buttons.append(
            {
                "text": "← Назад",
                "callback_data": f"acts_page:{page - 1}",
            }
        )

    buttons.append(
        {
            "text": f"{page + 1} / {total_pages}",
            "callback_data": "acts_noop",
        }
    )

    if page + 1 < total_pages:
        buttons.append(
            {
                "text": "Вперёд →",
                "callback_data": f"acts_page:{page + 1}",
            }
        )

    return {
        "inline_keyboard": [buttons],
    }


async def acts_page_payload(
    page: int,
    *,
    page_size: int = 10,
) -> tuple[str, dict | None, int]:
    total_acts = await get_acts_count()

    if total_acts <= 0:
        return "📄 Актов пока нет.", None, 0

    total_pages = max(
        1,
        (total_acts + page_size - 1) // page_size,
    )

    safe_page = min(max(page, 0), total_pages - 1)
    rows = await get_acts_page(
        limit=page_size,
        offset=safe_page * page_size,
    )

    parts = [
        (
            "📄 <b>Акты CarcityPRO</b>\n"
            f"Всего: <b>{total_acts}</b>"
        )
    ]

    start_number = safe_page * page_size + 1

    for row_number, row in enumerate(
        rows,
        start=start_number,
    ):
        created_at = row["created_at"].astimezone(ALMATY_TZ)

        parts.append(
            "\n"
            f"<b>{row_number}. №{escape(str(row['act_number']))}</b>\n"
            f"{escape(str(row.get('sto') or 'СТО не указано'))}\n"
            f"{escape(str(row.get('car') or 'Автомобиль не указан'))}\n"
            f"Позиций: {int(row['items_count'])}\n"
            f"Создал: {escape(user_label(row))}\n"
            f"{created_at:%d.%m.%Y %H:%M}"
        )

    return (
        "\n".join(parts),
        acts_keyboard(
            page=safe_page,
            total_pages=total_pages,
        ),
        safe_page,
    )


async def send_recent_acts(
    chat_id: int,
    *,
    page: int = 0,
) -> None:
    text, keyboard, _ = await acts_page_payload(page)

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    if keyboard:
        payload["reply_markup"] = keyboard

    await telegram_api("sendMessage", payload)


async def edit_recent_acts(
    *,
    chat_id: int,
    message_id: int,
    page: int,
) -> None:
    text, keyboard, _ = await acts_page_payload(page)

    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }

    if keyboard:
        payload["reply_markup"] = keyboard

    await telegram_api("editMessageText", payload)


async def send_acts_export(chat_id: int) -> None:
    acts = await get_all_acts_for_export()

    if not acts:
        await send_text_message(
            chat_id,
            "📄 Актов пока нет — экспортировать нечего.",
        )
        return

    workbook_bytes = build_acts_xlsx(
        acts,
        almaty_tz=ALMATY_TZ,
    )

    filename = (
        "CarcityPRO_acts_"
        + datetime.now(ALMATY_TZ).strftime("%Y-%m-%d_%H-%M")
        + ".xlsx"
    )

    url = f"{TELEGRAM_API_URL}/sendDocument"

    data = {
        "chat_id": str(chat_id),
        "caption": (
            "📊 Экспорт всех актов CarcityPRO\n"
            f"Актов: {len(acts)}"
        ),
    }

    files = {
        "document": (
            filename,
            workbook_bytes,
            (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            url,
            data=data,
            files=files,
        )

    if response.is_error:
        detail = "Telegram не принял Excel-файл."

        try:
            telegram_error = response.json()
            detail = telegram_error.get("description") or detail
        except ValueError:
            pass

        raise RuntimeError(detail)


async def send_users(chat_id: int) -> None:
    rows = await get_top_users(limit=10)

    if not rows:
        await send_text_message(chat_id, "👥 Пользователей пока нет.")
        return

    parts = ["👥 <b>Пользователи CarcityPRO</b>"]

    for index, row in enumerate(rows, start=1):
        parts.append(
            "\n"
            f"<b>{index}. {escape(user_label(row))}</b>\n"
            f"🚀 Запусков: {int(row['app_open_count'])}\n"
            f"📄 Актов: {int(row['act_count'])}"
        )

    await send_text_message(chat_id, "\n".join(parts))


async def send_pdf_to_telegram(
    chat_id: int,
    pdf_bytes: bytes,
    filename: str,
    act_number: str,
    item_count: int,
    public_url: str = "",
) -> None:
    url = f"{TELEGRAM_API_URL}/sendDocument"

    data = {
        "chat_id": str(chat_id),
        "caption": (
            f"📄 Акт дефектовки №{act_number}\n"
            f"Позиций: {item_count}\n\n"
            + (
                f"Ссылка для клиента:\n{public_url}\n\n"
                if public_url
                else ""
            )
            + "Сформировано в CarcityPRO"
        ),
    }

    files = {
        "document": (
            filename,
            pdf_bytes,
            "application/pdf",
        )
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            data=data,
            files=files,
        )

    if response.is_error:
        detail = "Telegram не принял PDF."

        try:
            telegram_error = response.json()
            detail = telegram_error.get("description") or detail
        except ValueError:
            pass

        raise HTTPException(status_code=502, detail=detail)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "CarcityPRO PDF API",
        "database": "enabled" if database_enabled() else "disabled",
    }


@app.post("/api/app-open")
async def app_open(payload: AppOpenRequest) -> dict:
    telegram = validate_telegram_init_data(payload.init_data)

    tracked = False

    try:
        tracked = await track_app_open(telegram["user"])
    except Exception as error:
        print(f"WARNING: app_open analytics failed: {error}")

    return {
        "ok": True,
        "tracked": tracked,
    }


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> dict:
    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        "",
    )

    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        raise HTTPException(
            status_code=403,
            detail="Invalid Telegram webhook secret.",
        )

    update = await request.json()
    callback_query = update.get("callback_query") or {}

    if callback_query:
        callback_id = str(callback_query.get("id") or "")
        callback_user = callback_query.get("from") or {}
        callback_data = str(callback_query.get("data") or "")
        callback_message = callback_query.get("message") or {}
        callback_chat = callback_message.get("chat") or {}

        if callback_id:
            try:
                await telegram_api(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback_id,
                    },
                )
            except Exception as error:
                print(f"WARNING: answerCallbackQuery failed: {error}")

        user_id = int(callback_user.get("id") or 0)

        if not is_admin(user_id):
            return {"ok": True}

        if callback_data == "acts_noop":
            return {"ok": True}

        if callback_data.startswith("acts_page:"):
            try:
                page = int(callback_data.split(":", 1)[1])
                chat_id = int(callback_chat["id"])
                message_id = int(callback_message["message_id"])

                await edit_recent_acts(
                    chat_id=chat_id,
                    message_id=message_id,
                    page=page,
                )
            except Exception as error:
                print(f"ERROR: acts pagination failed: {error}")

        return {"ok": True}

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    text = str(message.get("text") or "").strip()
    chat_id = chat.get("id")

    if chat.get("type") != "private" or not chat_id:
        return {"ok": True}

    try:
        if from_user.get("id"):
            await touch_user(from_user)
    except Exception as error:
        print(f"WARNING: touch_user failed: {error}")

    user_id = int(from_user.get("id") or 0)

    try:
        if text.startswith("/start"):
            await send_start_message(int(chat_id))

        elif text.startswith("/myid"):
            await send_text_message(
                int(chat_id),
                (
                    "🪪 Ваш Telegram ID:\n"
                    f"<code>{user_id}</code>"
                ),
            )

        elif (
            text.startswith("/help")
            or text.startswith("/commands")
        ):
            await send_commands_message(
                int(chat_id),
                user_id,
            )

        elif text.startswith("/stats"):
            if not is_admin(user_id):
                await send_text_message(int(chat_id), "Нет доступа.")
            else:
                await send_admin_stats(int(chat_id))

        elif text.startswith("/export_acts"):
            if not is_admin(user_id):
                await send_text_message(int(chat_id), "Нет доступа.")
            else:
                await send_acts_export(int(chat_id))

        elif text.startswith("/acts"):
            if not is_admin(user_id):
                await send_text_message(int(chat_id), "Нет доступа.")
            else:
                await send_recent_acts(
                    int(chat_id),
                    page=0,
                )

        elif text.startswith("/users"):
            if not is_admin(user_id):
                await send_text_message(int(chat_id), "Нет доступа.")
            else:
                await send_users(int(chat_id))

    except Exception as error:
        print(f"ERROR: Telegram command failed: {error}")
        await send_text_message(
            int(chat_id),
            "Не удалось выполнить команду. Попробуйте чуть позже.",
        )

    return {"ok": True}


@app.post("/api/voice-transcribe")
async def voice_transcribe(payload: VoiceTranscribeRequest) -> dict:
    validate_telegram_init_data(payload.init_data)

    raw_base64 = payload.audio_base64.strip()

    if "," in raw_base64:
        raw_base64 = raw_base64.split(",", 1)[1]

    try:
        audio_bytes = base64.b64decode(raw_base64, validate=True)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Audio base64 is invalid.",
        )

    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="Audio body is empty.",
        )

    if len(audio_bytes) > MAX_VOICE_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio is too large. Please record a shorter voice note.",
        )

    text = await transcribe_audio_with_groq(
        audio_bytes=audio_bytes,
        content_type=payload.content_type or "audio/webm",
    )

    return {
        "ok": True,
        "text": text,
    }


@app.get("/act/{public_token}", response_class=HTMLResponse)
async def public_act_page(public_token: str) -> HTMLResponse:
    token = clean_public_token(public_token)

    if not token:
        raise HTTPException(status_code=404, detail="Act not found.")

    try:
        row = await get_public_act_by_token(token)
    except Exception as error:
        print(f"ERROR: public act page failed: {error}")
        raise HTTPException(
            status_code=503,
            detail="Act storage is temporarily unavailable.",
        )

    if not row:
        raise HTTPException(status_code=404, detail="Act not found.")

    return HTMLResponse(build_public_act_html(row))


@app.get("/act/{public_token}/pdf")
async def public_act_pdf(public_token: str) -> Response:
    token = clean_public_token(public_token)

    if not token:
        raise HTTPException(status_code=404, detail="Act not found.")

    try:
        row = await get_public_act_by_token(token)
    except Exception as error:
        print(f"ERROR: public act PDF failed: {error}")
        raise HTTPException(
            status_code=503,
            detail="Act storage is temporarily unavailable.",
        )

    if not row:
        raise HTTPException(status_code=404, detail="Act not found.")

    act_number = str(row.get("act_number") or "act")
    items = row_items(row)

    pdf_bytes = generate_act_pdf(
        items=items,
        act_number=act_number,
        act_info=build_act_info_from_row(row),
    )

    filename = f"CarcityPRO_act_{act_number}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


@app.post("/api/generate-act")
async def generate_act(payload: GenerateActRequest) -> dict:
    telegram = validate_telegram_init_data(payload.init_data)

    act_number = (
        datetime.now(ALMATY_TZ).strftime("%Y%m%d-%H%M%S")
        + "-"
        + secrets.token_hex(2).upper()
    )

    public_token = secrets.token_urlsafe(18)
    public_url = f"{PUBLIC_BASE_URL}/act/{public_token}"
    public_pdf_url = f"{public_url}/pdf"

    items = [
        item.model_dump()
        for item in payload.items
    ]

    pdf_bytes = generate_act_pdf(
        items=items,
        act_number=act_number,
        act_info={
            "sto": payload.sto.strip(),
            "master": payload.master.strip(),
            "master_phone": payload.master_phone.strip(),
            "car": payload.car.strip(),
            "car_brand": payload.car_brand.strip(),
            "car_model": payload.car_model.strip(),
            "car_year": payload.car_year.strip(),
            "mileage": payload.mileage.strip(),
            "comment": payload.comment.strip(),
        },
    )

    filename = f"CarcityPRO_act_{act_number}.pdf"

    await send_pdf_to_telegram(
        chat_id=telegram["user_id"],
        pdf_bytes=pdf_bytes,
        filename=filename,
        act_number=act_number,
        item_count=len(items),
        public_url=public_url,
    )

    stored = False

    try:
        stored = await record_sent_act(
            user=telegram["user"],
            act_number=act_number,
            sto=payload.sto.strip(),
            master=payload.master.strip(),
            master_phone=payload.master_phone.strip(),
            client_phone=payload.client_phone.strip(),
            public_token=public_token,
            car=payload.car.strip(),
            car_brand=payload.car_brand.strip(),
            car_model=payload.car_model.strip(),
            car_year=payload.car_year.strip(),
            mileage=payload.mileage.strip(),
            comment=payload.comment.strip(),
            items=items,
        )
    except Exception as error:
        print(f"WARNING: act analytics failed: {error}")

    if stored:
        client_message = build_client_message(
            public_url=public_url,
            sto=payload.sto.strip(),
            master=payload.master.strip(),
            master_phone=payload.master_phone.strip(),
            car=payload.car.strip(),
            act_number=act_number,
        )

        whatsapp_url = build_whatsapp_url(
            client_phone=payload.client_phone.strip(),
            message=client_message,
        )
    else:
        client_message = ""
        whatsapp_url = ""
        public_url = ""
        public_pdf_url = ""

    return {
        "ok": True,
        "act_number": act_number,
        "items_count": len(items),
        "stored": stored,
        "public_url": public_url,
        "public_pdf_url": public_pdf_url,
        "whatsapp_url": whatsapp_url,
        "client_message": client_message,
    }
