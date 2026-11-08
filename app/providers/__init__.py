"""Provider factories — swap vendors here without touching the pipeline."""
from __future__ import annotations

from ..config import Settings, settings as default_settings
from .base import AvatarProvider, ProviderError, VoiceProvider
from .minimax import MiniMaxVoiceProvider
from .mock import MockAvatarProvider, MockVoiceProvider
from .zhiying import ZhiyingAvatarProvider


def build_voice_provider(settings: Settings = default_settings) -> VoiceProvider:
    if settings.mock:
        return MockVoiceProvider(settings)
    return MiniMaxVoiceProvider(settings)


def build_avatar_provider(settings: Settings = default_settings) -> AvatarProvider:
    if settings.mock:
        return MockAvatarProvider(settings)
    return ZhiyingAvatarProvider(settings)


__all__ = [
    "AvatarProvider",
    "ProviderError",
    "VoiceProvider",
    "build_avatar_provider",
    "build_voice_provider",
]
