#include "drm_gbm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>

// die() is provided by the main compositor
void die(const char *msg);

void advise_no_drm(void) {
    fprintf(stderr,
        "No DRM device found (expected /dev/dri/card[0-2]).\n"
        "This program must run on a Linux console with KMS/DRM available.\n"
        "Tips:\n"
        "  - Ensure GPU drivers are loaded (e.g., i915/amdgpu/nouveau).\n"
        "  - On Unraid, enable the iGPU or pass the GPU through, and expose /dev/dri.\n"
        "  - If running in a container, pass --device=/dev/dri and required privileges.\n"
        "  - Run from a real TTY; mode setting requires DRM master (often root).\n");
}

int open_drm_card(void) {
    const char *candidates[] = {
        "/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2"
    };
    for (size_t i = 0; i < sizeof(candidates)/sizeof(candidates[0]); i++) {
        int fd = open(candidates[i], O_RDWR | O_CLOEXEC);
        if (fd >= 0) return fd;
    }
    advise_no_drm();
    errno = ENODEV;
    die("open_drm_card");
    return -1;
}

void advise_dri_drivers(void) {
    fprintf(stderr,
        "DRM device opened, but GBM/EGL failed to create a window surface.\n"
        "Likely missing Mesa GBM/EGL or DRI driver files for your GPU.\n"
        "Check these locations for DRI drivers (should contain e.g. iris_dri.so/radeonsi_dri.so):\n"
        "  - /usr/lib64/dri\n"
        "  - /usr/lib/x86_64-linux-gnu/dri\n"
        "On Unraid, install the GPU plugin or Mesa packages providing DRI.\n");
}

void warn_if_missing_dri(void) {
    const char *paths[] = {
        "/usr/lib64/dri",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/aarch64-linux-gnu/dri",
        NULL
    };
    int found = 0;
    for (int i=0; paths[i]; ++i) {
        if (access(paths[i], R_OK) == 0) { found = 1; break; }
    }
    if (!found) {
        fprintf(stderr,
            "Warning: No standard DRI driver directories found.\n"
            "EGL/GBM may fail to create a surface. Ensure Mesa DRI drivers are installed.\n");
    }
}

static uint32_t get_prop_id(int fd, uint32_t obj_id, uint32_t obj_type, const char *name) {
    drmModeObjectProperties *props = drmModeObjectGetProperties(fd, obj_id, obj_type);
    if (!props) return 0;
    uint32_t id = 0;
    for (uint32_t i=0; i<props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (pr) {
            if (strcmp(pr->name, name) == 0) { id = pr->prop_id; drmModeFreeProperty(pr); break; }
            drmModeFreeProperty(pr);
        }
    }
    drmModeFreeObjectProperties(props);
    return id;
}

static int plane_is_primary(int fd, uint32_t plane_id) {
    drmModeObjectProperties *props = drmModeObjectGetProperties(fd, plane_id, DRM_MODE_OBJECT_PLANE);
    if (!props) return 0;
    int is_primary = 0;
    for (uint32_t i=0; i<props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (!pr) continue;
        if (strcmp(pr->name, "type") == 0 && (pr->flags & DRM_MODE_PROP_ENUM)) {
            for (int j=0; j<pr->count_enums; ++j) {
                if (strcmp(pr->enums[j].name, "Primary") == 0) {
                    uint64_t val = props->prop_values[i];
                    if (val == pr->enums[j].value) is_primary = 1;
                    break;
                }
            }
        }
        drmModeFreeProperty(pr);
        if (is_primary) break;
    }
    drmModeFreeObjectProperties(props);
    return is_primary;
}

void try_init_atomic(drm_ctx *d) {
    memset(&d->atomic, 0, sizeof d->atomic);
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0) return;
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) return;

    int crtc_index = -1;
    for (int i=0; i<d->res->count_crtcs; ++i)
        if (d->res->crtcs[i] == d->crtc_id) { crtc_index = i; break; }
    if (crtc_index < 0) return;

    drmModePlaneRes *pres = drmModeGetPlaneResources(d->fd);
    if (!pres) return;
    uint32_t chosen_plane = 0;
    for (uint32_t i=0; i<pres->count_planes; ++i) {
        drmModePlane *pl = drmModeGetPlane(d->fd, pres->planes[i]);
        if (!pl) continue;
        if (pl->possible_crtcs & (1u << crtc_index)) {
            if (plane_is_primary(d->fd, pl->plane_id)) { chosen_plane = pl->plane_id; drmModeFreePlane(pl); break; }
        }
        drmModeFreePlane(pl);
    }
    drmModeFreePlaneResources(pres);
    if (!chosen_plane) return;

    uint32_t mode_id = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "MODE_ID");
    uint32_t active  = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "ACTIVE");
    uint32_t out_fence_ptr = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "OUT_FENCE_PTR");
    uint32_t conn_crtc = get_prop_id(d->fd, d->conn_id, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID");
    uint32_t fb_id = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "FB_ID");
    uint32_t crtc_id = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_ID");
    uint32_t src_x = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_X");
    uint32_t src_y = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_Y");
    uint32_t src_w = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_W");
    uint32_t src_h = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_H");
    uint32_t crtc_x = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_X");
    uint32_t crtc_y = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_Y");
    uint32_t crtc_w = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_W");
    uint32_t crtc_h = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_H");
    uint32_t in_fence_fd = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "IN_FENCE_FD");

    if (!mode_id || !active || !conn_crtc || !fb_id || !crtc_id || !src_x || !src_y || !src_w || !src_h || !crtc_x || !crtc_y ||
        !crtc_w || !crtc_h) {
        return;
    }

    d->atomic.enabled = 1;
    d->atomic.plane_id = chosen_plane;
    d->atomic.crtc_props.mode_id = mode_id;
    d->atomic.crtc_props.active = active;
    d->atomic.conn_props.crtc_id = conn_crtc;
    d->atomic.plane_props.fb_id = fb_id;
    d->atomic.plane_props.crtc_id = crtc_id;
    d->atomic.crtc_props.out_fence_ptr = out_fence_ptr;
    d->atomic.plane_props.src_x = src_x;
    d->atomic.plane_props.src_y = src_y;
    d->atomic.plane_props.src_w = src_w;
    d->atomic.plane_props.src_h = src_h;
    d->atomic.plane_props.crtc_x = crtc_x;
    d->atomic.plane_props.crtc_y = crtc_y;
    d->atomic.plane_props.crtc_w = crtc_w;
    d->atomic.plane_props.crtc_h = crtc_h;
    d->atomic.plane_props.in_fence_fd = in_fence_fd;
}

void gbm_init(gbm_ctx *g, int drm_fd, int w, int h) {
    g->dev = gbm_create_device(drm_fd);
    if (!g->dev) die("gbm_create_device");
    g->surface = gbm_surface_create(g->dev, w, h, GBM_FORMAT_XRGB8888,
                                    GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!g->surface) die("gbm_surface_create");
    g->w = w; g->h = h;
}

uint32_t drm_fb_for_bo(int drm_fd, struct gbm_bo *bo) {
    uint32_t fb_id = 0;
    uint32_t width = gbm_bo_get_width(bo);
    uint32_t height = gbm_bo_get_height(bo);
    uint32_t stride = gbm_bo_get_stride(bo);
    uint32_t handle = gbm_bo_get_handle(bo).u32;
    uint32_t format = gbm_bo_get_format(bo);
    uint32_t handles[4] = {handle,0,0,0};
    uint32_t strides[4] = {stride,0,0,0};
    uint32_t offsets[4] = {0,0,0,0};
    uint64_t modifier = 0;
#ifdef DRM_FORMAT_MOD_INVALID
    modifier = gbm_bo_get_modifier(bo);
    if (modifier != DRM_FORMAT_MOD_INVALID) {
        struct drm_mode_fb_cmd2 cmd = {0};
        cmd.width = width; cmd.height = height; cmd.pixel_format = format ? format : DRM_FORMAT_XRGB8888;
        cmd.handles[0] = handles[0];
        cmd.pitches[0] = strides[0];
        cmd.offsets[0] = offsets[0];
        cmd.modifier[0] = modifier;
        cmd.flags = DRM_MODE_FB_MODIFIERS;
        if (drmIoctl(drm_fd, DRM_IOCTL_MODE_ADDFB2, &cmd) == 0) {
            return cmd.fb_id;
        }
    }
#endif
    if (drmModeAddFB2(drm_fd, width, height, format ? format : DRM_FORMAT_XRGB8888,
                      handles, strides, offsets, &fb_id, 0) == 0) {
        return fb_id;
    }
    int ret = drmModeAddFB(drm_fd, width, height, 24, 32, stride, handle, &fb_id);
    if (ret) die("drmModeAddFB");
    return fb_id;
}

void drm_set_mode(drm_ctx *d, gbm_ctx *g) {
    if (d->atomic.enabled) {
        g->bo = gbm_surface_lock_front_buffer(g->surface);
        if (!g->bo) die("gbm_surface_lock_front_buffer");
        g->fb_id = drm_fb_for_bo(d->fd, g->bo);

        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) die("drmModeAtomicAlloc");

        uint32_t blob_id = 0;
        if (drmModeCreatePropertyBlob(d->fd, &d->mode, sizeof(d->mode), &blob_id) != 0) {
            drmModeAtomicFree(req);
            die("drmModeCreatePropertyBlob");
        }

        int r = 0;
        int out_fence = -1;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.mode_id, blob_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.active, 1) <= 0;
        if (d->atomic.crtc_props.out_fence_ptr) {
            uint64_t ptr = (uint64_t)(uintptr_t)&out_fence;
            r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.out_fence_ptr, ptr) <= 0;
        }
        r |= drmModeAtomicAddProperty(req, d->conn_id, d->atomic.conn_props.crtc_id, d->crtc_id) <= 0;

        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_id, d->crtc_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.fb_id, g->fb_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_w, (uint64_t)d->mode.hdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_h, (uint64_t)d->mode.vdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_w, d->mode.hdisplay) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_h, d->mode.vdisplay) <= 0;

        if (r) {
            drmModeAtomicFree(req);
            drmModeDestroyPropertyBlob(d->fd, blob_id);
            die("drmModeAtomicAddProperty");
        }
        if (drmModeAtomicCommit(d->fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET, g) != 0) {
            drmModeAtomicFree(req);
            drmModeDestroyPropertyBlob(d->fd, blob_id);
            die("drmModeAtomicCommit (modeset)");
        }
        drmModeAtomicFree(req);
        drmModeDestroyPropertyBlob(d->fd, blob_id);
        if (out_fence >= 0) close(out_fence);
        g->in_flight = 0;
        return;
    }

    g->bo = gbm_surface_lock_front_buffer(g->surface);
    if (!g->bo) die("gbm_surface_lock_front_buffer");
    g->fb_id = drm_fb_for_bo(d->fd, g->bo);
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, g->fb_id, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) die("drmModeSetCrtc");
}

void page_flip(drm_ctx *d, gbm_ctx *g) {
    g->next_bo = gbm_surface_lock_front_buffer(g->surface);
    uint32_t fb = drm_fb_for_bo(d->fd, g->next_bo);
    if (d->atomic.enabled) {
        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) die("drmModeAtomicAlloc");
        int r = 0;
        int out_fence = -1;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_id, d->crtc_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.fb_id, fb) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_w, (uint64_t)d->mode.hdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_h, (uint64_t)d->mode.vdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_w, d->mode.hdisplay) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_h, d->mode.vdisplay) <= 0;
        if (d->atomic.crtc_props.out_fence_ptr) {
            uint64_t ptr = (uint64_t)(uintptr_t)&out_fence;
            r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.out_fence_ptr, ptr) <= 0;
        }
        if (r) { drmModeAtomicFree(req); die("drmModeAtomicAddProperty (flip)"); }
        unsigned int flags = d->atomic.nonblock ? DRM_MODE_ATOMIC_NONBLOCK : 0;
        if (drmModeAtomicCommit(d->fd, req, flags, d->atomic.nonblock ? g : NULL) != 0) {
            drmModeAtomicFree(req);
            fprintf(stderr, "drmModeAtomicCommit (flip) failed; falling back to legacy\n");
            d->atomic.enabled = 0;
        } else {
            drmModeAtomicFree(req);
            if (out_fence >= 0) close(out_fence);
            if (d->atomic.nonblock) {
                g->pending_bo = g->next_bo;
                g->pending_fb = fb;
                g->in_flight = 1;
                return;
            } else {
                if (g->bo) {
                    uint32_t old_fb = g->fb_id;
                    gbm_surface_release_buffer(g->surface, g->bo);
                    drmModeRmFB(d->fd, old_fb);
                }
                g->bo = g->next_bo;
                g->fb_id = fb;
                g->in_flight = 0;
                return;
            }
        }
    }
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, fb, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) fprintf(stderr, "drmModeSetCrtc (page_flip) failed: %d\n", ret);
    if (g->bo) {
        uint32_t old_fb = g->fb_id;
        gbm_surface_release_buffer(g->surface, g->bo);
        drmModeRmFB(d->fd, old_fb);
    }
    g->bo = g->next_bo;
    g->fb_id = fb;
}
