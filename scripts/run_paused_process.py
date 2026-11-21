import os
from run_seven_paused_serial import main
if __name__=='__main__':
    if os.environ.get('ASB_FIVE_FIXED_SNAPSHOT')=='1':
        from seven_process_repeats import series
        raise SystemExit(series())
    raise SystemExit(main(process_mode=True))
