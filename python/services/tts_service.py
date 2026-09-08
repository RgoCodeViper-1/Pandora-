import asyncio
import tempfile
import os
import uuid
import hashlib
import edge_tts
from core.cache import cache
from audio.processing import set_playback_active

try:
    import pygame
    pygame.mixer.init()
    PLAYER = "pygame"
except:
    PLAYER = "playsound"


class TTSService:
    def __init__(self, voice="en-US-EricNeural"):
        self.voice = voice
        self.rate = "+10%"
        self.pitch = "+0Hz"
        self._queue = asyncio.Queue()
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True

        while self._running:
            job = await self._queue.get()
            await self._play(job)

    async def speak(self, text: str, priority=False):
        job = {
            "id": str(uuid.uuid4()),
            "text": text
        }

        if priority:
            await self._play(job)
        else:
            await self._queue.put(job)

    async def interrupt(self) -> None:
        """Stop current playback and discard queued responses for barge-in."""
        if PLAYER == "pygame":
            pygame.mixer.music.stop()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _play(self, job):
        path = os.path.join(tempfile.gettempdir(), f"{job['id']}.mp3")
        cache_key = cache.make_key("tts", self.voice, self.rate, self.pitch, job["text"])
        metadata = {
            "text_sha256": hashlib.sha256(job["text"].encode("utf-8")).hexdigest(),
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
            "format": "mp3",
        }

        try:
            audio = cache.get_bytes(cache_key, expected_metadata=metadata)
            if isinstance(audio, bytes):
                with open(path, "wb") as output:
                    output.write(audio)
            else:
                com = edge_tts.Communicate(
                    job["text"],
                    voice=self.voice,
                    rate=self.rate,
                    pitch=self.pitch
                )
                await com.save(path)
                with open(path, "rb") as generated:
                    cache.set_bytes_with_metadata(
                        cache_key,
                        generated.read(),
                        metadata=metadata,
                        ttl_seconds=30 * 24 * 60 * 60,
                    )

            set_playback_active(True)
            try:
                if PLAYER == "pygame":
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.play()

                    while pygame.mixer.music.get_busy():
                        await asyncio.sleep(0.05)

                else:
                    from playsound import playsound
                    playsound(path)
            finally:
                set_playback_active(False)

        except Exception as e:
            print(f"[TTS ERROR] {e}")

        finally:
            try:
                os.remove(path)
            except:
                pass