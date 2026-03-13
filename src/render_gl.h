#ifndef RENDER_GL_H
#define RENDER_GL_H

#include <stdbool.h>

#include <GLES2/gl2.h>

#include "options.h"

typedef struct {
    GLuint rt_fbo;
    GLuint rt_tex;
    int rt_w;
    int rt_h;
    GLuint blit_prog;
    GLuint blit_vbo;
    GLint blit_u_tex;
    GLuint vid_fbo;
    GLuint vid_tex;
    int vid_w;
    int vid_h;
} render_gl_ctx;

void render_gl_reset_state_2d(void);
void render_gl_clear_color(float r, float g, float b, float a);
void render_gl_check(bool debug, const char *stage);
void render_gl_draw_border_rect(int x, int y, int w, int h, int thickness, int fb_w, int fb_h,
                                float r, float g, float b, float a);
void render_gl_ensure_rt(render_gl_ctx *ctx, int w, int h);
void render_gl_ensure_video_rt(render_gl_ctx *ctx, int w, int h);
void render_gl_blit_rt_to_screen(render_gl_ctx *ctx, rotation_t rot);
void render_gl_draw_tex_fullscreen(render_gl_ctx *ctx, GLuint tex);
void render_gl_draw_tex_to_rt(render_gl_ctx *ctx, GLuint tex, int x, int y, int w, int h, int rt_w, int rt_h);
void render_gl_destroy(render_gl_ctx *ctx);

#endif
