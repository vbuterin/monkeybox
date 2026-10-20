#!/usr/bin/env python3
"""Disposable GTK entry for clipboard tests. Writes only synthetic fixture text."""
import ctypes as C
import json
from pathlib import Path
import sys

g = C.CDLL(sys.argv[1])
root = Path(sys.argv[2])
for name, result, args in (
    ('gtk_init_check', C.c_int, [C.c_void_p, C.c_void_p]),
    ('gtk_window_new', C.c_void_p, [C.c_int]),
    ('gtk_entry_new', C.c_void_p, []),
    ('gtk_entry_set_text', None, [C.c_void_p, C.c_char_p]),
    ('gtk_entry_get_text', C.c_char_p, [C.c_void_p]),
    ('gtk_container_add', None, [C.c_void_p, C.c_void_p]),
    ('gtk_widget_show_all', None, [C.c_void_p]),
    ('gtk_widget_grab_focus', None, [C.c_void_p]),
    ('gtk_editable_select_region', None, [C.c_void_p, C.c_int, C.c_int]),
    ('gtk_editable_get_selection_bounds', C.c_int, [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]),
    ('g_signal_connect_data', C.c_ulong, [C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int]),
    ('gdk_event_get_keyval', C.c_int, [C.c_void_p, C.POINTER(C.c_uint)]),
    ('gdk_event_get_state', C.c_int, [C.c_void_p, C.POINTER(C.c_uint)]),
):
    f = getattr(g, name); f.restype = result; f.argtypes = args
assert g.gtk_init_check(None, None)
window = g.gtk_window_new(0)
entry = g.gtk_entry_new()
g.gtk_entry_set_text(entry, b'GUEST_SELECTED_TEXT')
g.gtk_container_add(window, entry)
@C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
def changed(widget, data):
    (root/'entry-text').write_text(g.gtk_entry_get_text(entry).decode())
@C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)
def key(widget, event, data):
    value = C.c_uint(); state = C.c_uint()
    g.gdk_event_get_keyval(event, C.byref(value)); g.gdk_event_get_state(event, C.byref(state))
    start = C.c_int(); end = C.c_int()
    selected = g.gtk_editable_get_selection_bounds(entry, C.byref(start), C.byref(end))
    with (root/'entry-keys').open('a') as f:
        f.write(json.dumps(dict(key=value.value, state=state.value, selected=selected, start=start.value, end=end.value))+'\n')
    return 0
g.g_signal_connect_data(entry, b'changed', changed, None, None, 0)
g.g_signal_connect_data(entry, b'key-press-event', key, None, None, 0)
g.gtk_widget_show_all(window)
g.gtk_widget_grab_focus(entry)
g.gtk_editable_select_region(entry, 0, -1)
(root/'entry-ready').touch()
g.gtk_main()
