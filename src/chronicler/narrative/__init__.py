from chronicler.narrative.anthropic import (
    AnthropicProvider,
    make_anthropic_provider,
)
from chronicler.narrative.claude_code import (
    ClaudeCodeProvider,
    make_claude_code_provider,
)
from chronicler.narrative.factory import (
    make_narrative_provider,
    resolve_backend,
)
from chronicler.narrative.pipeline import GenerationOutcome, generate_biography
from chronicler.narrative.prompt_builder import VERSION_BY_KIND as _VERSION_BY_KIND
from chronicler.narrative.prose_io import render_briefing_markdown
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
    PromptKind,
)

# Convenience aggregator (F029): the canonical prompt-template version
# strings written into the biographies rows. Each module is still the
# single source of truth for its own value; this dict just centralises
# the lookup so dashboards / cost reports can iterate without reaching
# into private module names.
NARRATIVE_PROMPT_VERSIONS: dict[str, str] = dict(_VERSION_BY_KIND)


__all__ = [
    "NARRATIVE_PROMPT_VERSIONS",
    "AnthropicProvider",
    "ClaudeCodeProvider",
    "GenerationOutcome",
    "NarrativeProvider",
    "NarrativeRequest",
    "NarrativeResponse",
    "PromptKind",
    "generate_biography",
    "make_anthropic_provider",
    "make_claude_code_provider",
    "make_narrative_provider",
    "render_briefing_markdown",
    "resolve_backend",
]
