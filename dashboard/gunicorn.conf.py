"""Process lifecycle hooks, automatically loaded from the Render deployment root."""


def post_worker_init(worker):
    from services import background_tasks
    background_tasks.start()


def worker_exit(server, worker):
    from services import background_tasks
    from repositories import lakebase
    background_tasks.stop()
    lakebase.close_pool()
