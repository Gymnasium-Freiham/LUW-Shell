import queue
from modules.workerthread import Worker
from . import logger  # NEW: centralized logger

class Master:
    def __init__(self, thread_count=4):
        self.thread_count = thread_count
        self.task_queue = queue.Queue()
        self.workers = []
        # task_queue uses Queue to support task_done/join
        # result_queue can be a lighter-weight SimpleQueue for speed when available
        self.task_queue = queue.Queue()
        self.workers = []
        try:
            # SimpleQueue is faster for put/get, no task tracking needed
            self.result_queue = queue.SimpleQueue()
        except AttributeError:
            self.result_queue = queue.Queue()


    def start(self):
        for i in range(self.thread_count):
            worker = Worker(i, self.task_queue, self.result_queue)  # Pass result_queue
            self.workers.append(worker)
            worker.start()

    def add_task(self, command, args):
        self.task_queue.put((command, args))

    def wait_for_completion(self):
        self.task_queue.join()
        # only show completion message when debug is not suppressed
        if not logger.is_debug_suppressed():
            logger.log("Alle Aufgaben erledigt")