# Lyndon Browser — Windows build (Win32 + WebView2)
#
# Run from the project root, in an MSYS2 MINGW64 shell:
#
#     make -f win32/Makefile          build
#     make -f win32/Makefile check    run the blocker tests
#     make -f win32/Makefile run      build and launch
#
# There is no WebKitGTK for Windows, so the GTK front end in src/ cannot be
# built here. What is shared is everything below the toolkit: the filter
# translator, the config file, the history store and the URL helpers compile
# from src/ unchanged, because lyndon.h drops the toolkit includes under
# _WIN32. The window, the tabs and the request blocking are in this directory.

APP        := lyndon.exe
VERSION    := 0.1.0
APP_ID     := org.lyndon.Browser

CC         ?= gcc
PKGS       := glib-2.0 gio-2.0 sqlite3 json-glib-1.0

WARN       := -Wall -Wextra -Wno-unused-parameter -Wshadow -Wpointer-arith \
              -Wformat=2 -Wno-missing-field-initializers -Wno-cast-function-type
OPT        ?= -O2 -fstack-protector-strong
STD        := -std=gnu11

# _WIN32_WINNT: Windows 10, for the DPI and dark-title-bar calls.
DEFS       := -DLYNDON_VERSION=\"$(VERSION)\" -DLYNDON_APP_ID=\"$(APP_ID)\" \
              -DUNICODE -D_UNICODE -DCOBJMACROS -DWIN32_LEAN_AND_MEAN \
              -D_WIN32_WINNT=0x0A00

# -isystem for the vendored SDK header: it is MIDL output and warns several
# hundred times under GCC, and none of it is ours to fix.
CFLAGS     += $(STD) $(WARN) $(OPT) $(DEFS) -Isrc -Iwin32 -isystem win32/webview2
CFLAGS     += $(shell pkg-config --cflags $(PKGS))

LDLIBS     := $(shell pkg-config --libs $(PKGS)) \
              -lole32 -loleaut32 -luuid -lcomctl32 -lshlwapi -lshell32 \
              -luser32 -lgdi32 -ladvapi32 -ldwmapi
# -mwindows: no console behind the window. Dropped by DEBUG=1 so that
# g_debug output has somewhere to go.
# -municode: the entry point is wWinMain, so argv arrives as UTF-16 and a
# path with non-ASCII in it survives. Without it the linker looks for
# WinMain and the build fails at the very last step.
LDFLAGS    += -mwindows -municode

ifeq ($(DEBUG),1)
  OPT      := -O0 -g3
  LDFLAGS  := -municode
  DEFS     += -DLY_DEBUG=1
endif

# -- sources ---------------------------------------------------------------
# From src/, unchanged: everything that never names a GTK or WebKit type.
SHARED     := src/abp.c src/config.c src/store.c src/util.c src/import.c
WINSRC     := win32/block.c win32/tab.c win32/chrome.c win32/main.c \
              win32/ui.c win32/downloads.c win32/panel.c win32/prefs.c \
              win32/passwords.c win32/scheme.c

OBJDIR     := build-win
OBJS       := $(patsubst %.c,$(OBJDIR)/%.o,$(SHARED) $(WINSRC))
DEPS       := $(OBJS:.o=.d)

RES        := $(OBJDIR)/lyndon.res.o

.PHONY: all check run clean help deps dist

all: $(APP)

$(APP): $(OBJS) $(RES)
	@echo "  LD    $@"
	@$(CC) -o $@ $^ $(LDFLAGS) $(LDLIBS)
	@echo "  ==>   $@ ready"

$(OBJDIR)/%.o: %.c
	@mkdir -p $(dir $@)
	@echo "  CC    $<"
	@$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

# The manifest asks for per-monitor DPI and the common-controls v6 theme;
# without it the tab strip is drawn in the Windows 95 style.
$(RES): win32/lyndon.rc win32/lyndon.manifest
	@mkdir -p $(dir $@)
	@echo "  RC    $<"
	@windres -Iwin32 $< -O coff -o $@

# -- the blocker on its own ------------------------------------------------
check: $(OBJDIR)/test-block.exe
	@echo
	@./$(OBJDIR)/test-block.exe data

$(OBJDIR)/test-block.exe: win32/test-block.c win32/block.c src/abp.c src/util.c src/config.c
	@mkdir -p $(OBJDIR)
	@echo "  CC    $@"
	@$(CC) $(STD) $(WARN) -O1 -g $(DEFS) -Isrc -Iwin32 -isystem win32/webview2 \
	  $(shell pkg-config --cflags $(PKGS)) -o $@ $^ \
	  $(shell pkg-config --libs $(PKGS)) -lshlwapi -lole32

deps:
	@echo "Checking build dependencies…"
	@pkg-config --exists glib-2.0 || { echo "  MISSING: glib-2.0"; exit 1; }
	@pkg-config --exists sqlite3  || { echo "  MISSING: sqlite3";  exit 1; }
	@test -f win32/webview2/WebView2.h || { echo "  MISSING: win32/webview2/WebView2.h"; exit 1; }
	@echo "  glib-2.0  $$(pkg-config --modversion glib-2.0)"
	@echo "  sqlite3   $$(pkg-config --modversion sqlite3)"
	@echo "  WebView2  header present"
	@echo
	@echo "  The WebView2 *runtime* also has to be installed; it ships with"
	@echo "  Microsoft Edge, so on Windows 10/11 it already is."

# Run in place. The GLib DLLs live in the MSYS2 prefix, which is not on the
# PATH of a plain cmd.exe — so put it there for the duration rather than
# copying anything. Use `dist` for something that runs without MSYS2 at all.
run: $(APP)
	@PATH="$(MINGW_BIN):$$PATH" ./$(APP)

MINGW_BIN := $(shell dirname $$(which gcc))
DISTDIR   := dist

# A folder that runs on a machine with no MSYS2 on it: the executable, the
# data it reads, the WebView2 loader, and the five GLib DLLs. The list is
# taken from ldd rather than written down, so it stays right when the
# dependencies change.
dist: $(APP)
	@rm -rf $(DISTDIR)
	@mkdir -p $(DISTDIR)
	@cp $(APP) $(DISTDIR)/
	@cp -r data $(DISTDIR)/
	@cp win32/webview2/x64/WebView2Loader.dll $(DISTDIR)/ 2>/dev/null || 	  echo "  note: win32/webview2/x64/WebView2Loader.dll missing — see win32/webview2/README.md"
	@for dll in $$(ldd $(APP) | grep -io '/mingw64/bin/[^ ]*\.dll'); do 	  cp "$$dll" $(DISTDIR)/ ; 	done
	@echo "  ==>   $(DISTDIR)/ ready ($$(du -sh $(DISTDIR) | cut -f1)), runs without MSYS2"

clean:
	@rm -rf $(OBJDIR) $(APP)

help:
	@echo "make -f win32/Makefile           build"
	@echo "make -f win32/Makefile check     blocker tests"
	@echo "make -f win32/Makefile run       build and launch"
	@echo "make -f win32/Makefile dist     self-contained folder in dist/"
	@echo "make -f win32/Makefile DEBUG=1   console build with g_debug output"
	@echo "make -f win32/Makefile deps      verify dependencies"

-include $(DEPS)
