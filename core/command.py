class Command:
    def __init__(self, intent:str, parameters:dict=None):
        self.intent = intent
        self.parameters = parameters