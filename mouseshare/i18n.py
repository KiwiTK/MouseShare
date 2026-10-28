"""App-authored messages. Chinese is the only language, so messages are the source text."""


def render(value):
    return value.render() if isinstance(value, Message) else value


class Message(str):
    """A formatted app message that remembers its source and parameters."""
    def __new__(cls, source, values=None):
        values = values or {}
        text = source.format(**{k: render(v) for k, v in values.items()}) if values else source
        obj = super().__new__(cls, text)
        obj.source, obj.values = source, values
        return obj

    def render(self):
        return str(Message(self.source, self.values))

    def __add__(self, other):
        return Message('{left}{right}', dict(left=self, right=other))

    def __radd__(self, other):
        return Message('{left}{right}', dict(left=other, right=self))


def tr(source, **values):
    return Message(source, values)


def exception_text(exc):
    """Preserve an app-authored exception message until the GUI renders it."""
    return exc.args[0] if exc.args and isinstance(exc.args[0], Message) else str(exc)


def status_message(value):
    """Recover app-authored transfer statuses from older local history records."""
    return value if isinstance(value, Message) else tr(value)
