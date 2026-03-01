import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import pyotp
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

from yeh import routes
from yeh.config import ResolvedAccount

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginResult:
    final_url: str
    csrf_token: str | None
    cookie_jar_json: str


def login(
    account: ResolvedAccount, debug: bool = False, show_browser: bool = False
) -> LoginResult:
    if not account.hey_passwd:
        raise ValueError("missing hey_passwd in env or config")

    sign_in_url = routes.https_url(account.hey_host, routes.SIGN_IN)
    _ensure_allowed(sign_in_url, account.hey_host)

    driver = _build_driver(debug=debug, show_browser=show_browser)
    wait = WebDriverWait(driver, 30)
    try:
        driver.get(sign_in_url)
        _wait_ready(wait)
        _ensure_driver_allowed(driver, account.hey_host)

        email_input = wait.until(
            ec.presence_of_element_located((By.NAME, "email_address"))
        )
        password_input = wait.until(
            ec.presence_of_element_located((By.NAME, "password"))
        )
        email_input.clear()
        email_input.send_keys(account.hey_email)
        password_input.clear()
        password_input.send_keys(account.hey_passwd)

        submit = _find_first(
            driver,
            [
                (By.CSS_SELECTOR, "input[type='submit'][name='commit']"),
                (By.CSS_SELECTOR, "button[type='submit']"),
            ],
        )
        if submit is None:
            raise ValueError("unable to locate sign-in submit button")
        submit.click()

        time.sleep(0.5)
        _wait_ready(wait)
        _ensure_driver_allowed(driver, account.hey_host)
        LOG.debug("post-sign-in url=%s title=%s", driver.current_url, driver.title)

        if driver.title.strip().lower() == "action blocked":
            raise ValueError(
                "HEY blocked automated sign-in request (Action blocked page)"
            )

        if _needs_totp(driver):
            _complete_totp(driver, wait, account)

        final_url = _resolve_authenticated_url(driver, wait, account.hey_host)
        _ensure_allowed(final_url, account.hey_host)
        if not _is_authenticated_url(final_url):
            raise ValueError("authentication did not leave sign-in flow")

        csrf_token = _read_csrf_token(driver)
        cookie_jar_json = json.dumps(driver.get_cookies(), separators=(",", ":"))
        return LoginResult(
            final_url=final_url, csrf_token=csrf_token, cookie_jar_json=cookie_jar_json
        )
    finally:
        driver.quit()


def _complete_totp(
    driver: WebDriver, wait: WebDriverWait, account: ResolvedAccount
) -> None:
    try:
        totp_link = driver.find_element(
            By.CSS_SELECTOR,
            "a[href*='two_factor_authentication/challenge'][href*='scheme_type=totp']",
        )
        totp_link.click()
    except NoSuchElementException:
        driver.get(routes.https_url(account.hey_host, routes.TWO_FACTOR_CHALLENGE_TOTP))

    _wait_ready(wait)
    _ensure_driver_allowed(driver, account.hey_host)

    code_input = wait.until(ec.presence_of_element_located((By.NAME, "code")))
    if not account.hey_totp:
        raise ValueError("HEY requested TOTP but hey_totp is missing")
    otp = pyotp.TOTP(account.hey_totp).now()
    code_input.clear()
    code_input.send_keys(otp)

    submit = _find_first(
        driver,
        [
            (By.CSS_SELECTOR, "input[type='submit'][name='commit']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "form button"),
        ],
    )
    if submit is None:
        raise ValueError("unable to locate TOTP verify submit button")
    before_submit_url = driver.current_url
    submit.click()

    try:
        wait.until(
            lambda d: (
                d.current_url != before_submit_url
                or "invalid" in (d.page_source or "").lower()
                or "incorrect" in (d.page_source or "").lower()
            )
        )
    except TimeoutException as exc:
        raise ValueError("TOTP verification did not complete") from exc

    _wait_ready(wait)
    _ensure_driver_allowed(driver, account.hey_host)

    if _needs_totp(driver):
        body = (driver.page_source or "").lower()
        if "invalid" in body or "incorrect" in body:
            raise ValueError("TOTP verification failed")


def _needs_totp(driver: WebDriver) -> bool:
    path = urlparse(driver.current_url).path.lower()
    if "two_factor" in path or "challenge" in path:
        return True
    body = (driver.page_source or "").lower()
    return "two_factor_authentication/challenge" in body or "security key" in body


def _read_csrf_token(driver: WebDriver) -> str | None:
    try:
        meta = driver.find_element(By.CSS_SELECTOR, "meta[name='csrf-token']")
    except NoSuchElementException:
        return None
    value = meta.get_attribute("content")
    return value or None


def _build_driver(debug: bool, show_browser: bool) -> ChromeWebDriver:
    options = Options()
    if not show_browser:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1360,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=en-US")
    if debug:
        options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=options)  # type: ignore[operator]


def _wait_ready(wait: WebDriverWait) -> None:
    wait.until(
        lambda d: (
            d.execute_script("return document.readyState")
            in ("interactive", "complete")
        )
    )


def _find_first(driver: WebDriver, locators: list[tuple[str, str]]):
    for by, value in locators:
        found = driver.find_elements(by, value)
        if found:
            return found[0]
    return None


def _ensure_driver_allowed(driver: WebDriver, host: str) -> None:
    _ensure_allowed(driver.current_url, host)


def _ensure_allowed(url: str, host: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    if parsed.hostname != host:
        raise ValueError(f"refusing non-HEY host: {parsed.hostname}")


def _is_authenticated_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if path.startswith(routes.SIGN_IN):
        return False
    return "two_factor_authentication/challenge" not in path


def _resolve_authenticated_url(
    driver: WebDriver, wait: WebDriverWait, host: str
) -> str:
    candidates = [
        driver.current_url,
        routes.https_url(host, "/"),
        routes.https_url(host, routes.IMBOX),
    ]
    for candidate in candidates:
        _ensure_allowed(candidate, host)
        driver.get(candidate)
        _wait_ready(wait)
        _ensure_driver_allowed(driver, host)
        current = driver.current_url
        LOG.debug("post-auth candidate=%s resolved=%s", candidate, current)
        if _is_authenticated_url(current):
            return current
    return driver.current_url
