"""Telegram messenger implementation wrapping existing telegram.py functions."""
import json
import threading
import time

from messenger import MessengerBase
from config import BOT_TOKEN, CHAT_ID, POLL_TIMEOUT, log
import telegram as tg
import i18n


class TelegramMessenger(MessengerBase):
    """Telegram Bot API messenger."""

    platform_name = "telegram"
    max_message_length = 3900

    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token or BOT_TOKEN
        self.chat_id = chat_id or CHAT_ID
        self._polling = False
        self._thread = None

    # --- Message sending ---

    def send_text(self, text, **kwargs):
        parse_mode = kwargs.get("parse_mode")
        params = {"chat_id": self.chat_id, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        params.update({k: v for k, v in kwargs.items()
                       if k in ("reply_markup", "disable_web_page_preview")})
        result = tg.tg_api("sendMessage", params)
        try:
            return str(result["result"]["message_id"])
        except (TypeError, KeyError):
            return None

    def send_html(self, html, **kwargs):
        result = self.send_text(html, parse_mode="HTML", **kwargs)
        if result is None:
            import re
            plain = re.sub(r"<[^>]+>", "", html)
            result = self.send_text(plain)
        return result

    def send_long(self, header, body_md, footer=None):
        tg.send_long(header, body_md, footer=footer)

    def edit_message(self, msg_id, text, **kwargs):
        params = {
            "chat_id": self.chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": kwargs.get("parse_mode", "HTML"),
        }
        params.update({k: v for k, v in kwargs.items()
                       if k in ("reply_markup", "disable_web_page_preview")})
        tg.tg_api("editMessageText", params)

    def delete_message(self, msg_id):
        tg.delete_msg(msg_id)

    def send_typing(self):
        tg.send_typing()

    # --- Rich UI ---

    def send_keyboard(self, text, buttons, **kwargs):
        """Send inline keyboard. buttons: list of rows, each row is list of {text, data}."""
        keyboard = []
        for row in buttons:
            kb_row = []
            for btn in row:
                item = {"text": btn["text"], "callback_data": btn["data"]}
                if "url" in btn:
                    item = {"text": btn["text"], "url": btn["url"]}
                kb_row.append(item)
            keyboard.append(kb_row)
        markup = json.dumps({"inline_keyboard": keyboard})
        return self.send_html(text, reply_markup=markup)

    def answer_callback(self, callback_id, text=""):
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        tg.tg_api("answerCallbackQuery", params)

    # --- Files ---

    def download_file(self, file_ref):
        from downloader import download_tg_file
        if isinstance(file_ref, dict):
            file_id = file_ref.get("file_id")
            fname = file_ref.get("file_name")
            return download_tg_file(file_id, fname)
        return download_tg_file(file_ref)

    # --- Formatting ---

    def format_md(self, markdown):
        return tg.md_to_telegram_html(markdown)

    # --- Lifecycle ---

    def start(self):
        """Start Telegram polling in a background thread."""
        self._polling = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._polling = False

    def _poll_loop(self):
        """Telegram long-polling loop."""
        offset = 0
        while self._polling:
            try:
                result = tg.tg_api("getUpdates", {
                    "offset": offset,
                    "timeout": POLL_TIMEOUT,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                })
                if not result or not result.get("ok"):
                    time.sleep(5)
                    continue
                for upd in result.get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        self._process_update(upd)
                    except Exception as e:
                        log.error("TG update error: %s", e, exc_info=True)
            except Exception as e:
                log.error("TG poll error: %s", e, exc_info=True)
                time.sleep(5)

    def _process_update(self, update):
        """Route a Telegram update to the hub."""
        from messenger import set_current_messenger
        set_current_messenger(self)

        # --- Callback queries ---
        cb = update.get("callback_query")
        if cb:
            cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            if cb_chat != self.chat_id:
                return
            data = cb.get("data", "")
            msg_id = str(cb.get("message", {}).get("message_id", ""))
            cb_id = cb["id"]
            if self.hub:
                self.hub.on_callback(self.platform_name, cb_id, msg_id, data)
            return

        # --- Messages ---
        msg = update.get("message")
        if not msg:
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != self.chat_id:
            log.warning("TG unauthorized: %s", chat_id)
            return

        text = msg.get("text", "").strip()
        caption = msg.get("caption", "").strip()
        files = []

        # Photo
        photos = msg.get("photo")
        if photos:
            best = max(photos, key=lambda p: p.get("file_size", 0))
            local = self.download_file({"file_id": best["file_id"]})
            if local:
                from downloader import build_file_prompt
                prompt = build_file_prompt(local, caption or i18n.t("file_prompt.photo_caption"))
                self.on_message(prompt, [local])
            else:
                self.send_html(f"<i>{i18n.t('error.photo_fail')}</i>")
            return

        # Document
        doc = msg.get("document")
        if doc:
            fname = doc.get("file_name", "file")
            local = self.download_file({"file_id": doc["file_id"], "file_name": fname})
            if local:
                from downloader import build_file_prompt
                prompt = build_file_prompt(local, caption or i18n.t("file_prompt.doc_caption"))
                self.on_message(prompt, [local])
            else:
                self.send_html(f"<i>{i18n.t('error.file_fail')}</i>")
            return

        if text:
            self.on_message(text)
