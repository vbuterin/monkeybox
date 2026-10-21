/* Compile with wlroots internal headers and the generated text-input client
 * header. Exercises real xkb/wlr objects without a compositor or physical input.
 */
#define WLR_USE_UNSTABLE
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <xkbcommon/xkbcommon-compose.h>
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
    unsetenv("MBOX_XIM_SOCKET");
    struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    assert(context);
    struct xkb_keymap *original = xkb_keymap_new_from_names(context, NULL, XKB_KEYMAP_COMPILE_NO_FLAGS);
    assert(original);
    static const struct wlr_keyboard_impl impl = { .name = "test" };
    wlr_keyboard_init(&keyboard, &impl, "test");
    assert(wlr_keyboard_set_keymap(&keyboard, original));
    /* Host compose:ralt must survive the actual parent-keymap import. No guest
     * US-only replacement and no virtual input on the live desktop. */
    struct xkb_rule_names names = { .layout = "us", .options = "compose:ralt" };
    struct xkb_keymap *host = xkb_keymap_new_from_names(context, &names, 0);
    assert(host);
    struct xkb_state *host_state = xkb_state_new(host);
    xkb_keycode_t ralt = xkb_keymap_key_by_name(host, "RALT");
    assert(xkb_state_key_get_one_sym(host_state, ralt) == XKB_KEY_Multi_key);
    char *map = xkb_keymap_get_as_string(host, XKB_KEYMAP_FORMAT_TEXT_V1);
    FILE *mapfile = tmpfile(); assert(mapfile && map);
    size_t maplen = strlen(map) + 1;
    assert(fwrite(map, 1, maplen, mapfile) == maplen && fflush(mapfile) == 0);
    mbox_keymap(&keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fileno(mapfile), maplen);
    assert(xkb_state_key_get_one_sym(keyboard.xkb_state, ralt) == XKB_KEY_Multi_key);
    struct xkb_compose_table *table = xkb_compose_table_new_from_locale(context, "en_US.UTF-8", 0);
    assert(table);
    struct xkb_compose_state *compose = xkb_compose_state_new(table, 0); assert(compose);
    xkb_compose_state_feed(compose, xkb_state_key_get_one_sym(keyboard.xkb_state, ralt));
    xkb_compose_state_feed(compose, XKB_KEY_apostrophe);
    xkb_compose_state_feed(compose, XKB_KEY_e);
    assert(xkb_compose_state_get_status(compose) == XKB_COMPOSE_COMPOSED);
    char composed[16]; xkb_compose_state_get_utf8(compose, composed, sizeof(composed));
    assert(strcmp(composed, "é") == 0);
    xkb_compose_state_unref(compose); xkb_compose_table_unref(table);
    free(map); fclose(mapfile); xkb_state_unref(host_state); xkb_keymap_unref(host);
    assert(wlr_keyboard_set_keymap(&keyboard, original));
    puts("PASS: host Right Alt Compose survives guest keymap import; Compose apostrophe e produces é");
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
    setenv("MBOX_XIM_SOCKET", "/nonexistent/mbox-test-xim.sock", 1);
    input.commit = strdup("你好");
    mbox_text_done(&input, NULL, 3);
    assert(input.commit == NULL);
    assert(count == sizeof(expected) / sizeof(expected[0]));
    unsetenv("MBOX_XIM_SOCKET");
    /* Exercise the production request framing and H/N routing with a private
     * socket, without a compositor. Xlib/Electron delivery is tested separately. */
    char directory[] = "/tmp/mbox-xim-unit-XXXXXX";
    assert(mkdtemp(directory));
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    snprintf(address.sun_path, sizeof(address.sun_path), "%s/xim", directory);
    int server = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    assert(server >= 0);
    assert(bind(server, (void *)&address, sizeof(address)) == 0);
    assert(listen(server, 1) == 0);
    pid_t child = fork();
    assert(child >= 0);
    if (child == 0) {
        for (int i = 0; i < 2; i++) {
            int peer = accept(server, NULL, NULL);
            assert(peer >= 0);
            char value[64];
            ssize_t size = recv(peer, value, sizeof(value), 0);
            assert(size == 6 && memcmp(value, "你好", 6) == 0);
            char result = i == 0 ? 'H' : 'N';
            assert(send(peer, &result, 1, 0) == 1);
            close(peer);
        }
        _exit(0);
    }
    setenv("MBOX_XIM_SOCKET", address.sun_path, 1);
    assert(mbox_xim_commit("你好"));
    assert(!mbox_xim_commit("你好"));
    int status;
    assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    close(server);
    unlink(address.sun_path);
    rmdir(directory);
    unsetenv("MBOX_XIM_SOCKET");
    wl_list_remove(&listener.link);
    wlr_keyboard_finish(&keyboard);
    xkb_keymap_unref(original);
    xkb_context_unref(context);
    puts("PASS: Russian/Chinese/emoji commits, keymap/modifier restoration, invalid UTF-8 and control rejection");
}
