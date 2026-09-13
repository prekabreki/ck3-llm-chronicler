"""Real CK3 coat-of-arms extraction + composition (ck3_chronicler-7ao).

Three subsystems work together to render every character's *actual*
in-game heraldry instead of the procedural fallback that ships in v0.7
Phase 1 (frontend/src/components/Heraldry.tsx).

1. **Extractor** (this module): one-time CLI that locates the user's
   CK3 install, walks ``gfx/coat_of_arms/`` for pattern + emblem DDS
   files, converts each to PNG via Pillow, parses the named-color
   palette in ``common/named_colors/``, and outputs a manifest +
   asset directory under the chronicler data dir.

2. **Backend exposure**: see ``chronicler.save.parse`` — the rakaly
   JSON's top-level ``coat_of_arms.coat_of_arms_manager_database``
   block is parsed into typed ``CoaDefinition`` records and exposed
   per-character via the API.

3. **Frontend renderer**: ``frontend/src/components/Heraldry.tsx``
   gains a real-CoA mode that walks the nested structure (pattern +
   sub-shields + colored_emblems with masks/instances), composes via
   SVG ``<image>`` layers and ``<feColorMatrix>`` tinting (since the
   pattern + emblem textures are grayscale masks the engine recolors).

Personal-use only — extracted Paradox assets stay on the local machine.
"""

from chronicler.heraldry.extractor import (
    DEFAULT_STEAM_LIBRARIES,
    MANIFEST_SCHEMA_VERSION,
    ExtractionSummary,
    NamedColor,
    extract_assets,
    find_ck3_install,
    parse_named_colors,
)
from chronicler.heraldry.resolver import (
    resolve_character_coa,
)

__all__ = [
    "DEFAULT_STEAM_LIBRARIES",
    "MANIFEST_SCHEMA_VERSION",
    "ExtractionSummary",
    "NamedColor",
    "extract_assets",
    "find_ck3_install",
    "parse_named_colors",
    "resolve_character_coa",
]
