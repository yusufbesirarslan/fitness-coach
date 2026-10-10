"""Production SDK sends must stay behind admission; taxonomy is code-owned."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDK_TAILS = {("messages", "create"), ("messages", "stream"), ("completions", "create"),
             ("responses", "create"), ("responses", "stream"), ("images", "generate"),
             ("images", "edit"), ("embeddings", "create"), ("transcriptions", "create")}
SDK_METHODS = {"invoke_model", "invoke_model_with_response_stream", "converse", "converse_stream"}


def tail(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    return tuple(reversed(names))


def direct_sends(source):
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and (tail(node.func)[-2:] in SDK_TAILS or node.func.attr in SDK_METHODS)]


@pytest.mark.parametrize("source", [
    "client.messages.create(model='x')", "client.messages.stream(model='x')",
    "client.chat.completions.create(model='x')", "client.responses.create(model='x')",
    "client.invoke_model(body='x')", "client.invoke_model_with_response_stream(body='x')",
    "client.converse(modelId='x')", "client.converse_stream(modelId='x')",
    "client.images.generate(prompt='x')", "client.images.edit(image='x')",
    "client.embeddings.create(input='x')",
])
def test_new_direct_sdk_sends_fail_static_qualification(source):
    assert direct_sends(source) == [1]


def test_client_construction_and_admission_forwarding_are_allowed():
    assert direct_sends("client = AnthropicBedrock(max_retries=0)\ncall.create(client.messages.create)") == []


def test_all_production_python_is_free_of_direct_sdk_sends():
    offenders = []
    for directory in ("app", "fitx_mcp", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            for line in direct_sends(path.read_text(encoding="utf-8")):
                offenders.append((str(path.relative_to(ROOT)), line))
    assert offenders == []


CANONICAL = {"coach", "training_plan", "nutrition_plan", "nutrition", "menu_extract", "menu_ocr",
             "vision", "summary", "health_probe", "other"}
BOUNDARIES = {"_openai_chat", "_claude_chat", "_heavy_chat", "_heavy_complete",
              "_bedrock_validate_image", "_bedrock_compare_images", "_bedrock_image_message", "admit"}
# These forwarding helpers carry a feature supplied by their audited callers.
FORWARDERS = {"_openai_chat", "_claude_chat", "_heavy_chat", "_heavy_complete",
              "_bedrock_validate_image", "_bedrock_compare_images", "_bedrock_image_message"}
# Default vision policy is intentional and preserves its existing budget/guard.
DEFAULT_VISION = {("app/services/ai_coach.py", "_tool_analyze_gym_photo"),
                  ("app/services/menu_extract.py", "validate_pump_check")}


def test_every_production_ai_boundary_has_a_canonical_feature():
    from app.services import ai_input_budget
    assert set(ai_input_budget.FEATURES) == CANONICAL
    errors = []
    class Visitor(ast.NodeVisitor):
        def __init__(self, path):
            self.path, self.stack = path, []
        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()
        def visit_Call(self, node):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in BOUNDARIES:
                feature = next((kw.value for kw in node.keywords if kw.arg == "feature"), None)
                fn = self.stack[-1] if self.stack else ""
                if isinstance(feature, ast.Constant) and feature.value in CANONICAL - {"other"}:
                    pass
                elif self.path == "app/services/ai.py" and fn in FORWARDERS and isinstance(feature, ast.Name) and feature.id == "feature":
                    pass
                elif feature is None and (self.path, fn) in DEFAULT_VISION:
                    pass
                elif self.path == "app/blueprints/mobile_nutrition_closure.py" and fn == "_generation_chat" and any(kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == "kwargs" for kw in node.keywords):
                    # nutrition_plan_generation supplies feature=nutrition_plan.
                    pass
                else:
                    errors.append((self.path, fn, node.lineno))
            self.generic_visit(node)
    for path in (ROOT / "app").rglob("*.py"):
        Visitor(path.relative_to(ROOT).as_posix()).visit(ast.parse(path.read_text(encoding="utf-8")))
    assert errors == []


@pytest.mark.parametrize("path, function, helper", [
    ("app/services/mobile_pump_checks/analysis.py", "analyze_image", "_bedrock_validate_image"),
    ("app/services/mobile_pump_check_comparisons/analysis.py", "analyze_comparison", "_bedrock_compare_images"),
])
def test_injected_mobile_vision_provider_keeps_intentional_default(path, function, helper):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and any(isinstance(child, ast.Assign) and isinstance(child.value, ast.Name)
                         and child.value.id == helper for child in ast.walk(node))]
    assert len(functions) == 1
    calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "provider"]
    assert len(calls) == 1
    assert all(kw.arg != "feature" for kw in calls[0].keywords)
    # The shared helper's code-owned default is vision, so its budget stays exact.
    from app.services import ai
    import inspect
    assert inspect.signature(getattr(ai, helper)).parameters["feature"].default == "vision"
