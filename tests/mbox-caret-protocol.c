/* Real wlroots text-input-v3 server/client regression on a socketpair.
 * No display connection, input device, guest profile or host IM is used. */
#define WLR_USE_UNSTABLE
#include <assert.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <wayland-client.h>
#include <wayland-server.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_seat.h>
#include "text-input-unstable-v3-client-protocol.h"
#include "../programs/mbox/sway-caret-state.h"

static struct wl_display *server;
static struct wlr_seat *seat;
static struct wlr_surface *surface;
static struct wl_listener new_surface, new_text, enabled, committed, disabled, text_destroy;
static unsigned processed;
static struct wlr_text_input_v3 *input;
static void update(struct wl_listener *listener, void *data) {
    unsigned serial = input->current_serial;
    assert(serial == ++processed);
    bool valid = mbox_caret_has_rectangle(input, surface);
    if (serial == 3 || serial == 4) {
        assert(valid);
        /* wlroots' enable-time feature snapshot stays empty: the old bug. */
        assert(!(input->active_features & WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE));
        assert(input->current.cursor_rectangle.x == (serial == 3 ? 153 : 240));
    } else {
        assert(!valid);
    }
    assert(!mbox_caret_ack(input, true)); /* never compete with a real guest IM */
    assert(mbox_caret_ack(input, false));
}
static void got_surface(struct wl_listener *listener, void *data) {
    surface = data;
    wlr_seat_keyboard_notify_enter(seat, surface, NULL, 0, NULL);
}
static void destroyed_text(struct wl_listener *listener, void *data) {
    wl_list_remove(&enabled.link);
    wl_list_remove(&committed.link);
    wl_list_remove(&disabled.link);
    wl_list_remove(&text_destroy.link);
}
static void got_text(struct wl_listener *listener, void *data) {
    input = data;
    enabled.notify = committed.notify = disabled.notify = update;
    wl_signal_add(&input->events.enable, &enabled);
    wl_signal_add(&input->events.commit, &committed);
    wl_signal_add(&input->events.disable, &disabled);
    text_destroy.notify = destroyed_text;
    wl_signal_add(&input->events.destroy, &text_destroy);
    assert(surface);
    wlr_text_input_v3_send_enter(input, surface);
}
static void client_destroyed(struct wl_listener *listener, void *data) {
    assert(processed == 6);
    wl_display_terminate(server);
}
static struct wl_compositor *compositor;
static struct wl_seat *client_seat;
static struct zwp_text_input_manager_v3 *manager;
static unsigned last_done;
static bool entered;
static void global(void *data, struct wl_registry *registry, uint32_t name,
        const char *interface, uint32_t version) {
    if (!strcmp(interface, "wl_compositor")) compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 1);
    if (!strcmp(interface, "wl_seat")) client_seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    if (!strcmp(interface, "zwp_text_input_manager_v3")) manager = wl_registry_bind(registry, name, &zwp_text_input_manager_v3_interface, 1);
}
static void removed(void *d, struct wl_registry *r, uint32_t n) {}
static void enter(void *d, struct zwp_text_input_v3 *t, struct wl_surface *s) { entered = true; }
static void leave(void *d, struct zwp_text_input_v3 *t, struct wl_surface *s) { entered = false; }
static void preedit(void *d, struct zwp_text_input_v3 *t, const char *s, int32_t a, int32_t b) { assert(false); }
static void commit_string(void *d, struct zwp_text_input_v3 *t, const char *s) { assert(false); }
static void delete_text(void *d, struct zwp_text_input_v3 *t, uint32_t a, uint32_t b) { assert(false); }
static void done(void *d, struct zwp_text_input_v3 *t, uint32_t serial) { assert(serial == last_done + 1); last_done = serial; }
static void roundtrip(struct wl_display *display, struct zwp_text_input_v3 *text, unsigned serial) {
    zwp_text_input_v3_commit(text);
    assert(wl_display_roundtrip(display) >= 0);
    assert(last_done == serial);
}
int main(void) {
    alarm(10);
    int sockets[2]; assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    pid_t child = fork(); assert(child >= 0);
    if (!child) {
        close(sockets[0]);
        server = wl_display_create(); assert(server);
        struct wl_client *client = wl_client_create(server, sockets[1]); assert(client);
        struct wl_listener destroyed = { .notify = client_destroyed };
        wl_client_add_destroy_listener(client, &destroyed);
        struct wlr_compositor *comp = wlr_compositor_create(server, 6, NULL); assert(comp);
        seat = wlr_seat_create(server, "seat0"); assert(seat);
        struct wlr_text_input_manager_v3 *m = wlr_text_input_manager_v3_create(server); assert(m);
        new_surface.notify = got_surface; new_text.notify = got_text;
        wl_signal_add(&comp->events.new_surface, &new_surface);
        wl_signal_add(&m->events.new_text_input, &new_text);
        setenv("MBOX_CARET_FILE", "/unused", 1);
        wl_display_run(server);
        _exit(0);
    }
    close(sockets[1]);
    struct wl_display *display = wl_display_connect_to_fd(sockets[0]); assert(display);
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener reg_listener = {global, removed};
    wl_registry_add_listener(registry, &reg_listener, NULL);
    assert(wl_display_roundtrip(display) >= 0);
    assert(compositor && client_seat && manager);
    assert(wl_compositor_create_surface(compositor));
    struct zwp_text_input_v3 *text = zwp_text_input_manager_v3_get_text_input(manager, client_seat);
    const struct zwp_text_input_v3_listener listener = {
        .enter = enter, .leave = leave, .preedit_string = preedit,
        .commit_string = commit_string, .delete_surrounding_text = delete_text, .done = done,
    };
    zwp_text_input_v3_add_listener(text, &listener, NULL);
    assert(wl_display_roundtrip(display) >= 0); assert(entered);
    /* Chromium sends an initial inactive commit, then enable without a caret.
     * It waits for done before flushing its queued real rectangle. */
    roundtrip(display, text, 1);
    zwp_text_input_v3_enable(text); roundtrip(display, text, 2);
    zwp_text_input_v3_set_cursor_rectangle(text, 153, 216, 0, 15); roundtrip(display, text, 3);
    zwp_text_input_v3_set_cursor_rectangle(text, 240, 216, 0, 15); roundtrip(display, text, 4);
    zwp_text_input_v3_disable(text); roundtrip(display, text, 5);
    zwp_text_input_v3_enable(text); roundtrip(display, text, 6); /* no stale caret */
    /* Exit without protocol destructors: the private server is disposable. */
    wl_display_disconnect(display);
    int status; assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    puts("PASS: real text-input serial acknowledgements, late/moving caret and re-enable reset");
}
