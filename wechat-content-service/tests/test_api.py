from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.services import generator as generator_service
from app.services.automation import process_next_run
from app.services.llm import MockLLMProvider, TokenUsage


def test_generation_to_mock_publish_flow():
    with TestClient(app) as client:
        audiences = client.get("/api/audiences")
        assert audiences.status_code == 200
        assert {item["slug"] for item in audiences.json()} == {
            "senior-wellness",
            "middle-age-health",
        }

        response = client.post(
            "/api/articles/generate",
            json={
                "topic": "人到中年如何改善睡眠",
                "audience_slug": "middle-age-health",
                "category": "睡眠健康",
                "title_count": 5,
                "image_count": 3,
            },
        )
        assert response.status_code == 201
        article = response.json()
        assert article["status"] == "generated"
        assert len(article["title_candidates"]) == 5
        assert len(article["assets"]) == 3
        assert "过了35岁" in article["selected_title"]

        preview = client.get(f"/admin/articles/{article['id']}")
        assert preview.status_code == 200
        assert "一键上传到公众号草稿箱" in preview.text

        draft = client.post(
            f"/api/articles/{article['id']}/wechat-draft"
        )
        assert draft.status_code == 200
        assert draft.json()["media_id"].startswith("mock-draft-")
        preview = client.get(f"/admin/articles/{article['id']}")
        assert "重新核验微信草稿" in preview.text

        publish = client.post(f"/api/articles/{article['id']}/publish")
        assert publish.status_code == 200
        assert publish.json()["publish_id"].startswith("mock-publish-")

        metric = client.post(
            f"/api/articles/{article['id']}/metrics",
            json={
                "metric_date": "2026-07-25",
                "read_count": 1200,
                "read_users": 980,
                "share_count": 75,
                "share_users": 61,
            },
        )
        assert metric.status_code == 201
        assert metric.json()["read_count"] == 1200


def test_provider_status_and_mock_comparison():
    with TestClient(app) as client:
        providers = client.get("/api/providers")
        assert providers.status_code == 200
        assert {
            item["provider"] for item in providers.json()["text"]
        } == {"deepseek", "minimax", "kimi"}
        assert all("api_key" not in item for item in providers.json()["text"])

        response = client.post(
            "/api/articles/compare",
            json={
                "topic": "晚饭后如何安排散步和休息",
                "providers": ["mock"],
                "image_count": 1,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["errors"] == []
        assert len(result["articles"]) == 1
        assert result["articles"][0]["llm_provider"] == "mock"

        usage = client.get("/api/token-usage")
        assert usage.status_code == 200
        assert "models" in usage.json()
        assert "totals" in usage.json()


def test_headline_intensity_validation_and_admin_control():
    with TestClient(app) as client:
        admin = client.get("/admin")
        assert admin.status_code == 200
        assert 'name="headline_intensity"' in admin.text
        assert "4 — 强吸引（推荐）" in admin.text
        assert 'name="humanize"' in admin.text
        assert "Humanizer-zh 二次润色" in admin.text

        invalid = client.post(
            "/api/articles/generate",
            json={
                "topic": "晚饭后如何合理活动",
                "provider": "mock",
                "headline_intensity": 6,
            },
        )
        assert invalid.status_code == 422


def test_token_usage_is_persisted_and_aggregated(monkeypatch):
    class MeteredMockProvider(MockLLMProvider):
        def generate_article(self, prompt, context):
            result = super().generate_article(prompt, context)
            self.usage = TokenUsage(
                prompt_tokens=120,
                completion_tokens=80,
                total_tokens=200,
            )
            return result

    monkeypatch.setattr(
        generator_service,
        "build_llm_provider",
        lambda settings, provider_name=None: MeteredMockProvider(),
    )
    with TestClient(app) as client:
        generated = client.post(
            "/api/articles/generate",
            json={
                "topic": "晚饭后如何合理活动",
                "provider": "mock",
                "image_count": 1,
            },
        )
        assert generated.status_code == 201

        usage = client.get("/api/token-usage").json()
        mock = next(
            item for item in usage["models"] if item["provider"] == "mock"
        )
        assert mock["metered_requests"] >= 1
        assert mock["prompt_tokens"] >= 120
        assert mock["completion_tokens"] >= 80
        assert mock["total_tokens"] >= 200


def test_humanizer_runs_as_second_metered_model_call(monkeypatch):
    class HumanizingProvider(MockLLMProvider):
        name = "test-humanizer"
        model = "test-model"

        def generate_article(self, prompt, context):
            result = super().generate_article(prompt, context)
            self.usage = TokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
            if "Humanizer-zh" in prompt:
                result["opening"] = "吃过晚饭，先别急着往沙发上一躺。"
            return result

    monkeypatch.setattr(
        generator_service,
        "build_llm_provider",
        lambda settings, provider_name=None: HumanizingProvider(),
    )
    with TestClient(app) as client:
        generated = client.post(
            "/api/articles/generate",
            json={
                "topic": "晚饭后如何合理活动",
                "provider": "mock",
                "image_count": 1,
                "humanize": True,
            },
        )
        assert generated.status_code == 201
        article = generated.json()
        assert article["prompt_version"] == "v2-humanizer-zh"
        assert article["content"]["opening"] == "吃过晚饭，先别急着往沙发上一躺。"

        usage = client.get("/api/token-usage").json()
        humanizer = next(
            item
            for item in usage["models"]
            if item["provider"] == "test-humanizer"
        )
        assert humanizer["metered_requests"] == 2
        assert humanizer["total_tokens"] == 300


def test_daily_automation_generates_and_uploads_mock_draft():
    with TestClient(app) as client:
        created = client.post(
            "/api/automation/plans",
            json={
                "name": "测试每日计划",
                "audience_slug": "middle-age-health",
                "directions": "睡眠、适度活动和普通家庭可执行的健康习惯",
                "category": "生活方式",
                "provider": "mock",
                "run_time": "06:30",
                "timezone": "Asia/Shanghai",
                "image_count": 1,
                "auto_upload_draft": True,
                "enabled": True,
            },
        )
        assert created.status_code == 201
        plan = created.json()

        queued = client.post(
            f"/api/automation/plans/{plan['id']}/run-now"
        )
        assert queued.status_code == 202
        assert queued.json()["status"] == "pending"

        with SessionLocal() as session:
            completed = process_next_run(session, settings)
            assert completed is not None
            assert completed.status == "completed"
            assert completed.article_id is not None

        runs = client.get("/api/automation/runs").json()
        run = next(item for item in runs if item["id"] == queued.json()["id"])
        assert run["status"] == "completed"
        article = client.get(f"/api/articles/{run['article_id']}").json()
        assert article["status"] == "wechat_draft"
        assert article["wechat_media_id"].startswith("mock-draft-")
        assert "本文内容与配图含人工智能辅助生成" in article["content_html"]

        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "测试每日计划" in admin.text
        assert "立即运行一次" in admin.text
