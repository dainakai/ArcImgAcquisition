"""One serial worker with cooperative cancellation; Qt owns all GUI updates."""
from PySide6.QtCore import QThread, Signal

from .engine import Cancellation, Cancelled


class Worker(QThread):
    progress = Signal(str, int, int, object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancellation = Cancellation()

    def run(self):
        try:
            result = self.operation(self.cancellation, self.progress.emit)
            self.succeeded.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def cancel(self):
        self.cancellation.cancel()
