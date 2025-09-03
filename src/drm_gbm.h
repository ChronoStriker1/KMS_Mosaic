#ifndef DRM_GBM_H
#define DRM_GBM_H

#include <stdbool.h>
#include <stdint.h>
#include <drm.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <gbm.h>

// DRM context holding device and modesetting state
typedef struct {
    int fd;
    drmModeRes *res;
    drmModeConnector *conn;
    drmModeCrtc *orig_crtc;
    drmModeModeInfo mode;
    uint32_t crtc_id;
    uint32_t conn_id;
    struct {
        int enabled;             // 1 if client caps set and primary plane found
        uint32_t plane_id;       // primary plane for selected CRTC
        int nonblock;            // perform nonblocking atomic flips
        struct { uint32_t mode_id, active, out_fence_ptr; } crtc_props;
        struct { uint32_t crtc_id; } conn_props;
        struct {
            uint32_t fb_id, crtc_id, src_x, src_y, src_w, src_h,
                     crtc_x, crtc_y, crtc_w, crtc_h, in_fence_fd;
        } plane_props;
    } atomic;
} drm_ctx;

// GBM surface/buffer tracking
typedef struct {
    struct gbm_device *dev;
    struct gbm_surface *surface;
    struct gbm_bo *bo, *next_bo;
    uint32_t fb_id;
    struct gbm_bo *pending_bo;
    uint32_t pending_fb;
    int in_flight;
    int w, h; // cached dimensions
} gbm_ctx;

void advise_no_drm(void);
int open_drm_card(void);
void advise_dri_drivers(void);
void warn_if_missing_dri(void);
void try_init_atomic(drm_ctx *d);
void gbm_init(gbm_ctx *g, int drm_fd, int w, int h);
uint32_t drm_fb_for_bo(int drm_fd, struct gbm_bo *bo);
void drm_set_mode(drm_ctx *d, gbm_ctx *g);
void page_flip(drm_ctx *d, gbm_ctx *g);

#endif // DRM_GBM_H
