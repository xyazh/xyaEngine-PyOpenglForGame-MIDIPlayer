from .al import *          # noqa: F401,F403  兼容旧版绝对导入

import warnings

warnings.warn(
    "pyglgame.openal._al 已废弃，请改用 pyglgame.openal.al",
    DeprecationWarning,
    stacklevel=2,
)

