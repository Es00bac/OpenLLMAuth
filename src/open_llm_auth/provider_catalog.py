from __future__ import annotations

from typing import Any, Dict, List, Optional


PROVIDER_ID_ALIASES: Dict[str, str] = {
    "z.ai": "zai",
    "z-ai": "zai",
    "opencode-zen": "opencode",
    "qwen": "qwen-portal",
    "kimi-code": "kimi-coding",
    "kimi-for-coding": "kimi-coding",
    "bedrock": "amazon-bedrock",
    "aws-bedrock": "amazon-bedrock",
    "bytedance": "volcengine",
    "doubao": "volcengine",
    "x-ai": "xai",
    "chatgpt": "openai-codex",
    "codex": "openai-codex",
    "copilot": "github-copilot",
    "gh-copilot": "github-copilot",
}


def normalize_provider_id(provider_id: str) -> str:
    normalized = provider_id.strip().lower()
    return PROVIDER_ID_ALIASES.get(normalized, normalized)


PROVIDER_ENV_PRIORITY: Dict[str, List[str]] = {
    "github-copilot": ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"],
    "anthropic": ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "zai": ["ZAI_API_KEY", "Z_AI_API_KEY"],
    "zai-coding": ["ZAI_API_KEY", "Z_AI_API_KEY"],
    "zai-air": ["ZAI_API_KEY", "Z_AI_API_KEY"],
    "qwen-portal": ["QWEN_OAUTH_TOKEN", "QWEN_PORTAL_API_KEY"],
    "volcengine": ["VOLCANO_ENGINE_API_KEY"],
    "volcengine-plan": ["VOLCANO_ENGINE_API_KEY"],
    "byteplus": ["BYTEPLUS_API_KEY"],
    "byteplus-plan": ["BYTEPLUS_API_KEY"],
    "openai-codex": ["OPENAI_CODEX_OAUTH_TOKEN"],
    "minimax-portal": ["MINIMAX_OAUTH_TOKEN", "MINIMAX_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY", "KIMICODE_API_KEY", "KIMI_CODING_API_KEY"],
    "huggingface": ["HUGGINGFACE_HUB_TOKEN", "HF_TOKEN"],
}


PROVIDER_ENV_DEFAULT: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "voyage": "VOYAGE_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "litellm": "LITELLM_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "cloudflare-ai-gateway": "CLOUDFLARE_AI_GATEWAY_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "xiaomi": "XIAOMI_API_KEY",
    "synthetic": "SYNTHETIC_API_KEY",
    "venice": "VENICE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "together": "TOGETHER_API_KEY",
    "qianfan": "QIANFAN_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "vllm": "VLLM_API_KEY",
    "kilocode": "KILOCODE_API_KEY",
}


AWS_SDK_ENV_PRIORITY: List[str] = [
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
]


BUILTIN_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "zai": {
        "baseUrl": "https://api.z.ai/api/paas/v4",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "zai-coding": {
        "baseUrl": "https://api.z.ai/api/coding/paas/v4",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "zai-air": {
        "baseUrl": "https://api.z.ai/api/coding/paas/v4",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "openai": {
        "baseUrl": "https://api.openai.com/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "openai-codex": {
        "baseUrl": "https://chatgpt.com/backend-api",
        "api": "openai-codex-responses",
        "auth": "oauth",
    },
    "anthropic": {
        "baseUrl": "https://api.anthropic.com",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "minimax": {
        "baseUrl": "https://api.minimax.io/anthropic",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "minimax-portal": {
        "baseUrl": "https://api.minimax.io/anthropic",
        "api": "anthropic-messages",
        "auth": "oauth",
    },
    "moonshot": {
        "baseUrl": "https://api.moonshot.ai/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "kimi-coding": {
        "baseUrl": "https://api.kimi.com/coding",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "qwen-portal": {
        "baseUrl": "https://portal.qwen.ai/v1",
        "api": "openai-completions",
        "auth": "oauth",
    },
    "openrouter": {
        "baseUrl": "https://openrouter.ai/api/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "together": {
        "baseUrl": "https://api.together.xyz/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "huggingface": {
        "baseUrl": "https://router.huggingface.co/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "mistral": {
        "baseUrl": "https://api.mistral.ai/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "venice": {
        "baseUrl": "https://api.venice.ai/api/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "xiaomi": {
        "baseUrl": "https://api.xiaomimimo.com/anthropic",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "nvidia": {
        "baseUrl": "https://integrate.api.nvidia.com/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "qianfan": {
        "baseUrl": "https://qianfan.baidubce.com/v2",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "volcengine": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "volcengine-plan": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "byteplus": {
        "baseUrl": "https://ark.ap-southeast.bytepluses.com/api/v3",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "byteplus-plan": {
        "baseUrl": "https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "kilocode": {
        "baseUrl": "https://api.kilo.ai/api/gateway",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "opencode": {
        "baseUrl": "https://opencode.ai/zen/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "synthetic": {
        "baseUrl": "https://api.synthetic.new/anthropic",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "claude-cli": {
        "baseUrl": "",
        "api": "claude-cli",
        "auth": "cli",
    },
    "codex-cli": {
        "baseUrl": "",
        "api": "codex-cli",
        "auth": "cli",
    },
    "github-copilot": {
        "baseUrl": "https://api.individual.githubcopilot.com/v1",
        "api": "openai-completions",
        "auth": "token",
    },
    "google": {
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "cloudflare-ai-gateway": {
        "baseUrl": "",
        "api": "anthropic-messages",
        "auth": "api-key",
    },
    "vercel-ai-gateway": {
        "baseUrl": "https://gateway.ai.vercel.com/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "litellm": {
        "baseUrl": "http://127.0.0.1:4000/v1",
        "api": "openai-completions",
        "auth": "api-key",
    },
    "ollama": {
        "baseUrl": "http://127.0.0.1:11434/v1",
        "api": "openai-completions",
        "auth": "api-key",
        "authHeader": False,
    },
    "vllm": {
        "baseUrl": "http://127.0.0.1:8000/v1",
        "api": "openai-completions",
        "auth": "api-key",
        "authHeader": False,
    },
    "amazon-bedrock": {
        "baseUrl": "https://bedrock-runtime.us-east-1.amazonaws.com",
        "api": "bedrock-converse-stream",
        "auth": "aws-sdk",
    },
}


def _cost_free() -> Dict[str, float]:
    return {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0}


BUILTIN_MODELS: Dict[str, List[Dict[str, Any]]] = {
    "openai-codex": [
        {
            "id": "gpt-5.3-codex",
            "name": "GPT-5.3 Codex",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.3-codex-spark",
            "name": "GPT-5.3 Codex Spark",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 128000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.2-codex",
            "name": "GPT-5.2 Codex",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.1",
            "name": "GPT-5.1",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.1-codex-mini",
            "name": "GPT-5.1 Codex Mini",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
    ],
    "zai": [
        {
            "id": "glm-5",
            "name": "GLM 5",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "glm-4.7",
            "name": "GLM 4.7",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 202752,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "zai-coding": [
        {
            "id": "glm-5",
            "name": "GLM 5",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "glm-4.7",
            "name": "GLM 4.7",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 202752,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "zai-air": [
        {
            "id": "glm-4.5-air",
            "name": "GLM 4.5 Air",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        }
    ],
    "openai": [
        {
            "id": "gpt-5.2",
            "name": "GPT-5.2",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5-mini",
            "name": "GPT-5 Mini",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "anthropic": [
        {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "minimax": [
        {
            "id": "MiniMax-M2.1",
            "name": "MiniMax M2.1",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": {
                "input": 0.3,
                "output": 1.2,
                "cacheRead": 0.03,
                "cacheWrite": 0.12,
            },
        },
        {
            "id": "MiniMax-M2.5",
            "name": "MiniMax M2.5",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": {
                "input": 0.3,
                "output": 1.2,
                "cacheRead": 0.03,
                "cacheWrite": 0.12,
            },
        },
        {
            "id": "MiniMax-VL-01",
            "name": "MiniMax VL 01",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": {
                "input": 0.3,
                "output": 1.2,
                "cacheRead": 0.03,
                "cacheWrite": 0.12,
            },
        },
    ],
    "moonshot": [
        {
            "id": "kimi-k2.5",
            "name": "Kimi K2.5",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 256000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        }
    ],
    "kimi-coding": [
        {
            "id": "k2p5",
            "name": "Kimi for Coding",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 262144,
            "maxTokens": 32768,
            "cost": _cost_free(),
        }
    ],
    "qwen-portal": [
        {
            "id": "coder-model",
            "name": "Qwen Coder",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "vision-model",
            "name": "Qwen Vision",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "openrouter": [
        {
            "id": "auto",
            "name": "OpenRouter Auto",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        }
    ],
    "together": [
        {
            "id": "zai-org/GLM-4.7",
            "name": "GLM 4.7 Fp8",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 202752,
            "maxTokens": 8192,
            "cost": {
                "input": 0.45,
                "output": 2.0,
                "cacheRead": 0.45,
                "cacheWrite": 2.0,
            },
        },
        {
            "id": "moonshotai/Kimi-K2.5",
            "name": "Kimi K2.5",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 262144,
            "maxTokens": 32768,
            "cost": {"input": 0.5, "output": 2.8, "cacheRead": 0.5, "cacheWrite": 2.8},
        },
    ],
    "huggingface": [
        {
            "id": "deepseek-ai/DeepSeek-R1",
            "name": "DeepSeek R1",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 8192,
            "cost": {"input": 3.0, "output": 7.0, "cacheRead": 3.0, "cacheWrite": 3.0},
        },
        {
            "id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "name": "Llama 3.3 70B Instruct Turbo",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 8192,
            "cost": {
                "input": 0.88,
                "output": 0.88,
                "cacheRead": 0.88,
                "cacheWrite": 0.88,
            },
        },
    ],
    "venice": [
        {
            "id": "llama-3.3-70b",
            "name": "Llama 3.3 70B",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "claude-opus-45",
            "name": "Claude Opus 4.5 (via Venice)",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 202752,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "qianfan": [
        {
            "id": "deepseek-v3.2",
            "name": "DEEPSEEK V3.2",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 98304,
            "maxTokens": 32768,
            "cost": _cost_free(),
        }
    ],
    "nvidia": [
        {
            "id": "nvidia/llama-3.1-nemotron-70b-instruct",
            "name": "NVIDIA Llama 3.1 Nemotron 70B Instruct",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 4096,
            "cost": _cost_free(),
        }
    ],
    "xiaomi": [
        {
            "id": "mimo-v2-flash",
            "name": "Xiaomi MiMo V2 Flash",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 262144,
            "maxTokens": 8192,
            "cost": _cost_free(),
        }
    ],
    "kilocode": [
        {
            "id": "anthropic/claude-opus-4.6",
            "name": "Claude Opus 4.6",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "openai/gpt-5.2",
            "name": "GPT-5.2",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "opencode": [
        {
            "id": "openai/gpt-5.2",
            "name": "GPT-5.2 via OpenCode",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 200000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        }
    ],
    "google": [
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 1000000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 1000000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "gemini-embedding-2-preview",
            "name": "Gemini Embedding 2 Preview",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 8192,
            "maxTokens": 768,
            "cost": _cost_free(),
        },
    ],
    "github-copilot": [
        {
            "id": "gpt-4o",
            "name": "GPT-4o (Copilot)",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-4.1",
            "name": "GPT-4.1 (Copilot)",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-4.1-mini",
            "name": "GPT-4.1 Mini (Copilot)",
            "reasoning": False,
            "input": ["text", "image"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "claude-sonnet-4.6",
            "name": "Claude Sonnet 4.6 (Copilot)",
            "reasoning": True,
            "input": ["text", "image"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
        {
            "id": "o3-mini",
            "name": "o3-mini (Copilot)",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 128000,
            "maxTokens": 8192,
            "cost": _cost_free(),
        },
    ],
    "claude-cli": [
        {
            "id": "opus",
            "name": "Claude Opus",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 4096,
            "cost": _cost_free(),
        },
        {
            "id": "sonnet",
            "name": "Claude Sonnet",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 4096,
            "cost": _cost_free(),
        },
        {
            "id": "haiku",
            "name": "Claude Haiku",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 4096,
            "cost": _cost_free(),
        },
        {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 4096,
            "cost": _cost_free(),
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 200000,
            "maxTokens": 4096,
            "cost": _cost_free(),
        },
    ],
    "codex-cli": [
        {
            "id": "gpt-5.3-codex",
            "name": "GPT-5.3 Codex",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.2-codex",
            "name": "GPT-5.2 Codex",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
        {
            "id": "gpt-5.1-codex-mini",
            "name": "GPT-5.1 Codex Mini",
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 272000,
            "maxTokens": 128000,
            "cost": _cost_free(),
        },
    ],
    "vllm": [],
    "ollama": [
        {
            "id": "nomic-embed-text",
            "name": "nomic-embed-text",
            "reasoning": False,
            "input": ["text"],
            "contextWindow": 8192,
            "maxTokens": 0,
            "cost": _cost_free(),
        },
    ],
}


def resolve_cloudflare_ai_gateway_base_url(account_id: str, gateway_id: str) -> str:
    account = account_id.strip()
    gateway = gateway_id.strip()
    if not account or not gateway:
        return ""
    return f"https://gateway.ai.cloudflare.com/v1/{account}/{gateway}/anthropic"


def get_builtin_provider_config(provider_id: str) -> Optional[Dict[str, Any]]:
    return BUILTIN_PROVIDERS.get(normalize_provider_id(provider_id))


def get_builtin_provider_models(provider_id: str) -> List[Dict[str, Any]]:
    return list(BUILTIN_MODELS.get(normalize_provider_id(provider_id), []))


def get_all_builtin_provider_ids() -> List[str]:
    keys = set(BUILTIN_PROVIDERS.keys()) | set(BUILTIN_MODELS.keys())
    return sorted(keys)
