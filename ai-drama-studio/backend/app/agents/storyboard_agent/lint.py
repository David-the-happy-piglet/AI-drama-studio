"""Deterministic storyboard checks that run before any image or video spend.

The rules encode known weak spots of current video models and basic shot-design
craft (angle variety, something always moving, staggered reactions). They only
report findings; they never edit the storyboard. Text matching is heuristic, so
every finding is phrased as a prompt for a human or reviewer to look again.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

ERROR = "error"
WARNING = "warning"
INFO = "info"


class LintFinding(BaseModel):
    ref: str
    scene_index: int
    shot_index: int
    shot_id: str | None = None
    rule: str
    severity: str
    message: str
    suggestion: str = ""


# Shot-size buckets: consecutive shots in the same bucket read as visually flat.
_SIZE_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    # Order matters: more specific phrases first ("medium close-up" before "close-up").
    ("extreme_close", ("大特写", "extreme close", "ecu")),
    ("medium_close", ("近景", "medium close", "mcu")),
    ("close", ("特写", "close-up", "close up", "closeup", "cu")),
    ("medium", ("中景", "半身", "medium", "ms")),
    ("establishing", ("远景", "establishing", "extreme wide", "ews")),
    ("full", ("全景", "全身", "full", "wide", "ws")),
]

_EYE_LEVEL = ("eye_level", "eye level", "平视", "正面")
_NON_FRONTAL = ("过肩", "背后", "背影", "侧面", "侧拍", "透过", "over the shoulder", "ots", "profile", "from behind")
_TOP_DOWN = ("俯拍", "俯视", "顶拍", "top", "bird", "overhead", "high angle")
_STATIC_CAMERA = ("static", "固定", "不动", "locked", "fixed")

_WEAKNESS_RULES: list[tuple[str, str, re.Pattern[str], str, str]] = [
    (
        "ai_mirror",
        WARNING,
        re.compile(r"镜子|镜中|倒影|反光|mirror|reflection"),
        "镜子和倒影是视频模型的高风险画面",
        "改成不出现镜面，或用剪辑表达",
    ),
    (
        "ai_text",
        WARNING,
        re.compile(r"文字|字幕|招牌上写|写着|屏幕上显示|标语|手写|信上的字|text on"),
        "画面中的文字通常会被模型画错",
        "文字放到后期叠加，生成画面里不出现可读文字",
    ),
    (
        "ai_crossing",
        WARNING,
        re.compile(r"交叉|擦肩|穿过人群|交错|crossing"),
        "人物交叉走位容易导致脸和身体互换",
        "让人物各自在画面一侧移动，不交错",
    ),
    (
        "ai_light_change",
        WARNING,
        re.compile(r"灯灭|灯亮|关灯|开灯|闪电|突然变暗|突然亮|光线变化|天色变"),
        "一个镜头内换光会让颜色和人物漂移",
        "换光放到切镜处；闪电写成“云里闪一下白光”",
    ),
    (
        "ai_small_prop",
        INFO,
        re.compile(r"拿起|握着|捏着|递给|接过|翻开|按下|戒指|钥匙|硬币|手机屏"),
        "手拿小物件容易变形或凭空出现",
        "首帧就画出这个道具；动作写清手在哪、怎么拿",
    ),
    (
        "ai_simultaneous",
        WARNING,
        re.compile(r"同时|齐声|一起(?:举|站|回头|转身|鼓掌|看)|所有人|众人|大家都"),
        "群体同步动作一看就是模型生成的",
        "写清先后：谁第一个、过一两秒谁、最后谁",
    ),
    (
        "ai_negative_phrasing",
        INFO,
        re.compile(r"不要|不能|禁止|没有.{0,6}(?:出现|动)"),
        "否定写法经常无效",
        "尽量改成正面描述，例如“画面里一直只有这几个人”",
    ),
]

_CHARS_PER_SECOND = 5.0
_ACTION_CHAIN = re.compile(r"然后|接着|随后|之后|再|并且|同时")
_ACTIVE_MOTION = re.compile(r"走|跑|进入|进画|出画|离开|转身|靠近|推门|飘|落|经过|起身|坐下")


_CLAUSE_BREAK = re.compile(r"[，。；！？!?;\n]")
_NEGATION = re.compile(r"不|没有|无|禁止|避免|去掉|拒绝")


def _affirmative_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match not negated earlier in its clause.

    "不生成文字或字幕" negates both words; the clause is the scope, so a mention in
    a negated list is not treated as on-screen text.
    """

    for match in pattern.finditer(text):
        clause_start = 0
        for brk in _CLAUSE_BREAK.finditer(text, 0, match.start()):
            clause_start = brk.end()
        if not _NEGATION.search(text, clause_start, match.start()):
            return match
    return None


def _text(shot: Mapping[str, Any], *keys: str) -> str:
    return " ".join(str(shot.get(key) or "") for key in keys)


def _bucket(shot_type: str) -> str | None:
    lowered = shot_type.lower().strip()
    for name, words in _SIZE_BUCKETS:
        for word in words:
            if word.isascii():
                if re.search(rf"\b{re.escape(word)}\b", lowered):
                    return name
            elif word in lowered:
                return name
    return None


def _contains(value: str, words: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in words)


def _direction(shot: Mapping[str, Any]) -> dict[str, Any]:
    value = shot.get("direction") or {}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return dict(value)


def lint_storyboard(scenes: list[Mapping[str, Any]]) -> list[LintFinding]:
    """Lint ordered scenes, each a mapping with an ordered ``shots`` list.

    Shots are mappings using ``ShotCreate`` field names, so the same rules work on
    unpublished drafts and on persisted storyboards.
    """

    findings: list[LintFinding] = []
    flat: list[tuple[int, int, Mapping[str, Any]]] = []
    for scene_index, scene in enumerate(scenes, 1):
        shots = sorted(scene.get("shots") or [], key=lambda s: s.get("order_index", 0))
        for shot_index, shot in enumerate(shots, 1):
            flat.append((scene_index, shot_index, shot))

    def add(scene_index, shot_index, shot, rule, severity, message, suggestion=""):
        findings.append(
            LintFinding(
                ref=f"第{scene_index}场-第{shot_index}镜",
                scene_index=scene_index,
                shot_index=shot_index,
                shot_id=shot.get("id"),
                rule=rule,
                severity=severity,
                message=message,
                suggestion=suggestion,
            )
        )

    frontal: list[tuple[int, int, Mapping[str, Any]]] = []
    previous_bucket: str | None = None
    run: list[tuple[int, int, Mapping[str, Any]]] = []

    for scene_index, shot_index, shot in flat:
        direction = _direction(shot)
        body = _text(shot, "visual_description", "action", "composition", "generation_prompt")
        angle = str(shot.get("camera_angle") or "")
        movement = str(shot.get("camera_movement") or "")
        placement = f"{angle} {shot.get('composition') or ''}"

        # Angle variety: count plain frontal eye-level shots across the episode.
        if _contains(angle, _EYE_LEVEL) and not _contains(placement, _NON_FRONTAL):
            frontal.append((scene_index, shot_index, shot))

        # Shot-size variety: three in a row at the same size reads flat.
        bucket = _bucket(str(shot.get("shot_type") or ""))
        if bucket and bucket == previous_bucket:
            run.append((scene_index, shot_index, shot))
        else:
            run = [(scene_index, shot_index, shot)]
        previous_bucket = bucket
        if len(run) == 3:
            add(
                scene_index,
                shot_index,
                shot,
                "size_repetition",
                WARNING,
                f"连续 3 个镜头都是“{shot.get('shot_type')}”同一景别",
                "全景、半身、特写交替使用",
            )

        # Something must move in every shot.
        motion = [item for item in direction.get("motion_source") or [] if str(item).strip()]
        is_static = not movement.strip() or _contains(movement, _STATIC_CAMERA)
        has_subject_motion = bool(direction.get("frame_entry_exit")) or bool(
            _ACTIVE_MOTION.search(_text(shot, "action"))
        )
        if not motion and is_static and not has_subject_motion:
            add(
                scene_index,
                shot_index,
                shot,
                "dead_frame",
                WARNING,
                "固定机位、人物不走动，也没写什么在动，画面容易像静态图",
                "让人从远处走近、进出画，或让背景有路人/飘落物，并填写运动来源",
            )
        elif not motion:
            add(
                scene_index,
                shot_index,
                shot,
                "motion_missing",
                WARNING,
                "没有写清这个镜头里是什么在动",
                "至少写一项：摄影机、焦点、光线、前景飘过的东西或人物小动作",
            )

        # Known weak spots of video models.
        for rule, severity, pattern, message, suggestion in _WEAKNESS_RULES:
            match = _affirmative_match(pattern, body) if rule != "ai_negative_phrasing" else pattern.search(body)
            if match:
                add(
                    scene_index,
                    shot_index,
                    shot,
                    rule,
                    severity,
                    f"{message}（命中“{match.group(0)}”）",
                    suggestion,
                )

        characters = shot.get("characters") or []
        if len(characters) > 3:
            add(
                scene_index,
                shot_index,
                shot,
                "ai_crowd",
                WARNING,
                f"同一镜头有 {len(characters)} 个绑定角色，配角越多越容易出错",
                "拆镜，或把次要人物放到虚焦背景",
            )
        if characters and _contains(angle, _TOP_DOWN):
            add(
                scene_index,
                shot_index,
                shot,
                "ai_top_down_face",
                INFO,
                "俯拍有人物的镜头容易看不到脸、身份漂移",
                "确认这个镜头不需要认脸，或改为斜侧俯拍",
            )
        if len(_ACTION_CHAIN.findall(_text(shot, "action"))) >= 2:
            add(
                scene_index,
                shot_index,
                shot,
                "ai_action_chain",
                WARNING,
                "一个镜头里串了三个以上动作",
                "每镜只保留一两个主要动作，其余拆镜",
            )

        # Dialogue must fit the generated duration (about 5 Chinese chars/second,
        # plus 0.5 s of slack).
        dialogue = re.sub(r"\s|[，。！？、：；“”‘’…—（）()]", "", str(shot.get("dialogue") or ""))
        duration = float(shot.get("duration") or 0)
        if dialogue and duration and len(dialogue) / _CHARS_PER_SECOND > duration - 0.5:
            add(
                scene_index,
                shot_index,
                shot,
                "dialogue_overflow",
                WARNING,
                f"台词 {len(dialogue)} 字约需 {len(dialogue) / _CHARS_PER_SECOND:.1f} 秒，镜头只有 {duration:g} 秒",
                "加长镜头并留 0.5 秒余量，或拆镜，不删台词",
            )

        mode = str(shot.get("generation_mode") or "")
        if mode in {"image_to_video", "first_last_frame"} and not direction.get("first_frame"):
            add(
                scene_index,
                shot_index,
                shot,
                "first_frame_missing",
                INFO,
                "首帧模式但没写首帧画什么",
                "写出动作开始那一刻的单幅静态画面，动作要用的道具必须在首帧里",
            )
        if mode == "first_last_frame" and not direction.get("last_frame"):
            add(
                scene_index,
                shot_index,
                shot,
                "last_frame_missing",
                WARNING,
                "首尾帧模式但没写尾帧画什么",
                "写出动作结束时的画面；推近镜头的尾帧可以是首帧的局部放大",
            )

    if len(frontal) > 2:
        for scene_index, shot_index, shot in frontal[2:]:
            add(
                scene_index,
                shot_index,
                shot,
                "frontal_overuse",
                WARNING,
                f"全片平视正面已有 {len(frontal)} 镜，建议最多 2 镜",
                "改用俯拍、仰拍、背后、过肩或透过前景拍",
            )

    if flat:
        scene_index, shot_index, shot = flat[0]
        if not _direction(shot).get("motion_source") and not shot.get("dialogue"):
            add(
                scene_index,
                shot_index,
                shot,
                "weak_opening",
                INFO,
                "开场镜头没有动作也没有台词，观众可能划走",
                "把最抓眼球的画面放到前 3 秒",
            )

    order = {ERROR: 0, WARNING: 1, INFO: 2}
    findings.sort(key=lambda f: (f.scene_index, f.shot_index, order.get(f.severity, 3)))
    return findings


def lint_summary(findings: list[LintFinding]) -> dict[str, int]:
    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for finding in findings:
        summary[finding.severity] = summary.get(finding.severity, 0) + 1
    return summary
