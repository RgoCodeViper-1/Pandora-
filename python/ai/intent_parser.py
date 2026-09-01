# python/ai/intent_parser.py

"""
Compatibility shim only.
"""

class IntentParser:

    def parse(self, text):

        return IntentResult(
            intent="unknown",
            entities={},
            confidence=0.0,
            text=text
        )


class IntentResult:

    def __init__(
        self,
        intent,
        entities,
        confidence,
        text=""
    ):

        self.intent = intent
        self.entities = entities
        self.confidence = confidence
        self.text = text


    def to_dict(self):

        return {

            "intent": self.intent,
            "entities": self.entities,
            "confidence": self.confidence,
            "text": self.text
        }