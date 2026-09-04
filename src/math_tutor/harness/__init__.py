"""Provider-neutral pedagogical proposal validation and orchestration.

Harness modules may depend on the math-tutor domain and application contracts,
but not on infrastructure or vendor SDKs.
"""

from math_tutor.harness.context import HarnessContext
from math_tutor.harness.contracts import HarnessDecision, ToolName, ToolProposal
from math_tutor.harness.limits import HarnessLimits
from math_tutor.harness.loop import PedagogicalHarness

__all__ = ["HarnessContext", "HarnessDecision", "HarnessLimits", "PedagogicalHarness", "ToolName", "ToolProposal"]
