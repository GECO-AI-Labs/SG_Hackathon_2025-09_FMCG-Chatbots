# Standalone nginx configs

For a host where nginx is installed directly and owns port 80.

The current deployment target does **not** use these. It runs Nginx Proxy
Manager in docker, which already owns ports 80, 81 and 443, so installing
system nginx there would fail to bind. Use
`deploy/nginx-proxy-manager/README.md` instead.

These are kept for a future dedicated host, and because they document the
exact proxy settings streaming needs regardless of which nginx applies them.
