#ifndef DISPLAY_H
#define DISPLAY_H

#include <stdbool.h>
#include <stdint.h>

#include <drm.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <gbm.h>

#include <EGL/egl.h>

#include "options.h"

typedef struct {
    int fd;
    drmModeRes *res;
    drmModeConnector *conn;
    drmModeCrtc *orig_crtc;
    drmModeModeInfo mode;
    uint32_t crtc_id;
    uint32_t conn_id;
    struct {
        int enabled;
        uint32_t plane_id;
        int nonblock;
        struct { uint32_t mode_id, active, out_fence_ptr; } crtc_props;
        struct { uint32_t crtc_id; } conn_props;
        struct { uint32_t fb_id, crtc_id, src_x, src_y, src_w, src_h, crtc_x, crtc_y, crtc_w, crtc_h, in_fence_fd; } plane_props;
    } atomic;
} drm_ctx;

typedef struct {
    struct gbm_device *dev;
    struct gbm_surface *surface;
    struct gbm_bo *bo, *next_bo;
    uint32_t fb_id;
    struct gbm_bo *pending_bo;
    uint32_t pending_fb;
    int in_flight;
    int w, h;
} gbm_ctx;

typedef struct {
    EGLDisplay dpy;
    EGLConfig cfg;
    EGLContext ctx;
    EGLSurface surf;
} egl_ctx;

int display_open_drm_card(void);
void display_warn_if_missing_dri(void);
void display_preflight_expect_dri_driver(void);
void display_preflight_expect_dri_driver_diag(void);
const char *display_conn_type_str(uint32_t type);
void display_pick_connector_mode(drm_ctx *d, const options_t *opt, bool debug);
void display_gbm_init(gbm_ctx *g, int drm_fd, int w, int h, bool debug);
void display_egl_init(egl_ctx *e, gbm_ctx *g, bool debug);
void display_drm_set_mode(drm_ctx *d, gbm_ctx *g);
void display_page_flip(drm_ctx *d, gbm_ctx *g);
void display_on_page_flip(int fd, unsigned int sequence, unsigned int tv_sec, unsigned int tv_usec, void *user_data);

#endif
