#!/usr/bin/env python3
"""Unchanged ordinary control requests plus immediate append-only safety evidence."""

import importlib.util
import json
from pathlib import Path
import sys
import threading

SOURCE=Path('/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py')

def main():
    spec=importlib.util.spec_from_file_location('fixed_control_load',SOURCE)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    output=Path(sys.argv[sys.argv.index('--output')+1])
    original=mod.request;lock=threading.Lock()
    def recorded(*args,**kwargs):
        row=original(*args,**kwargs)
        with lock:
            with (output/'control-events-live.jsonl').open('a') as stream:
                stream.write(json.dumps(row)+'\n');stream.flush()
            if row['operation']=='image_asset' and not row['success']:
                try:
                    with (output/'image-control-failure.json').open('x') as stream:
                        json.dump(row,stream)
                except FileExistsError:pass
        return row
    mod.request=recorded
    mod.main()

if __name__=='__main__':main()
