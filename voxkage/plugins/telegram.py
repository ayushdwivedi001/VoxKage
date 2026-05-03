"""VoxKage Telegram Plugin — Bot-based messaging integration."""

from voxkage.plugins.base import VoxKagePlugin


class TelegramPlugin(VoxKagePlugin):
    name = "telegram"
    display_name = "Telegram Bot"
    description = "Send and receive messages via Telegram bot. Enables remote control of VoxKage from your phone."
    required_env_vars = ["TELEGRAM_BOT_TOKEN"]
    mcp_server_name = "voxkage-telegram"
    mcp_server_script = "mcp_servers/telegram_server.py"

    def setup_interactive(self) -> bool:
        print("  To set up Telegram, you need a Bot Token from @BotFather on Telegram.")
        print()
        print("  Steps:")
        print("    1. Open Telegram and search for @BotFather")
        print("    2. Send /newbot and follow the prompts")
        print("    3. Copy the bot token you receive")
        print()

        token = self._prompt("Bot Token", secret=True)
        if not token:
            return False

        self._write_env_var("TELEGRAM_BOT_TOKEN", token)

        chat_id = self._prompt("Your Chat ID (leave blank to auto-detect)")
        if chat_id:
            self._write_env_var("TELEGRAM_CHAT_ID", chat_id)
        else:
            print("  → VoxKage will auto-detect your Chat ID on first message.")

        return True
