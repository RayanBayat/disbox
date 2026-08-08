"""Runtime configuration, loaded from the environment.

Credentials never appear in source, in the vault's plaintext, or in a committed
file. They are read from the environment or a gitignored `.env`, and the values
are typed as `SecretStr` so an accidental print, log line, or traceback shows a
placeholder rather than the token itself.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "load_settings"]


class Settings(BaseSettings):
    """Values Disbox reads from the environment.

    Attributes:
        bot_token: Discord bot token. SecretStr, so it is not rendered by
            repr, str, logging, or an exception traceback.
        channel_id: Channel used as the blob store. Not a secret.
    """

    model_config = SettingsConfigDict(
        env_prefix="DISBOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr | None = Field(default=None)
    channel_id: int | None = Field(default=None)

    @property
    def discord_configured(self) -> bool:
        """Whether both values needed to reach Discord are present."""
        return self.bot_token is not None and self.channel_id is not None


def load_settings() -> Settings:
    """Read settings from the environment and `.env`."""
    return Settings()
