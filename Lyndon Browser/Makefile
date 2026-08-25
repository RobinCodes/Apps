# Lyndon Browser — a small, fast, private native browser
# Build: make          Run: make run          Install: sudo make install

APP        := lyndon
APP_ID     := org.lyndon.Browser
VERSION    := 0.1.0

PREFIX     ?= /usr/local
BINDIR     ?= $(PREFIX)/bin
DATADIR    ?= $(PREFIX)/share

PKGS       := gtk4 libadwaita-1 webkitgtk-6.0 libsoup-3.0 sqlite3 libsecret-1 json-glib-1.0
CC         ?= cc

# -- flags ---------------------------------------------------------------
# -Wno-missing-field-initializers: GActionEntry tables are written partially
# by design throughout GLib's own documentation.
WARN       := -Wall -Wextra -Wno-unused-parameter -Wshadow -Wpointer-arith \
              -Wstrict-prototypes -Wmissing-prototypes -Wformat=2 \
              -Wno-missing-field-initializers
OPT        ?= -O2 -fno-plt -fstack-protector-strong -D_FORTIFY_SOURCE=2
STD        := -std=gnu11

CFLAGS     += $(STD) $(WARN) $(OPT) -Isrc -Ibuild \
              -DLYNDON_VERSION=\"$(VERSION)\" -DLYNDON_APP_ID=\"$(APP_ID)\"
# Fail with something readable rather than a wall of pkg-config noise. Skipped
# for goals that do not compile anything.
ifeq (,$(filter clean help deps,$(MAKECMDGOALS)))
  MISSING := $(strip $(foreach p,$(PKGS),\
               $(if $(shell pkg-config --exists $(p) 2>/dev/null && echo ok),,$(p))))
  ifneq ($(MISSING),)
    $(info )
    $(info   Missing build dependencies: $(MISSING))
    $(info )
    $(info   Arch:    sudo pacman -S gtk4 libadwaita webkitgtk-6.0 libsoup3 base-devel)
    $(info   Fedora:  sudo dnf install gtk4-devel libadwaita-devel webkitgtk6.0-devel libsoup3-devel)
    $(info   Debian:  sudo apt install libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libsoup-3.0-dev)
    $(info )
    $(info   Note webkitgtk-6.0, not webkit2gtk-4.1 — the latter is the GTK3 generation.)
    $(info )
    $(error missing dependencies)
  endif
endif

CFLAGS     += $(shell pkg-config --cflags $(PKGS))
LDFLAGS    += $(shell pkg-config --libs $(PKGS)) -lm
LDFLAGS    += -Wl,-O1 -Wl,--as-needed -Wl,-z,relro -Wl,-z,now

ifeq ($(DEBUG),1)
  OPT      := -O0 -g3 -fsanitize=address,undefined
  LDFLAGS  += -fsanitize=address,undefined
endif

ifeq ($(LTO),1)
  CFLAGS   += -flto=auto
  LDFLAGS  += -flto=auto
endif

# -- sources -------------------------------------------------------------
SRCS       := $(wildcard src/*.c)
OBJS       := $(patsubst src/%.c,build/%.o,$(SRCS))
DEPS       := $(OBJS:.o=.d)

RES_XML    := data/lyndon.gresource.xml
RES_C      := build/lyndon-resources.c
RES_O      := build/lyndon-resources.o
RES_DEPS   := $(shell glib-compile-resources --sourcedir=data --generate-dependencies $(RES_XML) 2>/dev/null)

.PHONY: all run clean install uninstall check deps help

all: $(APP)

$(APP): $(OBJS) $(RES_O)
	@echo "  LD    $@"
	@$(CC) -o $@ $^ $(LDFLAGS)
	@echo "  ==>   $@ ready ($$(du -h $@ | cut -f1))"

build/%.o: src/%.c | build $(RES_C)
	@echo "  CC    $<"
	@$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

$(RES_O): $(RES_C)
	@echo "  CC    $<"
	@$(CC) $(CFLAGS) -Wno-missing-prototypes -c $< -o $@

$(RES_C): $(RES_XML) $(RES_DEPS) | build
	@echo "  GRES  $@"
	@glib-compile-resources --target=$@ --sourcedir=data --generate-source $(RES_XML)

build:
	@mkdir -p build

deps:
	@echo "Checking build dependencies…"
	@pkg-config --exists gtk4         || { echo "  MISSING: gtk4";         exit 1; }
	@pkg-config --exists libadwaita-1 || { echo "  MISSING: libadwaita-1"; exit 1; }
	@pkg-config --exists webkitgtk-6.0 || { echo "  MISSING: webkitgtk-6.0 (Arch: pacman -S webkitgtk-6.0)"; exit 1; }
	@echo "  gtk4          $$(pkg-config --modversion gtk4)"
	@echo "  libadwaita-1  $$(pkg-config --modversion libadwaita-1)"
	@echo "  webkitgtk-6.0 $$(pkg-config --modversion webkitgtk-6.0)"

run: $(APP)
	@./$(APP)

check: tests/test-abp
	@./tests/test-abp

tests/test-abp: tests/test-abp.c src/abp.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -o $@ $^ $(shell pkg-config --cflags --libs glib-2.0 gio-2.0)

install: $(APP)
	install -Dm755 $(APP) $(DESTDIR)$(BINDIR)/$(APP)
	install -d $(DESTDIR)$(DATADIR)/applications
	# Bake in the absolute path: a panel launcher does not always inherit a
	# PATH that includes ~/.local/bin.
	sed 's|^Exec=$(APP)|Exec=$(BINDIR)/$(APP)|' data/$(APP_ID).desktop \
	  > $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop
	chmod 644 $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop
	install -Dm644 data/icons/$(APP_ID).svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	-update-desktop-database $(DESTDIR)$(DATADIR)/applications 2>/dev/null
	-gtk-update-icon-cache -qtf $(DESTDIR)$(DATADIR)/icons/hicolor 2>/dev/null
	@echo "installed to $(DESTDIR)$(BINDIR)/$(APP)"

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/$(APP) \
	      $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop \
	      $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg

clean:
	rm -rf build $(APP) tests/test-abp

help:
	@echo "make            build"
	@echo "make run        build and launch"
	@echo "make check      run unit tests (filter-list converter)"
	@echo "make DEBUG=1    asan/ubsan build"
	@echo "make LTO=1      link-time optimised build"
	@echo "make deps       verify build dependencies"
	@echo "sudo make install"

-include $(DEPS)
