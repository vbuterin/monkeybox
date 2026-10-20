/* Included only in the guest Sway's input/text_input.c. Poll scene geometry
 * while focused, so moving/resizing a window updates an unchanged caret too. */
#include <wlr/types/wlr_scene.h>
#include <wlr/types/wlr_output_layout.h>
#include "caret.h"
#include "sway-caret-state.h"

struct mbox_caret_scene {
    struct wlr_surface *surface;
    int x, y;
    bool found;
};
static void mbox_find_surface(struct wlr_scene_buffer *buffer, int x, int y, void *data) {
    struct mbox_caret_scene *find = data;
    struct wlr_scene_surface *surface = wlr_scene_surface_try_from_buffer(buffer);
    if (surface && surface->surface == find->surface) {
        find->x = x;
        find->y = y;
        find->found = true;
    }
}
static int mbox_update_caret(void *data) {
    struct sway_input_method_relay *relay = data;
    struct wlr_surface *focus = relay->seat->wlr_seat->keyboard_state.focused_surface;
    struct mbox_caret result = { .time_ms = mbox_caret_now() };
    struct mbox_caret_scene find = { .surface = focus };
    if (focus) wlr_scene_node_for_each_buffer(&root->root_scene->tree.node, mbox_find_surface, &find);
    if (find.found) {
        bool valid = false;
        struct sway_text_input *text;
        wl_list_for_each(text, &relay->text_inputs, link) {
            if (mbox_caret_has_rectangle(text->input, focus)) {
                struct wlr_box box = text->input->current.cursor_rectangle;
                result.x = box.x; result.y = box.y;
                result.width = box.width; result.height = box.height;
                valid = true;
                break;
            }
        }
        if (!valid) {
            /* The XIM producer validates X focus and writes top-level-relative
             * pixels. Satellite is the focused native surface in this case. */
            pid_t pid = 0;
            wl_client_get_credentials(wl_resource_get_client(focus->resource), &pid, NULL, NULL);
            const char *satellite = getenv("MBOX_SATELLITE_PID_FILE");
            pid_t satellite_pid = 0;
            struct mbox_xim_caret xim;
            FILE *pidfile = satellite ? fopen(satellite, "r") : NULL;
            if (pidfile) {
                if (fscanf(pidfile, "%d", &satellite_pid) != 1) satellite_pid = 0;
                fclose(pidfile);
            }
            if (satellite_pid > 0 && pid == satellite_pid &&
                    mbox_caret_read(getenv("MBOX_XIM_CARET"), &xim, sizeof(xim))) {
                valid = mbox_caret_from_xim(&xim, focus->current.width, focus->current.height,
                    mbox_caret_now(), &result);
            }
        }
        if (valid && result.width >= 0 && result.height >= 0 &&
                llabs((long long)result.x) <= 65536 && llabs((long long)result.y) <= 65536 &&
                result.width <= 65536 && result.height <= 65536) {
            double x = find.x + (double)result.x, y = find.y + (double)result.y;
            struct wlr_output *output = wlr_output_layout_output_at(root->output_layout, x, y);
            if (output && output->transform == WL_OUTPUT_TRANSFORM_NORMAL) {
                struct wlr_box bounds;
                wlr_output_layout_get_box(root->output_layout, output, &bounds);
                /* wlroots Wayland backend attaches the full output buffer at
                 * buffer_scale=1: parent-surface units are nested output pixels.
                 * The parent's own HiDPI scale must NOT be multiplied again. */
                if (mbox_caret_to_output(&result, find.x, find.y, bounds.x, bounds.y, output->scale)) {
                    snprintf(result.output, sizeof(result.output), "%s", output->name);
                }
            }
        }
    }
    mbox_caret_write(getenv("MBOX_CARET_FILE"), &result, sizeof(result));
    wl_event_source_timer_update(relay->mbox_caret_timer, 30);
    return 0;
}
