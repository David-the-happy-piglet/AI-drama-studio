"""Seven-section video prompt compiler.

Turns a storyboard shot plus its character/outfit/location records into a fixed
structure: style, camera, composition & palette, characters, per-second beats,
sound, constraints. Everything is written explicitly so the video model has no
room to invent people, props, directions or lighting.

The compiler is pure: callers load records and pass plain mappings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

DEFAULT_STYLE = "电影写实，自然光影，真实质感"

_STATIC_WORDS = ("static", "固定", "不动", "locked", "fixed")
_OFFSCREEN_VOICE = ("画外音", "旁白", "电话", "画外", "内心独白", "V.O", "O.S", "OS：", "VO：")


class CompiledPrompt(BaseModel):
    prompt: str
    sections: dict[str, str]
    notes: list[str]


def _join(*parts: Any, sep: str = "，") -> str:
    return sep.join(str(p).strip().rstrip("。，") for p in parts if p and str(p).strip())


def _fmt(seconds: float) -> str:
    return f"{seconds:.1f}".rstrip("0").rstrip(".")


def _character_line(character: Mapping[str, Any]) -> str:
    features = character.get("distinctive_features") or []
    feature_text = "；".join(str(f) for f in features if str(f).strip())
    looks = _join(
        _join(character.get("apparent_age"), character.get("gender"), sep=""),
        character.get("face_description"),
        _join(character.get("hair_color"), character.get("hairstyle"), sep=""),
        character.get("body_type"),
        f"固定特征：{feature_text}（位置全程不变）" if feature_text else "",
    )
    outfit = character.get("outfit") or {}
    outfit_text = ""
    if outfit:
        components = "、".join(str(c) for c in outfit.get("components") or [] if str(c).strip())
        palette = "、".join(str(c) for c in outfit.get("color_palette") or [] if str(c).strip())
        outfit_text = _join(
            outfit.get("description") or outfit.get("name"),
            f"由{components}组成" if components else "",
            f"配色{palette}" if palette else "",
        )
    line = f"{character.get('name') or character.get('id')}：{looks or '外貌按参考图'}"
    if outfit_text:
        line += f"；服装：{outfit_text}"
    return line + "。"


def compile_shot_prompt(
    shot: Mapping[str, Any],
    *,
    scene: Mapping[str, Any] | None = None,
    characters: list[Mapping[str, Any]] | None = None,
    style: str | None = None,
    palette: str | None = None,
) -> CompiledPrompt:
    scene = scene or {}
    characters = characters or []
    direction = dict(shot.get("direction") or {})
    notes: list[str] = []
    duration = float(shot.get("duration") or 5)
    mode = str(shot.get("generation_mode") or "")
    has_last = mode == "first_last_frame"
    frame_ref = "首帧和尾帧" if has_last else "首帧"

    style_text = (style or "").strip() or DEFAULT_STYLE
    if not style:
        notes.append("项目未设置画风（settings_json.visual_style），使用默认画风")
    sections: dict[str, str] = {}
    sections["画风"] = f"{style_text.rstrip('。')}。整段的画风、颜色、人物设计与{frame_ref}完全一致。"

    movement = str(shot.get("camera_movement") or "").strip()
    if not movement or any(word in movement.lower() for word in _STATIC_WORDS):
        movement = "不推不拉不摇，不切镜"
    sections["镜头"] = (
        f"整段 {_fmt(duration)} 秒，"
        + _join(shot.get("shot_type"), shot.get("camera_angle"), movement)
        + "，一镜到底。"
    )

    place = str(scene.get("location_description") or "")
    time_of_day = str(scene.get("time_of_day") or "")
    location = _join(place, "" if time_of_day and time_of_day in place else time_of_day)
    sections["构图与配色"] = (
        _join(
            shot.get("composition"),
            f"地点：{location}" if location else "",
            f"配色：{palette}" if palette else "",
            "人物的头或眼睛落在画面左右约 38% 或 62% 的位置" if characters else "",
        )
        + f"。与{frame_ref}对得上。"
    )
    if not palette:
        notes.append("未设置配色（settings_json.palette），构图与配色段缺少色表")

    if characters:
        sections["人物"] = "\n".join(_character_line(c) for c in characters)
    else:
        sections["人物"] = "画面中只有环境和物件，没有人物。"

    beats = direction.get("beats") or []
    lines = ["0–0.2 秒：画面完全静止，和首帧一模一样。"]
    if beats:
        for beat in beats:
            lines.append(f"{_fmt(float(beat['start']))}–{_fmt(float(beat['end']))} 秒：{beat['text'].rstrip('。')}。")
    else:
        middle_end = max(duration - 0.3, 0.5)
        body = _join(
            shot.get("action") or shot.get("visual_description"),
            direction.get("frame_entry_exit"),
            direction.get("screen_direction"),
            f"表情：{direction['expression_beat']}" if direction.get("expression_beat") else shot.get("emotion"),
        )
        lines.append(f"0.2–{_fmt(middle_end)} 秒：{body}。")
        notes.append("未写逐秒节拍（direction.beats），逐秒段由动作描述合成")
    if has_last:
        lines.append("最后 0.3 秒：所有人完全停住，画面与尾帧完全一致。")
    elif direction.get("last_frame"):
        lines.append(f"最后 0.3 秒：动作停稳，停在：{direction['last_frame'].rstrip('。')}。")
    else:
        lines.append(
            "最后 0.3 秒：动作停稳，人物和摄影机都停住。"
            if characters
            else "最后 0.3 秒：画面和摄影机都停住。"
        )
    sections["逐秒"] = "\n".join(lines)

    motion = [str(m) for m in direction.get("motion_source") or [] if str(m).strip()]
    if motion:
        sections["逐秒"] += f"\n本镜运动来源：{'、'.join(motion)}。"

    dialogue = str(shot.get("dialogue") or "").strip()
    if dialogue and any(mark in dialogue for mark in _OFFSCREEN_VOICE):
        sections["声音"] = (
            f"画外声音：{dialogue}。声音来自画面外，画面里所有人嘴唇闭合，不对口型。"
            "另外只有画面里看得见来源的环境声。"
        )
    elif dialogue:
        sections["声音"] = (
            f"对白：{dialogue}。只有说这句台词的人嘴在动，其他人嘴唇闭合。"
            "另外只有画面里看得见来源的环境声，整段只有人声和环境声。"
        )
    else:
        sections["声音"] = "只有画面里看得见来源的环境声。人物嘴唇闭合，不说话。整段只有环境声。"

    count = len(characters)
    keep = (
        f"画面里一直只有首帧中的这 {count} 个人和这些东西，脸和身形全程不变，一镜到底"
        if count
        else "画面里一直只有首帧中的这些景物，不出现人物，一镜到底"
    )
    constraints = [
        keep,
        "画面干干净净，没有文字、字幕、水印",
    ]
    constraints += [str(c) for c in shot.get("continuity_requirements") or [] if str(c).strip()]
    sections["约束"] = "；".join(c.rstrip("。；") for c in constraints) + "。"

    prompt = "\n".join(f"【{name}】{text}" for name, text in sections.items())
    return CompiledPrompt(prompt=prompt, sections=sections, notes=notes)
