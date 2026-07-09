from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from pdf_generator import generate_act_pdf


GITHUB_PAGES_ORIGIN = "https://dubovetsdaryaa.github.io"
MINI_APP_URL = "https://dubovetsdaryaa.github.io/CarcityPRO-app/"
PUBLIC_BASE_URL = "https://carcitypro-backend.onrender.com"
WEBHOOK_PATH = "/telegram/webhook"

ALMATY_TZ = ZoneInfo("Asia/Almaty")
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60


def load_bot_token() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не задана. "
            "Добавьте токен Telegram-бота в Environment Variables."
        )

    return token


BOT_TOKEN = load_bot_token()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Telegram secret_token поддерживает A-Z, a-z, 0-9, _ и -.
# SHA-256 hex подходит под эти ограничения.
WEBHOOK_SECRET = hashlib.sha256(
    BOT_TOKEN.encode("utf-8")
).hexdigest()


class ActItem(BaseModel):
    mode: str = Field(min_length=1, max_length=30)
    group: str = Field(min_length=1, max_length=200)
    item: str = Field(min_length=1, max_length=300)
    position: str | None = Field(default=None, max_length=200)


class GenerateActRequest(BaseModel):
    init_data: str = Field(min_length=1)
    sto: str = Field(default="", max_length=150)
    master: str = Field(default="", max_length=150)
    car: str = Field(default="", max_length=200)
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


async def configure_telegram_webhook() -> None:
    webhook_url = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

    try:
        await telegram_api(
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message"],
            },
        )
        print(f"Telegram webhook configured: {webhook_url}")
    except Exception as error:
        # Не роняем весь FastAPI-сервис из-за временной ошибки Telegram.
        print(f"WARNING: Telegram webhook setup failed: {error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_telegram_webhook()
    yield


app = FastAPI(
    title="CarcityPRO PDF API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[GITHUB_PAGES_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
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


async def send_pdf_to_telegram(
    chat_id: int,
    pdf_bytes: bytes,
    filename: str,
    act_number: str,
    item_count: int,
) -> None:
    url = f"{TELEGRAM_API_URL}/sendDocument"

    data = {
        "chat_id": str(chat_id),
        "caption": (
            f"📄 Акт дефектовки №{act_number}\n"
            f"Позиций: {item_count}\n\n"
            "Сформировано в CarcityPRO"
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
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = str(message.get("text") or "").strip()

    if (
        text.startswith("/start")
        and chat.get("type") == "private"
        and chat.get("id")
    ):
        try:
            await send_start_message(int(chat["id"]))
        except Exception as error:
            print(f"ERROR: /start reply failed: {error}")
            raise HTTPException(
                status_code=502,
                detail="Не удалось отправить сообщение Telegram.",
            ) from error

    return {"ok": True}


@app.post("/api/generate-act")
async def generate_act(payload: GenerateActRequest) -> dict:
    telegram = validate_telegram_init_data(payload.init_data)

    act_number = datetime.now(ALMATY_TZ).strftime(
        "%Y%m%d-%H%M%S"
    )

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
            "car": payload.car.strip(),
        },
    )

    filename = f"CarcityPRO_act_{act_number}.pdf"

    await send_pdf_to_telegram(
        chat_id=telegram["user_id"],
        pdf_bytes=pdf_bytes,
        filename=filename,
        act_number=act_number,
        item_count=len(items),
    )

    return {
        "ok": True,
        "act_number": act_number,
        "items_count": len(items),
    }
