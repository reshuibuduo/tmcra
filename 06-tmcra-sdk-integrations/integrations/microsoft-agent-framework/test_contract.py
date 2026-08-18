from pathlib import Path


ROOT = Path(__file__).parent


def test_provider_uses_two_phase_lifecycle_and_session_state():
    source = (ROOT / "src" / "TMCRA.AgentFramework" / "TmcraAIContextProvider.cs").read_text(encoding="utf-8")
    options = (ROOT / "src" / "TMCRA.AgentFramework" / "TmcraMemoryOptions.cs").read_text(encoding="utf-8")
    assert "ProvideAIContextAsync" in source
    assert "StoreAIContextAsync" in source
    assert "AgentSession.StateBag" in source
    assert "ReconcilePendingAsync" in source
    assert "ITmcraPendingIngestStore" in options
    assert "_userTextStateKey" in source
    assert "requestedOccurredAt" in source
    assert "RemoveAsync" in source
    assert 'microsoft-agent-{turnId}' in source
    assert "AIContext.Instructions" not in source  # Object initializer keeps evidence transient.


def test_project_pins_verified_agent_framework_contract():
    project = (ROOT / "src" / "TMCRA.AgentFramework" / "TMCRA.AgentFramework.csproj").read_text()
    assert 'Microsoft.Agents.AI.Abstractions" Version="1.13.0"' in project
