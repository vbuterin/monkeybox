#!/usr/bin/env python3
"""Explicit preview doesn't consume transfers; indicator never reads payloads."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
source=Path(__file__).resolve().parents[1]/'programs/mbox/clipboard.py'
spec=importlib.util.spec_from_file_location('clipboard',source)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'XDG_RUNTIME_DIR':tmp}):
    from contextlib import redirect_stdout
    import io
    path=m.directory()
    record=dict(id='test',expires=time.monotonic()+60,text='<b>SYNTHETIC TRANSFER</b>')
    (path/'buffer.json').write_text(json.dumps(record))
    stream=io.StringIO()
    with patch.object(sys,'argv',['clipboard','preview']), patch.object(m,'bounded',return_value=b'SYNTHETIC HOST'), redirect_stdout(stream): m.main()
    data=json.loads(stream.getvalue())
    assert data['host']=='SYNTHETIC HOST' and data['transfer']==record['text']
    assert json.loads((path/'buffer.json').read_text())==record
    with patch.object(sys,'argv',['clipboard','pulse']): m.main()
    event=json.loads((path/'activity.json').read_text())
    assert set(event)=={'at','operation'} and event['operation']=='copy'
    proc=subprocess.Popen([sys.executable,str(source),'watch'],env=dict(os.environ),stdout=subprocess.PIPE)
    try:
        line=json.loads(proc.stdout.readline());assert line['activity']=='copy' and line['serial']==event['at']
        with patch.object(sys,'argv',['clipboard','pulse','paste']): m.main()
        line=json.loads(proc.stdout.readline());assert line['activity']=='paste' and line['serial']!=event['at']
        line=json.loads(proc.stdout.readline());assert line['activity']=='' and line['serial']==0
    finally: proc.terminate();proc.wait(timeout=3)
    record['expires']=time.monotonic()-1
    (path/'buffer.json').write_text(json.dumps(record))
    stream=io.StringIO()
    with patch.object(sys,'argv',['clipboard','preview']), patch.object(m,'bounded',side_effect=RuntimeError), redirect_stdout(stream): m.main()
    assert json.loads(stream.getvalue())['transfer']=='' and not (path/'buffer.json').exists()
print('PASS: click-only host preview, nonconsuming transfer preview, expiry, payload-free pulse/glow')
