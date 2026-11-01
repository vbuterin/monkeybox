#!/usr/bin/env python3
"""Compile/execute the production patch additions with small seat/device stubs.
This is a policy regression, not a substitute for a Sway build/runtime test.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
patch = (root / 'patches/mbox-sway-kb-refocus.patch').read_text()
assert 'mbox_heal' not in patch and 'timer' not in patch
assert 'notify_clear_focus' not in patch
hunks = patch.split('@@')[2::2]
added = ['\n'.join(line[1:] for line in h.splitlines() if line.startswith('+')) for h in hunks]
assert len(added) == 2
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define N_CONTAINER 1
struct wlr_keyboard_group;
struct wlr_keyboard { void *keymap; struct wlr_keyboard_group *group; };
struct wlr_keyboard_group { struct wlr_keyboard keyboard; };
struct sway_keyboard { struct wlr_keyboard *wlr; };
struct wlr_surface { bool mapped; };
struct sway_view { struct wlr_surface *surface; };
struct sway_container { struct sway_view *view; };
struct sway_node { int type; struct sway_container *sway_container; };
struct wlr_seat {
    struct { struct wlr_surface *focused_surface; } keyboard_state;
    struct wlr_keyboard *keyboard;
    bool grab;
};
struct sway_seat_device { struct sway_keyboard *keyboard; };
struct devices { struct sway_seat_device *items; size_t count; };
struct sway_seat { struct wlr_seat *wlr_seat; struct devices devices;
    void *focused_layer; int im_relay; };
#define wl_list_for_each(it, list, link) \
    for (size_t i = 0; i < (list)->count && ((it) = &(list)->items[i]); ++i)
static int enters, relays, switches;
static bool allowed = true;
static struct wlr_keyboard *wlr_seat_get_keyboard(struct wlr_seat *s) { return s->keyboard; }
static void wlr_seat_set_keyboard(struct wlr_seat *s, struct wlr_keyboard *k) {
    s->keyboard = k; ++switches;
}
static bool wlr_seat_keyboard_has_grab(struct wlr_seat *s) { return s->grab; }
static bool seat_is_input_allowed(struct sway_seat *s, struct wlr_surface *v) {
    (void)s; (void)v; return allowed;
}
static void seat_keyboard_notify_enter(struct sway_seat *s, struct wlr_surface *v) {
    s->wlr_seat->keyboard_state.focused_surface = v; ++enters;
}
static void sway_input_method_relay_set_focus(int *relay, struct wlr_surface *v) {
    (void)relay; (void)v; ++relays;
}
'''
source += '\nstatic void removed(struct sway_seat *seat) {\n' + added[0] + '\n}\n'
source += '\nstatic void same_node(struct sway_seat *seat, struct sway_node *node) {\n' + added[1] + '\n}\n'
source += r'''
int main(void) {
    struct wlr_surface surface = {.mapped = true}, popup = {.mapped = true};
    struct sway_view view = {&surface};
    struct sway_container con = {&view};
    struct sway_node node = {N_CONTAINER, &con};
    struct wlr_seat wlr = {0};
    struct sway_seat seat = {.wlr_seat = &wlr};
    same_node(&seat, &node);
    assert(enters == 1 && relays == 1 && wlr.keyboard_state.focused_surface == &surface);
    for (int i = 0; i < 100; ++i) same_node(&seat, &node);
    assert(enters == 1); // healthy same-node refocus never cycles focus
    wlr.keyboard_state.focused_surface = &popup;
    same_node(&seat, &node);
    assert(wlr.keyboard_state.focused_surface == &popup && enters == 1);
    wlr.keyboard_state.focused_surface = NULL;
    seat.focused_layer = &popup; same_node(&seat, &node); seat.focused_layer = NULL;
    wlr.grab = true; same_node(&seat, &node); wlr.grab = false;
    allowed = false; same_node(&seat, &node); allowed = true;
    surface.mapped = false; same_node(&seat, &node); surface.mapped = true;
    same_node(&seat, NULL);
    assert(enters == 1); // layers, grabs, locks, unmapped surfaces stay untouched
    struct wlr_keyboard_group group = {.keyboard = {.keymap = &surface}};
    struct wlr_keyboard physical = {.keymap = &surface, .group = &group};
    struct sway_keyboard keyboard = {&physical};
    struct sway_seat_device devices[] = {{NULL}, {&keyboard}};
    seat.devices = (struct devices){devices, 2};
    removed(&seat);
    assert(wlr.keyboard == &group.keyboard && switches == 1 && enters == 1);
    removed(&seat); assert(switches == 1); // don't replace a surviving active keyboard
    wlr.keyboard = NULL; physical.group = NULL;
    removed(&seat); assert(wlr.keyboard == &physical && switches == 2);
    wlr.keyboard = NULL; physical.keymap = NULL;
    removed(&seat); assert(!wlr.keyboard && switches == 2);
    assert(enters == 1); // keyboard restoration never manufactures focus changes
}
'''
with tempfile.TemporaryDirectory(prefix='mbox-focus-policy-') as tmp:
    src = Path(tmp) / 'focus.c'
    exe = Path(tmp) / 'focus'
    src.write_text(source)
    subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Wextra', '-Werror', str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('PASS: event-driven keyboard restoration; healthy focus, popup, layer, grab and lock guards')
