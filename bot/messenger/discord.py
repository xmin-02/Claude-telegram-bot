"""Discord messenger implementation using discord.py."""
import asyncio
import re
import threading

from messenger import MessengerBase
from config import DISCORD_TOKEN, DISCORD_CHANNEL_ID, log

try:
    import discord
except ImportError:
    discord = None


class DiscordMessenger(MessengerBase):
    """Discord Bot messenger."""

    platform_name = "discord"
    max_message_length = 2000

    def __init__(self, bot_token=None, channel_id=None):
        self.bot_token = bot_token or DISCORD_TOKEN
        self.channel_id = channel_id or DISCORD_CHANNEL_ID
        self._client = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    # --- Internal helpers ---

    def _get_channel(self):
        """Get the configured Discord channel object."""
        if not self._client or not self._client.is_ready():
            return None
        try:
            return self._client.get_channel(int(self.channel_id))
        except (ValueError, TypeError):
            return None

    def _run_async(self, coro):
        """Run an async coroutine from sync code, thread-safe."""
        if not self._loop or self._loop.is_closed():
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=30)
        except Exception as e:
            log.error("Discord async error: %s", e)
            return None

    # --- Message sending ---

    def send_text(self, text, **kwargs):
        ch = self._get_channel()
        if not ch:
            return None
        # Discord doesn't support HTML parse_mode; just send plain text
        msg = self._run_async(ch.send(text[:self.max_message_length]))
        return str(msg.id) if msg else None

    def send_html(self, html, **kwargs):
        """Send HTML-formatted text, converted to Discord markdown."""
        plain = self._html_to_discord(html)
        return self.send_text(plain, **kwargs)

    def send_long(self, header, body_md, footer=None):
        """Send a long markdown message, split into chunks."""
        chunks = self._split_message(body_md)
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            part = f" ({i+1}/{total})" if total > 1 else ""
            msg = f"**{header}{part}**\n{'━'*20}\n{chunk}"
            if footer and i == total - 1:
                msg += f"\n{'━'*20}\n*{footer}*"
            self.send_text(msg)

    def edit_message(self, msg_id, text, **kwargs):
        ch = self._get_channel()
        if not ch:
            return
        try:
            async def _edit():
                msg = await ch.fetch_message(int(msg_id))
                converted = self._html_to_discord(text)
                await msg.edit(content=converted[:self.max_message_length])
            self._run_async(_edit())
        except Exception as e:
            log.warning("Discord edit failed: %s", e)

    def delete_message(self, msg_id):
        ch = self._get_channel()
        if not ch:
            return
        try:
            async def _delete():
                msg = await ch.fetch_message(int(msg_id))
                await msg.delete()
            self._run_async(_delete())
        except Exception as e:
            log.warning("Discord delete failed: %s", e)

    def send_typing(self):
        ch = self._get_channel()
        if not ch:
            return
        self._run_async(ch.typing())

    # --- Rich UI ---

    def send_keyboard(self, text, buttons, **kwargs):
        """Send message with buttons. Discord uses Views for interactive components."""
        if not discord:
            return self.send_html(text)
        ch = self._get_channel()
        if not ch:
            return None

        view = discord.ui.View(timeout=300)
        for row_idx, row in enumerate(buttons):
            for btn in row:
                label = btn.get("text", "")
                if "url" in btn:
                    view.add_item(discord.ui.Button(
                        label=label, url=btn["url"], row=row_idx,
                    ))
                else:
                    cb_data = btn.get("data", "")
                    button = discord.ui.Button(
                        label=label,
                        custom_id=cb_data,
                        style=discord.ButtonStyle.secondary,
                        row=row_idx,
                    )
                    view.add_item(button)

        plain = self._html_to_discord(text)
        msg = self._run_async(ch.send(plain[:self.max_message_length], view=view))
        return str(msg.id) if msg else None

    def answer_callback(self, callback_id, text=""):
        """Discord interactions are acknowledged differently (via interaction response)."""
        # callback_id is the interaction object in Discord; handled in _process_interaction
        pass

    # --- Files ---

    def download_file(self, file_ref):
        """Download a Discord attachment."""
        import os
        import urllib.request
        from config import DATA_DIR

        if isinstance(file_ref, dict):
            url = file_ref.get("url", "")
            fname = file_ref.get("filename", "file")
        else:
            return None

        if not url:
            return None

        dl_dir = os.path.join(DATA_DIR, "downloads")
        os.makedirs(dl_dir, exist_ok=True)
        local_path = os.path.join(dl_dir, fname)
        try:
            urllib.request.urlretrieve(url, local_path)
            return local_path
        except Exception as e:
            log.error("Discord download failed: %s", e)
            return None

    # --- Formatting ---

    def format_md(self, markdown):
        """Discord natively supports markdown; return as-is."""
        return markdown

    # --- Lifecycle ---

    def start(self):
        """Start Discord bot in a background thread with its own event loop."""
        if not discord:
            log.error("discord.py not installed. Run: pip install discord.py")
            return
        if not self.bot_token:
            log.warning("Discord token not configured, skipping Discord start.")
            return

        self._thread = threading.Thread(target=self._run_bot, daemon=True)
        self._thread.start()
        # Wait for the bot to be ready (max 30s)
        self._ready.wait(timeout=30)

    def stop(self):
        if self._client and self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)

    def _run_bot(self):
        """Run the Discord bot in its own asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            log.info("Discord bot ready: %s", self._client.user)
            self._ready.set()

        @self._client.event
        async def on_message(message):
            # Ignore own messages
            if message.author == self._client.user:
                return
            # Only respond in the configured channel
            if str(message.channel.id) != self.channel_id:
                return
            self._process_message(message)

        @self._client.event
        async def on_interaction(interaction):
            if interaction.type == discord.InteractionType.component:
                self._process_interaction(interaction)

        try:
            self._loop.run_until_complete(self._client.start(self.bot_token))
        except Exception as e:
            log.error("Discord bot error: %s", e)
        finally:
            self._loop.close()

    def _process_message(self, message):
        """Route a Discord message to the hub."""
        from messenger import set_current_messenger
        set_current_messenger(self)

        text = message.content.strip()
        files = []

        # Handle attachments
        if message.attachments:
            import i18n
            for att in message.attachments:
                local = self.download_file({
                    "url": att.url,
                    "filename": att.filename,
                })
                if local:
                    from downloader import build_file_prompt
                    caption = text or i18n.t("file_prompt.doc_caption")
                    prompt = build_file_prompt(local, caption)
                    self.on_message(prompt, [local])
                    return

        if text:
            self.on_message(text)

    def _process_interaction(self, interaction):
        """Route a Discord button interaction to the hub."""
        from messenger import set_current_messenger
        set_current_messenger(self)

        custom_id = interaction.data.get("custom_id", "")
        msg_id = str(interaction.message.id) if interaction.message else ""

        # Acknowledge the interaction
        self._run_async(interaction.response.defer())

        if self.hub:
            self.hub.on_callback(
                self.platform_name,
                interaction,  # pass interaction object as callback_id
                msg_id,
                custom_id,
            )

    # --- Utility ---

    @staticmethod
    def _html_to_discord(html):
        """Convert Telegram-style HTML to Discord markdown."""
        text = html
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
        text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text)
        text = re.sub(r"<em>(.*?)</em>", r"*\1*", text)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
        text = re.sub(r"<pre>(.*?)</pre>", r"```\1```", text, flags=re.DOTALL)
        text = re.sub(r'<a href="(.*?)">(.*?)</a>', r"[\2](\1)", text)
        text = re.sub(r"<[^>]+>", "", text)  # strip remaining tags
        # Unescape HTML entities
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&amp;", "&").replace("&quot;", '"')
        return text

    def _split_message(self, text, limit=None):
        """Split text into chunks that fit Discord's limit."""
        limit = limit or (self.max_message_length - 200)  # leave room for header/footer
        if len(text) <= limit:
            return [text]
        chunks = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            # Find a good split point (newline or space)
            split_at = text.rfind("\n", 0, limit)
            if split_at < limit // 2:
                split_at = text.rfind(" ", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()
        return chunks
