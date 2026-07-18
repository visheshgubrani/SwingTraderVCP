import asyncio
import logging
from arq import run_worker
from app.worker import WorkerSettings

if __name__ == '__main__':
    # Initialize logging to capture arq output
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("arq")
    logger.setLevel(logging.INFO)
    
    print("Starting arq worker...")
    try:
        # Setup event loop for compatibility with Python 3.12+
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        run_worker(WorkerSettings)
    except KeyboardInterrupt:
        print("Worker stopped.")
