"""Honesty loop (v4.6).

Zero dependencies on the rest of Conscio by design: this directory is vendored
directly into the plugin assets tree (assets/hooks/conscio_honesty_pkg) which the
marketplace copies verbatim. An external import would break because the conscio
package is not installed alongside the plugin (it comes from PyPI via uvx), so
only what is shipped inside the plugin assets tree is accessible to hooks.
"""
