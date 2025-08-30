#define _GNU_SOURCE
#include "osd.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

#include <fontconfig/fontconfig.h>
#include <ft2build.h>
#include FT_FREETYPE_H

typedef struct {
    FT_Library ftlib;
    FT_Face face;
    int px_size;
    int baseline;
} font_ctx;

struct osd_ctx {
    font_ctx font;
    char *text;
    GLuint tex; int tw, th; // texture of rendered text
};

static void die_local(const char *msg) {
    fprintf(stderr, "osd: %s\n", msg);
    exit(1);
}

static char* find_monospace_font(void) {
    FcInit();
    FcPattern *pat = FcNameParse((const FcChar8*)"monospace");
    FcConfigSubstitute(NULL, pat, FcMatchPattern);
    FcDefaultSubstitute(pat);
    FcResult res; FcPattern *match = FcFontMatch(NULL, pat, &res); FcPatternDestroy(pat);
    if (!match) return NULL;
    FcChar8 *file = NULL;
    if (FcPatternGetString(match, FC_FILE, 0, &file) == FcResultMatch) {
        char *out = strdup((const char*)file);
        FcPatternDestroy(match);
        return out;
    }
    FcPatternDestroy(match); return NULL;
}

static void font_init(font_ctx *f, int px_size) {
    if (FT_Init_FreeType(&f->ftlib)) die_local("FT_Init_FreeType");
    char *path = find_monospace_font();
    if (!path) die_local("fontconfig monospace not found");
    if (FT_New_Face(f->ftlib, path, 0, &f->face)) die_local("FT_New_Face");
    free(path);
    FT_Set_Pixel_Sizes(f->face, 0, px_size);
    f->px_size = px_size;
    f->baseline = (f->face->size->metrics.ascender + 31) / 64;
}

static void font_destroy(font_ctx *f) {
    if (f->face) FT_Done_Face(f->face);
    if (f->ftlib) FT_Done_FreeType(f->ftlib);
}

static void render_text_to_rgba(font_ctx *f, const char *text, unsigned char **out, int *w, int *h) {
    // First pass: measure
    int pen_x = 0; int max_w = 0; int line_h = f->px_size + 6; int lines = 1;
    for (const unsigned char *p=(const unsigned char*)text; *p; ++p) {
        if (*p=='\n'){ if (pen_x>max_w) max_w=pen_x; pen_x=0; lines++; continue; }
        if (FT_Load_Char(f->face, *p, FT_LOAD_RENDER)) continue;
        pen_x += (f->face->glyph->advance.x + 31)/64;
    }
    if (pen_x>max_w) max_w=pen_x;
    *w = max_w ? max_w : 1; *h = lines * line_h;
    size_t sz = (size_t)(*w) * (*h) * 4; unsigned char *buf = calloc(sz,1);
    // Second pass: render
    int x=0,y=0; for (const unsigned char *p=(const unsigned char*)text; *p; ++p){
        if (*p=='\n'){ x=0; y+=line_h; continue; }
        if (FT_Load_Char(f->face, *p, FT_LOAD_RENDER)) continue;
        FT_GlyphSlot g = f->face->glyph;
        int gx = x + g->bitmap_left;
        int gy = y + f->baseline - g->bitmap_top;
        for (int yy=0; yy<(int)g->bitmap.rows; yy++){
            int py = gy + yy; if (py<0 || py>=*h) continue;
            for (int xx=0; xx<(int)g->bitmap.width; xx++){
                int px = gx + xx; if (px<0 || px>=*w) continue;
                unsigned char a = g->bitmap.buffer[yy*g->bitmap.width + xx];
                unsigned char *dst = &buf[(size_t)(py * (*w) + px) * 4];
                // white text with alpha
                dst[0] = 255; dst[1] = 255; dst[2] = 255; dst[3] = a;
            }
        }
        x += (g->advance.x + 31)/64;
    }
    *out = buf;
}

osd_ctx* osd_create(int font_px){ osd_ctx* o = calloc(1,sizeof *o); font_init(&o->font, font_px>0?font_px:20); glGenTextures(1,&o->tex); return o; }
void osd_destroy(osd_ctx* o){ if(!o) return; if(o->tex) glDeleteTextures(1,&o->tex); free(o->text); font_destroy(&o->font); free(o);} 

void osd_set_text(osd_ctx* o, const char *text){ if(!o) return; free(o->text); o->text = text?strdup(text):NULL; }

static GLuint osd_prog=0, osd_vbo=0; static GLint osd_u_tex=-1;
static GLuint compile_shader_dbg(GLenum type, const char *src){ GLuint s=glCreateShader(type); glShaderSource(s,1,&src,NULL); glCompileShader(s); GLint ok; glGetShaderiv(s,GL_COMPILE_STATUS,&ok); if(!ok){ char log[512]; GLsizei ln=0; glGetShaderInfoLog(s,sizeof log,&ln,log); fprintf(stderr,"osd shader compile failed (%s): %.*s\nSource:\n%.*s\n", type==GL_VERTEX_SHADER?"vertex":"fragment", ln, log, 200, src); exit(1);} return s; }
static void ensure_prog(void){ if(osd_prog) return; const char* vs="#version 100\n#ifdef GL_ES\nprecision mediump float;\nprecision mediump int;\n#endif\nattribute vec2 a_pos; attribute vec2 a_uv; varying vec2 v_uv; void main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0,1);}"; const char* fs="#version 100\nprecision mediump float; varying vec2 v_uv; uniform sampler2D u_tex; void main(){ gl_FragColor=texture2D(u_tex,v_uv);}"; GLuint v=compile_shader_dbg(GL_VERTEX_SHADER,vs), f=compile_shader_dbg(GL_FRAGMENT_SHADER,fs); osd_prog=glCreateProgram(); glAttachShader(osd_prog,v); glAttachShader(osd_prog,f); glBindAttribLocation(osd_prog,0,"a_pos"); glBindAttribLocation(osd_prog,1,"a_uv"); glLinkProgram(osd_prog); osd_u_tex=glGetUniformLocation(osd_prog,"u_tex"); glGenBuffers(1,&osd_vbo);} 

void osd_draw(osd_ctx* o, int x, int y, int fb_w, int fb_h){
    if(!o||!o->text) return;
    ensure_prog();
    unsigned char *rgba=NULL; int w=0,h=0;
    render_text_to_rgba(&o->font, o->text, &rgba, &w, &h);
    if(w<=0||h<=0){ free(rgba); return; }
    glBindTexture(GL_TEXTURE_2D, o->tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,rgba);
    free(rgba);
    float L = (2.0f * x / fb_w) - 1.0f; float R = (2.0f * (x + w) / fb_w) - 1.0f;
    float T = 1.0f - (2.0f * y / fb_h); float B = 1.0f - (2.0f * (y + h) / fb_h);
    float verts[] = { L,B, 0,0,  R,B, 1,0,  R,T, 1,1,  L,B, 0,0,  R,T, 1,1,  L,T, 0,1 };
    glUseProgram(osd_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, o->tex);
    glUniform1i(osd_u_tex, 0);
    glBindBuffer(GL_ARRAY_BUFFER, osd_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)(2*sizeof(float)));
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDrawArrays(GL_TRIANGLES,0,6);
    glDisable(GL_BLEND);
}
