import random
from speech.tts import speak


class DialogueManager:

    def __init__(self):
        self.last_greet = 0

    def startup(self):
        lines = [
            "Systems online.",
            "Pandora core initialized.",
            "All modules operational."
        ]
        speak(random.choice(lines), priority=True)

        speak("How may I assist you?")

    def wake_response(self):
        responses = [
            "Yes?",
            "I'm listening.",
            "Go ahead.",
            "What do you need?"
        ]
        speak(random.choice(responses), priority=True)

    def greeting(self):
        responses = [
            "Good to see you again.",
            "Always a pleasure.",
            "At your service."
        ]
        speak(random.choice(responses))

    def task_ack(self):
        responses = [
            "On it.",
            "Executing.",
            "Working on that."
        ]
        speak(random.choice(responses))

    def success(self):
        responses = [
            "Done.",
            "Completed.",
            "Task finished successfully."
        ]
        speak(random.choice(responses))

    def error(self):
        responses = [
            "Something went wrong.",
            "I couldn't complete that.",
            "Execution failed."
        ]
        speak(random.choice(responses))