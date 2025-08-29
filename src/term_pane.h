#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <vterm.h>

typedef struct term_pane term_pane;

typedef struct {
    int x, y, w, h;      // pane rect in framebuffer pixels
    int cols, rows;      // terminal grid size
    int cell_w, cell_h;  // cell size in pixels
} pane_layout;

// Create a terminal pane; spawn argv or shell cmd in PTY
term_pane* term_pane_create(const pane_layout *layout, int font_px, const char *cmd, char *const argv[]);
term_pane* term_pane_create_cmd(const pane_layout *layout, int font_px, const char *shell_cmd);
void term_pane_destroy(term_pane *tp);

// Resize pane (recreate buffers and send TIOCSWINSZ)
void term_pane_resize(term_pane *tp, const pane_layout *layout);

// Pump PTY -> libvterm; returns true if screen content changed
bool term_pane_poll(term_pane *tp);

// Render cached screen to OpenGL (upload texture when dirty)
void term_pane_render(term_pane *tp, int fb_w, int fb_h);

// Send input bytes to the PTY (for interactive control)
void term_pane_send_input(term_pane *tp, const char *buf, size_t len);
