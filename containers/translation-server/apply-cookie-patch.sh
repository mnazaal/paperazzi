#!/bin/sh
# Apply cookie-bridge patches to translation-server source.
# Called from Containerfile after git clone.

set -eu

WEB_SESSION="src/webSession.js"
WEB_ENDPOINT="src/webEndpoint.js"

# --- Patch webSession.js: inject cookies into _cookieSandbox ---
# Anchor: the line "this._cookieSandbox = cookieJar();"
# Insert the cookie injection block right after it.
if ! grep -q "_pziCookies" "$WEB_SESSION"; then
    sed -i '/this\._cookieSandbox = cookieJar();/a\
\
                        // --- pzi cookie bridge: inject browser cookies ---\
                        if (this._cookies) {\
                                var _pziCookies = this._cookies.split(/;\\s*/);\
                                for (var _i = 0; _i < _pziCookies.length; _i++) {\
                                        var _c = _pziCookies[_i].trim();\
                                        if (_c) {\
                                                this._cookieSandbox.setCookie(_c, url);\
                                        }\
                                }\
                        }\
                        // --- end pzi patch ---' \
        "$WEB_SESSION"
    echo "  pzi: patched $WEB_SESSION for cookie injection"
else
    echo "  pzi: $WEB_SESSION already patched, skipping"
fi


# --- Patch webEndpoint.js: forward cookies from request body to session ---
# Anchor: the line containing "await session.handleURL();"
# Insert the cookie-forwarding block right before it.
if ! grep -q "_pziCookies" "$WEB_ENDPOINT"; then
    sed -i '/await session\.handleURL();/i\
\
                // --- pzi cookie bridge: forward cookies to session ---\
                if (data && typeof data.cookies === "string" && data.cookies) {\
                        session._cookies = data.cookies;\
                }\
                // --- end pzi patch ---' \
        "$WEB_ENDPOINT"
    echo "  pzi: patched $WEB_ENDPOINT for cookie forwarding"
else
    echo "  pzi: $WEB_ENDPOINT already patched, skipping"
fi
