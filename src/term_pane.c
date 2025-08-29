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
} font_ctx;

typedef struct {
    GLuint tex;
    int tex_w, tex_h;
    bool dirty;
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
}

static void font_destroy(font_ctx *f) {
    if (f->face) FT_Done_Face(f->face);
    if (f->ftlib) FT_Done_FreeType(f->ftlib);
}

static glyph_bitmap render_glyph(font_ctx *f, uint32_t cp) {
    glyph_bitmap g = { .codepoint = cp };
    if (FT_Load_Char(f->face, cp, FT_LOAD_RENDER)) return g;
    FT_GlyphSlot slot = f->face->glyph;
    g.w = slot->bitmap.width;
    g.h = slot->bitmap.rows;
    g.bearing_x = slot->bitmap_left;
    g.bearing_y = slot->bitmap_top;
    g.advance = (slot->advance.x + 31)/64;
    size_t sz = g.w * g.h;
    g.bitmap = malloc(sz);
    memcpy(g.bitmap, slot->bitmap.buffer, sz);
    return g;
}

static void free_glyph(glyph_bitmap *g) {
    free(g->bitmap);
    memset(g, 0, sizeof(*g));
}

static void pane_tex_init(pane_tex *t, int w, int h) {
    t->tex_w = w; t->tex_h = h; t->dirty = true;
    glGenTextures(1, &t->tex);
    glBindTexture(GL_TEXTURE_2D, t->tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    t->pixels = calloc((size_t)w * h * 4, 1);
}

static void pane_tex_destroy(pane_tex *t) {
    if (t->tex) glDeleteTextures(1, &t->tex);
    free(t->pixels);
    memset(t, 0, sizeof(*t));
}

// Very small shader to draw the pane texture
static GLuint pane_program = 0, pane_vao = 0, pane_vbo = 0;
static GLint u_tex = -1, a_pos = -1, a_uv = -1;

static GLuint compile_shader(GLenum type, const char *src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok = 0; glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512]; glGetShaderInfoLog(s, sizeof log, NULL, log);
        fprintf(stderr, "shader error: %s\n", log);
        exit(1);
    }
    return s;
}

static void ensure_pane_program(void) {
    if (pane_program) return;
    const char *vs =
        "attribute vec2 a_pos;\n"
        "attribute vec2 a_uv;\n"
        "varying vec2 v_uv;\n"
        "void main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0);}";
    const char *fs =
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
    glDrawArrays(GL_TRIANGLES, 0, 6);
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

static int sattr_to_rgb_idx(const VTermScreenCell *cell, int is_fg) {
    VTermColor c = is_fg ? cell->fg : cell->bg;
    if (c.type == VTERM_COLOR_DEFAULT) return is_fg ? 7 : 0;
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

static void composite_cell(font_ctx *font, pane_tex *tex, int cx, int cy, const VTermScreenCell *cell) {
    // Fill background
    int x0 = cx * font->cell_w;
    int y0 = cy * font->cell_h;
    int x1 = x0 + font->cell_w;
    int y1 = y0 + font->cell_h;
    rgb8 bgc = color_from_index(sattr_to_rgb_idx(cell, 0));
    for (int y=y0; y<y1; y++) {
        uint8_t *row = tex->pixels + (size_t)y * tex->tex_w * 4 + x0 * 4;
        for (int x=x0; x<x1; x++) {
            row[0]=bgc.r; row[1]=bgc.g; row[2]=bgc.b; row[3]=255; row+=4;
        }
    }
    // Foreground glyphs (support wide=1 only; treat wide as '?')
    uint32_t cp = cell->chars[0];
    if (cp == 0) return;
    // Replace box-drawing with ASCII approx
    if (cp >= 0x2500 && cp <= 0x257F) {
        // crude mapping
        if (cp==0x2500||cp==0x2501) cp='-';
        else if (cp==0x2502||cp==0x2503) cp='|';
        else if (cp==0x2514||cp==0x2518||cp==0x250c||cp==0x2510) cp='+';
        else cp='+';
    }
    glyph_bitmap g = render_glyph(font, cp);
    rgb8 fgc = color_from_index(sattr_to_rgb_idx(cell, 1));
    int gx = x0 + (font->cell_w - g.w)/2 + g.bearing_x;
    int gy = y0 + font->baseline - g.bearing_y;
    for (int yy=0; yy<g.h; yy++) {
        int py = gy + yy; if (py<y0||py>=y1) continue;
        uint8_t *row = tex->pixels + (size_t)py * tex->tex_w * 4 + gx * 4;
        for (int xx=0; xx<g.w; xx++) {
            int px = gx + xx; if (px<x0||px>=x1) continue;
            uint8_t a = g.bitmap[yy*g.w + xx];
            if (a) {
                // alpha blend over background
                uint8_t *p = row + xx*4;
                p[0] = (uint8_t)((fgc.r * a + p[0]*(255-a))/255);
                p[1] = (uint8_t)((fgc.g * a + p[1]*(255-a))/255);
                p[2] = (uint8_t)((fgc.b * a + p[2]*(255-a))/255);
                p[3] = 255;
            }
        }
    }
    free_glyph(&g);
}

static int screen_damage(VTermRect rect, void *user) {
    term_pane *tp = (term_pane*)user;
    (void)rect; tp->dirty = true; return 1;
}

static void rebuild_surface(term_pane *tp) {
    // Re-render entire screen to CPU buffer
    tp->surface.dirty = true;
    for (int y=0; y<tp->layout.rows; y++) {
        for (int x=0; x<tp->layout.cols; x++) {
            VTermScreenCell cell; memset(&cell,0,sizeof cell);
            vterm_screen_get_cell(tp->vts, (VTermPos){.row=y,.col=x}, &cell);
            composite_cell(&tp->font, &tp->surface, x, y, &cell);
        }
    }
}

term_pane* term_pane_create(const pane_layout *layout, int font_px, const char *cmd, char *const argv[]) {
    term_pane *tp = calloc(1, sizeof *tp);
    tp->layout = *layout;
    // Font
    font_init(&tp->font, font_px > 0 ? font_px : 18);
    // Adjust cols/rows to pane size
    tp->layout.cols = tp->layout.w / tp->font.cell_w;
    tp->layout.rows = tp->layout.h / tp->font.cell_h;
    if (tp->layout.cols < 10) tp->layout.cols = 10;
    if (tp->layout.rows < 5) tp->layout.rows = 5;

    // VTerm
    tp->vt = vterm_new(tp->layout.rows, tp->layout.cols);
    vterm_set_utf8(tp->vt, 1);
    tp->vts = vterm_obtain_screen(tp->vt);
    vterm_screen_enable_altscreen(tp->vts, 1);
    vterm_screen_reset(tp->vts, 1);
    vterm_screen_set_damage_merge(tp->vts, VTERM_DAMAGE_SCROLL);
    vterm_screen_set_callbacks(tp->vts, &(VTermScreenCallbacks){ .damage=screen_damage }, tp);

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
    return tp;
}

term_pane* term_pane_create_cmd(const pane_layout *layout, int font_px, const char *shell_cmd) {
    term_pane *tp = calloc(1, sizeof *tp);
    tp->layout = *layout;
    font_init(&tp->font, font_px > 0 ? font_px : 18);
    tp->layout.cols = tp->layout.w / tp->font.cell_w;
    tp->layout.rows = tp->layout.h / tp->font.cell_h;
    if (tp->layout.cols < 10) tp->layout.cols = 10;
    if (tp->layout.rows < 5) tp->layout.rows = 5;
    tp->vt = vterm_new(tp->layout.rows, tp->layout.cols);
    vterm_set_utf8(tp->vt, 1);
    tp->vts = vterm_obtain_screen(tp->vt);
    vterm_screen_enable_altscreen(tp->vts, 1);
    vterm_screen_reset(tp->vts, 1);
    vterm_screen_set_damage_merge(tp->vts, VTERM_DAMAGE_SCROLL);
    vterm_screen_set_callbacks(tp->vts, &(VTermScreenCallbacks){ .damage=screen_damage }, tp);
    tp->child_pid = spawn_pty_shell(shell_cmd, &tp->pty_master);
    fcntl(tp->pty_master, F_SETFL, O_NONBLOCK);
    set_pty_winsize(tp->pty_master, tp->layout.cols, tp->layout.rows);
    int tex_w = tp->layout.cols * tp->font.cell_w;
    int tex_h = tp->layout.rows * tp->font.cell_h;
    pane_tex_init(&tp->surface, tex_w, tex_h);
    rebuild_surface(tp);
    return tp;
}

void term_pane_destroy(term_pane *tp) {
    if (!tp) return;
    if (tp->child_pid>0) kill(tp->child_pid, SIGTERM);
    if (tp->pty_master>=0) close(tp->pty_master);
    pane_tex_destroy(&tp->surface);
    font_destroy(&tp->font);
    if (tp->vt) vterm_free(tp->vt);
    free(tp);
}

void term_pane_resize(term_pane *tp, const pane_layout *layout) {
    tp->layout = *layout;
    int cols = tp->layout.w / tp->font.cell_w;
    int rows = tp->layout.h / tp->font.cell_h;
    if (cols<10) cols=10; if (rows<5) rows=5;
    vterm_set_size(tp->vt, rows, cols);
    set_pty_winsize(tp->pty_master, cols, rows);
    pane_tex_destroy(&tp->surface);
    pane_tex_init(&tp->surface, cols*tp->font.cell_w, rows*tp->font.cell_h);
    rebuild_surface(tp);
}

bool term_pane_poll(term_pane *tp) {
    bool changed = false;
    char buf[4096];
    for(;;){
        ssize_t n = read(tp->pty_master, buf, sizeof buf);
        if (n <= 0) break;
        vterm_input_write(tp->vt, buf, (size_t)n);
        changed = true;
    }
    if (changed) {
        // Drain screen damage callbacks
        vterm_screen_flush_damage(tp->vts);
        rebuild_surface(tp);
    }
    return changed;
}

void term_pane_render(term_pane *tp, int fb_w, int fb_h) {
    if (tp->surface.dirty) {
        glBindTexture(GL_TEXTURE_2D, tp->surface.tex);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, tp->surface.tex_w, tp->surface.tex_h,
                        GL_RGBA, GL_UNSIGNED_BYTE, tp->surface.pixels);
        tp->surface.dirty = false;
    }
    draw_textured_quad(tp->surface.tex, tp->layout.x, tp->layout.y,
                       tp->surface.tex_w, tp->surface.tex_h, fb_w, fb_h);
}

void term_pane_send_input(term_pane *tp, const char *buf, size_t len) {
    if (!tp) return;
    (void)write(tp->pty_master, buf, len);
}
