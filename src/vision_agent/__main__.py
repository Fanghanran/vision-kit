"""
主入口模块 — Vision Agent 程序入口

设计来源：docs/modules/main.md

职责：
- 命令行参数解析（--config, --check, --log-level, --version）
- 配置加载与校验
- 组件组装（按依赖顺序）
- 信号处理与优雅关闭
- 日志初始化

调用方式：
    python -m vision_agent --config configs/settings.yaml
    python -m vision_agent --check
    python -m vision_agent --version
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

logger = logging.getLogger("vision_agent")


# ─── 命令行参数 ──────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vision_agent",
        description="Vision Agent — 多路视频智能分析框架",
    )
    parser.add_argument(
        "--config",
        default="configs/settings.yaml",
        help="配置文件路径（默认 configs/settings.yaml）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅校验配置和运行环境，不启动系统",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="覆盖配置文件中的日志级别",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Vision Agent {__version__}",
    )
    return parser.parse_args()


# ─── 日志初始化 ──────────────────────────────────────────────


def setup_logging(config: dict, log_level: str | None = None) -> None:
    """初始化日志系统（main.md 3.4 节）"""
    system = config.get("system", {})
    level = log_level or system.get("log_level", "INFO")
    log_dir = system.get("log_dir", "logs")
    max_size = system.get("log_max_size_mb", 50) * 1024 * 1024
    backup_count = system.get("log_backup_count", 5)

    # 创建日志目录
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 日志格式
    fmt = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # 根日志器
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # 清除已有 handler（避免重复）
    root.handlers.clear()

    # 文件 handler
    try:
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "vision_agent.log"),
            maxBytes=int(max_size),
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(file_handler)
    except OSError as e:
        print(f"WARNING: 日志文件创建失败: {e}", file=sys.stderr)

    # 控制台 handler
    console_fmt = "%(asctime)s %(levelname)-5s %(message)s"
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(console_fmt, datefmt))
    root.addHandler(console_handler)

    # 注册日志脱敏 Filter
    try:
        from vision_agent.web.api.app import SanitizeFilter

        sanitize = SanitizeFilter()
        for handler in root.handlers:
            handler.addFilter(sanitize)
    except ImportError:
        pass

    # 降低第三方库日志级别
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ─── 组件组装 ────────────────────────────────────────────────


def assemble_components(config: dict) -> tuple:
    """按依赖顺序组装所有组件（main.md 3.5 节）

    Returns:
        (pipeline, database, web_app) 三元组
    """
    # 步骤 1：Storage
    from vision_agent.storage.database import DatabaseManager

    db_config = config.get("storage", {})
    database = DatabaseManager(db_config)
    database.connect()
    logger.info("component_init component=database")

    # 步骤 2：Cache
    from vision_agent.storage.cache import create_cache

    redis_config = config.get("redis", {})
    cache = create_cache(redis_config)
    logger.info("component_init component=cache type=%s", type(cache).__name__)

    # 步骤 3：Detector
    from vision_agent.core.detector import DetectorConfig, YOLODetector

    det_config = config.get("detector", {})
    gpu_config = config.get("gpu", {})
    detector_config = DetectorConfig(
        model_path=det_config.get("model_path", ""),
        confidence=det_config.get("confidence", 0.5),
        iou_threshold=det_config.get("iou", 0.45),
        batch_size=gpu_config.get("batch_size", 8),
        batch_timeout_ms=gpu_config.get("batch_timeout_ms", 50),
        input_size=det_config.get("input_size", 640),
        fp16=gpu_config.get("fp16", True),
        classes_filter=det_config.get("classes"),
    )
    device = f"cuda:{gpu_config.get('device_id', 0)}"
    detector = YOLODetector(detector_config, device=device)
    detector.warmup()
    logger.info("component_init component=detector model=%s", detector.model_name)

    # 步骤 4：Tracker
    from vision_agent.core.tracker import TrackerConfig

    tracker_cfg = config.get("tracker", {})
    tracker_config = TrackerConfig(
        tracker_type=tracker_cfg.get("type", "botsort"),
        track_thresh=tracker_cfg.get("track_thresh", 0.5),
        track_buffer=tracker_cfg.get("track_buffer", 30),
    )
    logger.info("component_init component=tracker")

    # 步骤 5：RuleEngine
    from vision_agent.rules.engine import RuleEngine

    rules_config = config.get("rules", {})
    rule_engine = RuleEngine(config=rules_config, cache=cache)
    rules_dir = rules_config.get("rules_dir", "configs/rules")
    if Path(rules_dir).exists():
        rule_engine.load_rules(rules_dir)
    logger.info(
        "component_init component=rule_engine rules=%d", len(rule_engine.list_rules())
    )

    # 步骤 6：LLM
    from vision_agent.llm.analyzer import LLMAnalyzer, LLMConfig

    llm_config_dict = config.get("llm", {})
    llm_analyzer = None
    if llm_config_dict.get("enabled", False):
        try:
            from vision_agent.llm.provider import (
                LLMProviderConfig,
                OpenAICompatibleProvider,
            )

            provider_config = LLMProviderConfig(
                api_base=llm_config_dict.get("api_base", ""),
                api_key=llm_config_dict.get("api_key", ""),
                model=llm_config_dict.get("model", "gpt-4o-mini"),
                timeout=llm_config_dict.get("timeout", 30),
                max_retries=llm_config_dict.get("max_retries", 2),
                monthly_budget=llm_config_dict.get("monthly_budget", 100.0),
            )
            provider = OpenAICompatibleProvider(provider_config)
            llm_config = LLMConfig(
                enabled=True,
                model=llm_config_dict.get("model", "gpt-4o-mini"),
            )
            llm_analyzer = LLMAnalyzer(config=llm_config, provider=provider)
            logger.info(
                "component_init component=llm_analyzer model=%s", provider_config.model
            )
        except Exception as e:
            logger.warning("llm_init_failed error=%s action=skip", str(e))

    # 步骤 7：Notifiers
    from vision_agent.actions.notifier import (
        EmailConfig,
        EmailNotifier,
        WebhookConfig,
        WebhookNotifier,
    )

    notifiers = []
    webhook_cfg = config.get("notification", {}).get("webhook", {})
    if webhook_cfg.get("enabled"):
        wh_config = WebhookConfig(
            type=webhook_cfg.get("type", "wechat"),
            url=webhook_cfg.get("url", ""),
            max_retries=webhook_cfg.get("max_retries", 2),
            timeout=webhook_cfg.get("timeout", 10),
        )
        notifiers.append(WebhookNotifier(wh_config))
        logger.info("component_init component=webhook_notifier type=%s", wh_config.type)

    email_cfg = config.get("notification", {}).get("email", {})
    if email_cfg.get("enabled"):
        em_config = EmailConfig(
            smtp_host=email_cfg.get("smtp_host", ""),
            smtp_port=email_cfg.get("smtp_port", 465),
            smtp_user=email_cfg.get("username", ""),
            smtp_pass=email_cfg.get("password", ""),
            to_addrs=email_cfg.get("recipients", []),
        )
        notifiers.append(EmailNotifier(em_config))
        logger.info("component_init component=email_notifier")

    # 步骤 8：Pipeline
    from vision_agent.core.camera import CameraConfig
    from vision_agent.core.pipeline import CameraConfigItem, PipelineConfig, VisionAgent

    # 摄像头配置
    camera_configs = []
    cameras_dir = Path("configs/cameras")
    if cameras_dir.exists():
        try:
            import yaml

            for yaml_file in sorted(cameras_dir.glob("*.yaml")):
                with open(yaml_file, encoding="utf-8") as f:
                    cam_data = yaml.safe_load(f)
                if not cam_data or "camera" not in cam_data:
                    continue
                cam = cam_data["camera"]
                resolution = cam.get("resolution", [640, 640])
                cam_config = CameraConfig(
                    camera_id=cam.get("id", ""),
                    camera_name=cam.get("name", ""),
                    rtsp_url=cam.get("rtsp_url", ""),
                    source_type=cam.get("source_type", "rtsp"),
                    video_path=cam.get("video_path", ""),
                    fps=cam.get("fps", 0),
                    width=resolution[0] if resolution else 640,
                    height=resolution[1] if len(resolution) > 1 else 640,
                )
                camera_configs.append(
                    CameraConfigItem(camera_config=cam_config, fps=cam.get("fps", 0))
                )
        except Exception as e:
            logger.error("camera_config_load_failed error=%s", str(e))

    if not camera_configs:
        logger.warning("no_cameras_configured")

    pipeline_config = PipelineConfig(
        frame_queue_size=config.get("pipeline", {}).get("frame_queue_size", 200),
        result_queue_size=config.get("pipeline", {}).get("result_queue_size", 100),
    )

    from vision_agent.core.recorder import RecorderConfig

    rec_cfg = config.get("recording", {})
    recorder_config = RecorderConfig(
        enabled=rec_cfg.get("enabled", True),
        buffer_duration=rec_cfg.get("buffer_duration", 30.0),
        output_dir=rec_cfg.get("output_dir", "data/clips"),
        snapshot_dir=rec_cfg.get("snapshot_dir", "data/snapshots"),
    )

    pipeline = VisionAgent(
        camera_configs=camera_configs,
        detector=detector,
        tracker_config=tracker_config,
        recorder_config=recorder_config,
        pipeline_config=pipeline_config,
        rule_engine=rule_engine,
        llm_analyzer=llm_analyzer,
        notifiers=notifiers,
        database=database,
    )
    logger.info("component_init component=pipeline cameras=%d", len(camera_configs))

    # 步骤 9：Web App
    from vision_agent.web.api.app import create_app

    web_config = config.get("web", {})
    web_app = create_app(
        database=database,
        pipeline=pipeline,
        config=web_config,
    )
    logger.info("component_init component=web_app")

    return pipeline, database, web_app, web_config


# ─── Web 服务线程 ────────────────────────────────────────────


def start_web_server(web_app: Any, web_config: dict) -> threading.Thread | None:
    """在独立线程中启动 uvicorn"""
    if web_app is None:
        return None

    try:
        import uvicorn
    except ImportError:
        logger.warning("uvicorn_not_installed action=skip_web")
        return None

    host = web_config.get("host", "0.0.0.0")
    port = web_config.get("port", 8080)

    def run_server() -> None:
        try:
            uvicorn.run(web_app, host=host, port=port, log_level="warning")
        except Exception as e:
            logger.error("web_server_error error=%s", str(e))

    thread = threading.Thread(target=run_server, name="web-server", daemon=True)
    thread.start()
    logger.info("web_server_started host=%s port=%d", host, port)
    return thread


# ─── 主函数 ──────────────────────────────────────────────────


def main() -> int:
    """主入口（main.md 3.1 节）

    Returns:
        退出码（0=正常, 1=启动失败, 2=参数错误）
    """
    args = parse_args()

    # 加载配置
    try:
        from vision_agent.config.settings import ConfigManager

        config_mgr = ConfigManager(args.config)
        config_mgr.load()
        config = config_mgr._global_config
    except FileNotFoundError:
        print(f"错误：配置文件不存在: {args.config}", file=sys.stderr)
        print("请从 configs/settings.yaml.example 复制并填写配置。", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"错误：配置加载失败: {e}", file=sys.stderr)
        return 1

    # --check 模式
    if args.check:
        print("Config OK")
        cameras_dir = Path("configs/cameras")
        cam_count = len(list(cameras_dir.glob("*.yaml"))) if cameras_dir.exists() else 0
        llm_enabled = config.get("llm", {}).get("enabled", False)
        print(f"  摄像头: {cam_count} 路")
        print(f"  LLM: {'启用' if llm_enabled else '禁用'}")
        return 0

    # 初始化日志
    setup_logging(config, args.log_level)
    logger.info("system_starting version=%s", __version__)

    # 组装组件
    try:
        pipeline, database, web_app, web_config = assemble_components(config)
    except Exception as e:
        logger.error("component_assembly_failed error=%s", str(e), exc_info=True)
        return 1

    # 注册信号处理器
    shutdown_event = threading.Event()

    def signal_handler(signum: int, frame: Any) -> None:
        logger.info("signal_received signal=%d", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动
    try:
        pipeline.start()
        start_web_server(web_app, web_config)
        logger.info("system_started cameras=%d", len(config.get("cameras", [])))
    except Exception as e:
        logger.error("system_start_failed error=%s", str(e), exc_info=True)
        return 1

    # 主循环：等待退出信号
    shutdown_event.wait()

    # 优雅关闭
    logger.info("system_stopping")
    pipeline.stop()
    database.close()
    logger.info("system_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
