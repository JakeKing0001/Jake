class Command:
    def __init__(self, intent: str, parameters: dict | None = None):
        self.intent = intent
        self.parameters = parameters