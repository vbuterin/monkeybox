/* cc -D_GNU_SOURCE tests/mbox-caret.c -lm -o /tmp/mbox-caret && /tmp/mbox-caret */
#include <assert.h>
#include <limits.h>
#include "../programs/mbox/caret.h"
int main(void) {
    struct mbox_caret caret = { .x = 70, .y = 40, .width = 1, .height = 20 };
    assert(mbox_caret_to_output(&caret, 900, 200, 800, 0, 1.5));
    assert(caret.x == 255 && caret.y == 360 && caret.width == 2 && caret.height == 30);
    /* A left-of-origin monitor and decorated window surface offset. */
    caret = (struct mbox_caret){ .x = 30, .y = 50, .width = 0, .height = 16 };
    assert(mbox_caret_to_output(&caret, -1200, 25, -1280, 0, 1));
    assert(caret.x == 110 && caret.y == 75 && caret.width == 0 && caret.height == 16);
    assert(!mbox_caret_to_output(&caret, INT_MAX, 0, INT_MIN, 0, 1));
    assert(!mbox_caret_to_output(&caret, 0, 0, 0, 0, NAN));
    struct mbox_xim_caret xim = { .time_ms = 1000, .x = 140, .y = 80,
        .width = 2, .height = 2, .window_width = 800, .window_height = 400 };
    assert(mbox_caret_from_xim(&xim, 400, 200, 1100, &caret));
    assert(caret.x == 70 && caret.y == 40 && caret.width == 1 && caret.height == 1);
    assert(!mbox_caret_from_xim(&xim, 400, 200, 1300, &caret));
    assert(!mbox_caret_from_xim(&xim, 400, 200, 900, &caret));
    xim.window_width = 0;
    assert(!mbox_caret_from_xim(&xim, 400, 200, 1100, &caret));
    xim.window_width = 800; xim.x = INT_MAX;
    assert(!mbox_caret_from_xim(&xim, 400, 200, 1100, &caret));
    char directory[] = "/tmp/mbox-caret-test-XXXXXX";
    assert(mkdtemp(directory));
    char path[256]; snprintf(path, sizeof(path), "%s/caret", directory);
    strcpy(caret.output, "WL-1");
    struct mbox_caret readback;
    mbox_caret_write(path, &caret, sizeof(caret));
    assert(mbox_caret_read(path, &readback, sizeof(readback)));
    assert(memcmp(&caret, &readback, sizeof(caret)) == 0);
    mbox_caret_write(path, &caret, sizeof(caret) - 1);
    assert(!mbox_caret_read(path, &readback, sizeof(readback)));
    unlink(path); rmdir(directory);
    puts("PASS: caret surface/output offsets, fractional scale, XIM pixel conversion, stale/malformed rejection and atomic records");
}
