class Command:
    """
    Command data model encapsulating command category and list of CLI statements.
    """
    def __init__(self, category: str, commands: list):
        self.category = category
        self.commands = commands