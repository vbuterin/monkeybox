/* Guest-local commit-only XIM server. No host X socket, bus or input grants.
 * Requests are bounded SOCK_SEQPACKET messages from nested-input.c. Reply N
 * means no X focus (use Wayland key relay); H means consumed, including an X
 * client without a focused IC (never inject bogus physical U keystrokes).
 */
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <time.h>
#include <xcb/xcb.h>
#include <xcb-imdkit/imdkit.h>
#include <xcb-imdkit/encoding.h>
#include "caret.h"

static int64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static xcb_im_input_context_t *focused;
static void callback(xcb_im_t *im, xcb_im_client_t *client,
        xcb_im_input_context_t *ic, const xcb_im_packet_header_fr_t *hdr,
        void *frame, void *arg, void *data) {
    if (getenv("MBOX_XIM_DEBUG")) fprintf(stderr, "opcode %u ic=%p\n", hdr->major_opcode, (void *)ic);
    switch (hdr->major_opcode) {
    case XCB_XIM_SET_IC_FOCUS: focused = ic; break;
    case XCB_XIM_UNSET_IC_FOCUS:
    case XCB_XIM_DESTROY_IC:
        if (focused == ic) focused = NULL;
        break;
    case XCB_XIM_DISCONNECT:
    case XCB_XIM_CLOSE:
        /* A disconnect may invalidate ICs without individual destroy callbacks.
         * Clearing on any disconnect is safer than retaining a stale pointer. */
        focused = NULL;
        break;
    case XCB_XIM_FORWARD_EVENT:
        xcb_im_forward_event(im, ic, arg);
        break;
    }
}
/* Chromium/GTK3 does not create its XIC until the first key event. Send a
 * guest-local NoSymbol event only to the already-focused X window to initialize
 * that context; never remap keys, synthesize text, or press a modifier. */
static void prime_context(xcb_connection_t *conn, xcb_window_t window) {
    const xcb_setup_t *setup = xcb_get_setup(conn);
    uint8_t count = setup->max_keycode - setup->min_keycode + 1;
    xcb_get_keyboard_mapping_reply_t *map = xcb_get_keyboard_mapping_reply(conn,
        xcb_get_keyboard_mapping(conn, setup->min_keycode, count), NULL);
    if (!map) return;
    xcb_keysym_t *symbols = xcb_get_keyboard_mapping_keysyms(map);
    for (int i = count - 1; i >= 0; --i) {
        bool empty = true;
        for (int j = 0; j < map->keysyms_per_keycode; ++j)
            if (symbols[i * map->keysyms_per_keycode + j]) empty = false;
        if (!empty) continue;
        xcb_key_press_event_t event = { .response_type = XCB_KEY_PRESS,
            .detail = setup->min_keycode + i, .event = window,
            .root = xcb_setup_roots_iterator(setup).data->root, .same_screen = 1 };
        xcb_send_event(conn, false, window, XCB_EVENT_MASK_KEY_PRESS, (char *)&event);
        event.response_type = XCB_KEY_RELEASE;
        xcb_send_event(conn, false, window, XCB_EVENT_MASK_KEY_RELEASE, (char *)&event);
        xcb_flush(conn);
        break;
    }
    free(map);
}
static void drain(xcb_connection_t *conn, xcb_im_t *im) {
    xcb_generic_event_t *event;
    while ((event = xcb_poll_for_event(conn))) {
        xcb_im_filter_event(im, event);
        free(event);
    }
}
/* Refresh even without new IC attributes: X windows may move/resize. */
static void publish_caret(xcb_connection_t *conn) {
    const char *path = getenv("MBOX_XIM_CARET");
    if (!path) return;
    struct mbox_xim_caret caret = {0};
    xcb_get_input_focus_reply_t *focus = xcb_get_input_focus_reply(conn, xcb_get_input_focus(conn), NULL);
    if (focus && focused &&
            (focus->focus == xcb_im_input_context_get_focus_window(focused) ||
             focus->focus == xcb_im_input_context_get_client_window(focused)) &&
            (xcb_im_input_context_get_preedit_attr_mask(focused) & XCB_XIM_XNSpotLocation_MASK)) {
        xcb_window_t window = xcb_im_input_context_get_focus_window(focused);
        if (!window) window = xcb_im_input_context_get_client_window(focused);
        xcb_window_t top = window;
        for (int i = 0; i < 32; i++) {
            xcb_query_tree_reply_t *tree = xcb_query_tree_reply(conn, xcb_query_tree(conn, top), NULL);
            if (!tree) { top = XCB_NONE; break; }
            bool done = tree->parent == tree->root;
            xcb_window_t parent = tree->parent;
            free(tree);
            if (done) break;
            if (!parent || parent == top) { top = XCB_NONE; break; }
            top = parent;
        }
        const xcb_im_preedit_attr_t *attr = xcb_im_input_context_get_preedit_attr(focused);
        xcb_translate_coordinates_reply_t *pos = top ? xcb_translate_coordinates_reply(conn,
            xcb_translate_coordinates(conn, window, top, attr->spot_location.x, attr->spot_location.y), NULL) : NULL;
        xcb_get_geometry_reply_t *geo = top ? xcb_get_geometry_reply(conn, xcb_get_geometry(conn, top), NULL) : NULL;
        if (pos && geo) {
            caret.time_ms = mbox_caret_now();
            caret.x = pos->dst_x; caret.y = pos->dst_y;
            caret.width = 1; caret.height = 1;
            caret.window_width = geo->width; caret.window_height = geo->height;
        }
        free(pos); free(geo);
    }
    free(focus);
    mbox_caret_write(path, &caret, sizeof(caret));
}
int main(int argc, char **argv) {
    if (argc != 2 || strlen(argv[1]) >= sizeof(((struct sockaddr_un *)0)->sun_path)) return 2;
    int screen_number;
    xcb_connection_t *conn = xcb_connect(NULL, &screen_number);
    if (xcb_connection_has_error(conn)) return 1;
    xcb_screen_iterator_t screens = xcb_setup_roots_iterator(xcb_get_setup(conn));
    for (int i = 0; i < screen_number; ++i) xcb_screen_next(&screens);
    xcb_window_t window = xcb_generate_id(conn);
    xcb_create_window(conn, XCB_COPY_FROM_PARENT, window, screens.data->root,
        0, 0, 1, 1, 0, XCB_WINDOW_CLASS_INPUT_OUTPUT, screens.data->root_visual, 0, NULL);
    uint32_t styles[] = { XCB_IM_PreeditNothing | XCB_IM_StatusNothing,
        XCB_IM_PreeditNone | XCB_IM_StatusNone };
    xcb_im_styles_t input_styles = { 2, styles };
    char *encodings[] = { "COMPOUND_TEXT" };
    xcb_im_encodings_t encoding_list = { 1, encodings };
    xcb_compound_text_init();
    xcb_im_t *im = xcb_im_create(conn, screen_number, window, "mbox", XCB_IM_ALL_LOCALES,
        &input_styles, NULL, NULL, &encoding_list, 0, callback, NULL);
    if (!im || !xcb_im_open_im(im)) return 1;
    xcb_im_set_use_sync_mode(im, true);
    xcb_flush(conn);
    int server = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    strcpy(address.sun_path, argv[1]);
    /* Parent creates a private 0700 runtime; never unlink somebody else's path. */
    if (server < 0 || bind(server, (void *)&address, sizeof(address)) || listen(server, 4)) return 1;
    struct pollfd fds[] = { { xcb_get_file_descriptor(conn), POLLIN, 0 }, { server, POLLIN, 0 } };
    while (!xcb_connection_has_error(conn)) {
        drain(conn, im);
        publish_caret(conn);
        xcb_flush(conn);
        if (poll(fds, 2, 30) < 0) { if (errno == EINTR) continue; break; }
        drain(conn, im);
        if (!(fds[1].revents & POLLIN)) continue;
        int peer = accept4(server, NULL, NULL, SOCK_CLOEXEC);
        if (peer < 0) continue;
        struct pollfd request = { peer, POLLIN, 0 };
        char text[65536];
        ssize_t size = poll(&request, 1, 100) > 0 ? recv(peer, text, sizeof(text), MSG_TRUNC) : -1;
        char result = 'H';
        xcb_get_input_focus_reply_t *focus = xcb_get_input_focus_reply(conn, xcb_get_input_focus(conn), NULL);
        /* Process pending focus/destroy notifications before dereferencing IC. */
        drain(conn, im);
        xcb_window_t original_focus = focus ? focus->focus : XCB_NONE;
        if (focus && focus->focus > XCB_INPUT_FOCUS_POINTER_ROOT && !focused) {
            prime_context(conn, focus->focus);
            struct pollfd xfd = { xcb_get_file_descriptor(conn), POLLIN, 0 };
            int64_t deadline = now_ms() + 120;
            while (!focused && now_ms() < deadline) {
                poll(&xfd, 1, 10);
                drain(conn, im);
                xcb_flush(conn);
            }
            /* Recheck focus after the asynchronous IC handshake. */
            free(focus);
            focus = xcb_get_input_focus_reply(conn, xcb_get_input_focus(conn), NULL);
            drain(conn, im);
        }
        if (getenv("MBOX_XIM_DEBUG")) fprintf(stderr, "focus=%u ic=%p client=%u icfocus=%u\n", focus ? focus->focus : 0, (void *)focused, focused ? xcb_im_input_context_get_client_window(focused) : 0, focused ? xcb_im_input_context_get_focus_window(focused) : 0);
        if (focus && focus->focus == original_focus &&
                (focus->focus == XCB_NONE || focus->focus == XCB_INPUT_FOCUS_POINTER_ROOT)) {
            result = 'N';
        } else if (focus && focus->focus == original_focus && focused && size > 0 && size <= 16384 &&
                (focus->focus == xcb_im_input_context_get_focus_window(focused) ||
                 focus->focus == xcb_im_input_context_get_client_window(focused))) {
            size_t length = 0;
            char *compound = xcb_utf8_to_compound_text(text, size, &length);
            if (compound && length <= 60000) {
                xcb_im_commit_string(im, focused, XCB_XIM_LOOKUP_CHARS, compound, length, 0);
                xcb_flush(conn);
            } else {
                fprintf(stderr, "mbox: XIM commit encoding failed or exceeded protocol limit\n");
            }
            free(compound);
        } else if (size > 0) {
            fprintf(stderr, "mbox: X11 IME commit dropped (no matching XIM context, focus changed, or oversized text)\n");
        }
        free(focus);
        send(peer, &result, 1, MSG_NOSIGNAL);
        close(peer);
    }
    close(server);
    unlink(argv[1]);
    xcb_im_close_im(im);
    xcb_im_destroy(im);
    xcb_disconnect(conn);
    return 1;
}
