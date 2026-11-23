"""Casting to speakers: server-side Connect output devices.

Speakers (Chromecast, DLNA renderers, Home Assistant media players, Snapcast groups) appear in every user's
Connect device list with kind "speaker". Transferring playback to one makes the server play the queue on it
(SpeakerSession); the speaker fetches audio from short-lived signed URLs (signing.py) on the plain-HTTP cast
port. See deploy/README.md "Casting" for configuration and networking.
"""

from nasmusic.cast.base import Media, Renderer, Status
from nasmusic.cast.registry import SpeakerRegistry
from nasmusic.cast.session import CastHooks, SpeakerSession, SpokenMedia

__all__ = ["CastHooks", "Media", "Renderer", "SpeakerRegistry", "SpeakerSession", "SpokenMedia", "Status"]
