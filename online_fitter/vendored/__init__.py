"""Vendored copy of the McClellan deuteron-NMR fitter modules.

These files are copied *verbatim* from the sibling ``Optimizied-McClellan-Fits/
fitter/`` package (see ``VENDORED_FROM.md`` for the exact source commit). They
import each other by bare module name (``import deuteron_model as dm``), so this
package puts its own directory on ``sys.path`` at import time. That lets the
files stay unedited, which keeps re-syncing with upstream a straight file copy.

Import them via, e.g.::

    import online_fitter.vendored          # runs this bootstrap
    import deuteron_model as dm            # now resolves to the vendored copy
    import fit_deuterons as fd
    import holeburn
    import cooldown_io as io

Do NOT edit the vendored ``.py`` files. Fix bugs upstream and re-vendor.
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
