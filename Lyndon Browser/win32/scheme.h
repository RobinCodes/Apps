/* scheme.h — the environment options, and with them the lyndon: scheme.
 *
 * The Linux build registers a URI scheme on the WebKit context and serves the
 * start page out of its GResource bundle, so the homepage in config.json can
 * be "lyndon:start" on both platforms. WebView2 will only navigate to a
 * scheme it was told about before the environment was created, and telling it
 * means handing CreateCoreWebView2EnvironmentWithOptions an options object.
 *
 * There is no such object to be had in C: the SDK ships a C++ class for it
 * and nothing else, so this file implements the four interfaces by hand. It
 * is dull, and it is the only way to have the same homepage on both builds
 * rather than a Windows-shaped exception in the config file.
 */
#pragma once

#include "lyndon.h"

#include <windows.h>

G_BEGIN_DECLS

/* The scheme both builds serve the start page on. */
#define LY_SCHEME "lyndon"

/* An options object registering LY_SCHEME, and carrying whatever browser
 * arguments the configuration implies. Returns NULL if it cannot be made, in
 * which case the caller should start the environment without options and do
 * without the custom scheme rather than not start at all.
 *
 * The return is an ICoreWebView2EnvironmentOptions*, typed as void* so that
 * this header does not drag WebView2.h into everything that includes it.
 * Release it once the environment has been asked for.
 *
 * `runtime_version` is the version of the WebView2 runtime actually
 * installed. It becomes TargetCompatibleBrowserVersion, which must not be
 * newer than what is on the machine: the SDK header defaults it to whatever
 * the SDK shipped with, and creation then fails with E_INVALIDARG on every
 * machine whose Edge is older than the SDK. Pass NULL to leave it. */
void *ly_environment_options_new (LyConfig *cfg, const char *runtime_version);

G_END_DECLS
