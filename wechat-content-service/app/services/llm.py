import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


def parse_token_usage(body: dict[str, Any]) -> TokenUsage:
    raw = body.get("usage")
    if not isinstance(raw, dict):
        return TokenUsage()

    def token_value(*keys: str) -> int | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    prompt_tokens = token_value("prompt_tokens", "input_tokens")
    completion_tokens = token_value("completion_tokens", "output_tokens")
    total_tokens = token_value("total_tokens")
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


class LLMProvider(ABC):
    name = "unknown"
    model = ""
    usage = TokenUsage()

    @abstractmethod
    def generate_article(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str = "只输出合法 JSON，不要输出 Markdown。",
    ) -> dict[str, Any]:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    name = "mock"
    model = "local-template"

    def generate_article(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.usage = TokenUsage()
        topic = context["topic"]
        audience = context["audience"]
        title_count = context["title_count"]
        image_count = context["image_count"]
        headline_intensity = context.get("headline_intensity", 4)
        age_label = audience["age_range"].split("-")[0]

        if headline_intensity <= 2:
            title_templates = [
                f"过了{age_label}岁，如何更稳妥地做好{topic}",
                f"关于{topic}，这几条日常建议简单又实用",
                f"{topic}的生活指南：从容易坚持的小事开始",
                f"人到中年，做好{topic}可以注意这几个方面",
                f"每天一点小改变，让{topic}更容易坚持",
                f"{topic}有哪些常见注意事项？",
                f"一份适合全家参考的{topic}实用清单",
                f"关于{topic}，先了解这些基础方法",
                f"循序渐进做好{topic}，不必急于求成",
                f"把{topic}放进日常生活的几个办法",
            ]
        elif headline_intensity == 3:
            title_templates = [
                f"过了{age_label}岁，做好{topic}要抓住这3点",
                f"关于{topic}，很多人容易忽略这几个细节",
                f"每天花10分钟，让{topic}更容易坚持",
                f"{topic}怎么做更合适？这份清单说清楚了",
                f"人到中年，想做好{topic}可以从这里开始",
                f"同样是{topic}，为什么有些人做得更轻松？",
                f"关于{topic}，最常见的4个误区",
                f"{topic}并不复杂，关键在这几个习惯",
                f"给家里人的{topic}实用建议",
                f"坚持不难：把{topic}拆成每天能做到的小事",
            ]
        else:
            title_templates = [
                f"过了{age_label}岁，关于{topic}，这几件事越早知道越好",
                f"很多人都忽略了：{topic}真正要注意的是这3点",
                f"别等身体提醒你，{topic}可以先从这几个习惯开始",
                f"{topic}不是小事，中年以后尤其要避开这几个误区",
                f"每天花10分钟，做好{topic}这件事并不难",
                f"同样是{topic}，为什么有些人做得更轻松？",
                f"关于{topic}，最容易被误解的4件事",
                f"人到中年，想做好{topic}，先别急着跟风",
                f"{topic}的实用清单，建议给家里人也看看",
                f"坚持不难：把{topic}拆成每天能做到的小事",
            ]
            if headline_intensity == 5:
                title_templates[0] = (
                    f"提醒家里人：过了{age_label}岁做{topic}，"
                    "很多人第一步就错了"
                )
                title_templates[1] = (
                    f"同样是{topic}，为什么有人轻松坚持？关键藏在这3点"
                )
        titles = title_templates[:title_count]

        sections = [
            {
                "heading": "先弄清楚：为什么这件事值得重视",
                "paragraphs": [
                    f"随着生活节奏和身体状态变化，{topic}往往会影响一天的精神和生活质量。很多人不是不重视，而是不知道应该从哪里开始。",
                    "真正有用的方法通常不复杂。与其追求一次改变很多，不如先找到最容易坚持的一两个习惯。",
                ],
            },
            {
                "heading": "从三个容易坚持的小习惯开始",
                "paragraphs": [
                    "第一，把目标变得具体。不要只说“以后注意”，而是确定每天什么时候做、做多久。",
                    "第二，循序渐进。身体不舒服时不要勉强，也不要因为一两天没有效果就频繁更换方法。",
                    "第三，记录自己的感受。睡眠、精神、食欲和活动能力的变化，都比盲目跟风更值得关注。",
                ],
            },
            {
                "heading": "这几个常见误区要避开",
                "paragraphs": [
                    "不要把某一种食物、动作或保健方法当成解决所有问题的办法。健康习惯通常需要饮食、活动、睡眠和情绪共同配合。",
                    "也不要用别人的经验替代自己的身体感受。存在慢性病、正在服药或持续不适的人，应结合专业人员建议调整。",
                ],
            },
            {
                "heading": "今天就能做的行动清单",
                "paragraphs": [
                    "选一个最容易完成的小目标，坚持一周后再决定是否增加难度。",
                    "如果身体出现持续或明显的不适，应及时就医，不要只依赖网络文章自行判断。",
                ],
            },
        ]

        image_prompts = [
            {
                "position": index,
                "asset_type": "cover" if index == 0 else "inline",
                "prompt": (
                    f"{audience['image_style']}，主题为“{topic}”，"
                    "温暖自然、真实生活场景、横向构图、无文字、无医疗器械特写"
                ),
            }
            for index in range(image_count)
        ]

        return {
            "title_candidates": titles,
            "selected_title": titles[0],
            "summary": f"围绕{topic}，分享几个更容易理解和坚持的日常建议。",
            "opening": (
                f"{topic}，是很多家庭都会关心的话题。"
                "与其被各种说法弄得焦虑，不如先从简单、稳妥、能坚持的事情做起。"
            ),
            "sections": sections,
            "conclusion": (
                "养生不是追求立刻见效，而是把合适的小习惯放进每天的生活里。"
                "本文用于日常健康科普，不能替代医生诊断和治疗建议。"
            ),
            "image_prompts": image_prompts,
        }

    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str = "只输出合法 JSON，不要输出 Markdown。",
    ) -> dict[str, Any]:
        self.usage = TokenUsage()
        direction_match = re.search(r"内容方向：\s*(.+)", prompt)
        direction = (
            direction_match.group(1).splitlines()[0].strip()
            if direction_match
            else "健康生活"
        )
        candidates = [
            f"{direction}：今天可以开始的三个生活小习惯",
            f"{direction}：怎样把一个好习惯坚持七天",
            f"{direction}：早晨和晚上分别适合做什么",
            f"{direction}：普通家庭容易忽略的生活细节",
            f"{direction}：不增加负担的日常行动清单",
            f"{direction}：如何根据身体感受循序渐进",
            f"{direction}：家人可以一起实践的小改变",
        ]
        topic = next(
            (candidate for candidate in candidates if f"- {candidate}" not in prompt),
            candidates[0],
        )
        return {
            "topic": topic,
            "category": "日常养生",
            "rationale": "符合内容方向，强调低风险、可执行的日常建议。",
        }


def parse_json_response(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("Top-level JSON must be an object", cleaned, 0)
    return parsed


@dataclass(frozen=True)
class ChatProviderConfig:
    name: str
    api_base: str
    api_key: str
    model: str
    key_env: str
    temperature: float = 0.8
    use_json_mode: bool = True
    max_tokens_field: str = "max_tokens"
    max_output_tokens: int = 4096
    extra_payload: dict[str, Any] = field(default_factory=dict)


class CompatibleChatProvider(LLMProvider):
    def __init__(self, config: ChatProviderConfig, timeout: float):
        if not config.api_base or not config.api_key or not config.model:
            raise LLMError(
                f"{config.name} is not configured. Fill {config.key_env} in .env"
            )
        self.name = config.name
        self.api_base = config.api_base.rstrip("/")
        self.api_key = config.api_key
        self.model = config.model
        self.timeout = timeout
        self.temperature = config.temperature
        self.use_json_mode = config.use_json_mode
        self.max_tokens_field = config.max_tokens_field
        self.max_output_tokens = config.max_output_tokens
        self.extra_payload = config.extra_payload
        self.usage = TokenUsage()

    def generate_article(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return self.generate_json(
            prompt,
            system_prompt=(
                "你是微信公众号内容编辑。只输出合法 JSON，不要输出 Markdown。"
            ),
        )

    def generate_json(
        self,
        prompt: str,
        *,
        system_prompt: str = "只输出合法 JSON，不要输出 Markdown。",
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            self.max_tokens_field: self.max_output_tokens,
            **self.extra_payload,
        }
        if self.use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            self.usage = parse_token_usage(body)
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise LLMError(f"{self.name} returned non-text content")
            return parse_json_response(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f" — {exc.response.text[:500]}"
            raise LLMError(f"{self.name} request failed: {exc}{detail}") from exc


def _provider_config(settings: Settings, provider_name: str) -> ChatProviderConfig:
    configs = {
        "compatible": ChatProviderConfig(
            name="compatible",
            api_base=settings.llm_api_base,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            key_env="LLM_API_KEY",
        ),
        "deepseek": ChatProviderConfig(
            name="deepseek",
            api_base=settings.deepseek_api_base,
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            key_env="DEEPSEEK_API_KEY",
        ),
        "minimax": ChatProviderConfig(
            name="minimax",
            api_base=settings.minimax_api_base,
            api_key=settings.minimax_api_key,
            model=settings.minimax_model,
            key_env="MINIMAX_API_KEY",
            use_json_mode=False,
            max_tokens_field="max_completion_tokens",
            max_output_tokens=2048,
        ),
        "kimi": ChatProviderConfig(
            name="kimi",
            api_base=settings.kimi_api_base,
            api_key=settings.moonshot_api_key,
            model=settings.kimi_model,
            key_env="MOONSHOT_API_KEY",
            temperature=1,
            max_tokens_field="max_completion_tokens",
            extra_payload={"reasoning_effort": "low"},
        ),
    }
    try:
        return configs[provider_name]
    except KeyError as exc:
        raise LLMError(f"Unsupported LLM provider: {provider_name}") from exc


def provider_catalog(settings: Settings) -> list[dict[str, str | bool]]:
    return [
        {
            "provider": config.name,
            "model": config.model,
            "configured": bool(config.api_key),
            "key_env": config.key_env,
        }
        for config in (
            _provider_config(settings, "deepseek"),
            _provider_config(settings, "minimax"),
            _provider_config(settings, "kimi"),
        )
    ]


def build_llm_provider(
    settings: Settings,
    provider_name: str | None = None,
) -> LLMProvider:
    selected = provider_name or settings.llm_provider
    if selected == "mock":
        return MockLLMProvider()
    config = _provider_config(settings, selected)
    return CompatibleChatProvider(config, settings.llm_timeout_seconds)
