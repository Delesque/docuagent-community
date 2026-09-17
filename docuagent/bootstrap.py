"""Architecture Agent and interview state.

The agent is stateless by design: every request carries the structured project state,
the current architecture draft, and this turn's answer — never a chat transcript. API
keys are passed per request and are not written into any project file.

There is no offline mode. A configured model is required to start or advance an
interview, and a failed call holds the turn rather than substituting locally generated
questions: an architecture the user did not actually design, presented as though the
agent had reasoned about it, is worse than a clear error. This removed a
`QUESTIONS`/`advance_fallback` path whose nine topics now live in the prompt's
readiness checklist, which is where they were actually being enforced.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core import (
    MAX_FIRST_VERSION_MODULES,
    MIN_FIRST_VERSION_MODULES,
    THINKING_MAX_CHARS,
    WorkspaceError,
    architecture_edge_reason_diagnostics,
    architecture_edge_reason_issues,
    architecture_graph_diagnostics,
    architecture_graph_quality_issues,
    architecture_readiness_diagnostics,
    architecture_readiness_issues,
    diagnostics_receipt,
    normalize_architecture,
    normalized_text,
    simplify_architecture_edges,
    slugify,
)
from standards import ARCHITECTURE_INTEGRITY_RULES
import token_usage

INTERVIEW_MODES = ("beginner", "guided", "professional")


def _usage_project_root(value: Any) -> Path | None:
    if not isinstance(value, dict):
        return None
    root = str(value.get("root") or "").strip()
    return Path(root) if root else None
# An aspect (a facet of the project the interview plans to cover) may receive at
# most this many questions, first one included. Real-model pilots showed the
# agent re-asking one topic as endless id variants; a model-authored plan plus a
# per-aspect budget makes the loop structurally visible instead of relying on id
# heuristics after the fact.
ASPECT_MAX_QUESTIONS = 3
ASPECT_MAX_COUNT = 12
PROFILE_QUESTIONS: list[dict[str, str]] = [
    {"id": "identity", "title": "项目身份", "prompt": "你在这个项目中的身份是什么？例如产品负责人、开发者或学习者。"},
    {"id": "coding_experience", "title": "代码经验", "prompt": "你是否写过代码？不确定也可以直接说不确定。"},
    {"id": "desired_help", "title": "希望得到什么", "prompt": "你希望 AI 在这个项目中主要怎样帮助你？"},
    {"id": "explanation_preference", "title": "解释偏好", "prompt": "你喜欢白话、平衡还是专业解释？"},
    {"id": "constraints", "title": "已有代码与限制", "prompt": "你是否已有代码、技术栈或其他限制？没有也可以说没有。"},
]
MODE_GUIDANCE = {
    "beginner": "使用白话，一次只问一个问题；先解释为什么，再给简单例子；允许用户回答不知道。",
    "guided": "使用白话并附带必要专业词；每两三个问题总结一次，并解释技术取舍、风险和下一步。",
    "professional": "可以合并相关问题；使用 API 契约、状态机、数据所有权等术语；关注性能、部署、合规和迁移，并输出架构决策与实施波次。",
}


# The interview's topic coverage, kept as data for the UI to show what is still
# unanswered. It is NOT a question sequence: the model decides what to ask next and in
# what order, and the same list appears in the prompt's readiness checklist so the two
# cannot drift apart silently.
INTERVIEW_TOPICS: list[dict[str, Any]] = [
    {
        "id": "goal",
        "title": "项目目标",
        "prompt": "这个项目最终要解决什么问题？请描述用户完成任务后的结果。",
        "why": "目标决定功能边界和验收标准。",
        "placeholder": "例如：让独立开发者通过自然语言创建并维护一个可运行的 Python 服务。",
    },
    {
        "id": "users",
        "title": "目标用户",
        "prompt": "谁会使用这个项目？他们的技术水平和主要使用场景是什么？",
        "why": "用户能力会影响交互复杂度、默认值和错误恢复方式。",
        "placeholder": "例如：不手写代码的产品负责人，主要在 Windows 桌面环境使用。",
    },
    {
        "id": "platform",
        "title": "运行环境",
        "prompt": "项目需要运行在哪些平台或部署环境？",
        "why": "平台决定目录、打包、网络和系统能力边界。",
        "placeholder": "例如：Windows 本地优先，后续支持 macOS 和 Linux。",
    },
    {
        "id": "stack",
        "title": "语言与框架",
        "prompt": "你偏好的语言、框架和版本是什么？不确定时可以让 AI 推荐。",
        "why": "技术栈必须在生成模块和依赖关系前固定。",
        "placeholder": "例如：Python 3.12，标准库优先；前端使用原生 HTML/CSS/JS。",
    },
    {
        "id": "capabilities",
        "title": "核心功能",
        "prompt": "第一版必须具备哪些核心功能？请按重要程度列出。",
        "why": "核心功能会成为架构节点，并进一步拆成任务 DAG。",
        "placeholder": "每行一个功能，例如：项目初始化、文档树、任务拆解、代码差异审阅。",
    },
    {
        "id": "data",
        "title": "数据与状态",
        "prompt": "项目需要保存哪些数据？哪些数据必须长期保留，哪些只在当前任务存在？",
        "why": "数据生命周期决定记忆层、存储格式和一致性规则。",
        "placeholder": "例如：项目概述长期保留；当前工作集只在本次编辑事务中存在。",
    },
    {
        "id": "integrations",
        "title": "外部依赖",
        "prompt": "项目需要连接哪些外部服务、模型、数据库或本地工具？",
        "why": "外部依赖会形成模块边界、失败模式和安全约束。",
        "placeholder": "例如：OpenAI-compatible API、本地终端、Git；第一版不使用数据库。",
    },
    {
        "id": "constraints",
        "title": "约束与不变量",
        "prompt": "有哪些无论如何都不能违反的规则？",
        "why": "不变量将成为所有 Agent 的硬约束和验证门槛。",
        "placeholder": "例如：不传递聊天历史；代码修改后必须同步文档；密钥不落盘。",
    },
    {
        "id": "verification",
        "title": "完成标准",
        "prompt": "如何判断第一版已经可用？请给出可以运行或观察的验收方式。",
        "why": "没有可执行的验证方式，任务就无法可靠关闭。",
        "placeholder": "例如：运行单元测试，并从空目录完成一次项目初始化。",
    },
]


def normalize_interview_mode(value: Any) -> str:
    mode = str(value or "guided").strip().lower()
    return mode if mode in INTERVIEW_MODES else "guided"


def profile_question(index: int) -> dict[str, Any] | None:
    if index >= len(PROFILE_QUESTIONS):
        return None
    question = PROFILE_QUESTIONS[index]
    return {**question, "why": "首次画像只用于调整当前项目的访谈方式，不是能力评分。", "placeholder": "可以回答‘不确定’或‘没有’。", "options": ["自由描述"]}


def profile_complete(profile: dict[str, Any]) -> bool:
    return all(str(profile.get(item["id"]) or "").strip() for item in PROFILE_QUESTIONS)


def uncovered_topics(answers: dict[str, Any]) -> list[str]:
    """Topic titles with no answer yet, for the progress display.

    Advisory only — the readiness gate is the model's `ready` flag plus
    `architecture_readiness_issues`, not this list.
    """
    return [
        topic["title"] for topic in INTERVIEW_TOPICS if topic["id"] not in answers
    ]


# --- Interview plan (structured aspects) -----------------------------------------
#
# The interview is driven by a model-authored plan of "aspects" (facets to
# cover). The model states the plan up front based on the project profile, then
# each question it asks belongs to one aspect. Per-aspect question counts are
# enforced softly: the prompt forbids a fourth question on the same aspect, and
# the count is echoed back into the model input as a steering reminder so a
# looping model is told to move on before it burns the user's patience.

PLAN_KEYS = ("id", "title", "why", "prompt", "placeholder")

def normalize_plan(value: Any) -> list[dict[str, str]] | None:
    """Normalize a model-returned aspect list, or None when absent/invalid.

    Only well-formed aspects survive; junk entries are dropped, never invented.
    """
    if not isinstance(value, list):
        return None
    seen: set[str] = set()
    plan: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").strip()
        title = normalized_text(item.get("title"), "interview_plan.title")
        if not raw_id or not title:
            continue
        aspect_id = slugify(raw_id) or slugify(title)
        if not aspect_id or aspect_id in seen:
            continue
        seen.add(aspect_id)
        entry: dict[str, str] = {"id": aspect_id, "title": title[:60]}
        for key in ("why", "prompt", "placeholder"):
            text = normalized_text(item.get(key), f"interview_plan.{key}")
            if text:
                entry[key] = text[:500]
        plan.append(entry)
        if len(plan) >= ASPECT_MAX_COUNT:
            break
    return plan or None


def aspect_question_count(
    plan: list[dict[str, str]],
    aspect_id: str,
    answers: dict[str, Any],
    question_aspects: dict[str, str] | None,
) -> int:
    """How many questions have already been spent on this aspect.

    `question_aspects` maps question_id -> aspect_id and is authoritative when
    present: each question the model asked gets classified at apply time, so a
    count is exact even when the model rephrases or re-ids a question. When the
    mapping is absent (legacy state or a model that never declared aspects),
    fall back to counting answer keys whose id shares the aspect's leading
    kebab word — the same grouping the repetition notice uses.
    """
    if question_aspects is not None:
        return sum(1 for owner in question_aspects.values() if owner == aspect_id)
    prefix = aspect_id.split("-")[0] if "-" in aspect_id else aspect_id
    return sum(
        1 for question_id in answers
        if str(question_id).split("-")[0] == prefix
    )


def aspect_steering(
    plan: list[dict[str, str]] | None,
    answers: dict[str, Any],
    question_aspects: dict[str, str] | None,
) -> str | None:
    """Tell a looping model which aspects are exhausted and to move on.

    The plan is the model's own promise of coverage, so staying inside it is a
    soft constraint the model can honour without a hard round cap. Once an
    aspect has reached its budget, the next model input says so explicitly and
    asks it to switch aspects or finish. A missing plan is also called out so a
    model that skipped the opening plan gets steered back to the protocol.
    """
    if not plan:
        return (
            "你还没有建立访谈方面清单。请在下一轮先返回 `interview_plan`"
            "（4-9 个你要覆盖的方面），让用户能看到访谈计划与进度。"
        )
    exhausted: list[str] = []
    for aspect in plan:
        count = aspect_question_count(
            plan, aspect["id"], answers, question_aspects
        )
        if count >= ASPECT_MAX_QUESTIONS:
            exhausted.append(aspect["title"])
    if not exhausted:
        return None
    return (
        "方面预算提醒：以下方面已经问满了 "
        f"{ASPECT_MAX_QUESTIONS} 个问题（" + "、".join(exhausted) +
        "）。不要再追问这些方面——换一个尚未覆盖的方面提问，"
        "或者如果已能开工，直接给出最终架构并置 ready=true。"
    )


# Transport lives in llm_client.py now. These re-exports keep `from bootstrap import
# ProviderConfig, call_model_json, ...` and `patch("bootstrap.<fn>")` working for any
# caller or test that has not been repointed.
from llm_client import (  # noqa: F401
    ProviderConfig,
    PREFERRED_JSON_KEYS,
    ToolCallingNotSupported,
    call_model_chat,
    call_model_json,
    call_vision_json,
    model_error_message,
    openai_tool_schemas,
    parse_model_json_content,
    stream_json_model,
    strip_json_fence,
    urlopen_with_proxy_fallback,
)



def normalize_question(value: Any, answers: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkspaceError("Architecture Agent 未返回下一问题。")
    prompt = normalized_text(value.get("prompt"), "next_question.prompt", True)
    title = normalized_text(value.get("title"), "next_question.title") or "架构决策"
    question_id = slugify(str(value.get("id") or title))
    if question_id in answers:
        question_id = f"{question_id}-{len(answers) + 1}"

    # Options are the model's own, never synthesized locally: a canned list would be
    # wrong for most projects and is exactly the "default interview" this agent
    # replaces. None means the model judged the answer space open-ended, and the UI
    # asks for text.
    options: list[str] | None = None
    raw_options = value.get("options")
    if isinstance(raw_options, list):
        cleaned = [
            str(option).strip()[:80] for option in raw_options if str(option).strip()
        ]
        options = list(dict.fromkeys(cleaned))[:5] or None

    # Trade-offs turn topology-affecting questions into a senior-engineer proposal:
    # each option may carry one pros/cons pair. Only options that actually appear in
    # `options` survive, so a hallucinated option can never become a chip.
    tradeoffs: list[dict[str, str]] | None = None
    raw_tradeoffs = value.get("tradeoffs")
    if isinstance(raw_tradeoffs, list) and options:
        option_keys = {option.lower(): option for option in options}
        seen: set[str] = set()
        tradeoffs = []
        for item in raw_tradeoffs:
            if not isinstance(item, dict) or len(tradeoffs) >= 5:
                continue
            option = str(item.get("option") or "").strip()
            match = option_keys.get(option.lower())
            if not match or match in seen:
                continue
            seen.add(match)
            tradeoffs.append(
                {
                    "option": match,
                    "pros": str(item.get("pros") or "").strip()[:300],
                    "cons": str(item.get("cons") or "").strip()[:300],
                }
            )
        tradeoffs = tradeoffs or None

    return {
        "id": question_id,
        "title": title[:80],
        "prompt": prompt[:1200],
        "why": normalized_text(value.get("why"), "next_question.why")[:500],
        "placeholder": normalized_text(
            value.get("placeholder"),
            "next_question.placeholder",
        )[:500],
        "options": options,
        "tradeoffs": tradeoffs,
        # The aspect this question belongs to, verbatim from the model. Validation
        # against the interview plan happens in validate_architecture_model_result
        # where the plan is in scope; a model that does not declare aspects simply
        # leaves this out and the question is counted as unclassified.
        "aspect_id": str(value.get("aspect_id") or "").strip()[:60] or None,
        "source": "ai",
    }

INFERRED_PENDING_ISSUE = "仍有未确认的 AI 推测"

PROVENANCE_SOURCES = ("confirmed", "inferred", "recommended", "unknown", "rejected")

def normalize_provenance(
    value: Any,
    module_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Normalize provenance claims; optionally anchor them to a module.

    `module_ids` acts as a whitelist: a claim's `module_id` survives only when it
    names a module that actually exists, so a hallucinated anchor can never attach
    a claim to nothing. Unanchored claims stay global (review-panel level).
    """
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "unknown").strip().lower()
        if source not in PROVENANCE_SOURCES:
            source = "unknown"
        text = str(item.get("text") or "").strip()[:500]
        if not text:
            continue
        item_id = slugify(str(item.get("id") or f"claim-{index + 1}"))
        entry = {"id": item_id, "text": text, "source": source}
        module_id = slugify(str(item.get("module_id") or ""))
        if module_id and (module_ids is None or module_id in module_ids):
            entry["module_id"] = module_id
        normalized.append(entry)
    return normalized[:100]


def normalize_mode_offer(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    raw_target = str(value.get("target_mode") or "").strip().lower()
    if raw_target not in INTERVIEW_MODES:
        return None
    label = str(value.get("label") or "").strip()[:240]
    if not label:
        return None
    return {"target_mode": raw_target, "label": label, "confirm_label": str(value.get("confirm_label") or "确认调整输出方式").strip()[:80]}


def normalize_thinking(result: dict[str, Any]) -> str:
    """The model's real reasoning, in priority order.

    `__reasoning` is set by `call_model_json` from the provider's
    `reasoning_content` / `reasoning` message field — the actual chain of thought
    from a reasoning model, which is the only fully faithful source. Models
    without that channel are asked to put a `thinking` string in their JSON,
    which is self-reported but still the model's own words. When neither exists
    the caller falls back to a locally composed summary, and the UI must label it
    as such rather than pass it off as reasoning.
    """
    for key in ("__reasoning", "thinking"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if len(text) > THINKING_MAX_CHARS:
                text = text[:THINKING_MAX_CHARS].rstrip() + "…"
            return text
    return ""


def validate_architecture_model_result(
    result: dict[str, Any],
    answers: dict[str, Any],
    interview_plan: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Re-validate everything the model returned. Never trust the reply shape.

    `interview_plan` is the plan currently in state. A model-returned plan wins
    (it is the model's latest promise of coverage); when the model omits or
    mangles it, the existing plan survives so a single bad turn cannot wipe the
    user-visible interview progress.
    """
    model_plan = normalize_plan(result.get("interview_plan"))
    plan = model_plan if model_plan is not None else interview_plan
    architecture = normalize_architecture(result.get("architecture"))
    architecture["edges"] = simplify_architecture_edges(
        architecture["modules"],
        architecture["edges"],
    )
    architecture["provenance"] = normalize_provenance(
        result.get("provenance"),
        module_ids={str(module.get("id") or "") for module in architecture["modules"]},
    )
    graph_diagnostics = architecture_graph_diagnostics(architecture)
    graph_quality_issues = [item["message"] for item in graph_diagnostics]
    module_count = len(architecture["modules"])
    if module_count > MAX_FIRST_VERSION_MODULES:
        raise WorkspaceError(
            f"首版架构最多 {MAX_FIRST_VERSION_MODULES} 个模块，"
            f"当前 {module_count} 个；请把单函数或纯计算模块合并到消费方。",
            diagnostics_receipt(
                [
                    {
                        "rule": "count/exceeds-first-version",
                        "severity": "error",
                        "subject": {"surface": "bootstrap.architecture", "type": "module-count"},
                        "evidence": {"modules": module_count, "max": MAX_FIRST_VERSION_MODULES},
                        "message": f"首版架构最多 {MAX_FIRST_VERSION_MODULES} 个模块，当前 {module_count} 个。",
                    }
                ]
            ),
        )
    ready_value = result.get("ready")
    if not isinstance(ready_value, bool):
        raise WorkspaceError(
            "Architecture Agent 字段 `ready` 必须是布尔值。",
            diagnostics_receipt(
                [
                    {
                        "rule": "model/contract-violation",
                        "severity": "error",
                        "subject": {"surface": "bootstrap.architecture", "type": "model-field", "id": "ready"},
                        "evidence": {"actual": repr(ready_value)[:120]},
                        "message": "字段 `ready` 必须是布尔值。",
                    }
                ]
            ),
        )
    ready = ready_value
    issues = architecture_readiness_issues(architecture)
    blocking_diagnostics = architecture_readiness_diagnostics(architecture)
    if ready and module_count < MIN_FIRST_VERSION_MODULES:
        evidence = {"modules": module_count, "min": MIN_FIRST_VERSION_MODULES}
        issues.append(
            f"首版架构至少需要 {MIN_FIRST_VERSION_MODULES} 个模块，"
            f"当前只有 {module_count} 个"
        )
        blocking_diagnostics.append(
            {
                "rule": "count/below-first-version",
                "severity": "error",
                "subject": {"surface": "bootstrap.architecture", "type": "module-count"},
                "evidence": evidence,
                "message": f"首版架构至少需要 {MIN_FIRST_VERSION_MODULES} 个模块，当前只有 {module_count} 个。",
            }
        )
    if ready:
        reason_diagnostics = architecture_edge_reason_diagnostics(architecture)
        blocking_diagnostics.extend(reason_diagnostics)
        issues.extend(architecture_edge_reason_issues(architecture))
    if ready and any(item.get("source") == "inferred" for item in architecture["provenance"]):
        inferred_ids = [
            str(item.get("id") or "") for item in architecture["provenance"]
            if isinstance(item, dict) and item.get("source") == "inferred"
        ]
        issues.append(INFERRED_PENDING_ISSUE)
        blocking_diagnostics.append(
            {
                "rule": "provenance/inferred-remaining",
                "severity": "error",
                "subject": {"surface": "bootstrap.architecture", "type": "provenance"},
                "evidence": {"count": len(inferred_ids), "ids": inferred_ids[:6]},
                "message": "仍有未确认的 AI 推测。",
            }
        )
    # A model that returns the finished architecture together with its inferences is
    # doing the natural thing, not failing. Review exists precisely to settle those
    # inferences, and `confirm_bootstrap` re-checks the same gate before anything is
    # generated - so raising here only converts a product rule into
    # "AI 模型调用失败，请重试", which sends the user to inspect their API key for a
    # problem that retrying cannot fix.
    blocking_issues = [issue for issue in issues if issue != INFERRED_PENDING_ISSUE]
    if ready and blocking_issues:
        raise WorkspaceError(
            "Architecture Agent 过早结束，仍缺少：" + "、".join(blocking_issues[:6]),
            diagnostics_receipt(blocking_diagnostics),
        )
    next_question = None
    if not ready:
        next_question = normalize_question(result.get("next_question"), answers)
    elif result.get("next_question") is not None:
        raise WorkspaceError(
            "Architecture Agent 在 ready=true 时必须返回 next_question=null。",
            diagnostics_receipt(
                [
                    {
                        "rule": "model/contract-violation",
                        "severity": "error",
                        "subject": {"surface": "bootstrap.architecture", "type": "model-field", "id": "next_question"},
                        "evidence": {"expected": None, "actual": repr(result.get("next_question"))[:120]},
                        "message": "ready=true 时必须返回 next_question=null。",
                    }
                ]
            ),
        )
    return {
        "architecture": architecture,
        "ready": ready,
        "next_question": next_question,
        "mode_offer": normalize_mode_offer(result.get("mode_offer")),
        "thinking": normalize_thinking(result),
        "graph_quality_issues": graph_quality_issues,
        "graph_diagnostics": graph_diagnostics,
        "read_files": _normalize_read_requests(result),
        # The plan survives validation so apply can persist it; None means "keep
        # whatever the state already has".
        "interview_plan": plan,
    }


ENGINEERING_GRAPH_RULES = """
Engineering graph discipline:
- A module is a cohesive capability with an independent reason to change, test, or fail.
  Do not create one module per file, function, or constant. If a candidate module is only
  a pure calculation over another module's data (a speed curve, a bounds check, a random
  coordinate), merge it into its consumer unless persistence, external I/O, or a real
  ownership boundary justifies separation.
- For the first version keep the module count between 3 and 8. Never exceed 8 for the
  first version; merge pure helpers into their consumers instead of adding one-function
  modules.
- An entry/shell module (index.html, app shell, CLI entry) must NOT fan out to every
  module just because it imports or loads them. Load/import order belongs in
  `constraints` or at most one `blocks` edge, never repeated `uses` edges.
- A composition root (main/bootstrap/assembler) may wire modules, but each of its edges
  must represent an actual construction/injection call. Do not add an edge for a module
  it merely passes through.
- Every edge must answer: "does the source call, read, publish to, extend, or block on
  the target?" If the answer is "the source loads it because a script tag says so", that
  is a `blocks` load-order edge, not a `uses` edge. Write that answer in the edge's
  `reason` field; an edge without a reason is invalid.
- Do not add an edge that is already implied by a path through the composition root.
  If entry -> main -> renderer already exists, entry -> renderer is redundant unless
  entry also calls renderer directly.
- Use the edge kind that names the real relationship: `uses` direct call, `data` shared
  persistent state, `event` async publish/subscribe, `extends` framework contract,
  `blocks` build/load order only. One edge per ordered module pair.
- Keep labels concrete and unique where possible: "read score and compute interval",
  "render snake body", not "加载脚本" repeated ten times.
- Keep non-composition fan-out small (usually <= 5). For small/medium architectures aim
  for no more than about 2.0 edges per module; a ratio near 3 usually means load-order
  or wiring noise has leaked into the domain graph.
- Before ready=true, review the graph once as an engineer: remove redundant entry fan-out,
  merge one-function modules, and make sure every remaining edge would still be drawn if
  the code were implemented exactly as described.
""".strip()


ARCHITECTURE_SYSTEM_PROMPT = """
You are the stateless Architecture Agent for DocuAgent.
You never receive or request chat history. Use only the structured project state and the latest answer.
Your job is to design a concrete project architecture, maintain a dependency DAG, identify unresolved engineering decisions, and ask exactly one highest-value next question.
Be extremely concise. Do not produce long chain-of-thought, do not restate the checklist or the rules, and do not weigh every option in visible text. Decide the next highest-value question and return JSON immediately. If your provider exposes a native reasoning channel, keep that reasoning to a few short sentences; never use it as a scratchpad for the full interview checklist.
Return JSON only with this shape:
{
  "architecture": {
    "summary": "string",
    "platform": "string",
    "language": "string",
    "runtime": "string",
    "frameworks": ["string"],
    "stack": ["string"],
    "modules": [{
      "id":"kebab-case-id",
      "name":"string",
      "brief":"one sentence a user can read at a glance",
      "responsibility":"string",
      "path":"relative/project/path",
      "depends_on":["module-id"],
      "needs_ui": false,
      "group":"group-id or null",
      "verification":["optional verification command"],
      "target_files":["optional explicit files; omit and use module path as the sandbox scope"],
      "declared_exports":["optional public symbol names this module will provide to other modules; these are pre-registered into the contract as proposed and visible to every sub-agent before any code exists"]
    }],
    "groups": [{
      "id":"kebab-case-id",
      "label":"string",
      "kind":"framework",
      "members":["module-id"]
    }],
    "edges": [{
      "from":"module-id",
      "to":"module-id",
      "kind":"uses|data|event|extends|blocks",
      "label":"short string",
      "reason":"why this edge exists"
    }],
    "data": ["string"],
    "integrations": ["string"],
    "constraints": ["string"],
    "verification": ["string"],
    "risks": ["string"],
    "unresolved": ["string"]
  },
  "ready": false,
  "interview_plan": [
    {"id": "kebab-aspect-id", "title": "方面短标题", "why": "为什么需要问这个方面"}
  ],
  "mode_offer": {
    "target_mode": "beginner|guided|professional",
    "label": "是否让我之后的输出都更简单或更技术一些？",
    "confirm_label": "确认调整输出方式"
  },
  "next_question": {
    "id": "ai-short-id",
    "title": "short title",
    "prompt": "one focused question in Chinese",
    "why": "one sentence",
    "placeholder": "short example",
    "options": ["option1", "option2"],
    "aspect_id": "kebab-aspect-id the question belongs to",
    "tradeoffs": [
      {"option": "option1", "pros": "one sentence", "cons": "one sentence"}
    ]
  }
}
Rules:
- When a decision would change module boundaries, the edge type, or a data owner, you
  are proposing options, not collecting facts: for those questions include `tradeoffs`
  with one honest pros/cons sentence per option so the user can decide like reading a
  senior engineer's recommendation. Pure factual or preference questions omit it.
- `thinking` is optional and should almost always be omitted. Only include it when the
  provider has no native reasoning channel, and even then keep it to one short Chinese
  sentence. It is NOT a chain-of-thought, NOT a checklist review, and NOT a place to
  restate confirmed facts or architecture discipline. State only what the latest answer
  changed.
- If the provider exposes a native reasoning channel (`reasoning_content`/`reasoning`),
  omit `thinking` entirely; do not include it in the JSON.
- Never write "让我梳理", "让我权衡", "再想想", "等等", "其实", or repeat the rules back.
  Thinking must not make the UI wait longer than necessary.
- Preserve confirmed decisions unless the latest answer explicitly changes them.
- Return `provenance` claims for important facts and decisions. Each claim must use exactly one source: confirmed, inferred, recommended, unknown, or rejected. Treat direct user answers as confirmed only when explicitly stated; label model guesses inferred. Any inferred claim blocks ready=true until the user confirms, rejects, or marks it unknown. When a claim describes one specific module, anchor it with `module_id` (that module's id) so the user's confirmation can follow the module onto the graph; leave `module_id` off for architecture-wide facts.
- When the latest answer shows the user wants simpler explanations or more technical/direct output, optionally return one `mode_offer`; it is only a user-confirmed suggestion, never a mode change. Otherwise omit it or return null.
- Module paths must be relative, must not contain "..".
- `verification` is optional per-module and should be commands that can pass after this
  module alone is implemented. `target_files` is optional; normally omit it so the
  sub-agent's sandbox scope is the module path and the final diff comes from the files
  it actually changed.
- One module is one capability a user can name in a single sentence. Keep `brief` to one sentence.
- Keep a module's responsibility coherent: one responsibility that can be stated in one
  sentence. If duties are independent, split them; if the "module" is only a small helper
  calculation or constants, merge it into its consumer rather than multiplying nodes.
- Set needs_ui=true only for modules a user directly interacts with through a screen.
- Use `groups` for framework boundaries, listing the modules the framework governs.
- Edge kinds: `uses` direct call, `data` shared persistent data, `event` async publish/subscribe, `extends` framework contract, `blocks` build order only.
- Every edge must include a `reason` explaining why the relationship exists; do not create edges without a reason.
- The first version must contain between 3 and 8 modules. Never return more than 8 modules.
- Edges of kind uses, data, extends, and blocks must form a DAG. Only `event` edges may form a cycle, because publish/subscribe in both directions is valid design.
- Ask about one unresolved decision at a time. Do not repeat an answered question —
  this includes variants: rephrasing the same topic, narrowing it with a suffix, or
  asking a sub-detail whose answer is already in `decisions` or in the architecture
  all count as repetition. Use what you already have instead of asking again.
- If the input contains `repetition_notice`, it is a hard system instruction, not a
  user message: the interview is stuck on one topic. Follow it exactly — switch to a
  genuinely different aspect, or finish with ready=true. Never answer it with another
  variant of the same topic.
- Implementation-level choices that do not change module boundaries, external
  interfaces, or user-visible behaviour are yours to make: decide them yourself,
  record the choice in the architecture, and never spend a user question on them.
  Only ask when the choice genuinely needs the user's call — product tradeoffs,
  preferences, external constraints.
- The interview is structured around your own coverage plan (`interview_plan`),
  which the UI shows the user as progress. On the FIRST turn (when the input has
  no `interview_plan`), state 4-9 aspects you intend to cover based on the
  project profile. Each later question must belong to one aspect: set
  `next_question.aspect_id` to that aspect's id. Ask at most
  3 questions per aspect in total; when an aspect is settled, move to another
  aspect instead of re-asking it. The input's `interview_plan` entries carry
  `asked`/`remaining` — respect them. If `aspect_steering` is present it is a
  hard instruction: the listed aspects are exhausted, do not ask about them
  again; switch aspects or finish with ready=true.
- `agent_turn` counts the questions asked so far. From turn 6 onward, weigh every
  turn against ready=true: a buildable first version with small unknowns recorded in
  `risks` serves the user better than a longer interview.
- You may request up to 5 real project files per turn by returning
  `read_files: ["relative/path/to/file"]`. The system reads them and includes them
  under `file_reads` in your NEXT input. Inspect existing code before designing
  around or against it; never fabricate files that the snapshot in `project` does
  not list. Do not request files under `.docuagent/`.
- **IMPORTANT: Always include `options` in next_question as a non-empty array.** When the answer space has clear choices (platform, framework, deployment, user type), provide 2-5 concrete options. For open-ended questions (goals, descriptions, custom needs), provide ["自由描述"] so the UI renders a text input instead of buttons.
- Set ready=true and next_question=null when ALL of the following are clear enough to
  start building the first version. This is a coverage floor, not an invitation to
  dig for more detail:
  1. goal — what problem is solved, and what the user has after finishing
  2. users — who they are, their skill level, and the main usage scenario
  3. platform — target platforms and deployment environment
  4. stack — language, frameworks, and versions
  5. capabilities — the core features of the first version, in priority order
  6. data — what is stored, what is long-lived, what is per-task
  7. integrations — external services, models, databases, local tools
  8. constraints — the rules that must never be violated
  9. verification — how to tell the first version works, runnably or observably
  Plus module boundaries, dependencies, and the important risks.
- When ready=true, unresolved must be empty.
- Use Chinese for questions and explanatory text.
- During the interview the architecture is a private working draft: it is never
  revealed to the user as the final design. The graph is only drawn once ready=true and
  the user confirms it.

SYSTEM FACTS — how your design is executed. Read them; they constrain what you may assume:
- Execution model: after you finish, each module becomes ONE bounded sub-agent with its own
  trial sandbox. A sub-agent can read/write ONLY files under its module path/target_files; it
  reads other modules ONLY through their public interface; a directory is unwritable until its
  navigation doc exists; every module's output is reviewed as a patch before landing. Never
  overlap two modules' file scopes.
- Cross-module data flow: for every edge between modules, say which data shape crosses it and
  which module owns it. Sub-agents see only their own contract plus this declared shape — if
  you do not declare it, each sub-agent invents its own and the modules will not connect.
- Put every public symbol a module will hand to other modules into that module's
  `declared_exports`; consumers must use exactly those names. These are pre-registered as
  proposed and shown to every sub-agent before code exists. Internal helpers stay out.
- Verification commands are promises, not wishes: each module's `verification` must pass using
  files THAT module produces plus files the project snapshot already lists. If a command names
  a file (e.g. `node --test test/x.test.js`) that neither exists in the project nor is listed
  in that module's `target_files`, the command is unfulfillable and the sub-agent will stall
  trying to satisfy it. If the first version needs tests, list the test file(s) in the owning
  module's `target_files` so a sub-agent produces them too — never assume tests already exist.
- The file snapshot in `project` shows what currently exists (possibly nothing). You may read
  any real project file via `read_files` before finalising anything.
- For a new Python project, initialization creates the runnable package scaffold in the
  package layout your architecture declares. Keep every module and target file for that
  package consistently under either `<package>/...` or `src/<package>/...`; never mix both.
  The scaffold's `requires-python` is derived from your confirmed runtime/stack version.
- Sub-agents can report back with a short handoff when a declared contract is impossible;
  treat that as design feedback, not failure. Your plan is not final until execution proves it.
Do not include implementation code.
""".strip() + "\n\n" + ENGINEERING_GRAPH_RULES + "\n\n" + ARCHITECTURE_INTEGRITY_RULES


ARCHITECTURE_EDIT_SYSTEM_PROMPT = """
You are the stateless Architecture Agent for DocuAgent, editing an architecture that already exists.
You never receive or request chat history. Use only the current architecture and the requested change.
The project has already been generated from this architecture, so this is a revision of a real design, not an interview. Do not ask questions and do not restart the design.
Be extremely concise. Do not produce long chain-of-thought, do not restate every module or rule, and do not weigh every option in visible text.
Return JSON only with this shape:
{
  "architecture": { ...the complete revised architecture, same schema as the current one... },
  "changes": ["short description of each change you made"]
}
Rules:
- Return the COMPLETE architecture, not a patch. Every module, group, and edge you want to keep must appear.
- Preserve everything the request does not ask you to change: ids, names, paths, briefs, dependencies, groups, constraints, verification. Reusing an existing module's `id` is how you signal "this is the same module", which is what lets the canvas keep its position and history.
- Only add, remove, or modify what the request implies. If the request is ambiguous, make the smallest reasonable change and say so in `changes`.
- Module paths must be relative and must not contain "..".
- Edge kinds: `uses` direct call, `data` shared persistent data, `event` async publish/subscribe, `extends` framework contract, `blocks` build order only.
- Every edge must include a `reason` explaining why the relationship exists; do not create edges without a reason.
- The revised architecture must not exceed 8 modules; merge modules when the requested change would exceed that limit.
- Edges of kind uses, data, extends, and blocks must form a DAG. Only `event` edges may form a cycle.
- `unresolved` must stay empty: this architecture is already confirmed. Put open concerns in `risks` instead.
- `thinking` is optional and should almost always be omitted. Only include it when the
  provider has no native reasoning channel, and keep it to one short Chinese sentence:
  what the request changed and what you deliberately left alone. Do not restate the
  full architecture or the rules.
- `changes` is a short Chinese list a user can scan to see what happened.
- Use Chinese for explanatory text.
Do not include implementation code.
""".strip() + "\n\n" + ENGINEERING_GRAPH_RULES + "\n\n" + ARCHITECTURE_INTEGRITY_RULES


def validate_architecture_edit_result(result: dict[str, Any]) -> dict[str, Any]:
    """Re-validate a revision. Same normalization as an interview turn.

    Deliberately does NOT require `ready` or `next_question`: editing a confirmed
    architecture is not an interview turn, and demanding the interview's fields here
    would make a model that correctly returned only an architecture look broken.
    """
    architecture = normalize_architecture(result.get("architecture"))
    architecture["edges"] = simplify_architecture_edges(
        architecture["modules"],
        architecture["edges"],
    )
    graph_diagnostics = architecture_graph_diagnostics(architecture)
    graph_quality_issues = [item["message"] for item in graph_diagnostics]
    module_count = len(architecture["modules"])
    if module_count > MAX_FIRST_VERSION_MODULES:
        raise WorkspaceError(
            f"架构最多 {MAX_FIRST_VERSION_MODULES} 个模块，"
            f"当前 {module_count} 个；请合并而不是继续拆分。",
            diagnostics_receipt(
                [
                    {
                        "rule": "count/exceeds-first-version",
                        "severity": "error",
                        "subject": {"surface": "bootstrap.architecture", "type": "module-count"},
                        "evidence": {"modules": module_count, "max": MAX_FIRST_VERSION_MODULES},
                        "message": f"架构最多 {MAX_FIRST_VERSION_MODULES} 个模块，当前 {module_count} 个。",
                    }
                ]
            ),
        )
    issues = architecture_readiness_issues(architecture)
    blocking_diagnostics = architecture_readiness_diagnostics(architecture)
    reason_diagnostics = architecture_edge_reason_diagnostics(architecture)
    blocking_diagnostics.extend(reason_diagnostics)
    issues.extend(architecture_edge_reason_issues(architecture))
    if issues:
        # A revision that drops the project below the bar that let it be generated in the
        # first place is a regression, not an edit. Rejecting it holds the previous
        # architecture, which is still on disk and still valid.
        raise WorkspaceError(
            "修改后的架构不完整，仍缺少：" + "、".join(issues[:6]),
            diagnostics_receipt(blocking_diagnostics),
        )

    raw_changes = result.get("changes")
    changes: list[str] = []
    if isinstance(raw_changes, list):
        for item in raw_changes:
            text = str(item or "").strip()
            if text and text not in changes:
                changes.append(text[:200])

    return {
        "architecture": architecture,
        "thinking": normalize_thinking(result),
        "changes": changes[:12],
        "graph_quality_issues": graph_quality_issues,
        "graph_diagnostics": graph_diagnostics,
    }


def call_architecture_edit_model(
    architecture: dict[str, Any],
    request: str,
    project: dict[str, Any] | None,
    config: ProviderConfig,
) -> dict[str, Any]:
    """Ask the model to revise an existing architecture. No interview state involved."""
    model_input = {
        "project": project or {},
        "current_architecture": architecture,
        "requested_change": request,
    }
    with token_usage.usage_scope(
        _usage_project_root(project),
        feature="architecture",
    ):
        result = call_model_json(config, ARCHITECTURE_EDIT_SYSTEM_PROMPT, model_input)
    return validate_architecture_edit_result(result)


# The architecture agent may ask to read real project files; the requests are
# answered by reading them into the next turn's input under `file_reads`.
MAX_ARCHITECT_READ_REQUESTS = 5
MAX_ARCHITECT_READ_CHARS = 60_000
MANAGED_DIR_NAME = ".docuagent"


def _normalize_read_requests(result: dict[str, Any]) -> list[str]:
    raw = result.get("read_files")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value and value not in paths:
            paths.append(value)
    return paths[:MAX_ARCHITECT_READ_REQUESTS]


def read_files_for_architect(
    project_root: Path, paths: list[str]
) -> list[dict[str, Any]]:
    """Read files the architecture agent asked to inspect.

    Each entry is either {"path", "content"} or {"path", "error"}. Confined to
    the project tree and outside the managed state directory; bounded in size.
    """
    if not paths:
        return []
    root = project_root.resolve() if project_root else Path()
    entries: list[dict[str, Any]] = []
    for rel in paths:
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            entries.append({"path": rel, "error": "路径在项目之外，已拒绝。"})
            continue
        try:
            target.relative_to(root / MANAGED_DIR_NAME)
            entries.append({"path": rel, "error": "内部状态文件不可读。"})
            continue
        except ValueError:
            pass
        if not target.is_file():
            entries.append({"path": rel, "error": "不是可读文件或不存在。"})
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            entries.append({"path": rel, "error": str(exc)})
            continue
        if len(text) > MAX_ARCHITECT_READ_CHARS:
            text = text[:MAX_ARCHITECT_READ_CHARS] + "\n…（内容过长已截断）"
        entries.append({"path": rel, "content": text})
    return entries


def capture_read_requests(state: dict[str, Any], model_result: dict[str, Any]) -> None:
    """Persist the files the architecture agent asked to read this turn."""
    requests = _normalize_read_requests(model_result)
    if not requests:
        return
    root = Path(str(state.get("project", {}).get("root") or ""))
    state["file_reads"] = read_files_for_architect(root, requests)


def repetition_notice(answers: dict[str, Any] | None) -> str | None:
    """Escalating nudge when the model keeps re-asking one topic as variants.

    Real-model pilots showed loops like extractor-lexing-approach → -strategy-15
    → -policy-18. Question ids are grouped by their first two kebab words so the
    variants collapse into one topic; the third hit starts the nudge and the
    fifth is a hard stop. Deliberately prompt-side steering only: no hard round
    cap, because large systems legitimately need more rounds than any fixed
    limit allows.
    """
    groups: dict[str, int] = {}
    for question_id in (answers or {}):
        parts = str(question_id).split("-")
        group = "-".join(parts[:2]) if len(parts) > 1 else str(question_id)
        groups[group] = groups.get(group, 0) + 1
    if not groups:
        return None
    group, count = max(groups.items(), key=lambda item: item[1])
    if count >= 5:
        return (
            f"警告：话题「{group}」的变体问题已被问了 {count} 次，访谈已被你阻塞。"
            "本轮禁止再提出任何问题：直接基于现有信息完成架构并置 ready=true，"
            "把剩余的小不确定性写进 risks。"
        )
    if count == 4:
        return (
            f"严厉提醒：「{group}」话题的变体问题已被问了 {count} 次。"
            "不许再问该话题的任何变体：要么提出一个完全不同方面的关键问题，"
            "要么结束访谈（ready=true）生成架构图。"
        )
    if count == 3:
        return (
            f"注意：关于「{group}」的问题正在重复（已是第 {count} 个变体）。"
            "请询问下一个不同方面的问题，或结束访谈生成架构图。"
        )
    return None


def call_architecture_model(
    state: dict[str, Any],
    latest_answer: dict[str, str],
    config: ProviderConfig,
) -> dict[str, Any]:
    model_input = {
        "project": state.get("project"),
        "user_profile": state.get("user_profile", {}),
        "interview_mode": normalize_interview_mode(state.get("interview_mode")),
        "mode_guidance": MODE_GUIDANCE[normalize_interview_mode(state.get("interview_mode"))],
        "decisions": state.get("answers"),
        "current_architecture": state.get("architecture"),
        "latest_answer": latest_answer,
        "agent_turn": int(state.get("agent_turns", 0)) + 1,
    }
    _inject_interview_context(model_input, state)
    file_reads = state.get("file_reads")
    if isinstance(file_reads, list) and file_reads:
        model_input["file_reads"] = file_reads
    notice = repetition_notice(state.get("answers"))
    if notice:
        model_input["repetition_notice"] = notice
    with token_usage.usage_scope(
        _usage_project_root(state.get("project")),
        feature="architecture",
    ):
        result = call_model_json(config, ARCHITECTURE_SYSTEM_PROMPT, model_input)
    return validate_architecture_model_result(
        result,
        state.get("answers", {}),
        interview_plan=state.get("interview_plan"),
    )


def _inject_interview_context(model_input: dict[str, Any], state: dict[str, Any]) -> None:
    """Give the model its own coverage plan plus per-aspect budget feedback.

    The plan is the model's promise of what it will cover, so the UI progress
    and the model's actual questions finally speak the same vocabulary. Steering
    tells it which aspects are exhausted before it spends another question there.
    """
    plan = state.get("interview_plan")
    if not plan:
        return
    question_aspects = state.get("question_aspects")
    enriched = []
    for aspect in plan:
        count = aspect_question_count(
            plan, aspect["id"], state.get("answers", {}), question_aspects
        )
        enriched.append(
            {
                "id": aspect["id"],
                "title": aspect["title"],
                "asked": count,
                "remaining": max(0, ASPECT_MAX_QUESTIONS - count),
            }
        )
    model_input["interview_plan"] = enriched
    steering = aspect_steering(plan, state.get("answers", {}), question_aspects)
    if steering:
        model_input["aspect_steering"] = steering




def stream_architecture_model(
    state: dict[str, Any],
    latest_answer: dict[str, str],
    config: ProviderConfig,
    timeout: int = 180,
):
    """Call the Architecture Agent with `stream: true` and yield NDJSON events.

    Events: `reasoning` (real thinking tokens), `content` (raw JSON tokens), and a
    final `done` carrying the validated model result.
    """
    model_input = {
        "project": state.get("project"),
        "decisions": state.get("answers"),
        "current_architecture": state.get("architecture"),
        "latest_answer": latest_answer,
        "agent_turn": int(state.get("agent_turns", 0)) + 1,
    }
    _inject_interview_context(model_input, state)
    file_reads = state.get("file_reads")
    if isinstance(file_reads, list) and file_reads:
        model_input["file_reads"] = file_reads
    notice = repetition_notice(state.get("answers"))
    if notice:
        model_input["repetition_notice"] = notice
    def scoped_stream():
        with token_usage.usage_scope(
            _usage_project_root(state.get("project")),
            feature="architecture",
        ):
            yield from stream_json_model(
                config,
                ARCHITECTURE_SYSTEM_PROMPT,
                model_input,
                timeout=timeout,
                validate=lambda result: validate_architecture_model_result(
                    result,
                    state.get("answers", {}),
                    interview_plan=state.get("interview_plan"),
                ),
            )

    return scoped_stream()


def stream_architecture_edit_model(
    architecture: dict[str, Any],
    request: str,
    project: dict[str, Any] | None,
    config: ProviderConfig,
    timeout: int = 180,
):
    """Streaming variant of `call_architecture_edit_model`."""
    model_input = {
        "project": project or {},
        "current_architecture": architecture,
        "requested_change": request,
    }
    def scoped_stream():
        with token_usage.usage_scope(
            _usage_project_root(project),
            feature="architecture",
        ):
            yield from stream_json_model(
                config,
                ARCHITECTURE_EDIT_SYSTEM_PROMPT,
                model_input,
                timeout=timeout,
                validate=validate_architecture_edit_result,
            )

    return scoped_stream()


def apply_architecture_model_result(
    state: dict[str, Any],
    model_result: dict[str, Any],
    provider: ProviderConfig,
) -> None:
    capture_read_requests(state, model_result)
    state["architecture"] = model_result["architecture"]
    state["agent_mode"] = "ai"
    state["model_name"] = provider.model
    state["agent_turns"] = int(state.get("agent_turns", 0)) + 1
    state["model_notice"] = f"已由 {provider.model} 更新架构；API Key 未保存。"
    # Empty when the model exposed no reasoning; the UI then shows a local
    # summary explicitly marked as such instead of claiming it is thinking.
    state["thinking"] = model_result.get("thinking", "")
    state["mode_offer"] = model_result.get("mode_offer")
    plan = model_result.get("interview_plan")
    if plan is not None:
        state["interview_plan"] = plan
    # Record which aspect the freshly asked question belongs to, so the
    # per-aspect budget can be enforced across turns. A question without an
    # aspect_id (legacy model or a model that skipped the protocol) is simply
    # unclassified — the repetition notice still guards obvious loops.
    next_question = model_result.get("next_question")
    if next_question and isinstance(next_question, dict):
        aspect_id = str(next_question.get("aspect_id") or "").strip()
        question_id = str(next_question.get("id") or "").strip()
        if aspect_id and question_id:
            state.setdefault("question_aspects", {})[question_id] = aspect_id
    graph_quality_issues = model_result.get("graph_quality_issues")
    if isinstance(graph_quality_issues, list):
        state["graph_quality_issues"] = graph_quality_issues[:6]
        if graph_quality_issues:
            state["model_notice"] += (
                "；架构图质量提醒：" + graph_quality_issues[0]
            )
    graph_diagnostics = model_result.get("graph_diagnostics")
    if isinstance(graph_diagnostics, list):
        state["graph_diagnostics"] = graph_diagnostics[:6]
    if model_result["ready"]:
        state["status"] = "review"
        state["current_question"] = None
        state["progress"] = 95
    else:
        state["status"] = "interviewing"
        state["current_question"] = model_result["next_question"]


def refresh_progress(state: dict[str, Any]) -> None:
    status = state.get("status")
    if status == "initialized" or status == "ready":
        state["progress"] = 100
    elif status == "review":
        state["progress"] = 95
    else:
        answered = len(state.get("answers", {}))
        turns = int(state.get("agent_turns", 0))
        state["progress"] = min(90, max(5, round((answered + turns) / 10 * 100)))


def split_items(value: str) -> list[str]:
    """Split a free-text answer on newlines and CJK or ASCII list punctuation."""
    parts = re.split(r"[\n,，;；]+", value)
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]
