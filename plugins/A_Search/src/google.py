# google类是纯粹的vibe coding，我不会写playwright
# 没有加在依赖里 如果要使用请先执行以下两行
# uv pip install playwright
# uv run python -m playwright install chromium
# 同时运行时会创建一个缓存目录
import os
import queue
import re
import threading
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from . import Search


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


class Google(Search):
    """
    Google reverse image search is intentionally kept optional.

    Useful environment variables:
    - GOOGLE_BROWSER_EXECUTABLE: path to Chrome for Testing chrome.exe
    - GOOGLE_BROWSER_CHANNEL: chrome, chrome-beta, chrome-dev, chrome-canary
    - GOOGLE_PROXY: proxy passed to Playwright, default system.
      Use http://127.0.0.1:7897 to force a port, or none/direct to disable proxy.
    - GOOGLE_HEADLESS: 0/1, default 0 because Google Lens is less reliable headless
    - GOOGLE_USER_DATA_DIR: a dedicated automation profile directory
    - GOOGLE_DISABLE_QUIC: 0/1, default 1 to keep Google traffic on proxy-friendly HTTP/TLS
    """

    def __init__(self, start_browser: bool = True):
        """初始化 Google 搜图引擎，并按配置决定是否立即启动浏览器。"""
        super().__init__()
        self.name = "Google"
        self.base_introduction = "谷歌以图搜图,结果多为网页匹配"
        self.Introduction = self.base_introduction
        self.special_intro = "啊哦,无结果,可能是 Google 验证、请求过频或图片无匹配"
        self.gemini_analysis = ""
        self.html_data = b""
        self.last_error = ""
        self.last_url = ""

        project_root = Path(__file__).resolve().parents[1]
        self.browser_executable = os.getenv("GOOGLE_BROWSER_EXECUTABLE")
        self.browser_channel = os.getenv("GOOGLE_BROWSER_CHANNEL")
        self.proxy = os.getenv("GOOGLE_PROXY", "system").strip()
        self.headless = env_bool("GOOGLE_HEADLESS", False)
        self.timeout_ms = int(os.getenv("GOOGLE_TIMEOUT_MS", "45000"))
        self.user_data_dir = Path(
            os.getenv("GOOGLE_USER_DATA_DIR", project_root / ".cache" / "google-automation-profile")
        )
        self._playwright = None
        self._context = None
        self._page = None
        self._task_queue = None
        self._browser_thread = None
        self._browser_start_error = ""

        if start_browser:
            try:
                self.start_browser()
            except Exception as exc:
                self._browser_start_error = str(exc)

    def parse_result(self):
        """解析 Google 结果页 HTML，填充 result_list、max_similarity 和 debug_info。"""
        html = self._html_text()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        self.debug_info["last_url"] = self.last_url
        self.debug_info["html_title"] = soup.title.get_text(strip=True) if soup.title else ""
        self.debug_info["anchor_count"] = len(soup.select("a[href]"))
        self.debug_info["proxy"] = self.proxy or "system"
        exact_result_count = len(soup.select("a.ngTNl.ggLgoc[href]"))
        is_exact_page = (
            exact_result_count > 0
            or self._is_exact_matches_url(self.last_url)
            or self._has_active_exact_tab(soup)
        )
        self.debug_info["exact_result_count"] = exact_result_count
        self.debug_info["is_exact_matches_page"] = is_exact_page
        lens_result_count = len(soup.select("a.LBcIee[href]"))
        self.debug_info["lens_result_count"] = lens_result_count
        content_guidelines = self._is_content_guidelines_page(page_text)
        self.debug_info["content_guidelines"] = content_guidelines
        visual_search_expired = self._is_visual_search_expired_page(page_text)
        self.debug_info["visual_search_expired"] = visual_search_expired
        self.debug_info["zero_results"] = (
            exact_result_count == 0 and lens_result_count == 0 and self._is_zero_results_page(page_text)
        )

        self.result_list = []
        self.max_similarity = 0

        if self._looks_like_google_challenge(html, page_text):
            self.debug_info["no_result_reason"] = "google_challenge"
            self.last_error = "Google 返回了验证页或 JS cookie 挑战，未拿到搜索结果"
            return

        if content_guidelines:
            self.debug_info["no_result_reason"] = "content_guidelines"
            return

        if visual_search_expired:
            self.debug_info["no_result_reason"] = "visual_search_expired"
            self.last_error = "Google Lens 视觉搜索内容已过期"
            return

        if exact_result_count > 0:
            self._parse_exact_match_cards(soup)
            self.max_similarity = self.result_list[0]["similarity"] if self.result_list else 0
            if not self.result_list:
                self.debug_info["no_result_reason"] = "exact_cards_unparsed"
            return

        if is_exact_page:
            self.debug_info["no_result_reason"] = "exact_matches_empty"
            return

        if self.debug_info.get("exact_required"):
            self.debug_info["no_result_reason"] = "exact_matches_unavailable"
            return

        self._parse_best_guess(soup)
        result_count_before_lens = len(self.result_list)
        self._parse_lens_result_cards(soup)
        if len(self.result_list) == result_count_before_lens:
            self._parse_standard_results(soup)
            self._parse_lens_like_links(soup)

        if self.result_list:
            self.max_similarity = self.result_list[0]["similarity"]

    def search(self):
        """执行一次完整的 Google 以图搜图，并把浏览器结果交给解析流程。"""
        self.reset_results()
        self.Introduction = self.base_introduction
        self.gemini_analysis = ""
        self.last_url = ""

        try:
            self.html_data = self._search_with_browser()
        except Exception as exc:
            self.last_error = f"Google 浏览器搜索失败: {exc}"
            return

        self.parse_result()

    def _search_with_browser(self) -> bytes:
        """校验本地图片路径，并把实际搜索任务投递到浏览器线程。"""
        image_path = Path(self.img_url).expanduser().resolve()
        if not image_path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")

        return self._run_in_browser_thread(self._search_with_page, image_path)

    def _search_with_page(self, image_path: Path) -> bytes:
        """在 Playwright 页面中上传图片、切到精确匹配页，并返回最终 HTML。"""
        page = self._ensure_worker_page()
        max_attempts = self._visual_search_max_attempts()
        last_content = b""

        for attempt in range(max_attempts):
            if attempt:
                self.debug_info["visual_search_retry_count"] = attempt
                try:
                    page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                except Exception:
                    pass

            self._upload_image(page, image_path)
            initial_state = self._wait_for_result_page(page)
            self.debug_info["exact_required"] = True
            if initial_state == "content_guidelines":
                self.last_url = page.url
                return page.content().encode("utf-8", errors="ignore")
            if initial_state != "visual_search_expired":
                self._capture_gemini_analysis(page)

            self._switch_to_exact_matches(page)
            self._wait_for_exact_terminal_state(page)
            self.last_url = page.url
            last_content = page.content().encode("utf-8", errors="ignore")

            if not self._html_has_visual_search_expired(last_content):
                return last_content

            self.debug_info["visual_search_expired_attempts"] = attempt + 1

        return last_content

    def _capture_gemini_analysis(self, page):
        """在 Lens 初始页切 Exact 前，等待并提取 AI/Gemini 图像分析文本。"""
        wait_ms = int(os.getenv("GOOGLE_GEMINI_WAIT_MS", "8000"))
        analysis = ""

        if wait_ms > 0:
            try:
                handle = page.wait_for_function(
                    """
                    () => {
                        const clean = (text) => (text || "").replace(/\\s+/g, " ").trim();
                        const cleanPart = (text) => {
                            return clean(text)
                                .replace(/\\s*(Instagram|Reddit|Pinterest|pixiv|X|Twitter|YouTube|Facebook)?\\s*\\+\\d+\\s*$/i, "")
                                .trim();
                        };
                        const fromRoot = (root) => {
                            if (!root) return "";
                            const parts = [];
                            const items = root.querySelectorAll("li.Z1qcYe, ul.KsbFXc li, ul.U6u95 li");
                            for (const item of items) {
                                const textNodes = item.querySelectorAll(".T286Pc");
                                const raw = textNodes.length
                                    ? Array.from(textNodes).map((node) => clean(node.innerText || node.textContent)).join(" ")
                                    : cleanPart(item.innerText || item.textContent);
                                const text = cleanPart(raw);
                                if (text && !parts.includes(text)) parts.push(text);
                            }
                            return parts.join(" ");
                        };

                        const headingLabels = new Set(["AI 概览", "AI Overview"]);
                        const headings = Array.from(document.querySelectorAll("[role='heading'], h1, h2, h3, div, span"))
                            .filter((node) => headingLabels.has(clean(node.innerText || node.textContent)));
                        for (const heading of headings) {
                            let root = heading;
                            for (let depth = 0; root && depth < 10; depth += 1, root = root.parentElement) {
                                const text = fromRoot(root);
                                if (text) return text;
                            }
                        }

                        return fromRoot(document.querySelector("ul.KsbFXc, ul.U6u95"));
                    }
                    """,
                    timeout=wait_ms,
                )
                analysis = handle.json_value() or ""
            except Exception:
                analysis = ""

        if not analysis:
            try:
                analysis = self._extract_gemini_analysis_from_html(page.content())
            except Exception:
                analysis = ""

        self._set_gemini_analysis(analysis)

    def start_browser(self):
        """启动或复用后台浏览器线程，保证后续任务可以串行投递执行。"""
        if self._browser_thread and self._browser_thread.is_alive():
            return

        self.close()
        self._task_queue = queue.Queue()
        ready_queue = queue.Queue(maxsize=1)
        self._browser_thread = threading.Thread(
            target=self._browser_worker,
            args=(ready_queue, self._task_queue),
            daemon=True,
            name="GoogleSearchBrowser",
        )
        self._browser_thread.start()

        try:
            ok, payload = ready_queue.get(timeout=max(30, self.timeout_ms / 1000 + 15))
        except queue.Empty as exc:
            self.close()
            raise RuntimeError("Google 浏览器启动超时") from exc

        if not ok:
            self.close()
            raise payload

    def _browser_worker(self, ready_queue, task_queue):
        """浏览器线程入口：创建 Playwright 上下文，并循环处理搜索任务。"""
        from playwright.sync_api import sync_playwright

        ready_sent = False
        try:
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir),
                **self._launch_options(),
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            self._browser_start_error = ""
            ready_queue.put((True, None))
            ready_sent = True

            while True:
                task = task_queue.get()
                if task is None:
                    break

                func, args, kwargs, response_queue = task
                try:
                    response_queue.put((True, func(*args, **kwargs)))
                except Exception as exc:
                    response_queue.put((False, exc))
        except Exception as exc:
            if not ready_sent:
                ready_queue.put((False, exc))
        finally:
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass

            self._page = None
            self._context = None
            self._playwright = None

    def _launch_options(self):
        """根据环境变量组装 Playwright 启动参数，包括代理、浏览器和无头模式。"""
        launch_options = {
            "headless": self.headless,
            "locale": "zh-CN",
            "viewport": {"width": 1365, "height": 900},
            "args": [
                "--lang=zh-CN",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if env_bool("GOOGLE_DISABLE_QUIC", True):
            launch_options["args"].append("--disable-quic")

        if self.browser_executable:
            launch_options["executable_path"] = self.browser_executable
        elif self.browser_channel:
            launch_options["channel"] = self.browser_channel

        proxy_mode = self.proxy.lower()
        if proxy_mode in {"none", "direct"}:
            launch_options["args"].append("--no-proxy-server")
        elif proxy_mode and proxy_mode != "system":
            launch_options["proxy"] = {"server": self.proxy}

        return launch_options

    def close(self):
        """关闭浏览器线程和 Playwright 资源，释放当前页面状态。"""
        browser_thread = self._browser_thread
        task_queue = self._task_queue

        if (
            task_queue
            and browser_thread
            and browser_thread.is_alive()
            and threading.current_thread() is not browser_thread
        ):
            try:
                task_queue.put(None)
                browser_thread.join(timeout=10)
            except Exception:
                pass

        self._page = None
        self._context = None
        self._playwright = None
        self._task_queue = None
        self._browser_thread = None

    def __del__(self):
        """对象被回收时尽量关闭浏览器资源，避免后台线程残留。"""
        try:
            self.close()
        except Exception:
            pass

    def _run_in_browser_thread(self, func, *args, **kwargs):
        """把同步 Playwright 操作提交到浏览器线程，并等待结果或异常。"""
        self.start_browser()

        response_queue = queue.Queue(maxsize=1)
        self._task_queue.put((func, args, kwargs, response_queue))

        try:
            ok, payload = response_queue.get(timeout=self._browser_task_timeout_seconds())
        except queue.Empty as exc:
            raise RuntimeError("Google 浏览器任务超时") from exc

        if ok:
            return payload
        raise payload

    def _browser_task_timeout_seconds(self) -> float:
        """计算单个浏览器任务的超时时间，兼容重试次数配置。"""
        configured = os.getenv("GOOGLE_TASK_TIMEOUT_MS")
        if configured:
            try:
                return max(60, int(configured) / 1000)
            except ValueError:
                pass

        return max(60, (self.timeout_ms / 1000 + 60) * self._visual_search_max_attempts())

    def _ensure_worker_page(self):
        """确保浏览器线程中存在可用页面，页面关闭时自动新建。"""
        if self._page and not self._page.is_closed():
            return self._page

        if not self._context:
            raise RuntimeError("Google 浏览器实例未初始化")

        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self._page

    def _upload_image(self, page, image_path: Path):
        """尝试从多个 Google 入口进入搜图流程，并上传指定图片。"""
        entry_urls = [
            "https://lens.google.com/?hl=zh-CN",
            "https://www.google.com/imghp?hl=zh-CN",
            "https://images.google.com/?hl=zh-CN",
        ]
        last_error = None

        for entry_url in entry_urls:
            try:
                page.goto(entry_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._accept_google_consent(page)

                if self._set_file_input(page, image_path, timeout=8000):
                    return

                self._click_image_search_button(page)
                self._click_upload_tab(page)

                if self._set_file_input(page, image_path, timeout=15000):
                    return
            except Exception as exc:
                last_error = exc

        detail = f": {last_error}" if last_error else ""
        raise RuntimeError(f"找不到 Google Lens 的图片上传控件{detail}")

    def _accept_google_consent(self, page):
        """处理 Google 首次访问可能出现的同意/拒绝 Cookie 弹窗。"""
        selectors = [
            "button:has-text('全部接受')",
            "button:has-text('接受全部')",
            "button:has-text('I agree')",
            "button:has-text('Accept all')",
            "button:has-text('Reject all')",
        ]

        for selector in selectors:
            try:
                button = page.locator(selector).first
                button.click(timeout=1500)
                return
            except Exception:
                continue

    def _set_file_input(self, page, image_path: Path, timeout: int) -> bool:
        """寻找文件上传控件并设置图片文件，成功时返回 True。"""
        selectors = [
            "input[type='file'][name='encoded_image']",
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ]

        for selector in selectors:
            try:
                file_input = page.locator(selector).first
                file_input.wait_for(state="attached", timeout=timeout)
                file_input.set_input_files(str(image_path))
                self._submit_upload_form(file_input)
                return True
            except Exception:
                continue

        return False

    def _submit_upload_form(self, file_input):
        """触发表单 input/change 事件并提交上传表单。"""
        try:
            file_input.evaluate(
                """
                input => {
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));

                    const form = input.form;
                    if (!form || !form.action) return false;

                    if (typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                    } else {
                        HTMLFormElement.prototype.submit.call(form);
                    }
                    return true;
                }
                """
            )
        except Exception:
            pass

    def _click_image_search_button(self, page):
        """点击“按图搜索”入口按钮，兼容中英文界面。"""
        selectors = [
            "[aria-label*='Search by image']",
            "[aria-label*='按图搜索']",
            "[aria-label*='以图搜']",
            "[title*='Search by image']",
            "[title*='按图搜索']",
            "div[role='button']:has-text('Search by image')",
            "div[role='button']:has-text('按图搜索')",
        ]

        for selector in selectors:
            try:
                target = page.locator(selector).first
                target.click(timeout=2500)
                return True
            except Exception:
                continue

        return False

    def _click_upload_tab(self, page):
        """点击 Google Lens 上传文件标签页，兼容中英文界面。"""
        selectors = [
            "text=上传文件",
            "text=上传图片",
            "text=Upload a file",
            "text=Upload image",
        ]

        for selector in selectors:
            try:
                target = page.locator(selector).first
                target.click(timeout=2500)
                return True
            except Exception:
                continue

        return False

    def _wait_for_result_page(self, page):
        """等待页面进入 Google Lens 或图片搜索结果状态。"""
        for state, timeout in (("domcontentloaded", 15000), ("networkidle", 20000)):
            try:
                page.wait_for_load_state(state, timeout=timeout)
            except Exception:
                pass

        for _ in range(20):
            if self._is_lens_result_url(page.url):
                return self._wait_for_lens_terminal_state(page)
            page.wait_for_timeout(1000)

        selectors = [
            "text=Exact matches",
            "text=完全匹配",
            "text=视觉匹配",
            "text=Visual matches",
            "text=About this image",
            "a.ngTNl.ggLgoc[href]",
            "a.LBcIee[href]",
            "a[href*='/imgres?']",
            "a[href*='imgrefurl=']",
        ]

        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(timeout=5000)
                return selector
            except Exception:
                continue

        if self._is_lens_result_url(page.url):
            return self._wait_for_lens_terminal_state(page)

        self.last_url = page.url
        raise RuntimeError(f"Google 未进入结果页，当前 URL: {page.url}")

    def _wait_for_lens_terminal_state(self, page):
        """等待 Lens 初始结果页达到可解析、过期或内容限制等终态。"""
        result_wait_ms = int(os.getenv("GOOGLE_RESULT_WAIT_MS", "45000"))
        try:
            handle = page.wait_for_function(
                """
                () => {
                    const text = document.body ? document.body.innerText : "";
                    if (
                        text.includes("Visual search content has expired") ||
                        text.includes("This visual search content has expired") ||
                        text.includes("视觉搜索内容已过期") ||
                        text.includes("视觉搜索内容已经过期") ||
                        text.includes("视觉搜索已过期")
                    ) {
                        return "visual_search_expired";
                    }

                    if (
                        text.includes("This search can't be processed due to content guidelines") ||
                        text.includes("Please try a different image or keywords") ||
                        text.includes("content guidelines") ||
                        text.includes("抱歉，我不支持解答此类请求") ||
                        text.includes("我不支持解答此类请求")
                    ) {
                        return "content_guidelines";
                    }

                    const exactResultCount = document.querySelectorAll("a.ngTNl.ggLgoc[href]").length;
                    if (exactResultCount > 0) return "exact_results";

                    const lensResultCount = document.querySelectorAll("a.LBcIee[href]").length;
                    if (lensResultCount > 0) return "lens_results";

                    const exactTab = Array.from(document.querySelectorAll("a")).some((link) => {
                        const label = link.innerText || link.textContent || "";
                        return (
                            label.includes("Exact matches") ||
                            label.includes("完全匹配")
                        );
                    });
                    if (exactTab) return "exact_tab";

                    return false;
                }
                """,
                timeout=result_wait_ms,
            )
            state = handle.json_value()
            if state:
                self.debug_info["lens_wait_state"] = state
            return state
        except Exception:
            return ""

    def _switch_to_exact_matches(self, page):
        """从 Lens 结果页切换到 Exact matches 页面，失败时记录调试标记。"""
        if self._is_exact_matches_url(page.url):
            return

        try:
            tab_state = page.evaluate(
                """
                () => {
                    const labels = ["Exact matches", "完全匹配"];
                    const links = Array.from(document.querySelectorAll("a"));
                    const link = links.find((candidate) => {
                        const text = candidate.innerText || candidate.textContent || "";
                        return labels.some((label) => text.includes(label));
                    });
                    if (!link) return { href: "", active: false };

                    const active = (
                        link.getAttribute("aria-current") === "page" ||
                        Boolean(link.querySelector("[aria-current='page']")) ||
                        Boolean(link.closest("[aria-current='page']")) ||
                        !link.getAttribute("href")
                    );
                    return { href: link.href || link.getAttribute("href") || "", active };
                }
                """
            )
        except Exception:
            tab_state = {}

        if tab_state.get("active"):
            return

        selectors = [
            "a.C6AK7c:has-text('Exact matches')",
            "a.C6AK7c:has-text('完全匹配')",
            "a:has-text('Exact matches')",
            "a:has-text('完全匹配')",
        ]
        for selector in selectors:
            try:
                tab = page.locator(selector).first
                tab.click(timeout=3000)
                for state, timeout in (("domcontentloaded", 10000), ("networkidle", 15000)):
                    try:
                        page.wait_for_load_state(state, timeout=timeout)
                    except Exception:
                        pass
                return
            except Exception:
                continue

        href = tab_state.get("href") or ""
        if href:
            try:
                page.goto(href, wait_until="domcontentloaded", timeout=self.timeout_ms)
                return
            except Exception:
                pass

        self.debug_info["exact_tab_missing"] = True

    def _wait_for_exact_terminal_state(self, page):
        """等待精确匹配页出现结果、空结果或限制页等终态。"""
        result_wait_ms = int(os.getenv("GOOGLE_RESULT_WAIT_MS", "45000"))
        try:
            handle = page.wait_for_function(
                """
                () => {
                    const text = document.body ? document.body.innerText : "";
                    if (
                        text.includes("Visual search content has expired") ||
                        text.includes("This visual search content has expired") ||
                        text.includes("视觉搜索内容已过期") ||
                        text.includes("视觉搜索内容已经过期") ||
                        text.includes("视觉搜索已过期")
                    ) {
                        return "visual_search_expired";
                    }

                    if (
                        text.includes("This search can't be processed due to content guidelines") ||
                        text.includes("Please try a different image or keywords") ||
                        text.includes("content guidelines") ||
                        text.includes("抱歉，我不支持解答此类请求") ||
                        text.includes("我不支持解答此类请求")
                    ) {
                        return "content_guidelines";
                    }

                    const exactResultCount = document.querySelectorAll("a.ngTNl.ggLgoc[href]").length;
                    if (exactResultCount > 0) return "exact_results";

                    const labels = ["Exact matches", "完全匹配"];
                    const exactTabs = Array.from(document.querySelectorAll("a")).filter((link) => {
                        const label = link.innerText || link.textContent || "";
                        return labels.some((candidate) => label.includes(candidate));
                    });
                    const activeExact = exactTabs.some((link) => {
                        return (
                            link.getAttribute("aria-current") === "page" ||
                            Boolean(link.querySelector("[aria-current='page']")) ||
                            Boolean(link.closest("[aria-current='page']")) ||
                            !link.getAttribute("href")
                        );
                    });
                    if (!activeExact) return false;

                    const resultStats = document.querySelector("#result-stats")?.innerText || "";
                    const combined = `${text} ${resultStats}`;
                    if (
                        /(^|\\D)0\\s+results/i.test(combined) ||
                        combined.includes("No results found") ||
                        combined.includes("找到约 0 条结果") ||
                        combined.includes("没有与您的搜索相匹配的内容") ||
                        combined.includes("没有找到") ||
                        combined.includes("找不到") ||
                        combined.includes("未找到")
                    ) {
                        return "exact_empty";
                    }

                    return false;
                }
                """,
                timeout=result_wait_ms,
            )
            state = handle.json_value()
            if state:
                self.debug_info["exact_wait_state"] = state
            return state
        except Exception:
            return ""

    def _is_lens_result_url(self, url: str) -> bool:
        """判断 URL 是否像 Google Lens 的视觉搜索结果页。"""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        udm_values = set(query.get("udm", []))
        return "vsrid" in query or bool(udm_values & {"26", "44", "48"})

    def _is_exact_matches_url(self, url: str) -> bool:
        """判断 URL 是否指向 Google 的 Exact matches 结果页。"""
        parsed = urlparse(url or "")
        query = parse_qs(parsed.query)
        return "48" in query.get("udm", [])

    def _has_active_exact_tab(self, soup: BeautifulSoup) -> bool:
        """从 HTML 中判断 Exact matches 标签是否处于激活状态。"""
        labels = ("Exact matches", "完全匹配")
        for text_node in soup.find_all(string=lambda value: value and any(label in value for label in labels)):
            node = text_node.parent
            for _ in range(6):
                if not node:
                    break
                if node.get("aria-current") == "page":
                    return True
                if node.name == "a" and "C6AK7c" in (node.get("class") or []) and not node.get("href"):
                    return True
                node = node.parent
        return False

    def _html_text(self) -> str:
        """把 html_data 统一转换成字符串，方便 BeautifulSoup 和文本检测使用。"""
        if isinstance(self.html_data, bytes):
            return self.html_data.decode("utf-8", errors="ignore")
        return str(self.html_data or "")

    def _visual_search_max_attempts(self) -> int:
        """读取视觉搜索过期重试次数，并转换成最大尝试次数。"""
        try:
            retries = int(os.getenv("GOOGLE_EXPIRED_RETRIES", "1"))
        except ValueError:
            retries = 1
        return max(1, retries + 1)

    def _html_has_visual_search_expired(self, html_data: bytes | str) -> bool:
        """检查 HTML 内容是否表示 Google Lens 视觉搜索结果已过期。"""
        if isinstance(html_data, bytes):
            html = html_data.decode("utf-8", errors="ignore")
        else:
            html = str(html_data or "")

        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        return self._is_visual_search_expired_page(page_text)

    def _set_gemini_analysis(self, analysis: str):
        analysis = self._clean_gemini_analysis(analysis)
        if not analysis:
            self.debug_info["gemini_analysis_found"] = False
            return

        self.gemini_analysis = analysis
        self.debug_info["gemini_analysis_found"] = True
        self.debug_info["gemini_analysis"] = analysis
        self.Introduction = f"{self.base_introduction}\ngemini分析结果：{analysis}"

    def _extract_gemini_analysis_from_html(self, html_data: bytes | str) -> str:
        """从保存下来的 Lens 初始页 HTML 中提取 AI/Gemini 图像分析。"""
        if isinstance(html_data, bytes):
            html = html_data.decode("utf-8", errors="ignore")
        else:
            html = str(html_data or "")

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "svg"]):
            tag.decompose()

        for root in soup.select("ul.KsbFXc, ul.U6u95"):
            parts = self._gemini_parts_from_root(root)
            if parts:
                return self._clean_gemini_analysis(" ".join(parts))

        text_parts = [
            self._clean_text(str(text_node))
            for text_node in soup.find_all(string=True)
            if self._clean_text(str(text_node))
        ]
        return self._gemini_analysis_from_text_parts(text_parts)

    def _gemini_parts_from_root(self, root) -> list[str]:
        parts = []
        items = root.select("li.Z1qcYe") or root.select("li")
        for item in items:
            text_nodes = item.select(".T286Pc")
            if text_nodes:
                raw_text = " ".join(node.get_text(" ", strip=True) for node in text_nodes)
            else:
                raw_text = item.get_text(" ", strip=True)

            text = self._clean_gemini_analysis_part(raw_text)
            if text and text not in parts:
                parts.append(text)
        return parts

    def _gemini_analysis_from_text_parts(self, text_parts: list[str]) -> str:
        headings = {"AI 概览", "AI Overview"}
        skip_markers = (
            "无法针对此搜索生成",
            "目前无法生成",
            "AI 概览",
            "AI Overview",
        )
        stop_markers = (
            "5 个网站",
            "个网站",
            "搜索结果",
            "Search results",
            "完全匹配",
            "Exact matches",
            "外观匹配",
            "Visual matches",
        )

        for index, text in enumerate(text_parts):
            if text not in headings:
                continue

            parts = []
            for candidate in text_parts[index + 1 :]:
                candidate = self._clean_gemini_analysis_part(candidate)
                if not candidate:
                    continue
                if any(candidate.startswith(marker) for marker in skip_markers):
                    continue
                if any(marker in candidate for marker in stop_markers):
                    break
                if re.match(r"^\d{4}年\d{1,2}月\d{1,2}日", candidate):
                    break
                if candidate in parts:
                    continue

                parts.append(candidate)
                if len(parts) >= 6:
                    break

            analysis = self._clean_gemini_analysis(" ".join(parts))
            if analysis:
                return analysis

        return ""

    def _clean_gemini_analysis_part(self, text: str) -> str:
        text = self._clean_text(text).replace("\u200b", "")
        text = re.sub(
            r"\s*(Instagram|Reddit|Pinterest|pixiv|X|Twitter|YouTube|Facebook)?\s*\+\d+\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip()

    def _clean_gemini_analysis(self, text: str) -> str:
        text = self._clean_text(text).replace("\u200b", "")
        try:
            max_chars = int(os.getenv("GOOGLE_GEMINI_MAX_CHARS", "500"))
        except ValueError:
            max_chars = 500

        if max_chars > 0 and len(text) > max_chars:
            return f"{text[:max_chars].rstrip()}..."
        return text

    def _parse_best_guess(self, soup: BeautifulSoup):
        """解析 Google 页面上的“最佳猜测”文本并作为低置信度结果加入。"""
        best_guess = soup.select_one("a.VkpGBb, a.IawNd-BaJUVc")
        if not best_guess:
            return

        guess_text = self._clean_text(best_guess.get_text(" ", strip=True))
        if not guess_text:
            return

        self._append_result(
            similarity=0.0,
            title=f"最佳猜测: {guess_text}",
            img_url="",
            origin_url="",
        )

    def _parse_standard_results(self, soup: BeautifulSoup):
        """解析传统 Google 搜索结果块，作为 Lens 卡片解析失败后的兜底。"""
        for block in soup.select("div.g, div[data-sokoban-container]"):
            title_elem = block.select_one("h3")
            link_elem = block.select_one("a[href^='http'], a[href^='/url']")
            snippet = block.select_one("span.st, div.VwiC3b, span.aCOpRe")

            if not title_elem or not link_elem:
                continue

            title = self._clean_text(title_elem.get_text(" ", strip=True))
            href = self._clean_google_href(link_elem.get("href", ""))
            snippet_text = self._clean_text(snippet.get_text(" ", strip=True)) if snippet else ""

            self._append_result(
                similarity=0.0,
                title=title,
                img_url="",
                origin_url=href,
                snippet=snippet_text,
            )

    def _parse_exact_match_cards(self, soup: BeautifulSoup):
        """解析 Exact matches 页面中的匹配卡片。"""
        for link in soup.select("a.ngTNl.ggLgoc[href]"):
            href = self._clean_google_href(link.get("href", ""))
            if not href or self._is_noise_href(href):
                continue

            title = self._title_from_exact_card(link, href)
            if not title:
                continue

            self._append_result(
                similarity=0.0,
                title=title,
                img_url=self._preview_from_lens_card(link),
                origin_url=href,
                source=self._source_from_exact_card(link, href),
                match_type="exact",
            )

    def _parse_lens_result_cards(self, soup: BeautifulSoup):
        """解析 Google Lens 视觉匹配卡片。"""
        for link in soup.select("a.LBcIee[href]"):
            href = self._clean_google_href(link.get("href", ""))
            if not href or self._is_noise_href(href):
                continue

            title = self._title_from_lens_card(link)
            if not title:
                continue

            source = self._source_from_lens_card(link)
            self._append_result(
                similarity=0.0,
                title=title,
                img_url=self._preview_from_lens_card(link),
                origin_url=href,
                source=source,
            )

    def _parse_lens_like_links(self, soup: BeautifulSoup):
        """兜底遍历页面链接，提取看起来像搜索结果的外部链接。"""
        for link in soup.select("a[href]"):
            raw_href = link.get("href", "")
            href, img_url = self._extract_result_urls(raw_href)

            if not href or self._is_noise_href(href):
                continue

            title = self._title_from_link(link, href)
            if not title:
                continue

            self._append_result(
                similarity=0.0,
                title=title,
                img_url=img_url,
                origin_url=href,
            )

    def _append_result(
        self,
        similarity: float,
        title: str,
        img_url: str,
        origin_url: str,
        snippet: str = "",
        **extra,
    ):
        """规范化并去重后，把一个搜索结果追加到 result_list。"""
        normalized_origin = self._normalize_result_key(origin_url)
        if normalized_origin and any(
            self._normalize_result_key(item.get("origin_url", "")) == normalized_origin
            for item in self.result_list
        ):
            return

        result = {
            "similarity": similarity,
            "title": title,
            "img_url": img_url,
            "origin_url": origin_url,
        }
        if snippet:
            result["snippet"] = snippet
        for key, value in extra.items():
            if value:
                result[key] = value
        self.result_list.append(result)

    def _extract_result_urls(self, href: str) -> tuple[str, str]:
        """从 Google 跳转链接中提取原始页面 URL 和预览图片 URL。"""
        cleaned = self._clean_google_href(href)
        parsed = urlparse(cleaned)
        query = parse_qs(parsed.query)

        if parsed.netloc.endswith("google.com") and parsed.path == "/imgres":
            origin_url = query.get("imgrefurl", [""])[0]
            img_url = query.get("imgurl", [""])[0]
            return origin_url, img_url

        return cleaned, ""

    def _clean_google_href(self, href: str) -> str:
        """把 Google 相对链接或 /url 跳转链接还原为可访问的绝对 URL。"""
        if not href:
            return ""

        href = href.strip()
        absolute_href = urljoin("https://www.google.com", href)
        parsed = urlparse(absolute_href)
        query = parse_qs(parsed.query)

        if parsed.netloc.endswith("google.com") and parsed.path == "/url":
            for key in ("url", "q"):
                if query.get(key):
                    return query[key][0]

        return absolute_href

    def _title_from_link(self, link, href: str) -> str:
        """按属性、标题节点、文本、域名的顺序为普通链接提取标题。"""
        for attr in ("aria-label", "title"):
            value = link.get(attr)
            if value:
                return self._clean_text(value)

        heading = link.select_one("h3, [role='heading']")
        if heading:
            text = self._clean_text(heading.get_text(" ", strip=True))
            if text:
                return text

        text = self._clean_text(link.get_text(" ", strip=True))
        if text:
            return text[:180]

        parsed = urlparse(href)
        return parsed.netloc

    def _title_from_lens_card(self, link) -> str:
        """从 Lens 卡片中提取标题，并剔除可能混入的来源文本。"""
        title_elem = link.select_one(".Yt787, [role='heading'], h3")
        if title_elem:
            title = self._clean_text(title_elem.get_text(" ", strip=True))
            if title:
                return title

        text = self._clean_text(link.get_text(" ", strip=True))
        source = self._source_from_lens_card(link)
        if source and text.startswith(source):
            text = text[len(source):].strip()
        return text[:180]

    def _title_from_exact_card(self, link, href: str) -> str:
        """从 Exact matches 卡片中提取标题，并剔除末尾来源文本。"""
        title_elem = link.select_one(".dctkEf, .ZhosBf, [role='heading'], h3")
        if title_elem:
            title = self._clean_text(title_elem.get_text(" ", strip=True))
            if title:
                return title

        text = self._clean_text(link.get_text(" ", strip=True))
        source = self._source_from_exact_card(link, href)
        if source and text.endswith(source):
            text = text[: -len(source)].strip()
        return text[:180]

    def _source_from_lens_card(self, link) -> str:
        """从 Lens 卡片中提取站点来源文本。"""
        source_elem = link.select_one(".R8BTeb")
        return self._clean_text(source_elem.get_text(" ", strip=True)) if source_elem else ""

    def _source_from_exact_card(self, link, href: str) -> str:
        """从 Exact matches 卡片中提取来源，缺失时回退到 URL 域名。"""
        source_elem = link.select_one(".XC18Gb, .iDBaYb, .LbKnXb, .R8BTeb")
        source = self._clean_text(source_elem.get_text(" ", strip=True)) if source_elem else ""
        if source:
            return source

        parsed = urlparse(href)
        return parsed.netloc

    def _preview_from_lens_card(self, link) -> str:
        """在卡片邻近节点中寻找可用的预览图 URL。"""
        node = link
        for _ in range(4):
            node = node.parent
            if not node:
                break

            for img in node.select("img"):
                img_url = img.get("data-src") or img.get("src") or ""
                if img_url and not img_url.startswith("data:image/"):
                    alt = img.get("alt") or ""
                    if alt != "Visually searched image":
                        return img_url

        return ""

    def _is_noise_href(self, href: str) -> bool:
        """过滤 Google 自身页面、账号页、静态资源等非结果链接。"""
        parsed = urlparse(href)
        host = parsed.netloc.lower()

        if not host:
            return True

        if (
            host == "google.com"
            or host.endswith(".google.com")
            or host.startswith("www.google.")
            or host.startswith("lens.google.")
            or host.startswith("accounts.google.")
            or host.startswith("support.google.")
            or host.startswith("policies.google.")
        ):
            return True

        google_asset_hosts = ("gstatic.com", "googleusercontent.com")
        return any(host == google_host or host.endswith(f".{google_host}") for google_host in google_asset_hosts)

    def _normalize_result_key(self, url: str) -> str:
        """归一化结果 URL，用于去重比较。"""
        parsed = urlparse(url)
        return parsed._replace(fragment="").geturl()

    def _clean_text(self, text: str) -> str:
        """压缩多余空白并去掉首尾空格。"""
        return re.sub(r"\s+", " ", text or "").strip()

    def _is_content_guidelines_page(self, page_text: str) -> bool:
        """判断页面文本是否表示 Google 内容政策限制。"""
        markers = [
            "This search can't be processed due to content guidelines",
            "Please try a different image or keywords",
            "content guidelines",
            "抱歉，我不支持解答此类请求",
            "我不支持解答此类请求",
        ]
        return any(marker in page_text for marker in markers)

    def _is_visual_search_expired_page(self, page_text: str) -> bool:
        """判断页面文本是否表示 Lens 视觉搜索内容已过期。"""
        lower_text = page_text.lower()
        markers = [
            "visual search content has expired",
            "this visual search content has expired",
            "visual search content expired",
            "视觉搜索内容已过期",
            "视觉搜索内容已经过期",
            "视觉搜索已过期",
        ]
        return any(marker in page_text or marker in lower_text for marker in markers)

    def _is_zero_results_page(self, page_text: str) -> bool:
        """判断页面文本是否表示没有搜索结果。"""
        markers = [
            "About 0 results",
            "No results found",
            "找到约 0 条结果",
            "没有与您的搜索相匹配的内容",
            "没有找到",
            "未找到",
        ]
        return any(marker in page_text for marker in markers)

    def _looks_like_google_challenge(self, html: str, page_text: str) -> bool:
        """判断页面是否像 Google 验证、JS Cookie 或异常流量挑战页。"""
        combined = f"{html}\n{page_text}"
        if "/sorry/" in (self.last_url or ""):
            return True

        traffic_markers = [
            "unusual traffic",
            "detected unusual traffic",
            "Our systems have detected",
            "异常流量",
            "我们的系统检测到您的计算机网络中存在异常流量",
        ]
        if any(marker in combined for marker in traffic_markers):
            return True

        if self.last_url and self._is_lens_result_url(self.last_url):
            return False

        markers = [
            "/httpservice/retry/enablejs",
            "If you're having trouble accessing Google Search",
            "如果您在几秒钟内没有被重定向",
        ]
        return any(marker in combined for marker in markers)
