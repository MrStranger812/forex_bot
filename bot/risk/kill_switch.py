from dataclasses import dataclass


@dataclass(slots=True)
class KillSwitch:
    active: bool = False
    reason: str | None = None

    def trigger(self, reason: str) -> None:
        if not reason:
            raise ValueError("kill-switch reason is required")
        self.active = True
        self.reason = reason

    def assert_entry_allowed(self) -> None:
        if self.active:
            raise RuntimeError(f"kill switch active: {self.reason}")

    def reset_for_new_session(self, *, operator_acknowledged: bool) -> None:
        if not operator_acknowledged:
            raise PermissionError("operator acknowledgement required")
        self.active = False
        self.reason = None
