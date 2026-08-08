"""PyInstaller runtime hook: paddle's set_paddle_lib_path() (paddle/base/core.py)
looks for a 'paddle/libs' dir under site.getsitepackages(), and falls back to
site.USER_SITE if none of those match. Inside a frozen bundle there is no real
site-packages dir, so it falls through to the site.USER_SITE branch, which is
None in this frozen context -> os.path.sep.join([None, 'paddle', 'libs']) raises
TypeError before paddle even finishes importing. Fix: make getsitepackages()
report the bundle dir (where PyInstaller actually put paddle/libs) so the first,
working branch is taken instead."""
import os
import site
import sys

_bundle_dir = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)

_original_getsitepackages = getattr(site, "getsitepackages", None)


def _patched_getsitepackages():
    paths = []
    if _original_getsitepackages:
        try:
            paths = list(_original_getsitepackages())
        except Exception:
            paths = []
    paths.insert(0, _bundle_dir)
    return paths


site.getsitepackages = _patched_getsitepackages
