CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11
# Embed an rpath so the binary can find bundled libs at runtime
LDFLAGS ?= -Wl,-rpath,'$$ORIGIN/../lib/kms_mosaic' -Wl,--enable-new-dtags -rdynamic

PKGS = libdrm gbm egl glesv2 mpv vterm freetype2 fontconfig

PKG_CFLAGS := $(shell pkg-config --cflags $(PKGS))
PKG_LIBS   := $(shell pkg-config --libs   $(PKGS))

SRC = src/kms_mosaic.c src/term_pane.c src/osd.c
BIN = kms_mosaic

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) $(PKG_CFLAGS) -o $@ $(SRC) $(PKG_LIBS) $(LDFLAGS)

clean:
	rm -f $(BIN)

.PHONY: all clean
