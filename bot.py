import os
import logging
import asyncio
import pydub
import traceback
import html
import json
import tempfile
from pathlib import Path
from datetime import datetime

import telegram
from telegram import (
    Update,
    User,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    AIORateLimiter,
    filters
)
from telegram.constants import ParseMode, ChatAction
import openai
import sqlite3
import re
from configs import *


logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).parent.resolve()

openai.api_key = openai_api_key

con = sqlite3.connect(f"{BASE_DIR}/db.sqlite3")
cur = con.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        first_name VARCHAR (50),
        last_name VARCHAR (50),
        allowed BIT
    );
""")
cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        channelname VARCHAR (100) NOT NULL
    );
""")
con.commit()


def user_exists(user_id):
    user = cur.execute(f"""SELECT user_id FROM users WHERE user_id = "{user_id}";""").fetchone()
    if user:
        user = user[0]
    else:
        user = None
    return user


def channel_exists(channelname):
    user = cur.execute(f"""SELECT channelname FROM channels WHERE channelname = "{channelname}";""").fetchone()
    if user:
        user = user[0]
    else:
        user = None
    return user


def get_allowed_users():
    allowed_users = cur.execute(f"""SELECT user_id FROM users WHERE allowed = 1;""").fetchall()
    if allowed_users:
        out_allowed_users = []
        for allowed_user in allowed_users:
            out_allowed_users.append(allowed_user[0])
    else:
        out_allowed_users = []
    return out_allowed_users


def get_users():
    users = cur.execute(f"""SELECT user_id FROM users;""").fetchall()
    if users:
        out_users = []
        for channel in users:
            out_users.append(channel[0])
    else:
        out_users = []
    return out_users


def get_channels():
    channels = cur.execute(f"""SELECT channelname FROM channels;""").fetchall()
    if channels:
        out_channels = []
        for channel in channels:
            out_channels.append(channel[0])
    else:
        out_channels = []
    return out_channels


def add_channel(channelname):
    cur.execute(f"""INSERT INTO channels VALUES ("{channelname}");""")
    con.commit()


def remove_channel(channelname):
    cur.execute(f"""DELETE FROM channels WHERE channelname = "{channelname}";""")
    con.commit()


def register_user_if_not_exists(update: Update, context: CallbackContext, user: User):
    if not user_exists(user.id):
        cur.execute(f"""INSERT INTO users VALUES ({user.id},{update.message.chat_id},"{user.first_name}","{user.last_name}",0);""")
        con.commit()


async def start_handle(update: Update, context: CallbackContext):
    register_user_if_not_exists(update, context, update.message.from_user)
    allowed_user = await check_allowed_user(update)
    if allowed_user:
        reply_text = "Ay yoo, I'm ChatGPT so say something."
        await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)
    else:
        await send_sub_message(update)


def admin_check(func):
    async def wrapper(*args, **kwargs):
        username = args[0].message.from_user.username

        if username in admins:
            await func(*args, **kwargs)

    return wrapper


@admin_check
async def admin_handle(update: Update, context: CallbackContext):

    reply_text = "Use your commands, My boss.\n"
    reply_text += '''
Commands:
    /add - Input : A channel-name without @ to add.
    /remove - Input : A channel-name without @ to remove.
    /send - Input : A message that you want sent to all users.
'''

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


@admin_check
async def add_handle(update: Update, context: CallbackContext):
    channelname = re.findall(r'^/add (.+)$', update.message.text)[0]

    if not channel_exists(channelname):
        add_channel(channelname)
        reply_text = "Channel added."
        await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)
    else:
        reply_text = "This channel already exists."
        await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


@admin_check
async def remove_handle(update: Update, context: CallbackContext):
    channelname = re.findall(r'^/remove (.+)$', update.message.text)[0]

    if channel_exists(channelname):
        remove_channel(channelname)
        reply_text = "Channel removed."
        await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)
    else:
        reply_text = "This channel not exists."
        await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


@admin_check
async def send_all_handle(update: Update, context: CallbackContext):
    bot = update.get_bot()

    message = re.findall(r'^/send (.+)$', update.message.text)[0]

    users = get_users()
    for user in users:
        await bot.send_message(user, message)
    else:
        await update.message.reply_text("Message sent to all users.", parse_mode=ParseMode.HTML)


async def send_message(update: Update, prompt=None):
    if not prompt:
        prompt = update.message.text
        if prompt:
            if prompt[0] == "/":
                return 1

    try:
        if len(re.findall(r"\w+", prompt)) > 50:
            raise ValueError()
        response = await openai.Completion.acreate(model="text-davinci-003", prompt=prompt, temperature=0.7, max_tokens=4000)
    except ValueError:
        response = "Maximum allowed chat length is 50 words."
    except Exception as err:
        print(err)
        response = "Something went wrong."
    else:
        response = response.choices[0].text

    reply_text = response
    reply_text += f"\n\n<a href='https://t.me/{bot_username}'>Bot</a> • <a href='https://t.me/{main_channel}'>Channel</a>"

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)


async def check_allowed_user(update: Update):
    bot = update.get_bot()

    channels = get_channels()
    for channel in channels:
        try:
            status = (await bot.get_chat_member(f"@{channel}", update.message.from_user.id)).status
        except Exception as err:
            print(err)
            return False

        bad_status = ["left"]
        if status in bad_status:
            return False
    else:
        return True


async def send_sub_message(update: Update):
    channels = get_channels()

    keyboard = [
        [InlineKeyboardButton(f"@{channel}", url=f"https://t.me/{channel}") for channel in channels]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    reply_text = "🔑PLease join this channels to unlock all features of the bot."

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def message_handle(update: Update, context: CallbackContext):
    register_user_if_not_exists(update, context, update.message.from_user)
   
    allowed_user = await check_allowed_user(update)
    if allowed_user:
        await send_message(update)
    else:
        await send_sub_message(update)


async def voice_handle(update: Update, context: CallbackContext):
    allowed_user = await check_allowed_user(update)
    if not allowed_user:
        await send_sub_message(update)
        return 0

    voice = update.message.voice
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)
        voice_ogg_path = tmp_dir / "voice.ogg"

        # download
        voice_file = await context.bot.get_file(voice.file_id)
        await voice_file.download_to_drive(voice_ogg_path)

        # convert to mp3
        voice_mp3_path = tmp_dir / "voice.mp3"
        pydub.AudioSegment.from_file(voice_ogg_path).export(voice_mp3_path, format="mp3")

        # transcribe
        with open(voice_mp3_path, "rb") as f:
            transcribed_text = openai.Audio.transcribe("whisper-1", f)["text"]

    text = f"🎤: <i>{transcribed_text}</i>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    await send_message(update, transcribed_text)


async def error_handle(update: Update, context: CallbackContext) -> None:
    def split_text_into_chunks(text, chunk_size):
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]

    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    try:
        # collect error message
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        message = (
            f"An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )

        # split text into multiple messages due to 4096 character limit
        for message_chunk in split_text_into_chunks(message, 4096):
            try:
                await context.bot.send_message(update.effective_chat.id, message_chunk, parse_mode=ParseMode.HTML)
            except telegram.error.BadRequest:
                # answer has invalid characters, so we send it without parse_mode
                await context.bot.send_message(update.effective_chat.id, message_chunk)
    except:
        await context.bot.send_message(update.effective_chat.id, "Some error in error handler")


def run_bot() -> None:
    application = (
        ApplicationBuilder()
        .token(telegram_token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .build()
    )

    # add handlers
    application.add_handler(CommandHandler("start", start_handle))
    application.add_handler(CommandHandler("admin", admin_handle))
    application.add_handler(CommandHandler("add", add_handle))
    application.add_handler(CommandHandler("remove", remove_handle))
    application.add_handler(CommandHandler("send", send_all_handle))
    application.add_handler(MessageHandler(filters.TEXT, message_handle))
    application.add_handler(MessageHandler(filters.VOICE, voice_handle))

    # application.add_error_handler(error_handle)

    # start the bot
    print("Bot was started.")
    application.run_polling()


if __name__ == "__main__":
    run_bot()
