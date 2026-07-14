"""
开发 Agent 基类

三个 Agent（写/查/测）共享的：
- LLM 调用
- 文件读写
- 代码上下文收集
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_llm():
    """获取 LLM 实例，复用项目已有的 provider"""
    try:
        from sentinelmind.llm.provider import OpenAICompatibleProvider
        from sentinelmind.config.settings import ConfigManager

        config = ConfigManager()
        config.load("configs/settings.yaml")
        llm_config = config.get("llm", {})

        return OpenAICompatibleProvider(
            base_url=llm_config.get("base_url", "https://api.openai.com/v1"),
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", "gpt-4o"),
            timeout=llm_config.get("timeout", 60),
            max_retries=llm_config.get("max_retries", 2),
        )
    except Exception as e:
        print(f"[agent] 无法加载 LLM provider: {e}")
        print("[agent] 使用环境变量 OPENAI_API_KEY")
        import os

        from sentinelmind.llm.provider import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("OPENAI_API_KEY", os.getenv("LLM_API_KEY", "")),
            model=os.getenv("LLM_MODEL", "gpt-4o"),
        )


def collect_context(globs: list[str], max_file_size: int = 80_000) -> str:
    """根据 glob 模式收集项目代码上下文"""
    import fnmatch

    parts: list[str] = []
    total_size = 0

    for pattern in globs:
        # 支持 **/*.py 模式
        if "**" in pattern:
            for f in PROJECT_ROOT.glob(pattern):
                if total_size > max_file_size:
                    break
                try:
                    content = f.read_text(encoding="utf-8")
                    parts.append(f"# === {f.relative_to(PROJECT_ROOT)} ===\n{content}")
                    total_size += len(content)
                except Exception:
                    continue
        else:
            # 按 fnmatch 在 src 下搜索
            src_dir = PROJECT_ROOT / "src"
            for f in src_dir.rglob("*.py"):
                if total_size > max_file_size:
                    break
                rel = f.relative_to(PROJECT_ROOT)
                if fnmatch.fnmatch(str(rel), pattern) or fnmatch.fnmatch(f.name, pattern):
                    try:
                        content = f.read_text(encoding="utf-8")
                        parts.append(f"# === {rel} ===\n{content}")
                        total_size += len(content)
                    except Exception:
                        continue

    return "\n\n".join(parts)


def run_cmd(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """运行命令，返回 (returncode, stdout, stderr)"""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or str(PROJECT_ROOT))
    return result.returncode, result.stdout, result.stderr


def call_llm(provider, system_prompt: str, user_message: str) -> str:
    """调用 LLM，返回文本响应"""
    try:
        # 使用 chat 方法
        resp = provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return resp if isinstance(resp, str) else resp.get("content", str(resp))
    except Exception as e:
        return f"[LLM 调用失败] {e}"
