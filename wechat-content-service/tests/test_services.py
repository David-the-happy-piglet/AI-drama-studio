import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.models import AudienceProfile
from app.services.generator import audience_to_context
from app.services.humanizer import build_humanizer_prompt
from app.services.images import (
    ImageProviderError,
    MiniMaxImageProvider,
    MixedImageProvider,
    OpenverseImageProvider,
    WikimediaImageProvider,
    normalize_search_query,
)
from app.services.llm import (
    MockLLMProvider,
    build_llm_provider,
    parse_json_response,
    parse_token_usage,
)
from app.services.prompts import build_article_prompt
from app.services.renderer import render_wechat_html
from app.services.safety import scan_article
from app.services.wechat import WechatClient, WechatError


def sample_audience() -> AudienceProfile:
    return AudienceProfile(
        id=1,
        slug="middle-age-health",
        name="中年健康管理",
        age_range="35-55",
        description="工作和家庭压力较大的人群",
        interests_json=json.dumps(["睡眠", "职场压力"], ensure_ascii=False),
        writing_style="节奏明快，强调可执行性",
        title_preferences_json=json.dumps(
            ["人到中年……", "每天坐8小时的人……"],
            ensure_ascii=False,
        ),
        article_length=1000,
        image_style="现代都市生活中的中国中年人物",
        prohibited_topics_json=json.dumps(["建议停药"], ensure_ascii=False),
        enabled=True,
    )


def test_audience_context_is_dynamic():
    context = audience_to_context(sample_audience())
    assert context["name"] == "中年健康管理"
    assert context["age_range"] == "35-55"
    assert context["interests"] == ["睡眠", "职场压力"]


def test_prompt_contains_selected_audience():
    context = audience_to_context(sample_audience())
    prompt = build_article_prompt(
        topic="改善久坐带来的疲劳",
        category="职场健康",
        audience=context,
        title_count=5,
        image_count=3,
    )
    assert "中年健康管理" in prompt
    assert "35-55" in prompt
    assert "人到中年" in prompt
    assert "目标长度：约1000字" in prompt
    assert "不得返回数组、对象或其他类型" in prompt


def test_prompt_applies_headline_intensity():
    context = audience_to_context(sample_audience())
    prompt = build_article_prompt(
        topic="改善久坐带来的疲劳",
        category="职场健康",
        audience=context,
        title_count=5,
        image_count=3,
        headline_intensity=5,
    )
    assert "标题吸睛程度：5/5" in prompt
    assert "信息缺口" in prompt
    assert "不得编造数字" in prompt


def test_humanizer_prompt_preserves_structure_and_removes_ai_patterns():
    prompt = build_humanizer_prompt(
        {
            "title_candidates": ["标题一"],
            "selected_title": "标题一",
            "summary": "此外，这是一个至关重要的习惯。",
            "opening": "值得注意的是，睡眠十分重要。",
            "sections": [{"heading": "建议", "paragraphs": ["第一，早点睡。"]}],
            "conclusion": "总而言之，未来可期。",
            "image_prompts": [{"position": 0, "prompt": "图片"}],
        }
    )
    assert "Humanizer-zh" in prompt
    assert "title_candidates、selected_title 和 image_prompts 原样保留" in prompt
    assert "机械连接词" in prompt
    assert "不虚构个人经历" in prompt
    assert "目标至少 45/50" in prompt


def test_mock_generation_and_rendering():
    context = audience_to_context(sample_audience())
    result = MockLLMProvider().generate_article(
        "prompt",
        {
            "topic": "改善久坐带来的疲劳",
            "category": "职场健康",
            "audience": context,
            "title_count": 5,
            "image_count": 3,
        },
    )
    assert len(result["title_candidates"]) == 5
    assert len(result["image_prompts"]) == 3
    html = render_wechat_html(result, ["cover.png", "one.png", "two.png"])
    assert result["sections"][0]["heading"] in html
    assert "one.png" in html


def test_renderer_adds_web_image_attribution():
    html = render_wechat_html(
        {
            "opening": "开头",
            "sections": [{"heading": "小标题", "paragraphs": ["正文"]}],
            "conclusion": "结尾",
        },
        ["cover.png"],
        [
            {
                "source_kind": "web",
                "source_page_url": "https://commons.wikimedia.org/wiki/File:Tea.jpg",
                "source_author": "Example Author",
                "license_name": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            }
        ],
    )
    assert "图片来源与许可" in html
    assert "Example Author" in html
    assert "CC BY-SA 4.0" in html
    assert "人工智能辅助生成" not in html


def test_mock_headline_changes_with_intensity():
    context = audience_to_context(sample_audience())
    base_context = {
        "topic": "改善久坐带来的疲劳",
        "category": "职场健康",
        "audience": context,
        "title_count": 5,
        "image_count": 1,
    }
    calm = MockLLMProvider().generate_article(
        "prompt",
        {**base_context, "headline_intensity": 1},
    )
    strong = MockLLMProvider().generate_article(
        "prompt",
        {**base_context, "headline_intensity": 5},
    )
    assert calm["selected_title"] != strong["selected_title"]
    assert "提醒家里人" in strong["selected_title"]


def test_safety_scan_blocks_high_risk_claims():
    flags = scan_article("每天吃一片，不用吃药，也能彻底治愈。")
    assert "包含绝对治疗承诺" in flags
    assert "可能诱导停止或替代正规治疗" in flags


def test_parse_json_response_handles_reasoning_and_markdown():
    content = """<think>先规划文章结构</think>
```json
{"selected_title": "晚饭后的三个小习惯"}
```"""
    assert parse_json_response(content) == {"selected_title": "晚饭后的三个小习惯"}


def test_kimi_uses_required_temperature():
    provider = build_llm_provider(
        Settings(moonshot_api_key="test-key"),
        "kimi",
    )
    assert provider.temperature == 1


def test_token_usage_supports_openai_and_alias_fields():
    assert (
        parse_token_usage(
            {
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_tokens": 200,
                }
            }
        ).total_tokens
        == 200
    )
    aliased = parse_token_usage({"usage": {"input_tokens": 25, "output_tokens": 15}})
    assert aliased.prompt_tokens == 25
    assert aliased.completion_tokens == 15
    assert aliased.total_tokens == 40


def test_minimax_images_respect_disabled_aigc_watermark(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"image_base64": ["anBlZw=="]}}

    def fake_post(url, *, headers, json, timeout):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setattr("app.services.images.httpx.post", fake_post)
    provider = MiniMaxImageProvider(
        Settings(
            minimax_api_key="test-key",
            aigc_watermark=False,
            generated_assets_dir=str(tmp_path),
        )
    )
    generated = provider.generate("prompt", "title", 0)
    assert captured["aigc_watermark"] is False
    assert Path(str(generated["local_path"])).read_bytes() == b"jpeg"


def test_minimax_images_report_business_error(monkeypatch, tmp_path: Path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": None,
                "metadata": {"failed_count": 1},
                "base_resp": {
                    "status_code": 1008,
                    "status_msg": "insufficient balance",
                },
            }

    monkeypatch.setattr(
        "app.services.images.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    provider = MiniMaxImageProvider(
        Settings(
            minimax_api_key="test-key",
            generated_assets_dir=str(tmp_path),
        )
    )

    try:
        provider.generate("prompt", "title", 0)
    except ImageProviderError as exc:
        message = str(exc)
        assert "status_code=1008" in message
        assert "insufficient balance" in message
    else:
        raise AssertionError("Expected MiniMax business error")


def test_minimax_images_report_empty_data(monkeypatch, tmp_path: Path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": None,
                "metadata": {"failed_count": 1},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }

    monkeypatch.setattr(
        "app.services.images.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    provider = MiniMaxImageProvider(
        Settings(
            minimax_api_key="test-key",
            generated_assets_dir=str(tmp_path),
        )
    )

    try:
        provider.generate("prompt", "title", 0)
    except ImageProviderError as exc:
        message = str(exc)
        assert "returned no image data" in message
        assert "failed_count=1" in message
    else:
        raise AssertionError("Expected missing MiniMax image data error")


def test_wikimedia_provider_downloads_reusable_image(monkeypatch, tmp_path: Path):
    image_buffer = BytesIO()
    Image.new("RGB", (800, 600), "white").save(image_buffer, format="JPEG")
    image_bytes = image_buffer.getvalue()

    class FakeResponse:
        def __init__(self, *, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    search_payload = {
        "query": {
            "pages": [
                {
                    "imageinfo": [
                        {
                            "mime": "image/jpeg",
                            "thumburl": "https://upload.wikimedia.test/tea.jpg",
                            "descriptionurl": ("https://commons.wikimedia.org/wiki/File:Tea.jpg"),
                            "extmetadata": {
                                "Artist": {"value": "<b>Example Author</b>"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {
                                    "value": ("https://creativecommons.org/licenses/by-sa/4.0/")
                                },
                            },
                        }
                    ]
                }
            ]
        }
    }

    def fake_get(url, **kwargs):
        if "api.php" in url:
            return FakeResponse(payload=search_payload)
        return FakeResponse(content=image_bytes)

    monkeypatch.setattr("app.services.images.httpx.get", fake_get)
    provider = WikimediaImageProvider(
        Settings(
            generated_assets_dir=str(tmp_path),
            public_base_url="http://content.test",
        )
    )
    result = provider.generate("prompt", "tea", 1, "green tea cup")

    assert result["source_kind"] == "web"
    assert result["source_author"] == "Example Author"
    assert result["license_name"] == "CC BY-SA 4.0"
    assert Path(str(result["local_path"])).read_bytes() == image_bytes


def test_openverse_provider_preserves_source_and_license(monkeypatch, tmp_path: Path):
    image_buffer = BytesIO()
    Image.new("RGB", (800, 600), "white").save(image_buffer, format="JPEG")
    image_bytes = image_buffer.getvalue()

    class FakeResponse:
        def __init__(self, *, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    search_payload = {
        "results": [
            {
                "thumbnail": "https://api.openverse.test/image/thumb/",
                "foreign_landing_url": "https://example.test/source-image",
                "creator": "Example Photographer",
                "license": "by",
                "license_version": "4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
            }
        ]
    }

    def fake_get(url, **kwargs):
        if url.endswith("/images/"):
            return FakeResponse(payload=search_payload)
        return FakeResponse(content=image_bytes)

    monkeypatch.setattr("app.services.images.httpx.get", fake_get)
    provider = OpenverseImageProvider(
        Settings(
            generated_assets_dir=str(tmp_path),
            public_base_url="http://content.test",
        )
    )
    result = provider.generate(
        "prompt",
        "tea",
        1,
        ["green tea", "cup"],
    )

    assert result["source_kind"] == "web"
    assert result["source_author"] == "Example Photographer"
    assert result["license_name"] == "CC BY 4.0"
    assert result["source_page_url"] == "https://example.test/source-image"
    assert Path(str(result["local_path"])).read_bytes() == image_bytes


def test_search_query_normalization_handles_model_type_drift():
    assert normalize_search_query(" healthy family ", "fallback") == "healthy family"
    assert (
        normalize_search_query(
            ["healthy", " family ", "healthy", 2026, {"ignored": True}],
            "fallback",
        )
        == "healthy family 2026"
    )
    assert normalize_search_query({"unexpected": "object"}, "fallback") == "fallback"
    assert normalize_search_query([], "fallback") == "fallback"


def test_mixed_provider_uses_web_for_alternating_inline_images():
    class StubProvider:
        def __init__(self, name):
            self.name = name

        def generate(self, prompt, title, position, search_query=None):
            return {"source_kind": self.name}

    provider = MixedImageProvider(
        StubProvider("ai"),
        StubProvider("web"),
        fallback_to_ai=True,
    )
    assert provider.generate("p", "t", 0)["source_kind"] == "ai"
    assert provider.generate("p", "t", 1)["source_kind"] == "web"
    assert provider.generate("p", "t", 2)["source_kind"] == "ai"


def test_mixed_provider_falls_back_to_ai():
    class AiProvider:
        def generate(self, prompt, title, position, search_query=None):
            return {"source_kind": "ai"}

    class FailingWebProvider:
        def generate(self, prompt, title, position, search_query=None):
            raise ImageProviderError("no result")

    provider = MixedImageProvider(
        AiProvider(),
        FailingWebProvider(),
        fallback_to_ai=True,
    )
    assert provider.generate("p", "t", 1)["source_kind"] == "ai"


def test_mixed_provider_falls_back_to_web_when_ai_fails():
    class FailingAiProvider:
        def generate(self, prompt, title, position, search_query=None):
            raise ImageProviderError("no AI image")

    class WebProvider:
        def generate(self, prompt, title, position, search_query=None):
            return {"source_kind": "web"}

    provider = MixedImageProvider(
        FailingAiProvider(),
        WebProvider(),
        fallback_to_ai=True,
    )
    assert provider.generate("p", "t", 0)["source_kind"] == "web"


def test_wechat_draft_media_id_is_verified(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"news_item": [{"title": "已上传标题"}]}

    monkeypatch.setattr(
        "app.services.wechat.httpx.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    client = WechatClient(
        Settings(
            wechat_enabled=True,
            wechat_app_id="app-id",
            wechat_app_secret="secret",
        )
    )
    client._token = "cached-token"
    client._token_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    client.verify_draft("media-id", "已上传标题")

    try:
        client.verify_draft("media-id", "另一个标题")
    except WechatError as exc:
        assert "title mismatch" in str(exc)
    else:
        raise AssertionError("Expected title mismatch to fail verification")
