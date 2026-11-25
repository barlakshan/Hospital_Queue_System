"""Small wrapper around the WebDriver plus the user journeys tests share."""
import time

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

PASSWORD = "Uat-Pass-123"
TIMEOUT = 10


def button(text):
    return (By.XPATH, f"//button[normalize-space()='{text}']")


class Browser:
    def __init__(self, driver, base_url, slowmo=0):
        self.driver = driver
        self.base_url = base_url
        self.slowmo = slowmo

    # ── basics ────────────────────────────────────────────────────────────
    def wait(self, condition, message=""):
        return WebDriverWait(
            self.driver, TIMEOUT,
            ignored_exceptions=[StaleElementReferenceException],
        ).until(condition, message)

    def _locator(self, target):
        return target if isinstance(target, tuple) else (By.CSS_SELECTOR, target)

    def _pause(self):
        if self.slowmo:
            time.sleep(self.slowmo)

    def go(self, path):
        self.driver.get(self.base_url + path)
        self._pause()

    def see(self, target):
        return self.wait(EC.visibility_of_element_located(self._locator(target)),
                         f"not visible: {target}")

    def click(self, target):
        element = self.wait(EC.element_to_be_clickable(self._locator(target)),
                            f"not clickable: {target}")
        # Bootstrap turns on smooth scrolling, so Selenium's own scroll can
        # still be moving when it clicks. Jump there first.
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'})", element)
        element.click()
        self._pause()

    def fill(self, target, value):
        field = self.see(target)
        field.clear()
        field.send_keys(value)

    def choose(self, target, text):
        Select(self.see(target)).select_by_visible_text(text)

    def text_of(self, target):
        return self.see(target).text

    def see_text(self, text, within="body"):
        locator = self._locator(within)
        self.wait(EC.text_to_be_present_in_element(locator, text),
                  f"text not found in {within}: {text!r}")

    def path_is(self, path):
        self.wait(lambda d: d.current_url.split("?")[0] == self.base_url + path,
                  f"expected {path}, got {self.driver.current_url}")

    def accept_alert(self):
        alert = self.wait(EC.alert_is_present(), "no alert appeared")
        text = alert.text
        alert.accept()
        return text

    # ── journeys ──────────────────────────────────────────────────────────
    def sign_in(self, email, password=PASSWORD):
        self.go("/auth/login")
        self.fill("#email", email)
        self.fill("#password", password)
        self.click("button[type=submit]")

    def submit_registration(self, name, mobile, service="General OPD", priority=None):
        self.go("/register/UAT")
        self.fill("input[name=full_name]", name)
        self.fill("input[name=mobile]", mobile)
        self.choose("#service-type", service)
        if priority:
            self.click(".pb-priority-toggle")
            self.click((By.XPATH, f"//div[contains(@class,'pb-chip') and normalize-space()='{priority}']"))
        self.click("#submit-btn")

    def register_patient(self, name, mobile, **options):
        """Register through the public form and return the token number."""
        self.submit_registration(name, mobile, **options)
        return self.text_of("#token-number")
