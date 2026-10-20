/* Guest-only text-input state handling, shared with the protocol regression. */
#ifndef MBOX_SWAY_CARET_STATE_H
#define MBOX_SWAY_CARET_STATE_H
#include <stdlib.h>
#include <wlr/types/wlr_text_input_v3.h>

static inline bool mbox_caret_ack(struct wlr_text_input_v3 *input, bool has_input_method) {
    if (!getenv("MBOX_CARET_FILE") || has_input_method) return false;
    /* Chromium gates subsequent state (including its first real caret) on
     * done(current_serial). No guest IM exists to produce this acknowledgement.
     * Acknowledge enable, ordinary commits (also inactive ones), and disable.
     * Do not synthesize preedit or a second text delivery path. */
    if (input->focused_surface) wlr_text_input_v3_send_done(input);
    return true;
}
static inline bool mbox_caret_has_rectangle(const struct wlr_text_input_v3 *input,
        const struct wlr_surface *focus) {
    /* active_features is frozen at the first enable commit in wlroots 0.20.
     * current.features includes rectangles supplied by later commits and is
     * reset by enable. Electron commonly sends its first caret after enable. */
    return focus && input->focused_surface == focus && input->current_enabled &&
        (input->current.features & WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE);
}
#endif
