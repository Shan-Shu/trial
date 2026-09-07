"""多模型提供商工厂 + 三节点“角色 → LLM”绑定。

三个节点分别绑定（可在 .env 覆盖）:
- 文献检索节点  → DeepSeek V4（默认 deepseek-v4-pro，DeepSeek 官方 API）
- 质量评估节点  → GLM 4.7 Flash（glm-4.7-flash，智谱 BigModel）
- 知识提取节点  → DeepSeek V4 Pro（deepseek-v4-pro；临时替代 OpenAI gpt-5.6，
  因当前网络无法访问 api.openai.com，可随时用 ROLE_PROVIDER_KNOWLEDGE/openai 切回）
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# 加载项目根目录 .env（幂等）
load_dotenv()

SUPPORTED_PROVIDERS: dict[str, str] = {
    "openai": "OpenAI 官方 API",
    "deepseek": "DeepSeek 官方 API（V4 家族）",
    "qwen": "通义千问 DashScope（OpenAI 兼容模式）",
    "glm": "智谱 GLM（BigModel，OpenAI 兼容模式）",
    "anthropic": "Anthropic Claude",
    "google": "Google Gemini",
}

# 节点角色 → LLM 默认绑定（provider / model / 所需 API Key）
ROLE_PROVIDER = {
    "retriever": "deepseek",
    "quality": "glm",
    "knowledge": "deepseek",   # 临时：用 DeepSeek V4 Pro 替代 OpenAI gpt-5.6
}
ROLE_MODEL_ENV = {
    "retriever": "RETRIEVAL_MODEL",
    "quality": "QUALITY_MODEL",
    "knowledge": "KNOWLEDGE_MODEL",
}
ROLE_MODEL_DEFAULT = {
    "retriever": "deepseek-v4-pro",   # DeepSeek V4（可换 deepseek-v4-flash）
    "quality": "glm-4.7-flash",       # GLM 4.7 Flash
    "knowledge": "deepseek-v4-pro",   # 临时：DeepSeek V4 Pro 替代 gpt-5.6
}
ROLE_KEY_ENV = {
    "retriever": "DEEPSEEK_API_KEY",
    "quality": "ZHIPU_API_KEY",
    "knowledge": "DEEPSEEK_API_KEY",
}
ROLE_LABEL = {
    "retriever": "文献检索节点",
    "quality": "质量评估节点",
    "knowledge": "知识提取节点",
}


def build_chat_model(
    provider: str | None = None,
    model_name: str | None = None,
    temperature: float = 0.2,
) -> object:
    """按提供商构建 ChatModel。

    参数:
        provider: 见 SUPPORTED_PROVIDERS；None 时取环境变量 LLM_PROVIDER，默认 openai。
        model_name: 覆盖默认模型名；None 时取对应 *_MODEL 环境变量。
        temperature: 采样温度。
    """
    provider = (provider or os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"不支持的 provider: {provider}，可选: {list(SUPPORTED_PROVIDERS)}")

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=temperature,
        )

    if provider == "deepseek":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
            temperature=temperature,
        )

    if provider == "qwen":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name or os.getenv("QWEN_MODEL", "qwen-plus"),
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=temperature,
        )

    if provider == "glm":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name or os.getenv("GLM_MODEL", "glm-4.7-flash"),
            api_key=os.getenv("ZHIPU_API_KEY") or os.getenv("GLM_API_KEY"),
            base_url="https://open.bigmodel.cn/api/paas/v4",
            temperature=temperature,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=temperature,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model_name or os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"),
            api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=temperature,
        )

    raise AssertionError("unreachable")  # pragma: no cover


def build_role_model(
    role: str,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> object:
    """构建“节点角色”绑定的 LLM。

    role: retriever | quality | knowledge。
    默认按 ROLE_PROVIDER/ROLE_MODEL_DEFAULT 绑定目标模型；可用环境变量
    RETRIEVAL_MODEL / QUALITY_MODEL / KNOWLEDGE_MODEL 覆盖具体 model id。
    """
    if role not in ROLE_PROVIDER:
        raise ValueError(f"未知角色: {role}，可选: {list(ROLE_PROVIDER)}")
    key_env = ROLE_KEY_ENV[role]
    if not os.getenv(key_env):
        raise ValueError(
            f"角色 {role}({ROLE_LABEL[role]}) 缺少 API Key，请在 .env 中设置 {key_env}"
        )
    provider = provider or os.getenv(
        "ROLE_PROVIDER_" + role.upper(), ROLE_PROVIDER[role]
    )
    model = (model or os.getenv(ROLE_MODEL_ENV[role], ROLE_MODEL_DEFAULT[role])).strip()
    return build_chat_model(provider=provider, model_name=model, temperature=temperature)


def role_model_binding(role: str) -> dict[str, str]:
    """返回角色的解析绑定信息（不含 Key），便于日志/看板展示。"""
    return {
        "role": role,
        "label": ROLE_LABEL[role],
        "provider": os.getenv("ROLE_PROVIDER_" + role.upper(), ROLE_PROVIDER[role]),
        "model": os.getenv(ROLE_MODEL_ENV[role], ROLE_MODEL_DEFAULT[role]),
        "key_env": ROLE_KEY_ENV[role],
        "has_key": bool(os.getenv(ROLE_KEY_ENV[role])),
    }
