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
                "target_length": 800,
            },
        )
        assert response.status_code == 201
        article = response.json()
        assert article["status"] == "generated"
        assert len(article["title_candidates"]) == 5
        assert len(article["assets"]) == 3
        assert article["target_length"] == 800
        assert "过了35岁" in article["selected_title"]

        preview = client.get(f"/admin/articles/{article['id']}")
        assert preview.status_code == 200
        assert "一键上传到公众号草稿箱" in preview.text

        draft = client.post(f"/api/articles/{article['id']}/wechat-draft")
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
        assert {item["provider"] for item in providers.json()["text"]} == {
            "deepseek",
            "minimax",
            "kimi",
        }
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


def test_generation_normalizes_list_image_search_queries(monkeypatch):
    class ListQueryMockProvider(MockLLMProvider):
        def generate_article(self, prompt, context):
            result = super().generate_article(prompt, context)
            result["image_prompts"][0]["search_query"] = [
                "healthy family",
                "dinner",
            ]
            return result

    monkeypatch.setattr(
        generator_service,
        "build_llm_provider",
        lambda settings, provider_name=None: ListQueryMockProvider(),
    )
    with TestClient(app) as client:
        generated = client.post(
            "/api/articles/generate",
            json={
                "topic": "家庭晚饭如何搭配得更轻松",
                "provider": "mock",
                "image_count": 1,
            },
        )
        assert generated.status_code == 201
        search_query = generated.json()["content"]["image_prompts"][0][
            "search_query"
        ]
        assert search_query == "healthy family dinner"


def test_headline_intensity_validation_and_admin_control():
    with TestClient(app) as client:
        admin = client.get("/admin")
        assert admin.status_code == 200
        assert 'name="headline_intensity"' in admin.text
        assert "4 — 强吸引（推荐）" in admin.text
        assert 'name="humanize"' in admin.text
        assert "Humanizer-zh 二次润色" in admin.text
        assert 'name="target_length"' in admin.text
        assert 'name="auto_upload_draft"' in admin.text
        assert "生成完成后立即推送到公众号草稿箱" in admin.text
        assert "短篇 — 约 800 字" in admin.text
        assert "深度 — 约 2500 字" in admin.text

        invalid = client.post(
            "/api/articles/generate",
            json={
                "topic": "晚饭后如何合理活动",
                "provider": "mock",
                "headline_intensity": 6,
            },
        )
        assert invalid.status_code == 422

        invalid_length = client.post(
            "/api/articles/generate",
            json={
                "topic": "晚饭后如何合理活动",
                "provider": "mock",
                "target_length": 200,
            },
        )
        assert invalid_length.status_code == 422


def test_admin_generation_can_upload_draft_immediately():
    with TestClient(app) as client:
        generated = client.post(
            "/admin/generate",
            data={
                "topic": "晚饭后如何安排散步和休息",
                "category": "生活方式",
                "audience_slug": "middle-age-health",
                "provider": "mock",
                "target_length": "800",
                "headline_intensity": "3",
                "auto_upload_draft": "true",
            },
            follow_redirects=False,
        )
        assert generated.status_code == 303
        location = generated.headers["location"]
        assert location.startswith("/admin/articles/")
        assert "draft_message=" in location

        preview = client.get(location)
        assert preview.status_code == 200
        assert "mock-draft-" in preview.text
        assert "重新核验微信草稿" in preview.text

        article_id = int(location.split("?", 1)[0].rstrip("/").split("/")[-1])
        admin = client.get("/admin")
        assert (
            f'action="/admin/articles/{article_id}/wechat-draft"'
            in admin.text
        )
        assert "重新核验" in admin.text


def test_admin_generation_does_not_upload_when_option_is_unchecked():
    with TestClient(app) as client:
        generated = client.post(
            "/admin/generate",
            data={
                "topic": "周末如何安排轻松的家庭活动",
                "category": "生活方式",
                "audience_slug": "middle-age-health",
                "provider": "mock",
                "target_length": "800",
                "headline_intensity": "3",
            },
            follow_redirects=False,
        )
        assert generated.status_code == 303
        location = generated.headers["location"]
        assert "draft_message=" not in location

        preview = client.get(location)
        assert preview.status_code == 200
        assert "一键上传到公众号草稿箱" in preview.text
        assert "mock-draft-" not in preview.text

        article_id = int(location.rstrip("/").split("/")[-1])
        admin = client.get("/admin")
        assert (
            f'action="/admin/articles/{article_id}/wechat-draft"'
            in admin.text
        )
        assert "推送草稿箱" in admin.text


def test_admin_generation_shows_draft_upload_error(monkeypatch):
    def fail_upload(*args, **kwargs):
        raise RuntimeError("微信草稿接口测试失败")

    monkeypatch.setattr("app.admin.upload_article_to_draft", fail_upload)
    with TestClient(app) as client:
        generated = client.post(
            "/admin/generate",
            data={
                "topic": "每天如何安排简单的伸展活动",
                "category": "生活方式",
                "audience_slug": "middle-age-health",
                "provider": "mock",
                "target_length": "800",
                "headline_intensity": "3",
                "auto_upload_draft": "true",
            },
            follow_redirects=False,
        )
        assert generated.status_code == 303
        location = generated.headers["location"]
        assert "draft_error=" in location

        preview = client.get(location)
        assert preview.status_code == 200
        assert "草稿上传失败" in preview.text
        assert "微信草稿接口测试失败" in preview.text


def test_admin_can_create_and_use_a_target_audience():
    with TestClient(app) as client:
        form = client.get("/admin/audiences/new")
        assert form.status_code == 200
        assert "添加目标受众" in form.text
        assert 'name="slug"' in form.text

        created = client.post(
            "/admin/audiences",
            data={
                "slug": "young-family-health",
                "name": "年轻家庭健康",
                "age_range": "28-42",
                "description": "关注家庭饮食、睡眠和亲子活动的年轻父母",
                "interests": "家庭饮食\n睡眠\n亲子运动",
                "writing_style": "自然、简洁、重视家庭生活场景",
                "title_preferences": "有孩子的家庭……\n下班后坚持这几个习惯……",
                "article_length": "900",
                "image_style": "真实自然的中国年轻家庭生活场景",
                "prohibited_topics": "宣称治愈疾病\n制造健康焦虑",
                "enabled": "true",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert created.headers["location"].startswith("/admin?audience_created=")

        admin = client.get("/admin")
        assert "年轻家庭健康" in admin.text
        assert "添加目标受众" in admin.text

        generated = client.post(
            "/api/articles/generate",
            json={
                "topic": "年轻家庭如何安排晚饭后的活动",
                "audience_slug": "young-family-health",
                "provider": "mock",
                "image_count": 1,
            },
        )
        assert generated.status_code == 201
        article = generated.json()
        assert article["target_length"] == 900
        assert "过了28岁" in article["selected_title"]

        duplicate = client.post(
            "/admin/audiences",
            data={
                "slug": "young-family-health",
                "name": "重复画像",
                "age_range": "28-42",
                "article_length": "900",
                "enabled": "true",
            },
        )
        assert duplicate.status_code == 200
        assert "该唯一标识已存在" in duplicate.text


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
        mock = next(item for item in usage["models"] if item["provider"] == "mock")
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
        humanizer = next(item for item in usage["models"] if item["provider"] == "test-humanizer")
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
                "target_length": 1800,
                "auto_upload_draft": True,
                "enabled": True,
            },
        )
        assert created.status_code == 201
        plan = created.json()
        assert plan["target_length"] == 1800

        queued = client.post(f"/api/automation/plans/{plan['id']}/run-now")
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
        assert article["target_length"] == 1800
        assert article["wechat_media_id"].startswith("mock-draft-")
        assert "人工智能辅助生成" not in article["content_html"]

        admin_before_second = client.get("/admin")
        assert 'id="new-automation-plan"' in admin_before_second.text
        assert 'action="/admin/automation-plans"' in admin_before_second.text
        assert "新增定时任务" in admin_before_second.text

        second = client.post(
            "/admin/automation-plans",
            data={
                "name": "家庭饮食晚间计划",
                "audience_slug": "senior-wellness",
                "directions": "普通家庭容易执行的晚饭搭配和厨房习惯",
                "category": "家庭饮食",
                "provider": "mock",
                "run_time": "20:15",
                "timezone": "Asia/Shanghai",
                "target_length": "800",
                "headline_intensity": "3",
            },
            follow_redirects=False,
        )
        assert second.status_code == 303
        plans = client.get("/api/automation/plans").json()
        second_plan = next(
            item for item in plans if item["name"] == "家庭饮食晚间计划"
        )
        assert len(plans) >= 2

        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "定时任务列表" in admin.text
        assert "执行时间" in admin.text
        assert "模型 / 篇幅" in admin.text
        assert "测试每日计划" in admin.text
        assert "家庭饮食晚间计划" in admin.text
        assert "每天 20:15" in admin.text
        assert "仅生成" in admin.text
        assert "已停用" in admin.text
        assert f'id="plan-{plan["id"]}"' in admin.text
        assert f'id="plan-{second_plan["id"]}"' in admin.text
        assert "立即运行一次" in admin.text
