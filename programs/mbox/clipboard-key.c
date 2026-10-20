/* Fixed guest-local Copy/Paste requests, never a general input service. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "virtual-keyboard.h"

static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *manager;
static uint32_t seat_id, manager_id;
static int global_lost;
static void global(void *data, struct wl_registry *registry, uint32_t id,
                   const char *interface, uint32_t version) {
    (void)data; (void)version;
    if (!global_lost && !seat && version >= 1 && !strcmp(interface, "wl_seat")) {
        seat = wl_registry_bind(registry, id, &wl_seat_interface, 1);
        seat_id = id;
    }
    if (!global_lost && !manager && version >= 1 && !strcmp(interface, "zwp_virtual_keyboard_manager_v1")) {
        manager = wl_registry_bind(registry, id, &zwp_virtual_keyboard_manager_v1_interface, 1);
        manager_id = id;
    }
}
static void removed(void *data, struct wl_registry *registry, uint32_t id) {
    (void)data; (void)registry;
    if (id == seat_id && seat) { global_lost = 1; wl_seat_destroy(seat); seat = NULL; }
    if (id == manager_id && manager) {
        global_lost = 1;
        zwp_virtual_keyboard_manager_v1_destroy(manager); manager = NULL;
    }
}
static const struct wl_registry_listener listener = {global, removed};
int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "copy") && strcmp(argv[1], "paste"))) return 2;
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !seat || !manager) return 1;
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (!context) return 1;
    struct xkb_rule_names names = {.rules="evdev", .model="pc105", .layout="us"};
    struct xkb_keymap *map = xkb_keymap_new_from_names(context, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    if (!map) return 1;
    char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
    FILE *file = tmpfile();
    if (!text || !file) return 1;
    size_t size = strlen(text) + 1;
    if (fwrite(text, 1, size, file) != size || fflush(file)) return 1;
    struct zwp_virtual_keyboard_v1 *keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
    zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fileno(file), size);
    if (wl_display_roundtrip(display) < 0 || global_lost) return 1;
    xkb_mod_index_t ctrl = xkb_keymap_mod_get_index(map, XKB_MOD_NAME_CTRL);
    if (ctrl == XKB_MOD_INVALID || ctrl >= 32) return 1;
    /* Standard evdev codes with a complete, stable keymap (not wtype's tiny
       per-command map, which reuses keycode 1 for unrelated keys). */
    uint32_t code = !strcmp(argv[1], "copy") ? 46 : 47;
    zwp_virtual_keyboard_v1_modifiers(keyboard, 1u << ctrl, 0, 0, 0);
    zwp_virtual_keyboard_v1_key(keyboard, 0, code, WL_KEYBOARD_KEY_STATE_PRESSED);
    zwp_virtual_keyboard_v1_key(keyboard, 0, code, WL_KEYBOARD_KEY_STATE_RELEASED);
    zwp_virtual_keyboard_v1_modifiers(keyboard, 0, 0, 0, 0);
    if (wl_display_roundtrip(display) < 0 || global_lost) return 1;
    zwp_virtual_keyboard_v1_destroy(keyboard);
    wl_display_roundtrip(display);
    fclose(file); free(text); xkb_keymap_unref(map); xkb_context_unref(context);
    wl_display_disconnect(display);
    return 0;
}
