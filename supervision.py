"""Reminder rules driven only by eligible elapsed time and explicit responses."""
from dataclasses import dataclass

INTERVAL = 30 * 60
RESPONSE_WINDOW = 60
ANGER_THRESHOLD = 60 * 60
# 生气需要的连续忽略次数。1 = 提醒后不理一次就生气。
ANGER_IGNORES = 1
# 生气持续多久后自动消气。没有这个出口，「无进展 ≥ 60 分钟」一旦成立就永远成立
# （无进展时长只在完成任务时清零），宠物会一直板着脸，温和提醒也一起停掉。
CALM_DELAY = 30 * 60


@dataclass
class Supervisor:
    no_progress: float = 0.0
    ignored: int = 0
    next_reminder: float = INTERVAL
    response_left: float | None = None
    angry: bool = False
    active: bool = False
    calm_left: float = 0.0

    def suspend(self) -> None:
        self.active = False
        self.ignored = 0
        self.response_left = None
        self.angry = False
        self.calm_left = 0.0
        self.next_reminder = INTERVAL

    def respond(self, minutes: int) -> None:
        self.ignored = 0
        self.response_left = None
        self.angry = False
        self.calm_left = 0.0
        self.next_reminder = minutes * 60

    def complete(self) -> None:
        self.no_progress = 0.0
        self.respond(30)

    def advance(self, seconds: float) -> list[str]:
        if not self.active or seconds <= 0:
            return []
        self.no_progress += seconds
        if self.angry:
            self.calm_left -= seconds
            if self.calm_left <= 0:
                self.calm_left = 0.0
                self.angry = False
                self.ignored = 0
                self.next_reminder = INTERVAL
                return ["calm"]
            return []
        self.next_reminder -= seconds
        events = []
        if self.response_left is not None:
            self.response_left -= seconds
            if self.response_left <= 0:
                self.response_left = None
                self.ignored += 1
                events.append("ignored")
        if self.ignored >= ANGER_IGNORES and self.no_progress >= ANGER_THRESHOLD:
            self.angry = True
            self.calm_left = CALM_DELAY
            return events + ["angry"]
        if self.next_reminder <= 0 and self.response_left is None:
            self.response_left = RESPONSE_WINDOW
            self.next_reminder = INTERVAL
            events.append("reminder")
        return events
