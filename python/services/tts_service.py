import asyncio
import tempfile
import os
import uuid
import edge_tts

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

    async def _play(self, job):
        path = os.path.join(tempfile.gettempdir(), f"{job['id']}.mp3")

        try:
            com = edge_tts.Communicate(
                job["text"],
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch
            )

            await com.save(path)

            if PLAYER == "pygame":
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()

                while pygame.mixer.music.get_busy():
                    await asyncio.sleep(0.05)

            else:
                from playsound import playsound
                playsound(path)

        except Exception as e:
            print(f"[TTS ERROR] {e}")

        finally:
            try:
                os.remove(path)
            except:
                pass