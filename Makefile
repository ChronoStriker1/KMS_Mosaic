CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11
# Embed an rpath so the binary can find bundled libs at runtime
LDFLAGS ?= -Wl,-rpath,'$$ORIGIN/../lib/kms_mosaic' -Wl,--enable-new-dtags -rdynamic

PKGS = libdrm gbm egl glesv2 mpv vterm freetype2 fontconfig

PKG_CFLAGS := $(shell pkg-config --cflags $(PKGS))
PKG_LIBS   := $(shell pkg-config --libs   $(PKGS))

SRC = src/kms_mosaic.c src/app.c src/options.c src/layout.c src/media.c src/display.c src/render_gl.c src/panes.c src/runtime.c src/frame.c src/ui.c src/term_pane.c src/osd.c src/font_util.c
BIN = kms_mosaic

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(PKG_CFLAGS) -o $@ $(SRC) $(PKG_LIBS) $(LDFLAGS)

clean:
	rm -f $(BIN)

.PHONY: all clean
