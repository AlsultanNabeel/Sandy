from concurrent.futures import ThreadPoolExecutor

sandy_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="SandyWorker")
