/* Included by the mbox-only wlroots Wayland backend's seat.c.
 * Parent text-input is an application capability, NOT a host input-method or
 * virtual-keyboard grant. Commit Unicode as guest-local keyboard events so X11
 * applications as well as Wayland applications receive host IME text. Candidate
 * UI remains on the host; inline preedit/surrounding-text editing is unsupported.
 */
#include <sys/mman.h>
#include <xkbcommon/xkbcommon.h>
#include "text-input-unstable-v3-client-protocol.h"

struct mbox_input {
    struct wlr_keyboard *keyboard;
    struct wl_registry *registry;
    struct zwp_text_input_manager_v3 *manager;
    struct zwp_text_input_v3 *text;
    struct wl_seat *seat;
    struct wl_listener destroy;
    char *commit;
};

static void mbox_text_enter(void *data, struct zwp_text_input_v3 *text,
        struct wl_surface *surface) {
    zwp_text_input_v3_enable(text);
    zwp_text_input_v3_set_content_type(text, 0, ZWP_TEXT_INPUT_V3_CONTENT_PURPOSE_NORMAL);
    zwp_text_input_v3_set_cursor_rectangle(text, 0, 0, 1, 1);
    zwp_text_input_v3_commit(text);
}
static void mbox_text_leave(void *data, struct zwp_text_input_v3 *text,
        struct wl_surface *surface) {
    struct mbox_input *input = data;
    free(input->commit);
    input->commit = NULL;
    zwp_text_input_v3_disable(text);
    zwp_text_input_v3_commit(text);
}
static void mbox_text_preedit(void *data, struct zwp_text_input_v3 *text,
        const char *value, int32_t begin, int32_t end) {}
static void mbox_text_delete(void *data, struct zwp_text_input_v3 *text,
        uint32_t before, uint32_t after) {}
static void mbox_text_commit(void *data, struct zwp_text_input_v3 *text,
        const char *value) {
    struct mbox_input *input = data;
    free(input->commit);
    input->commit = value ? strndup(value, 65536) : NULL;
}
/* Strict UTF-8: never interpret malformed or truncated input as keystrokes. */
static uint32_t mbox_utf8(const unsigned char **cursor) {
    const unsigned char *p = *cursor;
    uint32_t cp = *p++;
    int n = 0;
    uint32_t minimum = 0;
    if (cp < 0x80) { *cursor = p; return cp; }
    if (cp >= 0xc2 && cp <= 0xdf) { cp &= 0x1f; n = 1; minimum = 0x80; }
    else if (cp >= 0xe0 && cp <= 0xef) { cp &= 0xf; n = 2; minimum = 0x800; }
    else if (cp >= 0xf0 && cp <= 0xf4) { cp &= 7; n = 3; minimum = 0x10000; }
    else { return 0; }
    for (int i = 0; i < n; i++) {
        if ((*p & 0xc0) != 0x80) { return 0; }
        cp = (cp << 6) | (*p++ & 0x3f);
    }
    if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) { return 0; }
    *cursor = p;
    return cp;
}
static void mbox_text_done(void *data, struct zwp_text_input_v3 *text, uint32_t serial) {
    struct mbox_input *input = data;
    struct wlr_keyboard *keyboard = input->keyboard;
    if (!input->commit || !keyboard->keymap) { return; }
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (!context) { return; }
    struct xkb_keymap *original = xkb_keymap_ref(keyboard->keymap);
    struct wlr_keyboard_modifiers mods = keyboard->modifiers;
    const unsigned char *p = (const unsigned char *)input->commit;
    while (*p) {
        uint32_t cp = mbox_utf8(&p);
        if (!cp) { break; }
        /* Do not turn committed control characters into application shortcuts. */
        if (cp < 0x20 || cp == 0x7f) { continue; }
        char map[512];
        snprintf(map, sizeof(map), "xkb_keymap { "
            "xkb_keycodes { minimum=8; maximum=255; <IM> = 30; }; "
            "xkb_types { type \"ONE_LEVEL\" { modifiers=None; map[None]=Level1; }; }; "
            "xkb_compatibility {}; "
            "xkb_symbols { key <IM> { type=\"ONE_LEVEL\", [ 0x%x ] }; }; };",
            xkb_utf32_to_keysym(cp));
        struct xkb_keymap *keymap = xkb_keymap_new_from_string(context, map,
            XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
        if (!keymap) { break; }
        wlr_keyboard_set_keymap(keyboard, keymap);
        xkb_keymap_unref(keymap);
        wlr_keyboard_notify_modifiers(keyboard, 0, 0, 0, 0);
        struct wlr_keyboard_key_event event = {
            .time_msec = get_current_time_msec(), .keycode = 22,
            .state = WL_KEYBOARD_KEY_STATE_PRESSED, .update_state = false,
        };
        wl_signal_emit_mutable(&keyboard->events.key, &event);
        event.state = WL_KEYBOARD_KEY_STATE_RELEASED;
        wl_signal_emit_mutable(&keyboard->events.key, &event);
    }
    wlr_keyboard_set_keymap(keyboard, original);
    wlr_keyboard_notify_modifiers(keyboard, mods.depressed, mods.latched, mods.locked, mods.group);
    xkb_keymap_unref(original);
    xkb_context_unref(context);
    free(input->commit);
    input->commit = NULL;
}
static const struct zwp_text_input_v3_listener mbox_text_listener = {
    .enter = mbox_text_enter, .leave = mbox_text_leave,
    .preedit_string = mbox_text_preedit, .commit_string = mbox_text_commit,
    .delete_surrounding_text = mbox_text_delete, .done = mbox_text_done,
};
static void mbox_registry_global(void *data, struct wl_registry *registry,
        uint32_t name, const char *interface, uint32_t version) {
    struct mbox_input *input = data;
    if (!input->manager && strcmp(interface, "zwp_text_input_manager_v3") == 0) {
        input->manager = wl_registry_bind(registry, name, &zwp_text_input_manager_v3_interface, 1);
        input->text = zwp_text_input_manager_v3_get_text_input(input->manager, input->seat);
        zwp_text_input_v3_add_listener(input->text, &mbox_text_listener, input);
    }
}
static void mbox_registry_remove(void *data, struct wl_registry *registry, uint32_t name) {}
static const struct wl_registry_listener mbox_registry_listener = {
    .global = mbox_registry_global, .global_remove = mbox_registry_remove,
};
static void mbox_input_destroy(struct wl_listener *listener, void *data) {
    struct mbox_input *input = wl_container_of(listener, input, destroy);
    wl_list_remove(&input->destroy.link);
    if (input->text) { zwp_text_input_v3_destroy(input->text); }
    if (input->manager) { zwp_text_input_manager_v3_destroy(input->manager); }
    wl_registry_destroy(input->registry);
    free(input->commit);
    free(input);
}
static void mbox_input_init(struct wlr_wl_seat *seat) {
    struct mbox_input *input = calloc(1, sizeof(*input));
    if (!input) { return; }
    input->keyboard = &seat->wlr_keyboard;
    input->seat = seat->wl_seat;
    input->registry = wl_display_get_registry(seat->backend->remote_display);
    input->destroy.notify = mbox_input_destroy;
    wl_signal_add(&seat->wlr_keyboard.base.events.destroy, &input->destroy);
    wl_registry_add_listener(input->registry, &mbox_registry_listener, input);
}
/* wlroots normally discards the parent's keymap but forwards modifier groups:
 * this makes any non-US host layout wrong in a nested compositor. */
static void mbox_keymap(struct wlr_keyboard *keyboard, uint32_t format, int fd, uint32_t size) {
    if (format != WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1 || size == 0 || size > 16 * 1024 * 1024) { return; }
    char *map = mmap(NULL, size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (map == MAP_FAILED) { return; }
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    if (context && map[size - 1] == '\0') {
        struct xkb_keymap *keymap = xkb_keymap_new_from_string(context, map,
            XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
        if (keymap) {
            wlr_keyboard_set_keymap(keyboard, keymap);
            xkb_keymap_unref(keymap);
        }
    }
    if (context) { xkb_context_unref(context); }
    munmap(map, size);
}
