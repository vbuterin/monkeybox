/* Compile with wlroots internal headers and the generated text-input client
 * header. Exercises real xkb/wlr objects without a compositor or physical input.
 */
#define WLR_USE_UNSTABLE
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client-protocol.h>
#include <wlr/interfaces/wlr_keyboard.h>
#include <wlr/types/wlr_keyboard.h>
#include "backend/wayland.h"
static int64_t get_current_time_msec(void) { return 0; }
#include "../programs/mbox/nested-input.c"

static uint32_t received[64];
static size_t count;
static struct wlr_keyboard keyboard;
static void on_key(struct wl_listener *listener, void *data) {
    struct wlr_keyboard_key_event *event = data;
    if (event->state == WL_KEYBOARD_KEY_STATE_PRESSED) {
        assert(count < 64);
        received[count++] = xkb_state_key_get_utf32(keyboard.xkb_state, event->keycode + 8);
    }
}
int main(void) {
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    assert(context);
    struct xkb_keymap *original = xkb_keymap_new_from_names(context, NULL, XKB_KEYMAP_COMPILE_NO_FLAGS);
    assert(original);
    static const struct wlr_keyboard_impl impl = { .name = "test" };
    wlr_keyboard_init(&keyboard, &impl, "test");
    assert(wlr_keyboard_set_keymap(&keyboard, original));
    struct wl_listener listener = { .notify = on_key };
    wl_signal_add(&keyboard.events.key, &listener);
    wlr_keyboard_notify_modifiers(&keyboard, 1, 0, 0, 0);
    struct mbox_input input = { .keyboard = &keyboard, .commit = strdup("привет你好🙂a") };
    mbox_text_done(&input, NULL, 1);
    const uint32_t expected[] = { 0x43f, 0x440, 0x438, 0x432, 0x435, 0x442, 0x4f60, 0x597d, 0x1f642, 'a' };
    assert(count == sizeof(expected) / sizeof(expected[0]));
    assert(memcmp(received, expected, sizeof(expected)) == 0);
    assert(keyboard.keymap == original && keyboard.modifiers.depressed == 1);
    assert(input.commit == NULL);
    const unsigned char *bad[] = {
        (const unsigned char *)"\xc0\x80", (const unsigned char *)"\xed\xa0\x80",
        (const unsigned char *)"\xf4\x90\x80\x80", (const unsigned char *)"\xe4\xbd",
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        assert(mbox_utf8(&bad[i]) == 0);
    }
    input.commit = strdup("\n\t\x7f");
    mbox_text_done(&input, NULL, 2);
    assert(count == sizeof(expected) / sizeof(expected[0]));
    wl_list_remove(&listener.link);
    wlr_keyboard_finish(&keyboard);
    xkb_keymap_unref(original);
    xkb_context_unref(context);
    puts("PASS: Russian/Chinese/emoji commits, keymap/modifier restoration, invalid UTF-8 and control rejection");
}
