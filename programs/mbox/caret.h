/* Private, invocation-local caret records. No host paths or desktop queries. */
#ifndef MBOX_CARET_H
#define MBOX_CARET_H
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <math.h>
struct mbox_caret {
    int64_t time_ms;
    char output[128];
    int32_t x, y, width, height;
};
struct mbox_xim_caret {
    int64_t time_ms;
    int32_t x, y, width, height; /* top-level X window pixels */
    int32_t window_width, window_height;
};
static inline int64_t mbox_caret_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static inline bool mbox_caret_read(const char *path, void *record, size_t size) {
    if (!path) return false;
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    bool ok = fread(record, 1, size, f) == size && fgetc(f) == EOF;
    fclose(f);
    return ok;
}
static inline void mbox_caret_write(const char *path, const void *record, size_t size) {
    if (!path) return;
    char temp[4096];
    if (snprintf(temp, sizeof(temp), "%s.tmp", path) >= (int)sizeof(temp)) return;
    FILE *f = fopen(temp, "wb");
    if (!f) return;
    bool ok = fwrite(record, 1, size, f) == size;
    if (fclose(f) != 0) ok = false;
    if (ok) rename(temp, path);
    else unlink(temp);
}
/* Xwayland-satellite scales X window pixels into its Wayland surface. */
static inline bool mbox_caret_from_xim(const struct mbox_xim_caret *in, int sw, int sh,
        int64_t now, struct mbox_caret *out) {
    if (in->time_ms <= 0 || in->time_ms > now || now - in->time_ms > 250 ||
            in->window_width <= 0 || in->window_height <= 0 || sw <= 0 || sh <= 0 ||
            in->window_width > 65536 || in->window_height > 65536 ||
            in->x < 0 || in->x > in->window_width || in->y < 0 || in->y > in->window_height ||
            in->width < 0 || in->height < 0 || in->width > 65536 || in->height > 65536) return false;
    out->x = (int64_t)in->x * sw / in->window_width;
    out->y = (int64_t)in->y * sh / in->window_height;
    out->width = (int64_t)in->width * sw / in->window_width;
    out->height = (int64_t)in->height * sh / in->window_height;
    return true;
}
/* Convert nested layout logical units to the parent's output wl_surface.
 * Nested outputs use the normal transform and buffer_scale=1. */
static inline bool mbox_caret_to_output(struct mbox_caret *caret, int sx, int sy,
        int ox, int oy, double scale) {
    if (!isfinite(scale) || scale <= 0 || scale > 16 ||
            llabs((long long)caret->x) > 65536 || llabs((long long)caret->y) > 65536 ||
            caret->width < 0 || caret->height < 0 || caret->width > 65536 || caret->height > 65536) return false;
    double x = ((double)sx + caret->x - ox) * scale;
    double y = ((double)sy + caret->y - oy) * scale;
    double w = ceil(caret->width * scale), h = ceil(caret->height * scale);
    if (x < 0 || y < 0 || x > 65536 || y > 65536 || w > 65536 || h > 65536) return false;
    caret->x = floor(x); caret->y = floor(y);
    caret->width = w; caret->height = h;
    return true;
}
#endif
