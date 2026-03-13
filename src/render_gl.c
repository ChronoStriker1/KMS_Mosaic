#include "render_gl.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
        "void main(){ gl_FragColor = texture2D(u_tex, v_uv); }";
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

void render_gl_blit_rt_to_screen(render_gl_ctx *ctx, rotation_t rot) {
    render_gl_ensure_blit_prog(ctx);
    glUseProgram(ctx->blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, ctx->rt_tex);
    glUniform1i(ctx->blit_u_tex, 0);

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

void render_gl_draw_tex_fullscreen(render_gl_ctx *ctx, GLuint tex) {
    render_gl_ensure_blit_prog(ctx);
    glUseProgram(ctx->blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(ctx->blit_u_tex, 0);
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

void render_gl_destroy(render_gl_ctx *ctx) {
    if (!ctx) return;
    render_gl_delete_target(&ctx->rt_tex, &ctx->rt_fbo);
    render_gl_delete_target(&ctx->vid_tex, &ctx->vid_fbo);
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
    ctx->blit_u_tex = -1;
}
