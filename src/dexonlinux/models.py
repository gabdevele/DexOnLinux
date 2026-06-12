class AdbDevice:
    def __init__(self, serial, state, description=""):
        self.serial = serial
        self.state = state
        self.description = description

    @property
    def is_authorized(self):
        return self.state == "device"

    def label(self):
        suffix = f" {self.description}" if self.description else ""
        return f"{self.serial} ({self.state}){suffix}"


class ScrcpyDisplay:
    def __init__(self, display_id, description="", width=None, height=None):
        self.display_id = display_id
        self.description = description
        self.width = width
        self.height = height

    def label(self):
        details = []
        if self.description:
            details.append(self.description)
        hint = self.hint()
        if hint:
            details.append(hint)
        if not details:
            details.append("unknown display")
        suffix = f" - {' | '.join(details)}" if details else ""
        return f"display {self.display_id}{suffix}"

    def hint(self):
        if not self.width or not self.height:
            return ""
        if self.width > self.height:
            return "likely DeX / external display (recommended)"
        if self.height > self.width:
            return "likely phone screen"
        return ""


class SinkctlEvent:
    def __init__(self, name, **data):
        self.name = name
        self.data = data
