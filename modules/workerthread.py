import threading
import queue
from modules import commands
from . import logger  # NEW: centralized logger

class Worker(threading.Thread):
    def __init__(self, id, task_queue, result_queue):
        super().__init__()
        self.id = id
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.daemon = True
        # cache result put for micro-optimizations; DO NOT cache commands map (must stay dynamic)
        self._result_put = result_queue.put

    def run(self):
        # Block on get(); significantly reduces wakeups and syscalls vs timeout loop
        while True:
            task = self.task_queue.get()  # blocks until an item is available
            try:
                if task is None:
                    # sentinel for shutdown
                    break
                command, args = task
                # Dynamic lookup so new commands added at runtime are visible
                func = commands.COMMANDS.get(command)
                if func:
                    result = func(args)
                else:
                    result = f"Unbekanntes Kommando: {command}"
                    # debug-only diagnostic
                    if not logger.is_debug_suppressed():
                        logger.log(f"Worker {self.id} kennt Command '{command}' nicht")
            except Exception as e:
                # keep worker alive on command exceptions
                result = f"Fehler bei {command}: {e}"
            finally:
                try:
                    # mark task done for Queue.join compatibility; ignore errors when task_queue is not a Queue
                    if hasattr(self.task_queue, "task_done"):
                        self.task_queue.task_done()
                except Exception:
                    pass
                # fast put to result queue
                try:
                    self._result_put((self.id, result))
                except Exception:
                    pass
        # exit run
