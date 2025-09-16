from typing import Optional
import sh, logging, time

logger = logging.getLogger("dexonlinux")
logger.setLevel(logging.DEBUG)

class ProcessError(Exception):
    def __init__(self, process: sh.RunningCommand):
        self.process = process
        self.exit_code = process.exit_code
        super().__init__(f"Process exited with code {self.exit_code}")

def kill_if_running(pkill: sh.Command, command: sh.Command, *args, **kwargs) -> Optional[sh.RunningCommand]:
    #TODO: fix this, currently NOT WORKING
    try:
        process = command(*args, **kwargs)
        if not process.process.is_alive():
            raise ProcessError(process)
    except ProcessError as e:
        print(e.exit_code)
        logger.warning(f"{command} is running, attempting to kill it.")
        try:
            pkill("-f", str(command))
            time.sleep(1)
            process = command(*args, **kwargs)
            logger.warning(f"{command} (hopefully) killed and restarted.")
            return process
        except Exception as e:
            logger.error(f"Failed to kill or restart {command}: {e}")
    except Exception as e:
        logger.error(f"Error while starting {command}: {e}")
    return None