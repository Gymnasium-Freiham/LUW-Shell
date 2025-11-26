from modules.mainthread import Master

class Initializer:
    def __init__(self, thread_count=4):
        self.thread_count = thread_count
    
    def init(self):
        master = Master(self.thread_count)
        master.start()
        return master