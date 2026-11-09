import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.agents.qa_agent.reviewer import ReviewReport, run_review
from app.agents.storyboard_agent.lint import lint_storyboard
from app.prompts.seven_section import compile_shot_prompt


def _shot(**overrides):
    base = {
        "order_index": 1,
        "duration": 5,
        "shot_type": "中景",
        "camera_angle": "低角度仰拍",
        "camera_movement": "缓慢推近",
        "visual_description": "林夏站在走廊尽头",
        "action": "林夏朝画面左边走去",
        "generation_mode": "text_to_video",
        "direction": {"motion_source": ["摄影机推近", "人物走动"]},
    }
    return base | overrides


def _rules(findings):
    return {f.rule for f in findings}


def test_clean_shot_has_no_warnings():
    findings = lint_storyboard([{"shots": [_shot()]}])
    assert [f for f in findings if f.severity != "info"] == []


def test_lint_flags_craft_and_model_weak_spots():
    shots = [
        _shot(order_index=1, camera_angle="eye_level", shot_type="特写"),
        _shot(order_index=2, camera_angle="eye_level", shot_type="特写"),
        _shot(
            order_index=3,
            camera_angle="平视",
            shot_type="close-up",
            camera_movement="static",
            action="",
            visual_description="她看着镜子里的倒影",
            direction={},
        ),
        _shot(
            order_index=4,
            dialogue="我早就知道你会回来，可是我从来没有想过你会用这种方式回来找我。",
            duration=5,
            action="所有人同时回头",
        ),
    ]
    findings = lint_storyboard([{"shots": shots}])
    rules = _rules(findings)
    assert {
        "frontal_overuse",
        "size_repetition",
        "dead_frame",
        "ai_mirror",
        "dialogue_overflow",
        "ai_simultaneous",
    } <= rules
    frontal = [f for f in findings if f.rule == "frontal_overuse"]
    assert [f.shot_index for f in frontal] == [3]


def test_negated_weak_spots_are_not_flagged():
    shot = _shot(generation_prompt="画面干净，没有文字、字幕、水印；不出现镜子。不生成文字或字幕。", visual_description="她看向招牌上写着的店名")
    rules = _rules(lint_storyboard([{"shots": [shot]}]))
    assert "ai_mirror" not in rules
    assert "ai_text" in rules  # the affirmative "招牌上写着" still counts


def test_medium_close_up_is_not_bucketed_as_close_up():
    shots = [
        _shot(order_index=1, shot_type="close-up"),
        _shot(order_index=2, shot_type="medium close-up"),
        _shot(order_index=3, shot_type="close-up"),
    ]
    assert "size_repetition" not in _rules(lint_storyboard([{"shots": shots}]))


def test_first_last_frame_requires_last_frame_description():
    findings = lint_storyboard(
        [{"shots": [_shot(generation_mode="first_last_frame", direction={"motion_source": ["x"], "first_frame": "a"})]}]
    )
    assert "last_frame_missing" in _rules(findings)


def test_seven_section_prompt_structure():
    compiled = compile_shot_prompt(
        _shot(
            generation_mode="first_last_frame",
            dialogue="终于找到你了。",
            continuity_requirements=["左手一直戴着红绳"],
            direction={
                "motion_source": ["人物走近"],
                "beats": [{"start": 0.2, "end": 3, "text": "林夏从画面右侧走到 62% 处停下"}],
            },
        ),
        scene={"location_description": "老旧公寓走廊", "time_of_day": "夜"},
        characters=[
            {
                "name": "林夏",
                "apparent_age": "26岁",
                "gender": "女",
                "distinctive_features": ["左眼下方小痣"],
                "outfit": {"description": "米色风衣", "components": ["风衣", "白衬衫"]},
            }
        ],
        style="干净的现代 2D 手绘动画，平涂色块",
        palette="冷蓝墙面，暖黄台灯为强调色",
    )
    assert list(compiled.sections) == ["画风", "镜头", "构图与配色", "人物", "逐秒", "声音", "约束"]
    assert compiled.prompt.startswith("【画风】干净的现代 2D 手绘动画")
    assert "首帧和尾帧完全一致" in compiled.sections["画风"]
    assert "0–0.2 秒：画面完全静止" in compiled.sections["逐秒"]
    assert "0.2–3 秒：林夏从画面右侧" in compiled.sections["逐秒"]
    assert "画面与尾帧完全一致" in compiled.sections["逐秒"]
    assert "对白：终于找到你了" in compiled.sections["声音"]
    assert "左眼下方小痣" in compiled.sections["人物"]
    assert "这 1 个人" in compiled.sections["约束"]
    assert "左手一直戴着红绳" in compiled.sections["约束"]
    assert compiled.notes == []


def test_voiceover_keeps_everyone_on_screen_silent():
    compiled = compile_shot_prompt(
        _shot(dialogue="对白（画外音）：C01：曾经我们也很好。"),
        scene={"location_description": "车内·海岸公路｜日", "time_of_day": "日"},
    )
    assert "画外声音" in compiled.sections["声音"] and "不对口型" in compiled.sections["声音"]
    assert "只有说这句台词的人" not in compiled.sections["声音"]
    assert "日，日" not in compiled.sections["构图与配色"]
    assert "这这" not in compiled.sections["约束"] and "不出现人物" in compiled.sections["约束"]
    assert "画面和摄影机都停住" in compiled.sections["逐秒"]


def test_static_shot_without_dialogue_compiles_silent_and_locked():
    compiled = compile_shot_prompt(_shot(camera_movement="static", direction={}))
    assert "不推不拉不摇" in compiled.sections["镜头"]
    assert "不说话" in compiled.sections["声音"]
    assert "没有人物" in compiled.sections["人物"]
    assert compiled.notes  # missing style / palette / beats are reported


def _fake_report(verdict="pass", issues=None):
    return ReviewReport(
        understood_story="她把文件放到桌上",
        issues=issues or [],
        verdict=verdict,
    )


def test_hard_defect_forces_fail(monkeypatch):
    monkeypatch.setenv("REVIEW_API_URL", "https://review.example.invalid/v1/chat/completions")
    monkeypatch.setenv("REVIEW_API_KEY", "k")
    monkeypatch.setenv("REVIEW_MODEL", "vision-model")
    body = {
        "understood_story": "x",
        "issues": [{"problem": "杯子大小不一致", "severity": "硬伤", "certainty": "确定"}],
        "verdict": "pass",
    }
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]},
        )

    report, model = asyncio.run(
        run_review("story", "剧本正文", transport=httpx.MockTransport(handler))
    )
    assert report.verdict == "fail"
    assert model == "vision-model"
    # Cold reader: only a system prompt and the artifact, no history.
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert seen["body"]["messages"][1]["content"] == "剧本正文"


def test_compiled_prompt_api_and_apply(client, shot):
    client.put(
        "/api/v1/projects/PRJ_REVENGE_001/director-settings",
        json={"visual_style": "电影写实，冷色调", "palette": "深蓝与暖橙"},
    )
    read = client.get(f"/api/v1/shots/{shot['id']}/compiled-prompt")
    assert read.status_code == 200, read.text
    data = read.json()
    assert data["prompt"].startswith("【画风】电影写实，冷色调")
    assert "Alice" in data["sections"]["人物"]
    assert "charcoal blazer" in data["sections"]["人物"]
    applied = client.post(
        f"/api/v1/shots/{shot['id']}/compiled-prompt/apply",
        json={"expected_version": shot["version"]},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["generation_prompt"] == data["prompt"]
    assert applied.json()["version"] == shot["version"] + 1


def test_episode_lint_api(client, shot):
    response = client.get("/api/v1/episodes/EP001/storyboard-lint")
    assert response.status_code == 200, response.text
    rules = {f["rule"] for f in response.json()["findings"]}
    assert "motion_missing" in rules and "dead_frame" not in rules


def _submit(client, shot, **extra):
    return client.post(
        f"/api/v1/shots/{shot['id']}/generations",
        json={"request_token": str(uuid4()), "confirmed": True, "provider": "mock", **extra},
    )


def test_review_gate_required_blocks_until_pass(client, shot, monkeypatch):
    monkeypatch.setenv("REVIEW_GATE", "required")
    verdicts = iter(["fail", "pass"])

    async def fake_review(stage, material, images=None, **kwargs):
        assert stage == "shot_pre_submit"
        assert "【提示词】" in material
        return _fake_report(next(verdicts)), "fake-reviewer"

    monkeypatch.setattr("app.services.director.run_review", fake_review)

    blocked = _submit(client, shot)
    assert blocked.status_code == 422
    assert "提交前审核未完成" in blocked.json()["detail"]

    failed = client.post(f"/api/v1/shots/{shot['id']}/reviews")
    assert failed.status_code == 200, failed.text
    assert failed.json()["verdict"] == "fail"
    assert "未通过" in _submit(client, shot).json()["detail"]

    passed = client.post(f"/api/v1/shots/{shot['id']}/reviews").json()
    created = _submit(client, shot)
    assert created.status_code == 201, created.text
    snapshot = created.json()["request_snapshot"]["metadata"]["pre_submit_review"]
    assert snapshot == {"mode": "required", "review_id": passed["id"], "verdict": "pass"}

    # Editing the shot invalidates the old pass.
    edited = client.patch(
        f"/api/v1/shots/{shot['id']}",
        json={"expected_version": shot["version"], "generation_prompt": "新的提示词"},
    )
    assert edited.status_code == 200
    assert _submit(client, shot).status_code == 422

    overridden = _submit(client, shot, review_override_reason="导演确认可以直接拍")
    assert overridden.status_code == 201
    meta = overridden.json()["request_snapshot"]["metadata"]["pre_submit_review"]
    assert meta["override_reason"] == "导演确认可以直接拍"
    assert [r["verdict"] for r in client.get("/api/v1/reviews", params={"target_id": shot["id"]}).json()] == ["pass", "fail"]


def test_review_gate_warn_records_missing_review(client, shot):
    created = _submit(client, shot)
    assert created.status_code == 201
    meta = created.json()["request_snapshot"]["metadata"]["pre_submit_review"]
    assert meta == {"mode": "warn", "verdict": "missing"}


def test_script_story_review_sends_only_screenplay(client, project, monkeypatch):
    captured = {}

    async def fake_review(stage, material, images=None, **kwargs):
        captured["stage"], captured["material"] = stage, material
        return _fake_report(), "fake"

    monkeypatch.setattr("app.services.director.run_review", fake_review)
    draft = {
        "screenplay": "林夏在雨夜收到一封信。",
        "scenes": [{"location": "公寓", "shots": [_shot()]}],
    }
    script = client.post(
        f"/api/v1/projects/{project['id']}/external-storyboards",
        json={"title": "雨夜来信", "source_idea": "一封信", "draft": draft, "request_token": str(uuid4())},
    )
    assert script.status_code == 200, script.text
    story = client.post(f"/api/v1/scripts/{script.json()['id']}/reviews", json={"stage": "story"})
    assert story.status_code == 200, story.text
    assert captured == {"stage": "story", "material": "林夏在雨夜收到一封信。"}

    board = client.post(f"/api/v1/scripts/{script.json()['id']}/reviews", json={"stage": "storyboard"})
    assert board.status_code == 200
    assert captured["stage"] == "storyboard"
    assert "第1场-第1镜" in captured["material"]
    assert "林夏在雨夜收到一封信" not in captured["material"]

    lint = client.get(f"/api/v1/scripts/{script.json()['id']}/storyboard-lint")
    assert lint.status_code == 200


@pytest.mark.parametrize("stage", ["shot_pre_submit"])
def test_unconfigured_reviewer_is_explicit(monkeypatch, stage):
    for name in ("REVIEW_API_URL", "REVIEW_API_KEY", "REVIEW_MODEL", "PLANNING_API_URL", "PLANNING_API_KEY", "PLANNING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="审核模型未配置"):
        asyncio.run(run_review(stage, "x"))


def test_review_gate_status_tracks_the_prompt_being_sent(client, shot, monkeypatch):
    async def fake_review(stage, material, images=None, **kwargs):
        return _fake_report("pass"), "fake"

    monkeypatch.setattr("app.services.director.run_review", fake_review)
    edited = "【画风】本次手改的提示词"
    reviewed = client.post(f"/api/v1/shots/{shot['id']}/reviews", json={"prompt": edited})
    assert reviewed.status_code == 200, reviewed.text
    gate = client.post(f"/api/v1/shots/{shot['id']}/review-gate", json={"prompt": edited}).json()
    assert gate["verdict"] == "pass" and gate["report"]["verdict"] == "pass"
    other = client.post(f"/api/v1/shots/{shot['id']}/review-gate", json={"prompt": "别的"}).json()
    assert other["verdict"] == "missing"
    monkeypatch.setenv("REVIEW_GATE", "required")
    assert _submit(client, shot, prompt=edited).status_code == 201


def test_loose_draft_lint_accepts_incomplete_shots(client):
    response = client.post(
        "/api/v1/storyboard-lint",
        json={"scenes": [{"shots": [{"duration": 5, "shot_type": "", "visual_description": ""}]}]},
    )
    assert response.status_code == 200, response.text
