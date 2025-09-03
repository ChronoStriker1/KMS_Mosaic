#define _GNU_SOURCE
#include "term_pane.h"
#include "color.h"

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

#include <fontconfig/fontconfig.h>
#include <ft2build.h>
#include FT_FREETYPE_H

// No libvterm callbacks are registered to avoid ABI mismatches.

typedef struct {
    uint32_t codepoint;
    int w, h, bearing_x, bearing_y, advance;
    unsigned char *bitmap; // 8-bit alpha
} glyph_bitmap;

typedef struct {
    FT_Library ftlib;
    FT_Face face;
    int px_size;
    int cell_w, cell_h, baseline;
    // Simple glyph cache
    struct {
        uint32_t cp;
        glyph_bitmap gb;
        int used;
    } *cache;
    int cache_cap;
    int cache_count;
} font_ctx;

typedef struct {
    GLuint tex;
    int tex_w, tex_h;
    bool dirty;
    int dirty_y0, dirty_y1; // deprecated single range
    struct { int y0, y1; } dirty_ranges[4];
    int dirty_count;
    uint8_t *pixels; // RGBA8
} pane_tex;

struct term_pane {
    pane_layout layout;
    VTerm *vt;
    VTermScreen *vts;
    int pty_master;
    pid_t child_pid;
    bool dirty;

    font_ctx font;
    pane_tex surface;
    uint8_t alpha;

    // Row-level change detection
    uint32_t *row_hash; // length = layout.rows

    int use_shell_cmd;
    char *shell_cmd;
    char **argv_dup;
};

static void die(const char *msg) {
    perror(msg);
    exit(1);
}

static char* find_monospace_font(void) {
    FcInit();
    FcPattern *pat = FcNameParse((const FcChar8*)"monospace");
    FcConfigSubstitute(NULL, pat, FcMatchPattern);
    FcDefaultSubstitute(pat);
    FcResult res;
    FcPattern *match = FcFontMatch(NULL, pat, &res);
    FcPatternDestroy(pat);
    if (!match) return NULL;
    FcChar8 *file = NULL;
    if (FcPatternGetString(match, FC_FILE, 0, &file) == FcResultMatch) {
        char *out = strdup((const char*)file);
        FcPatternDestroy(match);
        return out;
    }
    FcPatternDestroy(match);
    return NULL;
}

static void font_init(font_ctx *f, int px_size) {
    if (FT_Init_FreeType(&f->ftlib)) die("FT_Init_FreeType");
    char *path = find_monospace_font();
    if (!path) die("fontconfig monospace not found");
    if (FT_New_Face(f->ftlib, path, 0, &f->face)) die("FT_New_Face");
    free(path);
    FT_Set_Pixel_Sizes(f->face, 0, px_size);
    f->px_size = px_size;
    // Measure cell from 'M'
    FT_Load_Char(f->face, 'M', FT_LOAD_RENDER);
    f->cell_w = (f->face->glyph->advance.x + 31) / 64; // 26.6 to int
    f->cell_h = px_size + 2; // add small leading
    f->baseline = (f->face->size->metrics.ascender + 31) / 64;
    f->cache = NULL; f->cache_cap = 0; f->cache_count = 0;
}

static void font_destroy(font_ctx *f) {
    if (f->cache) {
        for (int i=0;i<f->cache_count;i++) free(f->cache[i].gb.bitmap);
        free(f->cache);
    }
    if (f->face) FT_Done_Face(f->face);
    if (f->ftlib) FT_Done_FreeType(f->ftlib);
}

static glyph_bitmap* get_glyph(font_ctx *f, uint32_t cp) {
    for (int i=0;i<f->cache_count;i++) if (f->cache[i].used && f->cache[i].cp==cp) return &f->cache[i].gb;
    if (FT_Load_Char(f->face, cp, FT_LOAD_RENDER)) return NULL;
    FT_GlyphSlot slot = f->face->glyph;
    if (f->cache_count == f->cache_cap) {
        int nc = f->cache_cap ? f->cache_cap*2 : 256;
        f->cache = realloc(f->cache, (size_t)nc * sizeof(*f->cache));
        for (int i=f->cache_cap;i<nc;i++) f->cache[i].used = 0;
        f->cache_cap = nc;
    }
    int idx = f->cache_count++;
    f->cache[idx].cp = cp; f->cache[idx].used = 1;
    glyph_bitmap *g = &f->cache[idx].gb;
    g->codepoint = cp;
    g->w = slot->bitmap.width; g->h = slot->bitmap.rows;
    g->bearing_x = slot->bitmap_left; g->bearing_y = slot->bitmap_top;
    g->advance = (slot->advance.x + 31)/64;
    size_t sz = (size_t)g->w * g->h; g->bitmap = malloc(sz);
    memcpy(g->bitmap, slot->bitmap.buffer, sz);
    return g;
}

static void pane_tex_init(pane_tex *t, int w, int h) {
    t->tex_w = w; t->tex_h = h; t->dirty = true;
    t->dirty_y0 = 0; t->dirty_y1 = h; t->dirty_count = 1; t->dirty_ranges[0].y0 = 0; t->dirty_ranges[0].y1 = h;
    glGenTextures(1, &t->tex);
    glBindTexture(GL_TEXTURE_2D, t->tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    t->pixels = calloc((size_t)w * h * 4, 1);
}

static void pane_tex_destroy(pane_tex *t) {
    if (t->tex) glDeleteTextures(1, &t->tex);
    free(t->pixels);
    memset(t, 0, sizeof(*t));
}

static uint32_t mix_hash(uint32_t h, uint32_t x){ h ^= x + 0x9e3779b9u + (h<<6) + (h>>2); return h; }
static uint32_t pane_row_hash(term_pane *tp, int y){
    uint32_t h = 2166136261u; VTermScreenCell cell;
    for (int x=0; x<tp->layout.cols; x++){
        memset(&cell,0,sizeof cell);
        vterm_screen_get_cell(tp->vts, (VTermPos){.row=y,.col=x}, &cell);
        uint32_t cp = cell.chars[0]; uint32_t fg=0,bg=0;
        if (cell.fg.type == VTERM_COLOR_INDEXED) fg = cell.fg.indexed.idx;
        else if (cell.fg.type == VTERM_COLOR_RGB) fg = (cell.fg.rgb.red<<16)|(cell.fg.rgb.green<<8)|cell.fg.rgb.blue;
        if (cell.bg.type == VTERM_COLOR_INDEXED) bg = cell.bg.indexed.idx;
        else if (cell.bg.type == VTERM_COLOR_RGB) bg = (cell.bg.rgb.red<<16)|(cell.bg.rgb.green<<8)|cell.bg.rgb.blue;
        h = mix_hash(h, cp); h = mix_hash(h, fg); h = mix_hash(h, bg);
    }
    return h;
}

// Very small shader to draw the pane texture
static GLuint pane_program = 0, pane_vbo = 0;
static GLint u_tex = -1, a_pos = -1, a_uv = -1;

static GLuint compile_shader(GLenum type, const char *src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024]; GLsizei ln=0; log[0]='\0';
        glGetShaderInfoLog(s, sizeof log, &ln, log);
        fprintf(stderr, "pane shader compile failed (%s): %.*s\nSource:\n%.*s\n",
                type==GL_VERTEX_SHADER?"vertex":"fragment",
                ln, log, 200, src);
        exit(1);
    }
    return s;
}

static void ensure_pane_program(void) {
    if (pane_program) return;
    const char *vs =
        "#version 100\n"
        "#ifdef GL_ES\n"
        "precision mediump float;\n"
        "precision mediump int;\n"
        "#endif\n"
        "attribute vec2 a_pos;\n"
        "attribute vec2 a_uv;\n"
        "varying vec2 v_uv;\n"
        "void main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0);}";
    const char *fs =
        "#version 100\n"
        "precision mediump float;\n"
        "varying vec2 v_uv;\n"
        "uniform sampler2D u_tex;\n"
        "void main(){ gl_FragColor = texture2D(u_tex, v_uv);}";
    GLuint vs_id = compile_shader(GL_VERTEX_SHADER, vs);
    GLuint fs_id = compile_shader(GL_FRAGMENT_SHADER, fs);
    pane_program = glCreateProgram();
    glAttachShader(pane_program, vs_id);
    glAttachShader(pane_program, fs_id);
    glBindAttribLocation(pane_program, 0, "a_pos");
    glBindAttribLocation(pane_program, 1, "a_uv");
    glLinkProgram(pane_program);
    GLint ok=0; glGetProgramiv(pane_program, GL_LINK_STATUS, &ok);
    if (!ok) { fprintf(stderr, "link error\n"); exit(1);}    
    a_pos = 0; a_uv = 1; u_tex = glGetUniformLocation(pane_program, "u_tex");
    glGenBuffers(1, &pane_vbo);
}

static void draw_textured_quad(GLuint tex, int x, int y, int w, int h, int fb_w, int fb_h) {
    ensure_pane_program();
    // Convert to NDC
    float L = (2.0f * x / fb_w) - 1.0f;
    float R = (2.0f * (x + w) / fb_w) - 1.0f;
    float T = 1.0f - (2.0f * y / fb_h);
    float B = 1.0f - (2.0f * (y + h) / fb_h);
    float verts[] = {
        L,B, 0,0,
        R,B, 1,0,
        R,T, 1,1,
        L,B, 0,0,
        R,T, 1,1,
        L,T, 0,1
    };
    glUseProgram(pane_program);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(u_tex, 0);
    glBindBuffer(GL_ARRAY_BUFFER, pane_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(a_pos);
    glVertexAttribPointer(a_pos, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)0);
    glEnableVertexAttribArray(a_uv);
    glVertexAttribPointer(a_uv, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)(2*sizeof(float)));
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glDisable(GL_BLEND);
}

static void set_pty_winsize(int pty_fd, int cols, int rows) {
    struct winsize ws = { .ws_col = (unsigned short)cols, .ws_row = (unsigned short)rows };
    ioctl(pty_fd, TIOCSWINSZ, &ws);
}

static int spawn_pty_argv(char *const argv[], int *master_out) {
    int mfd = posix_openpt(O_RDWR | O_NOCTTY);
    if (mfd < 0) die("posix_openpt");
    if (grantpt(mfd) < 0 || unlockpt(mfd) < 0) die("grant/unlock pt");
    char *slavename = ptsname(mfd);
    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {
        setsid();
        int sfd = open(slavename, O_RDWR);
        if (sfd < 0) _exit(1);
        ioctl(sfd, TIOCSCTTY, 0);
        dup2(sfd, 0); dup2(sfd, 1); dup2(sfd, 2);
        close(sfd); close(mfd);
        setenv("TERM", "xterm-256color", 1);
        execvp(argv[0], argv);
        _exit(1);
    }
    *master_out = mfd;
    return pid;
}

static int spawn_pty_shell(const char *cmd, int *master_out) {
    int mfd = posix_openpt(O_RDWR | O_NOCTTY);
    if (mfd < 0) die("posix_openpt");
    if (grantpt(mfd) < 0 || unlockpt(mfd) < 0) die("grant/unlock pt");
    char *slavename = ptsname(mfd);
    pid_t pid = fork();
    if (pid < 0) die("fork");
    if (pid == 0) {
        setsid();
        int sfd = open(slavename, O_RDWR);
        if (sfd < 0) _exit(1);
        ioctl(sfd, TIOCSCTTY, 0);
        dup2(sfd, 0); dup2(sfd, 1); dup2(sfd, 2);
        close(sfd); close(mfd);
        setenv("TERM", "xterm-256color", 1);
        execl("/bin/sh", "sh", "-lc", cmd, (char*)NULL);
        _exit(1);
    }
    *master_out = mfd;
    return pid;
}

static char **dup_argv(char *const argv[]){
    size_t n=0; while (argv && argv[n]) n++;
    char **copy = calloc(n+1, sizeof(char*));
    for (size_t i=0;i<n;i++) copy[i] = strdup(argv[i]);
    copy[n]=NULL; return copy;
}

static void free_argv(char **argv){ if(!argv) return; for (size_t i=0; argv[i]; i++) free(argv[i]); free(argv); }

static int sattr_to_rgb_idx(const VTermScreenCell *cell, int is_fg) {
    VTermColor c = is_fg ? cell->fg : cell->bg;
    // libvterm represents default fg/bg as distinct enum values
    if (c.type == VTERM_COLOR_DEFAULT_FG || c.type == VTERM_COLOR_DEFAULT_BG)
        return is_fg ? 7 : 0; // white fg / black bg defaults
    if (c.type == VTERM_COLOR_INDEXED) return c.indexed.idx;
    if (c.type == VTERM_COLOR_RGB) {
        // Find closest in 256 palette
        int best_idx = 15; int best_d = 1<<30;
        for (int i=0;i<256;i++){
            rgb8 cc = color_from_index(i);
            int dr = (int)cc.r - c.rgb.red;
            int dg = (int)cc.g - c.rgb.green;
            int db = (int)cc.b - c.rgb.blue;
            int d = dr*dr+dg*dg+db*db;
            if (d<best_d){best_d=d;best_idx=i;}
        }
        return best_idx;
    }
    return is_fg ? 7 : 0;
}

// Helpers to draw simple box-drawing lines into the pane texture
static void draw_h_line(pane_tex *tex, int y, int xstart, int xend,
                        int x0, int x1, int y0, int y1,
                        int thickness, rgb8 fgc, uint8_t alpha) {
    if (xstart > xend) { int t = xstart; xstart = xend; xend = t; }
    if (y < y0) y = y0;
    if (y >= y1) y = y1 - 1;
    for (int ty = y - thickness/2; ty <= y + thickness/2; ty++) {
        if (ty < y0 || ty >= y1) continue;
        int xs = xstart < x0 ? x0 : xstart;
        int xe = xend   > x1 ? x1 : xend;
        if (xs < 0) xs = 0;
        if (xe > tex->tex_w) xe = tex->tex_w;
        if (xs >= xe) continue;
        uint8_t *row = tex->pixels + (size_t)ty * tex->tex_w * 4 + xs * 4;
        for (int x = xs; x < xe && x < tex->tex_w; x++) {
            row[0] = fgc.r; row[1] = fgc.g; row[2] = fgc.b; row[3] = alpha; row += 4;
        }
    }
}

static void draw_v_line(pane_tex *tex, int x, int ystart, int yend,
                        int x0, int x1, int y0, int y1,
                        int thickness, rgb8 fgc, uint8_t alpha) {
    if (ystart > yend) { int t = ystart; ystart = yend; yend = t; }
    if (x < x0) x = x0;
    if (x >= x1) x = x1 - 1;
    int ys = ystart < y0 ? y0 : ystart;
    int ye = yend   > y1 ? y1 : yend;
    if (ys < 0) ys = 0;
    if (ye > tex->tex_h) ye = tex->tex_h;
    for (int y = ys; y < ye && y < tex->tex_h; y++) {
        for (int tx = x - thickness/2; tx <= x + thickness/2; tx++) {
            if (tx < x0 || tx >= x1) continue;
            if (tx < 0 || tx >= tex->tex_w) continue;
            uint8_t *p = tex->pixels + (size_t)y * tex->tex_w * 4 + tx * 4;
            p[0] = fgc.r; p[1] = fgc.g; p[2] = fgc.b; p[3] = alpha;
        }
    }
}

static void composite_cell(term_pane *tp, int cx, int cy, const VTermScreenCell *cell) {
    font_ctx *font = &tp->font;
    pane_tex *tex = &tp->surface;
    uint8_t alpha = tp->alpha;
    // Fill background
    int x0 = cx * font->cell_w;
    int y0 = cy * font->cell_h;
    int x1 = x0 + font->cell_w;
    int y1 = y0 + font->cell_h;
    rgb8 bgc = color_from_index(sattr_to_rgb_idx(cell, 0));
    for (int y=y0; y<y1; y++) {
        uint8_t *row = tex->pixels + (size_t)y * tex->tex_w * 4 + x0 * 4;
        for (int x=x0; x<x1; x++) {
            row[0]=bgc.r; row[1]=bgc.g; row[2]=bgc.b; row[3]=alpha; row+=4;
        }
    }
    // Foreground glyphs (support wide=1 only; treat wide as '?')
    uint32_t cp = cell->chars[0];
    if (cp == 0) return;
    // Handle Unicode box-drawing with simple vector lines for clarity
    if (cp >= 0x2500 && cp <= 0x257F) {
        rgb8 fgc = color_from_index(sattr_to_rgb_idx(cell, 1));
        int thickness = font->cell_h / 8; if (thickness < 1) thickness = 1; if (thickness > 2) thickness = 2;
        int cxm = (x0 + x1) / 2; // center x
        int cym = (y0 + y1) / 2; // center y
        switch (cp) {
            case 0x2500: case 0x2501: // ─
                draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2502: case 0x2503: // │
                draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x250C: // ┌
                draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2510: // ┐
                draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2514: // └
                draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2518: // ┘
                draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x253C: // ┼
                draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x252C: // ┬
                draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2534: // ┴
                draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x251C: // ├
                draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            case 0x2524: // ┤
                draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
            // Mixed single/double joins: approximate by drawing both segments slightly thicker
            case 0x256B: // ╋ (thick cross)
            case 0x256A: // ╊
            case 0x256D: // ╭
            case 0x256E: // ╮
            case 0x256F: // ╯
            case 0x2570: // ╰
            case 0x2523: // ┣
            case 0x252B: // ┫
            case 0x2533: // ┳
            case 0x253B: // ┻
            case 0x254B: // ╋
                { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2550: // double ─
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2551: // double │
            { int t2 = thickness+1; if (t2>3) t2=3; draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2554: // double ┌
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2557: // double ┐
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x255A: // double └
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x255D: // double ┘
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x256C: // double ┼
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2566: // double ┬
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, cym, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2569: // double ┴
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, cym, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2560: // double ├
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, x0+1, cxm, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            case 0x2563: // double ┤
            { int t2 = thickness+1; if (t2>3) t2=3; draw_h_line(tex, cym, cxm, x1-1, x0, x1, y0, y1, t2, fgc, alpha); draw_v_line(tex, cxm, y0+1, y1-1, x0, x1, y0, y1, t2, fgc, alpha); return; }
            default:
                // Fallback to ASCII-like minimal rendering
                draw_h_line(tex, cym, x0+1, x1-1, x0, x1, y0, y1, thickness, fgc, alpha); return;
        }
    }
    glyph_bitmap *g = get_glyph(font, cp);
    if (!g) return;
    rgb8 fgc = color_from_index(sattr_to_rgb_idx(cell, 1));
    int gx = x0 + (font->cell_w - g->w)/2 + g->bearing_x;
    int gy = y0 + font->baseline - g->bearing_y;
    // Clip horizontally to avoid negative pointer math when bearing_x shifts left
    int clip_x0 = gx < x0 ? x0 : gx;
    int clip_x1 = (gx + g->w) > x1 ? x1 : (gx + g->w);
    if (clip_x0 < clip_x1) {
        for (int yy=0; yy<g->h; yy++) {
            int py = gy + yy; if (py<y0||py>=y1) continue;
            int xx0 = clip_x0 - gx; // start within glyph bitmap
            int xx1 = clip_x1 - gx; // end within glyph bitmap
            uint8_t *row = tex->pixels + (size_t)py * tex->tex_w * 4 + clip_x0 * 4;
            for (int xx=xx0; xx<xx1; xx++) {
                uint8_t a = g->bitmap[yy*g->w + xx];
                if (a) {
                    uint8_t *p = row + (xx - xx0)*4;
                    p[0] = (uint8_t)((fgc.r * a + p[0]*(255-a))/255);
                    p[1] = (uint8_t)((fgc.g * a + p[1]*(255-a))/255);
                    p[2] = (uint8_t)((fgc.b * a + p[2]*(255-a))/255);
                    p[3] = alpha;
                }
            }
        }
    }
    // cached glyph owned by font, no free
}

// No screen damage callbacks are used; we rebuild surfaces on demand.

static void rebuild_surface(term_pane *tp) {
    // Re-render entire screen to CPU buffer
    if (tp->vts) vterm_screen_flush_damage(tp->vts);
    tp->surface.dirty = true;
    tp->surface.dirty_y0 = 0;
    tp->surface.dirty_y1 = tp->surface.tex_h;
    tp->surface.dirty_ranges[0].y0 = 0;
    tp->surface.dirty_ranges[0].y1 = tp->surface.tex_h;
    tp->surface.dirty_count = 1;
    for (int y=0; y<tp->layout.rows; y++) {
        for (int x=0; x<tp->layout.cols; x++) {
            VTermScreenCell cell; memset(&cell,0,sizeof cell);
            vterm_screen_get_cell(tp->vts, (VTermPos){.row=y,.col=x}, &cell);
            composite_cell(tp, x, y, &cell);
        }
        if (tp->row_hash) tp->row_hash[y] = pane_row_hash(tp, y);
    }
}

void term_pane_reset_screen(term_pane *tp, int hard) {
    if (!tp || !tp->vts) return;
    vterm_screen_reset(tp->vts, hard ? 1 : 0);
}

term_pane* term_pane_create(const pane_layout *layout, int font_px, const char *cmd, char *const argv[]) {
    term_pane *tp = calloc(1, sizeof *tp);
    tp->layout = *layout;
    (void)cmd; // cmd is informational; argv drives exec
    tp->use_shell_cmd = 0;
    tp->shell_cmd = NULL;
    tp->argv_dup = dup_argv(argv);
    // Font
    font_init(&tp->font, font_px > 0 ? font_px : 18);
    tp->alpha = 255;
    // Adjust cols/rows to pane size
    tp->layout.cols = tp->layout.w / tp->font.cell_w;
    tp->layout.rows = tp->layout.h / tp->font.cell_h;
    if (tp->layout.cols < 10) tp->layout.cols = 10;
    if (tp->layout.rows < 5) tp->layout.rows = 5;

    // VTerm
    tp->vt = vterm_new(tp->layout.rows, tp->layout.cols);
    vterm_set_utf8(tp->vt, 1);
    // Do not install VTermState callbacks to avoid ABI mismatches between
    // headers and shared library across distros. Use default state behavior.
    VTermState *st = vterm_obtain_state(tp->vt); (void)st;
    tp->vts = vterm_obtain_screen(tp->vt);
    vterm_screen_enable_altscreen(tp->vts, 1);
    vterm_screen_reset(tp->vts, 1);
    vterm_screen_set_damage_merge(tp->vts, VTERM_DAMAGE_SCROLL);
    // Avoid setting screen callbacks to prevent ABI mismatches; mark dirty on input instead.

    // PTY child
    tp->child_pid = spawn_pty_argv(argv, &tp->pty_master);
    fcntl(tp->pty_master, F_SETFL, O_NONBLOCK);
    set_pty_winsize(tp->pty_master, tp->layout.cols, tp->layout.rows);

    // Texture surface
    int tex_w = tp->layout.cols * tp->font.cell_w;
    int tex_h = tp->layout.rows * tp->font.cell_h;
    pane_tex_init(&tp->surface, tex_w, tex_h);

    // Prime screen empty
    rebuild_surface(tp);
    tp->row_hash = calloc((size_t)tp->layout.rows, sizeof(uint32_t));
    return tp;
}

term_pane* term_pane_create_cmd(const pane_layout *layout, int font_px, const char *shell_cmd) {
    term_pane *tp = calloc(1, sizeof *tp);
    tp->layout = *layout;
    font_init(&tp->font, font_px > 0 ? font_px : 18);
    tp->alpha = 255;
    tp->use_shell_cmd = 1;
    tp->shell_cmd = strdup(shell_cmd);
    tp->argv_dup = NULL;
    tp->layout.cols = tp->layout.w / tp->font.cell_w;
    tp->layout.rows = tp->layout.h / tp->font.cell_h;
    if (tp->layout.cols < 10) tp->layout.cols = 10;
    if (tp->layout.rows < 5) tp->layout.rows = 5;
    tp->vt = vterm_new(tp->layout.rows, tp->layout.cols);
    vterm_set_utf8(tp->vt, 1);
    VTermState *st2 = vterm_obtain_state(tp->vt); (void)st2;
    tp->vts = vterm_obtain_screen(tp->vt);
    vterm_screen_enable_altscreen(tp->vts, 1);
    vterm_screen_reset(tp->vts, 1);
    vterm_screen_set_damage_merge(tp->vts, VTERM_DAMAGE_SCROLL);
    // Avoid setting screen callbacks to prevent ABI mismatches; mark dirty on input instead.
    tp->child_pid = spawn_pty_shell(shell_cmd, &tp->pty_master);
    fcntl(tp->pty_master, F_SETFL, O_NONBLOCK);
    set_pty_winsize(tp->pty_master, tp->layout.cols, tp->layout.rows);
    int tex_w = tp->layout.cols * tp->font.cell_w;
    int tex_h = tp->layout.rows * tp->font.cell_h;
    pane_tex_init(&tp->surface, tex_w, tex_h);
    rebuild_surface(tp);
    tp->row_hash = calloc((size_t)tp->layout.rows, sizeof(uint32_t));
    return tp;
}

void term_pane_destroy(term_pane *tp) {
    if (!tp) return;
    if (tp->child_pid>0) kill(tp->child_pid, SIGTERM);
    if (tp->pty_master>=0) close(tp->pty_master);
    pane_tex_destroy(&tp->surface);
    font_destroy(&tp->font);
    free(tp->row_hash);
    if (tp->vt) vterm_free(tp->vt);
    if (tp->shell_cmd) free(tp->shell_cmd);
    if (tp->argv_dup) free_argv(tp->argv_dup);
    free(tp);
}

void term_pane_resize(term_pane *tp, const pane_layout *layout) {
    pane_tex old = tp->surface; tp->surface = (pane_tex){0};
    tp->layout = *layout;
    int cols = tp->layout.w / tp->font.cell_w;
    int rows = tp->layout.h / tp->font.cell_h;
    if (cols < 10) cols = 10;
    if (rows < 5) rows = 5;
    /* Update the stored layout dimensions so subsequent redraw logic knows the
     * terminal grid size.  The incoming layout only specifies x/y/w/h. */
    tp->layout.cols = cols;
    tp->layout.rows = rows;
    tp->layout.cell_w = tp->font.cell_w;
    tp->layout.cell_h = tp->font.cell_h;
    vterm_set_size(tp->vt, rows, cols);
    set_pty_winsize(tp->pty_master, cols, rows);
    if (tp->child_pid > 0) kill(tp->child_pid, SIGWINCH);
    int new_w = cols*tp->font.cell_w, new_h = rows*tp->font.cell_h;
    pane_tex_init(&tp->surface, new_w, new_h);
    if (old.pixels) {
        int copy_w = old.tex_w < tp->surface.tex_w ? old.tex_w : tp->surface.tex_w;
        int copy_h = old.tex_h < tp->surface.tex_h ? old.tex_h : tp->surface.tex_h;
        for (int y=0; y<copy_h; y++)
            memcpy(tp->surface.pixels + (size_t)y * tp->surface.tex_w * 4,
                   old.pixels + (size_t)y * old.tex_w * 4,
                   (size_t)copy_w * 4);
        glBindTexture(GL_TEXTURE_2D, tp->surface.tex);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, tp->surface.tex_w, tp->surface.tex_h, GL_RGBA, GL_UNSIGNED_BYTE, tp->surface.pixels);
    }
    if (old.tex) glDeleteTextures(1, &old.tex);
    free(old.pixels);
    free(tp->row_hash);
    tp->row_hash = calloc((size_t)tp->layout.rows, sizeof(uint32_t));
}

static void update_changed_rows(term_pane *tp) {
    if (!tp || !tp->vts) return;
    int run_start = -1, run_end = -1; // current contiguous changed run [start..end]
    tp->surface.dirty_count = 0;
    for (int y=0; y<tp->layout.rows; y++) {
        uint32_t h = pane_row_hash(tp, y);
        if (h != tp->row_hash[y]) {
            // Rebuild only this row
            for (int x=0; x<tp->layout.cols; x++) {
                VTermScreenCell cell; memset(&cell,0,sizeof cell);
                vterm_screen_get_cell(tp->vts, (VTermPos){.row=y,.col=x}, &cell);
                composite_cell(tp, x, y, &cell);
            }
            tp->row_hash[y] = h;
            if (run_start < 0) { run_start = y; run_end = y; }
            else if (y == run_end + 1) { run_end = y; }
            else {
                // close previous run
                if (tp->surface.dirty_count < 4) {
                    int y0 = run_start * tp->font.cell_h;
                    int y1 = (run_end + 1) * tp->font.cell_h;
                    int idx = tp->surface.dirty_count++;
                    tp->surface.dirty_ranges[idx].y0 = y0;
                    tp->surface.dirty_ranges[idx].y1 = y1;
                }
                run_start = y; run_end = y;
            }
        }
    }
    if (run_start >= 0) {
        if (tp->surface.dirty_count < 4) {
            int y0 = run_start * tp->font.cell_h;
            int y1 = (run_end + 1) * tp->font.cell_h;
            int idx = tp->surface.dirty_count++;
            tp->surface.dirty_ranges[idx].y0 = y0;
            tp->surface.dirty_ranges[idx].y1 = y1;
        }
    }
    tp->surface.dirty = tp->surface.dirty_count > 0;
    if (tp->surface.dirty) {
        // Populate legacy single range for compatibility (min/max over ranges)
        int y0 = tp->surface.tex_h, y1 = 0;
        for (int i=0;i<tp->surface.dirty_count;i++){ if (tp->surface.dirty_ranges[i].y0 < y0) y0 = tp->surface.dirty_ranges[i].y0; if (tp->surface.dirty_ranges[i].y1 > y1) y1 = tp->surface.dirty_ranges[i].y1; }
        tp->surface.dirty_y0 = y0; tp->surface.dirty_y1 = y1;
    }
}

bool term_pane_poll(term_pane *tp) {
    bool changed = false;
    char buf[4096];
    for(;;){
        ssize_t n = read(tp->pty_master, buf, sizeof buf);
        if (n <= 0) break;
        // Feed program output into the terminal emulator
        vterm_input_write(tp->vt, buf, (size_t)n);
        changed = true;
    }
    // Check if child exited and respawn if needed
    if (tp->child_pid > 0) {
        int status=0; pid_t r = waitpid(tp->child_pid, &status, WNOHANG);
        if (r == tp->child_pid) {
            term_pane_respawn(tp);
            changed = true;
        }
    }
    if (changed) {
        // Flush vterm damage and update only the changed rows
        if (tp->vts) vterm_screen_flush_damage(tp->vts);
        // Initialize dirty bounds to empty; expand in update_changed_rows
        tp->surface.dirty_y0 = tp->surface.tex_h;
        tp->surface.dirty_y1 = 0;
        tp->surface.dirty_count = 0;
        update_changed_rows(tp);
        if (tp->surface.dirty_count == 0) {
            // No row changes detected; nothing to upload
            tp->surface.dirty = false;
        }
    }
    return changed;
}

void term_pane_force_rebuild(term_pane *tp) {
    if (!tp) return;
    if (tp->vts) vterm_screen_flush_damage(tp->vts);
    rebuild_surface(tp);
}

void term_pane_respawn(term_pane *tp) {
    if (!tp) return;
    if (tp->pty_master>=0) { close(tp->pty_master); tp->pty_master = -1; }
    if (tp->use_shell_cmd) {
        tp->child_pid = spawn_pty_shell(tp->shell_cmd ? tp->shell_cmd : "/bin/sh", &tp->pty_master);
    } else {
        char *fallback_argv[] = { "/bin/sh", "-l", NULL };
        char **argv = tp->argv_dup ? tp->argv_dup : fallback_argv;
        tp->child_pid = spawn_pty_argv(argv, &tp->pty_master);
    }
    fcntl(tp->pty_master, F_SETFL, O_NONBLOCK);
    // Reapply size to PTY
    int cols = tp->layout.w / tp->font.cell_w;
    int rows = tp->layout.h / tp->font.cell_h;
    if (cols < 10) cols = 10;
    if (rows < 5) rows = 5;
    vterm_set_size(tp->vt, rows, cols);
    set_pty_winsize(tp->pty_master, cols, rows);
    if (tp->child_pid > 0) kill(tp->child_pid, SIGWINCH);
}

void term_pane_render(term_pane *tp, int fb_w, int fb_h) {
    // Upload pane pixels; keep simple and robust across layout changes
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_DITHER);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glViewport(0, 0, fb_w, fb_h);
    glBindTexture(GL_TEXTURE_2D, tp->surface.tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    if (tp->surface.dirty) {
        int n = tp->surface.dirty_count;
        if (n <= 0) n = 1;
        for (int i=0; i<n; i++) {
            int y0 = (i < tp->surface.dirty_count) ? tp->surface.dirty_ranges[i].y0 : 0;
            int y1 = (i < tp->surface.dirty_count) ? tp->surface.dirty_ranges[i].y1 : tp->surface.tex_h;
            if (y0 < 0) y0 = 0;
            if (y1 > tp->surface.tex_h) y1 = tp->surface.tex_h;
            if (y1 <= y0) continue;
            int h = y1 - y0;
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, y0, tp->surface.tex_w, h,
                           GL_RGBA, GL_UNSIGNED_BYTE,
                           tp->surface.pixels + (size_t)y0 * tp->surface.tex_w * 4);
        }
    }
    tp->surface.dirty = false;
    tp->surface.dirty_y0 = tp->surface.tex_h;
    tp->surface.dirty_y1 = 0;
    tp->surface.dirty_count = 0;
    draw_textured_quad(tp->surface.tex, tp->layout.x, tp->layout.y,
                       tp->surface.tex_w, tp->surface.tex_h, fb_w, fb_h);
}

void term_pane_send_input(term_pane *tp, const char *buf, size_t len) {
    if (!tp) return;
    ssize_t n = write(tp->pty_master, buf, len);
    (void)n;
}

bool term_measure_cell(int font_px, int *cell_w, int *cell_h) {
    font_ctx f = {0};
    if (font_px <= 0) font_px = 18;
    // Initialize a temporary freetype face via fontconfig monospace
    if (FT_Init_FreeType(&f.ftlib)) return false;
    char *path = find_monospace_font();
    if (!path) { FT_Done_FreeType(f.ftlib); return false; }
    if (FT_New_Face(f.ftlib, path, 0, &f.face)) { free(path); FT_Done_FreeType(f.ftlib); return false; }
    free(path);
    FT_Set_Pixel_Sizes(f.face, 0, font_px);
    FT_Load_Char(f.face, 'M', FT_LOAD_RENDER);
    int cw = (f.face->glyph->advance.x + 31) / 64;
    int ch = font_px + 2;
    FT_Done_Face(f.face);
    FT_Done_FreeType(f.ftlib);
    if (cell_w) *cell_w = cw;
    if (cell_h) *cell_h = ch;
    return true;
}

void term_pane_set_font_px(term_pane *tp, int font_px) {
    if (!tp) return;
    if (font_px <= 0) font_px = 18;
    // Recreate font
    font_destroy(&tp->font);
    font_init(&tp->font, font_px);
    // Update grid based on new cell size
    int cols = tp->layout.w / tp->font.cell_w;
    int rows = tp->layout.h / tp->font.cell_h;
    if (cols < 10) cols = 10;
    if (rows < 5) rows = 5;
    /* Update stored layout dimensions so subsequent logic uses the new grid */
    tp->layout.cols = cols;
    tp->layout.rows = rows;
    tp->layout.cell_w = tp->font.cell_w;
    tp->layout.cell_h = tp->font.cell_h;
    vterm_set_size(tp->vt, rows, cols);
    set_pty_winsize(tp->pty_master, cols, rows);
    if (tp->child_pid > 0) kill(tp->child_pid, SIGWINCH);
    // Recreate texture surface
    pane_tex old = tp->surface; tp->surface = (pane_tex){0};
    pane_tex_init(&tp->surface, cols*tp->font.cell_w, rows*tp->font.cell_h);
    if (old.pixels) {
        int copy_w = old.tex_w < tp->surface.tex_w ? old.tex_w : tp->surface.tex_w;
        int copy_h = old.tex_h < tp->surface.tex_h ? old.tex_h : tp->surface.tex_h;
        for (int y=0; y<copy_h; y++)
            memcpy(tp->surface.pixels + (size_t)y * tp->surface.tex_w * 4,
                   old.pixels + (size_t)y * old.tex_w * 4,
                   (size_t)copy_w * 4);
        glBindTexture(GL_TEXTURE_2D, tp->surface.tex);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, tp->surface.tex_w, tp->surface.tex_h, GL_RGBA, GL_UNSIGNED_BYTE, tp->surface.pixels);
    }
    if (old.tex) glDeleteTextures(1, &old.tex);
    free(old.pixels);
    free(tp->row_hash);
    tp->row_hash = calloc((size_t)rows, sizeof(uint32_t));
}

void term_pane_set_alpha(term_pane *tp, uint8_t alpha) {
    if (!tp) return;
    tp->alpha = alpha;
    rebuild_surface(tp);
}
