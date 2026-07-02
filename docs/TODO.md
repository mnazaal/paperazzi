# TODO

## systemd socket activation for `pzi server`

Optional enhancement to the systemd user service ([`packaging/systemd/pzi.service`](../packaging/systemd/pzi.service)).

**Idea:** a `pzi.socket` unit listens on `:8765` and starts `pzi server` on the
first browser-extension request; `--stop-after N` lets the server exit when idle;
the next request revives it. Near-zero idle footprint, auto-revive on demand.

**Needs:** `pzi server` must accept an inherited socket fd from systemd
(`LISTEN_FDS` / `sd_notify`) and bind to it instead of opening its own — a change
in the server bind path — plus the `.socket` unit.

**Priority: low.** The always-on user service already solves the "no terminal /
screen space" problem. The only gain here is reclaiming ~100–150 MB idle RAM
between capture sessions (no idle CPU cost). Not worth the extra maintained code
path on a single-user desktop.

**Revisit if:** pzi runs on a constrained always-on host (Pi / shared VPS) where
idle RAM matters. (If the concern is instead the node translation-server leaking
over long uptime, a timed restart — `RuntimeMaxSec` or a `.timer` — is a more
direct fix than socket activation.)
