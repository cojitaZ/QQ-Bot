import os
import shutil
import subprocess
from importlib import import_module
from pkgutil import iter_modules

import pytest
import tomlkit

from src.Bot import Bot

base_path = os.path.dirname(os.path.dirname(__file__))
configs_path = os.path.join(base_path, "configs")
plugins_path = os.path.join(base_path, "plugins")
plugins_config_path = os.path.join(configs_path, "plugins.toml")
plugins_template_config_path = os.path.join(configs_path, "plugins.toml.template")
groups_config_path = os.path.join(configs_path, "groups.toml")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return tomlkit.load(f).unwrap()


def get_plugin_names():
    return [name for _, name, ispkg in iter_modules([plugins_path]) if ispkg]


def get_tracked_plugin_names():
    """返回被 git 跟踪的插件包名，跳过本地未跟踪/被忽略的目录。"""
    output = subprocess.run(
        ["git", "-C", base_path, "ls-files", plugins_path],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted({line.split("/")[1] for line in output.splitlines() if line.count("/") >= 2})


@pytest.fixture(scope="session", autouse=True)
def ensure_config_files():
    """CI checkout 不含正式配置文件（/configs/*.toml 被 gitignore），从模板复制缺失项。"""
    for template in sorted(os.listdir(configs_path)):
        if not template.endswith(".toml.template"):
            continue
        config_path = os.path.join(configs_path, template[: -len(".template")])
        if not os.path.isfile(config_path):
            shutil.copyfile(os.path.join(configs_path, template), config_path)


@pytest.fixture
def bot():
    bot = Bot(configs_path=configs_path, plugins_path=plugins_path)
    bot.database_enable = False
    return bot


class Test:
    def test_bot_created(self, bot):
        """Bot 实例正常创建"""
        assert bot is not None

    def test_config_exists(self):
        """配置文件应存在"""
        assert os.path.exists(plugins_config_path), f"插件配置文件不存在: {plugins_config_path}"
        assert os.path.exists(plugins_template_config_path), (
            f"插件模板配置文件不存在: {plugins_template_config_path}"
        )
        assert os.path.exists(groups_config_path), f"群聊配置文件不存在: {groups_config_path}"

    def test_all_plugins_have_template_section(self):
        """所有被 git 跟踪的插件包都应在 plugins.toml.template 中声明 section"""
        template_config = load_config(plugins_template_config_path)

        plugin_packages = get_tracked_plugin_names()
        missing_sections = [name for name in plugin_packages if name not in template_config]

        assert plugin_packages, "未找到任何插件包"
        assert not missing_sections, (
            f"以下插件未在 plugins.toml.template 中声明: {missing_sections}"
        )

    @pytest.mark.parametrize("plugin_name", get_plugin_names())
    def test_plugin_importable(self, bot, plugin_name):
        """所有插件包（含未启用）应能被正常导入和实例化"""

        plugins_config = load_config(plugins_config_path)

        plugin_module = import_module(f".{plugin_name}", "plugins")
        PluginClass = getattr(plugin_module, plugin_name)
        plugin_instance = PluginClass(bot)
        if plugin_name in plugins_config:
            plugin_instance.config = plugins_config[plugin_name]

        assert plugin_instance is not None
