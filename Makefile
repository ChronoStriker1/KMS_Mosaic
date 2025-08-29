CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11

PKGS = libdrm gbm egl glesv2 mpv vterm freetype2 fontconfig

PKG_CFLAGS := $(shell pkg-config --cflags $(PKGS))
PKG_LIBS   := $(shell pkg-config --libs   $(PKGS))

SRC = src/kms_mpv_compositor.c src/term_pane.c src/osd.c
BIN = kms_mpv_compositor

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(PKG_CFLAGS) -o $@ $(SRC) $(PKG_LIBS)

clean:
	rm -f $(BIN)

.PHONY: all clean
