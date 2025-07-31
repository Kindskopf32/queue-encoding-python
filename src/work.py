import time
import subprocess

def long_running_chore(delay: int) -> None:
    time.sleep(delay)


def exec_script(file: str) -> None:
    subprocess.run(["/tmp/test/script.sh", file], capture_output=False)
