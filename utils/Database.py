# 数据库连接相关工具

import os
import sys

import tomlkit
from sqlalchemy.engine import URL


def build_database_url(
    database_username: str,
    database_passwd: str,
    database_address: str,
    database_name: str,
    use_async: bool = True,
) -> URL:
    """根据 bot.toml 的 [Init] 配置构造 PostgreSQL 连接 URL，默认异步。

    使用 ``URL.create`` 构造连接串，用户名和密码会由 SQLAlchemy 自动做 URL 转义，
    无需调用方手动处理含 ``@``、``:`` 等特殊字符的密码。
    """
    host, port = _split_address(database_address)
    return URL.create(
        "postgresql+asyncpg" if use_async else "postgresql",
        username=database_username,
        password=database_passwd,
        host=host,
        port=port,
        database=database_name,
    )


def _split_address(address: str) -> tuple[str, int | None]:
    """将 ``host:port`` 拆分为 ``(host, port)``；不含端口时返回 ``(address, None)``。"""
    if ":" in address:
        host, _, port = address.rpartition(":")
        if port.isdigit():
            return host, int(port)
    return address, None


def load_database_url() -> URL:
    """从 configs/bot.toml 读取数据库配置并构造同步连接 URL，仅供运行脚本使用"""
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "configs", "bot.toml"
    )
    if not os.path.exists(config_path):
        sys.exit(f"找不到配置文件：{config_path}")

    with open(config_path, encoding="utf-8") as f:
        init_config = tomlkit.load(f).unwrap().get("Init", {})

    if not init_config.get("database_enable", False):
        sys.exit("bot.toml 中 database_enable 为 false，请先启用数据库再建表")

    keys = {
        "database_username": init_config.get("database_username"),
        "database_address": init_config.get("database_address"),
        "database_passwd": init_config.get("database_passwd"),
        "database_name": init_config.get("database_name"),
    }
    missing = [key for key, value in keys.items() if not value]
    if missing:
        sys.exit(f"bot.toml 缺少以下数据库配置项：{', '.join(missing)}")

    # 使用 URL.create 构造连接串，用户名和密码会由 SQLAlchemy 自动做 URL 转义
    return build_database_url(**keys, use_async=False)
