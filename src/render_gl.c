#include "render_gl.h"

#include <errno.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <EGL/egl.h>

#ifndef GL_PIXEL_PACK_BUFFER_NV
#define GL_PIXEL_PACK_BUFFER_NV 0x88EB
#endif
#ifndef GL_MAP_READ_BIT_EXT
#define GL_MAP_READ_BIT_EXT 0x0001
#endif
#ifndef GL_STREAM_READ
#define GL_STREAM_READ 0x88E1
#endif

typedef void *(*render_gl_map_buffer_range_fn)(GLenum target, GLintptr offset,
                                               GLsizeiptr length, GLbitfield access);
typedef GLboolean (*render_gl_unmap_buffer_fn)(GLenum target);

static render_gl_map_buffer_range_fn render_gl_map_buffer_range;
static render_gl_unmap_buffer_fn render_gl_unmap_buffer;

void render_gl_reset_state_2d(void) {
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_DITHER);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
}

void render_gl_clear_color(float r, float g, float b, float a) {
    glClearColor(r, g, b, a);
    glClear(GL_COLOR_BUFFER_BIT);
}

void render_gl_check(bool debug, const char *stage) {
    if (!debug) return;
    GLenum err;
    int cnt = 0;
    while ((err = glGetError()) != GL_NO_ERROR) {
        fprintf(stderr, "GL error at %s: 0x%x\n", stage, (unsigned)err);
        if (++cnt > 8) break;
    }
}

void render_gl_draw_border_rect(int x, int y, int w, int h, int thickness, int fb_w, int fb_h,
                                float r, float g, float b, float a) {
    (void)fb_w;
    if (w <= 0 || h <= 0 || thickness <= 0) return;
    if (thickness > w / 2) thickness = w / 2;
    if (thickness > h / 2) thickness = h / 2;
    glEnable(GL_SCISSOR_TEST);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glClearColor(r, g, b, a);

    int sx = x;
    int sy = fb_h - (y + h);
    if (sx < 0) sx = 0;
    if (sy < 0) sy = 0;

    glScissor(sx, sy + h - thickness, w, thickness);
    glClear(GL_COLOR_BUFFER_BIT);
    glScissor(sx, sy, w, thickness);
    glClear(GL_COLOR_BUFFER_BIT);
    glScissor(sx, sy, thickness, h);
    glClear(GL_COLOR_BUFFER_BIT);
    glScissor(sx + w - thickness, sy, thickness, h);
    glClear(GL_COLOR_BUFFER_BIT);
    glDisable(GL_SCISSOR_TEST);
}

static GLuint render_gl_compile_shader(GLenum type, const char *src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024];
        GLsizei ln = 0;
        log[0] = '\0';
        glGetShaderInfoLog(s, (GLsizei)sizeof(log), &ln, log);
        fprintf(stderr, "shader compile failed (%s): %.*s\nSource:\n%.*s\n",
                type == GL_VERTEX_SHADER ? "vertex" : "fragment",
                ln, log, 200, src);
        exit(1);
    }
    return s;
}

static void render_gl_ensure_blit_prog(render_gl_ctx *ctx) {
    if (ctx->blit_prog) return;
    const char *vs =
        "#version 100\n"
        "#ifdef GL_ES\n"
        "precision mediump float;\n"
        "precision mediump int;\n"
        "#endif\n"
        "attribute vec2 a_pos;\n"
        "attribute vec2 a_uv;\n"
        "varying vec2 v_uv;\n"
        "void main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0); }";
    const char *fs =
        "#version 100\n"
        "precision mediump float;\n"
        "varying vec2 v_uv;\n"
        "uniform sampler2D u_tex;\n"
        "uniform float u_brightness;\n"
        "void main(){ vec4 c=texture2D(u_tex,v_uv); gl_FragColor=vec4(c.rgb*u_brightness,c.a); }";
    GLuint v = render_gl_compile_shader(GL_VERTEX_SHADER, vs);
    GLuint f = render_gl_compile_shader(GL_FRAGMENT_SHADER, fs);
    ctx->blit_prog = glCreateProgram();
    glAttachShader(ctx->blit_prog, v);
    glAttachShader(ctx->blit_prog, f);
    glBindAttribLocation(ctx->blit_prog, 0, "a_pos");
    glBindAttribLocation(ctx->blit_prog, 1, "a_uv");
    glLinkProgram(ctx->blit_prog);
    GLint ok;
    glGetProgramiv(ctx->blit_prog, GL_LINK_STATUS, &ok);
    if (!ok) {
        fprintf(stderr, "link fail\n");
        exit(1);
    }
    ctx->blit_u_tex = glGetUniformLocation(ctx->blit_prog, "u_tex");
    ctx->blit_u_brightness = glGetUniformLocation(ctx->blit_prog, "u_brightness");
    glGenBuffers(1, &ctx->blit_vbo);
}

static void render_gl_delete_target(GLuint *tex, GLuint *fbo) {
    if (*tex) {
        glDeleteTextures(1, tex);
        *tex = 0;
    }
    if (*fbo) {
        glDeleteFramebuffers(1, fbo);
        *fbo = 0;
    }
}

static void render_gl_ensure_preview_rt(render_gl_ctx *ctx, int w, int h) {
    if (ctx->preview_tex && ctx->preview_w == w && ctx->preview_h == h) return;
    render_gl_delete_target(&ctx->preview_tex, &ctx->preview_fbo);
    ctx->preview_w = w;
    ctx->preview_h = h;
    glGenTextures(1, &ctx->preview_tex);
    glBindTexture(GL_TEXTURE_2D, ctx->preview_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &ctx->preview_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, ctx->preview_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ctx->preview_tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "Preview FBO incomplete\n");
        exit(1);
    }
}

static void render_gl_ensure_pane_video_capacity(render_gl_ctx *ctx, int pane_index) {
    if (pane_index < 0) return;
    if (pane_index < ctx->pane_vid_cap) return;

    int new_cap = ctx->pane_vid_cap ? ctx->pane_vid_cap : 4;
    while (new_cap <= pane_index) new_cap *= 2;

    GLuint *next_fbos = calloc((size_t)new_cap, sizeof(*next_fbos));
    GLuint *next_texs = calloc((size_t)new_cap, sizeof(*next_texs));
    int *next_ws = calloc((size_t)new_cap, sizeof(*next_ws));
    int *next_hs = calloc((size_t)new_cap, sizeof(*next_hs));
    if (!next_fbos || !next_texs || !next_ws || !next_hs) {
        fprintf(stderr, "pane video target allocation failed\n");
        exit(1);
    }

    if (ctx->pane_vid_cap > 0) {
        memcpy(next_fbos, ctx->pane_vid_fbos, (size_t)ctx->pane_vid_cap * sizeof(*next_fbos));
        memcpy(next_texs, ctx->pane_vid_texs, (size_t)ctx->pane_vid_cap * sizeof(*next_texs));
        memcpy(next_ws, ctx->pane_vid_ws, (size_t)ctx->pane_vid_cap * sizeof(*next_ws));
        memcpy(next_hs, ctx->pane_vid_hs, (size_t)ctx->pane_vid_cap * sizeof(*next_hs));
    }
    free(ctx->pane_vid_fbos);
    free(ctx->pane_vid_texs);
    free(ctx->pane_vid_ws);
    free(ctx->pane_vid_hs);

    ctx->pane_vid_fbos = next_fbos;
    ctx->pane_vid_texs = next_texs;
    ctx->pane_vid_ws = next_ws;
    ctx->pane_vid_hs = next_hs;
    ctx->pane_vid_cap = new_cap;
}

void render_gl_ensure_rt(render_gl_ctx *ctx, int w, int h) {
    if (ctx->rt_tex && ctx->rt_w == w && ctx->rt_h == h) return;
    render_gl_delete_target(&ctx->rt_tex, &ctx->rt_fbo);
    ctx->rt_w = w;
    ctx->rt_h = h;
    glGenTextures(1, &ctx->rt_tex);
    glBindTexture(GL_TEXTURE_2D, ctx->rt_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &ctx->rt_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, ctx->rt_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ctx->rt_tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "FBO incomplete\n");
        exit(1);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}

void render_gl_ensure_video_rt(render_gl_ctx *ctx, int w, int h) {
    if (ctx->vid_tex && ctx->vid_w == w && ctx->vid_h == h) return;
    render_gl_delete_target(&ctx->vid_tex, &ctx->vid_fbo);
    ctx->vid_w = w;
    ctx->vid_h = h;
    glGenTextures(1, &ctx->vid_tex);
    glBindTexture(GL_TEXTURE_2D, ctx->vid_tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &ctx->vid_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, ctx->vid_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ctx->vid_tex, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "Video FBO incomplete\n");
        exit(1);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
}

bool render_gl_ensure_pane_video_rt(render_gl_ctx *ctx, int pane_index, int w, int h) {
    if (!ctx || pane_index < 0) return false;
    render_gl_ensure_pane_video_capacity(ctx, pane_index);
    if (ctx->pane_vid_texs[pane_index] &&
        ctx->pane_vid_ws[pane_index] == w &&
        ctx->pane_vid_hs[pane_index] == h) {
        return false;
    }

    render_gl_delete_target(&ctx->pane_vid_texs[pane_index], &ctx->pane_vid_fbos[pane_index]);
    ctx->pane_vid_ws[pane_index] = w;
    ctx->pane_vid_hs[pane_index] = h;
    glGenTextures(1, &ctx->pane_vid_texs[pane_index]);
    glBindTexture(GL_TEXTURE_2D, ctx->pane_vid_texs[pane_index]);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &ctx->pane_vid_fbos[pane_index]);
    glBindFramebuffer(GL_FRAMEBUFFER, ctx->pane_vid_fbos[pane_index]);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ctx->pane_vid_texs[pane_index], 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "Pane video FBO incomplete\n");
        exit(1);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return true;
}

GLuint render_gl_pane_video_fbo(const render_gl_ctx *ctx, int pane_index) {
    if (!ctx || pane_index < 0 || pane_index >= ctx->pane_vid_cap) return 0;
    return ctx->pane_vid_fbos[pane_index];
}

GLuint render_gl_pane_video_tex(const render_gl_ctx *ctx, int pane_index) {
    if (!ctx || pane_index < 0 || pane_index >= ctx->pane_vid_cap) return 0;
    return ctx->pane_vid_texs[pane_index];
}

void render_gl_blit_rt_to_screen_brightness(render_gl_ctx *ctx, rotation_t rot, float brightness) {
    render_gl_ensure_blit_prog(ctx);
    glUseProgram(ctx->blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, ctx->rt_tex);
    glUniform1i(ctx->blit_u_tex, 0);
    glUniform1f(ctx->blit_u_brightness, brightness < 0.f ? 0.f : (brightness > 1.f ? 1.f : brightness));

    const float L = -1.f, R = 1.f, B = -1.f, T = 1.f;
    const float u0 = 0.f, v0 = 0.f, u1 = 1.f, v1 = 1.f;
    const float quad[] =    { L,B, u0,v1,  R,B, u1,v1,  R,T, u1,v0,  L,B, u0,v1,  R,T, u1,v0,  L,T, u0,v0 };
    const float quad90[] =  { L,B, u1,v1,  R,B, u1,v0,  R,T, u0,v0,  L,B, u1,v1,  R,T, u0,v0,  L,T, u0,v1 };
    const float quad180[] = { L,B, u1,v0,  R,B, u0,v0,  R,T, u0,v1,  L,B, u1,v0,  R,T, u0,v1,  L,T, u1,v1 };
    const float quad270[] = { L,B, u0,v0,  R,B, u0,v1,  R,T, u1,v1,  L,B, u0,v0,  R,T, u1,v1,  L,T, u1,v0 };
    const float *src = quad;
    float verts[24];
    if (rot == ROT_90) src = quad90;
    else if (rot == ROT_180) src = quad180;
    else if (rot == ROT_270) src = quad270;
    memcpy(verts, src, sizeof(verts));

    glBindBuffer(GL_ARRAY_BUFFER, ctx->blit_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)(2 * sizeof(float)));
    glDrawArrays(GL_TRIANGLES, 0, 6);
}

void render_gl_blit_rt_to_screen(render_gl_ctx *ctx, rotation_t rot) {
    render_gl_blit_rt_to_screen_brightness(ctx, rot, 1.f);
}

void render_gl_draw_tex_fullscreen(render_gl_ctx *ctx, GLuint tex) {
    render_gl_ensure_blit_prog(ctx);
    glUseProgram(ctx->blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(ctx->blit_u_tex, 0);
    glUniform1f(ctx->blit_u_brightness, 1.f);
    const float L = -1.f, R = 1.f, B = -1.f, T = 1.f;
    const float verts[] = { L,B, 0,0,  R,B, 1,0,  R,T, 1,1,  L,B, 0,0,  R,T, 1,1,  L,T, 0,1 };
    glBindBuffer(GL_ARRAY_BUFFER, ctx->blit_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)(2 * sizeof(float)));
    glDrawArrays(GL_TRIANGLES, 0, 6);
}

void render_gl_draw_tex_to_rt(render_gl_ctx *ctx, GLuint tex, int x, int y, int w, int h, int rt_w, int rt_h) {
    render_gl_ensure_blit_prog(ctx);
    glUseProgram(ctx->blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(ctx->blit_u_tex, 0);
    glUniform1f(ctx->blit_u_brightness, 1.f);
    const float l = (2.0f * x / rt_w) - 1.0f;
    const float r = (2.0f * (x + w) / rt_w) - 1.0f;
    const float t = 1.0f - (2.0f * y / rt_h);
    const float b = 1.0f - (2.0f * (y + h) / rt_h);
    const float verts[] = { l,b, 0,0,  r,b, 1,0,  r,t, 1,1,  l,b, 0,0,  r,t, 1,1,  l,t, 0,1 };
    glBindBuffer(GL_ARRAY_BUFFER, ctx->blit_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)(2 * sizeof(float)));
    glDrawArrays(GL_TRIANGLES, 0, 6);
}

static bool render_gl_write_rgba_file(const char *path, const unsigned char *pixels,
                                      int w, int h) {
    if (!path || !pixels || w <= 0 || h <= 0) return false;
    size_t pixel_bytes = (size_t)w * (size_t)h * 4u;
    char tmp_path[4096];
    int tmp_len = snprintf(tmp_path, sizeof(tmp_path), "%s.tmp.%ld", path, (long)getpid());
    if (tmp_len <= 0 || (size_t)tmp_len >= sizeof(tmp_path)) return false;

    FILE *f = fopen(tmp_path, "wb");
    if (!f) return false;
    unsigned char header[8] = {
        (unsigned char)(w), (unsigned char)(w >> 8), (unsigned char)(w >> 16), (unsigned char)(w >> 24),
        (unsigned char)(h), (unsigned char)(h >> 8), (unsigned char)(h >> 16), (unsigned char)(h >> 24),
    };
    bool ok = fwrite(header, 1, sizeof(header), f) == sizeof(header) &&
              fwrite(pixels, 1, pixel_bytes, f) == pixel_bytes;

    if (fclose(f) != 0) ok = false;
    if (ok && rename(tmp_path, path) != 0) ok = false;
    if (!ok) {
        fprintf(stderr, "preview frame write failed for %s: %s\n", path, strerror(errno));
        remove(tmp_path);
    }
    return ok;
}

static bool render_gl_has_extension(const char *extensions, const char *wanted) {
    if (!extensions || !wanted || !*wanted || strchr(wanted, ' ')) return false;
    size_t wanted_len = strlen(wanted);
    const char *at = extensions;
    while ((at = strstr(at, wanted)) != NULL) {
        bool starts_token = at == extensions || at[-1] == ' ';
        bool ends_token = at[wanted_len] == '\0' || at[wanted_len] == ' ';
        if (starts_token && ends_token) return true;
        at += wanted_len;
    }
    return false;
}

static void render_gl_check_preview_pbo(render_gl_ctx *ctx) {
    if (ctx->preview_pbo_checked) return;
    ctx->preview_pbo_checked = true;
    const char *extensions = (const char *)glGetString(GL_EXTENSIONS);
    bool has_pbo = render_gl_has_extension(extensions, "GL_NV_pixel_buffer_object") ||
                   render_gl_has_extension(extensions, "GL_EXT_pixel_buffer_object");
    bool has_map_range = render_gl_has_extension(extensions, "GL_EXT_map_buffer_range");
    if (!has_pbo || !has_map_range) return;
    render_gl_map_buffer_range = (render_gl_map_buffer_range_fn)eglGetProcAddress("glMapBufferRangeEXT");
    render_gl_unmap_buffer = (render_gl_unmap_buffer_fn)eglGetProcAddress("glUnmapBufferOES");
    ctx->preview_pbo_supported = render_gl_map_buffer_range && render_gl_unmap_buffer;
}

static bool render_gl_ensure_preview_pbos(render_gl_ctx *ctx, size_t pixel_bytes) {
    if (ctx->preview_pbo_size == pixel_bytes && ctx->preview_pbos[0] && ctx->preview_pbos[1]) return true;
    if (ctx->preview_pbos[0] || ctx->preview_pbos[1]) glDeleteBuffers(2, ctx->preview_pbos);
    memset(ctx->preview_pbos, 0, sizeof(ctx->preview_pbos));
    ctx->preview_pbo_size = 0;
    ctx->preview_pbo_pending_index = -1;
    glGenBuffers(2, ctx->preview_pbos);
    if (!ctx->preview_pbos[0] || !ctx->preview_pbos[1]) return false;
    for (int i = 0; i < 2; ++i) {
        glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, ctx->preview_pbos[i]);
        glBufferData(GL_PIXEL_PACK_BUFFER_NV, (GLsizeiptr)pixel_bytes, NULL, GL_STREAM_READ);
    }
    glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, 0);
    if (glGetError() != GL_NO_ERROR) {
        glDeleteBuffers(2, ctx->preview_pbos);
        memset(ctx->preview_pbos, 0, sizeof(ctx->preview_pbos));
        return false;
    }
    ctx->preview_pbo_size = pixel_bytes;
    ctx->preview_pbo_write_index = 0;
    return true;
}

static bool render_gl_write_current_rgba_frame_pbo(render_gl_ctx *ctx, const char *path,
                                                   int w, int h, size_t pixel_bytes) {
    if (!render_gl_ensure_preview_pbos(ctx, pixel_bytes)) {
        ctx->preview_pbo_supported = false;
        return false;
    }

    if (ctx->preview_pbo_pending_index >= 0) {
        int pending = ctx->preview_pbo_pending_index;
        glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, ctx->preview_pbos[pending]);
        const unsigned char *pixels = render_gl_map_buffer_range(
            GL_PIXEL_PACK_BUFFER_NV, 0, (GLsizeiptr)ctx->preview_pbo_size, GL_MAP_READ_BIT_EXT);
        if (!pixels) {
            glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, 0);
            return false;
        }
        bool ok = render_gl_write_rgba_file(ctx->preview_pbo_pending_path, pixels,
                                            ctx->preview_pbo_pending_w, ctx->preview_pbo_pending_h);
        if (!render_gl_unmap_buffer(GL_PIXEL_PACK_BUFFER_NV)) ok = false;
        glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, 0);
        ctx->preview_pbo_pending_index = -1;
        return ok;
    }

    int write_index = ctx->preview_pbo_write_index;
    glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, ctx->preview_pbos[write_index]);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, (void *)0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER_NV, 0);
    if (glGetError() != GL_NO_ERROR) return false;
    int path_len = snprintf(ctx->preview_pbo_pending_path, sizeof(ctx->preview_pbo_pending_path), "%s", path);
    if (path_len <= 0 || (size_t)path_len >= sizeof(ctx->preview_pbo_pending_path)) return false;
    ctx->preview_pbo_pending_index = write_index;
    ctx->preview_pbo_pending_w = w;
    ctx->preview_pbo_pending_h = h;
    ctx->preview_pbo_write_index = 1 - write_index;
    return false;
}

bool render_gl_write_current_rgba_frame(render_gl_ctx *ctx, const char *path, int w, int h) {
    if (!ctx || !path || w <= 0 || h <= 0) return false;
    size_t pixel_bytes = (size_t)w * (size_t)h * 4u;

    render_gl_check_preview_pbo(ctx);
    if (ctx->preview_pbo_supported) {
        return render_gl_write_current_rgba_frame_pbo(ctx, path, w, h, pixel_bytes);
    }

    if (ctx->preview_pixels_cap < pixel_bytes) {
        unsigned char *next = realloc(ctx->preview_pixels, pixel_bytes);
        if (!next) return false;
        ctx->preview_pixels = next;
        ctx->preview_pixels_cap = pixel_bytes;
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, ctx->preview_pixels);
    if (glGetError() != GL_NO_ERROR) return false;
    return render_gl_write_rgba_file(path, ctx->preview_pixels, w, h);
}

bool render_gl_write_preview_frame(render_gl_ctx *ctx, const char *path, GLuint source_tex,
                                   int source_w, int source_h, int max_edge) {
    if (!ctx || !path || !source_tex || source_w <= 0 || source_h <= 0 || max_edge <= 0) return false;
    int out_w = source_w;
    int out_h = source_h;
    if (source_w > max_edge || source_h > max_edge) {
        if (source_w >= source_h) {
            out_w = max_edge;
            out_h = (int)(((int64_t)source_h * max_edge) / source_w);
        } else {
            out_h = max_edge;
            out_w = (int)(((int64_t)source_w * max_edge) / source_h);
        }
        if (out_w < 1) out_w = 1;
        if (out_h < 1) out_h = 1;
    }

    render_gl_ensure_preview_rt(ctx, out_w, out_h);
    glBindFramebuffer(GL_FRAMEBUFFER, ctx->preview_fbo);
    render_gl_reset_state_2d();
    glDisable(GL_BLEND);
    glViewport(0, 0, out_w, out_h);
    render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
    render_gl_draw_tex_fullscreen(ctx, source_tex);
    return render_gl_write_current_rgba_frame(ctx, path, out_w, out_h);
}

void render_gl_destroy(render_gl_ctx *ctx) {
    if (!ctx) return;
    render_gl_delete_target(&ctx->rt_tex, &ctx->rt_fbo);
    render_gl_delete_target(&ctx->vid_tex, &ctx->vid_fbo);
    render_gl_delete_target(&ctx->preview_tex, &ctx->preview_fbo);
    for (int i = 0; i < ctx->pane_vid_cap; ++i) {
        render_gl_delete_target(&ctx->pane_vid_texs[i], &ctx->pane_vid_fbos[i]);
    }
    free(ctx->pane_vid_fbos);
    free(ctx->pane_vid_texs);
    free(ctx->pane_vid_ws);
    free(ctx->pane_vid_hs);
    free(ctx->preview_pixels);
    if (ctx->preview_pbos[0] || ctx->preview_pbos[1]) glDeleteBuffers(2, ctx->preview_pbos);
    ctx->pane_vid_fbos = NULL;
    ctx->pane_vid_texs = NULL;
    ctx->pane_vid_ws = NULL;
    ctx->pane_vid_hs = NULL;
    ctx->preview_pixels = NULL;
    ctx->preview_pixels_cap = 0;
    memset(ctx->preview_pbos, 0, sizeof(ctx->preview_pbos));
    ctx->preview_pbo_size = 0;
    ctx->preview_pbo_pending_index = -1;
    ctx->pane_vid_cap = 0;
    if (ctx->blit_vbo) {
        glDeleteBuffers(1, &ctx->blit_vbo);
        ctx->blit_vbo = 0;
    }
    if (ctx->blit_prog) {
        glDeleteProgram(ctx->blit_prog);
        ctx->blit_prog = 0;
    }
    ctx->rt_w = 0;
    ctx->rt_h = 0;
    ctx->vid_w = 0;
    ctx->vid_h = 0;
    ctx->preview_w = 0;
    ctx->preview_h = 0;
    ctx->blit_u_tex = -1;
}
