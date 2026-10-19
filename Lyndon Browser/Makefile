# Lyndon Browser — a small, fast, private native browser
# Build: make          Run: make run          Install: sudo make install

APP        := lyndon
APP_ID     := org.lyndon.Browser
VERSION    := 0.1.0

PREFIX     ?= /usr/local
BINDIR     ?= $(PREFIX)/bin
DATADIR    ?= $(PREFIX)/share

PKGS       := gtk4 libadwaita-1 webkitgtk-6.0 libsoup-3.0 sqlite3 libsecret-1 json-glib-1.0 \
              zlib libcrypto
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
    $(info   Arch:    sudo pacman -S gtk4 libadwaita webkitgtk-6.0 libsoup3 libsecret json-glib zlib openssl base-devel)
    $(info   Fedora:  sudo dnf install gtk4-devel libadwaita-devel webkitgtk6.0-devel libsoup3-devel libsecret-devel json-glib-devel zlib-devel openssl-devel)
    $(info   Debian:  sudo apt install libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libsoup-3.0-dev libsecret-1-dev libjson-glib-dev zlib1g-dev libssl-dev)
    $(info )
    $(info   Note webkitgtk-6.0, not webkit2gtk-4.1 — the latter is the GTK3 generation.)
    $(info )
    $(error missing dependencies)
  endif
endif

CFLAGS     += $(shell pkg-config --cflags $(PKGS))
LDFLAGS    += $(shell pkg-config --libs $(PKGS)) -lm

# Optional: NSS is the only way to read Firefox's saved logins, and it is not
# worth failing a build over for people who do not use Firefox. Without it the
# import offers the CSV route instead.
ifeq ($(shell pkg-config --exists nss 2>/dev/null && echo 1),1)
  CFLAGS   += -DLYNDON_HAVE_NSS=1 $(shell pkg-config --cflags nss)
  LDFLAGS  += $(shell pkg-config --libs nss)
endif
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

.PHONY: all run clean install uninstall user-install user-uninstall \
        check deps help dist

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

# A built copy somewhere other than the checkout root, so that lyndon.py can
# build a new version while the old one is still the binary being run — the
# linker cannot write an executable that is running (ETXTBSY), and dist/ is
# what the launcher starts. Nothing is bundled: unlike the Windows dist, the
# data is already compiled into the binary and the libraries are the system's.
DISTDIR ?= dist

dist: $(APP)
	@mkdir -p $(DISTDIR)
	@cp -f $(APP) $(DISTDIR)/.$(APP).new
	@mv -f $(DISTDIR)/.$(APP).new $(DISTDIR)/$(APP)
	@echo "  ==>   $(DISTDIR)/$(APP) ready"

deps:
	@echo "Checking build dependencies…"
	@pkg-config --exists gtk4         || { echo "  MISSING: gtk4";         exit 1; }
	@pkg-config --exists libadwaita-1 || { echo "  MISSING: libadwaita-1"; exit 1; }
	@pkg-config --exists webkitgtk-6.0 || { echo "  MISSING: webkitgtk-6.0 (Arch: pacman -S webkitgtk-6.0)"; exit 1; }
	@echo "  gtk4          $$(pkg-config --modversion gtk4)"
	@echo "  libadwaita-1  $$(pkg-config --modversion libadwaita-1)"
	@echo "  webkitgtk-6.0 $$(pkg-config --modversion webkitgtk-6.0)"
	@echo "  libsecret-1   $$(pkg-config --modversion libsecret-1)"
	@echo "  zlib          $$(pkg-config --modversion zlib)"
	@echo "  libcrypto     $$(pkg-config --modversion libcrypto)"
	@echo "  nss           $$(pkg-config --modversion nss 2>/dev/null || echo 'absent - no Firefox password import')"

run: $(APP)
	@./$(APP)

check: tests/test-abp tests/test-util tests/test-store tests/test-engine \
       tests/test-archive tests/test-pwcheck
	@./tests/test-abp
	@./tests/test-util
	@./tests/test-store
	@./tests/test-engine
	@./tests/test-archive
	@./tests/test-pwcheck
	@# The launcher is Python, and what it decides about builds is the one
	@# thing here that can leave someone without a browser. Skipped rather
	@# than failed where there is no python3: it is not a build dependency.
	@command -v python3 >/dev/null && python3 tests/test-launcher.py || \
	  echo "  skip  tests/test-launcher.py (no python3)"

tests/test-abp: tests/test-abp.c src/abp.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -o $@ $^ $(shell pkg-config --cflags --libs glib-2.0 gio-2.0)

# util.c calls nothing from the toolkit, so this links against plain glib --
# but lyndon.h still pulls adwaita.h in on this platform, so the headers have
# to be on the include path even though none of their code ends up in the
# binary. Under win32/Makefile lyndon.h drops that include and the same two
# files build with glib alone.
tests/test-util: tests/test-util.c src/util.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -o $@ $^ \
	  $(shell pkg-config --cflags gtk4 libadwaita-1 webkitgtk-6.0) \
	  $(shell pkg-config --cflags --libs glib-2.0 gio-2.0)

# store.c needs sqlite, glib and util.c's URL handling -- grouping history by
# site is ly_uri_base_domain's answer. The test supplies the one symbol it
# wants from the rest of the browser. lyndon.h still pulls adwaita in on this
# platform, hence the headers on the include path -- see test-util.
tests/test-store: tests/test-store.c src/store.c src/util.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -o $@ $^ -lm \
	  $(shell pkg-config --cflags gtk4 libadwaita-1 webkitgtk-6.0) \
	  $(shell pkg-config --cflags --libs glib-2.0 gio-2.0 sqlite3)

# engine.c is the one test that needs the toolkit for real: it creates a
# WebKitUserContentManager and watches what happens to it. config.c and util.c
# come along because the engine is built from a config, and the compiled-in
# resources because it reads its stylesheets out of them.
tests/test-engine: tests/test-engine.c src/engine.c src/config.c src/util.c build/lyndon-resources.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -Ibuild \
	  -DLYNDON_VERSION=\"$(VERSION)\" -DLYNDON_APP_ID=\"$(APP_ID)\" -o $@ $^ \
	  $(shell pkg-config --cflags --libs $(PKGS))

# archive.c reaches into the engine for the user agent its crawler sends and
# into the store for the sweep's shortlist, so both come along; the rest is
# the same set test-engine needs.
tests/test-archive: tests/test-archive.c src/archive.c src/engine.c src/config.c \
                    src/store.c src/util.c build/lyndon-resources.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -Ibuild \
	  -DLYNDON_VERSION=\"$(VERSION)\" -DLYNDON_APP_ID=\"$(APP_ID)\" -o $@ $^ -lm \
	  $(shell pkg-config --cflags --libs $(PKGS))

# pwcheck.c judges a password and asks the breach service about it: libsoup
# for the one request it makes, libcrypto for the random bytes the generator
# is built from, and util.c because reuse is counted per site, which is
# ly_uri_base_domain's answer. credentials.c supplies LyCredential.
tests/test-pwcheck: tests/test-pwcheck.c src/pwcheck.c src/util.c src/credentials.c
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g -Isrc -o $@ $^ -lm \
	  $(shell pkg-config --cflags gtk4 libadwaita-1 webkitgtk-6.0) \
	  -DLYNDON_VERSION=\"$(VERSION)\" \
	  $(shell pkg-config --cflags --libs glib-2.0 gio-2.0 libsoup-3.0 libcrypto)

# -- installing -----------------------------------------------------------
# Two routes, and they are not the same thing.
#
# `user-install` is the one to use. It puts a link in ~/.local/bin pointing at
# lyndon.py in this checkout, and a .desktop file that runs it, so Lyndon
# starts the way the Python apps beside it do: the launcher fast-forwards the
# checkout, builds a new version behind the running browser, and the browser
# offers a Restart when one is ready. Nothing needs root, which is the whole
# reason it can replace its own binary.
#
# `install` is the packaging route: a copy in $(PREFIX), owned by whoever
# installed it. It cannot update itself -- there is no checkout under it and
# no write permission anyway -- so it is what a distribution package would do,
# not what someone running from git wants.

USER_BINDIR  ?= $(HOME)/.local/bin
USER_DATADIR ?= $(HOME)/.local/share
HERE         := $(CURDIR)

user-install: dist
	@install -d $(USER_BINDIR) $(USER_DATADIR)/applications
	@ln -sfn "$(HERE)/lyndon.py" $(USER_BINDIR)/$(APP)
	@sed 's|^Exec=$(APP)|Exec=$(USER_BINDIR)/$(APP)|' data/$(APP_ID).desktop \
	  > $(USER_DATADIR)/applications/$(APP_ID).desktop
	@chmod 644 $(USER_DATADIR)/applications/$(APP_ID).desktop
	@install -Dm644 data/icons/$(APP_ID).svg \
	  $(USER_DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	@-update-desktop-database $(USER_DATADIR)/applications 2>/dev/null
	@-gtk-update-icon-cache -qtf $(USER_DATADIR)/icons/hicolor 2>/dev/null
	@echo "  ==>   $(USER_BINDIR)/$(APP) -> $(HERE)/lyndon.py"
	@echo "        updates itself from this checkout; nothing here needs root."
	@if [ -e $(BINDIR)/$(APP) ] || [ -e /usr/bin/$(APP) ]; then \
	  echo ""; \
	  echo "  note: an older system-wide copy is still there. Which one runs"; \
	  echo "        is down to PATH order, and ~/.local/bin usually comes"; \
	  echo "        first, so this is a stale duplicate rather than a"; \
	  echo "        conflict -- but it will never update. Remove it with"; \
	  echo "        sudo make uninstall"; \
	fi

user-uninstall:
	rm -f $(USER_BINDIR)/$(APP) \
	      $(USER_DATADIR)/applications/$(APP_ID).desktop \
	      $(USER_DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg
	-update-desktop-database $(USER_DATADIR)/applications 2>/dev/null

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
	@echo "note: a copy installed this way cannot update itself -- see user-install."

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/$(APP) \
	      $(DESTDIR)$(DATADIR)/applications/$(APP_ID).desktop \
	      $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg

clean:
	rm -rf build dist dist-next dist-old* $(APP) \
	       tests/test-abp tests/test-util tests/test-store \
	       tests/test-engine tests/test-archive

help:
	@echo "make            build"
	@echo "make run        build and launch"
	@echo "make check      run unit tests (filter lists, URL handling, ranking,"
	@echo "                offline mirror paths, the launcher's build swap)"
	@echo "make dist       build into dist/, which is what lyndon.py starts"
	@echo "make user-install    ~/.local/bin link + .desktop, updates itself"
	@echo "make user-uninstall  remove them again"
	@echo "make DEBUG=1    asan/ubsan build"
	@echo "make LTO=1      link-time optimised build"
	@echo "make deps       verify build dependencies"
	@echo ""
	@echo "nss is optional: present at build time it enables Firefox password import."
	@echo "make user-install     (no root; this is the one you want)"
	@echo "sudo make install     (a packaged copy that cannot self-update)"

-include $(DEPS)
