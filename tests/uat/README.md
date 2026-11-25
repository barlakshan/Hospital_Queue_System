# Selenium UAT suite

Browser tests for the main user journeys. Each test starts its own copy of the app on an empty temporary database with the mock SMS provider, so no real data is touched and no SMS is sent.

## Run

Needs Google Chrome and internet access (pages load scripts and fonts from jsDelivr, and Selenium downloads its ChromeDriver on first use).

```bash
pip install -r requirements-dev.txt
python -m pytest -m uat                      # all scenarios, headless
UAT_HEADED=1 UAT_SLOWMO=0.5 python -m pytest -m uat   # watch them run
python -m pytest -m uat -k stf_01            # one scenario
python -m pytest -m uat --junitxml=uat-artifacts/results.xml   # report file
```

A failed test saves a screenshot of each of its browsers to `uat-artifacts/`.

A plain `python -m pytest` does not run these tests.

## Scenarios

| ID | Acceptance criterion |
|---|---|
| PAT-01 | A patient registers and receives a token; the mobile number is masked |
| PAT-02 | Registering twice for a service returns the same token |
| PAT-03 | A priority choice stays a request until staff confirm it |
| PAT-04 | An invalid mobile number is rejected |
| PAT-05 | A patient requests an appointment and sees a confirmation |
| STF-01 | Full visit: register, call, start, complete, rate |
| STF-02 | A visit cannot be completed without a condition summary |
| STF-03 | A confirmed priority patient is called first |
| STF-04 | An absent patient can be marked no-show |
| STF-05 | The display screen shows the called token and counter, not the name |
| STF-06 | Staff can schedule an appointment request |
| AUTH-01 | A wrong password is refused |
| AUTH-02 | Admin, manager and OPD staff land on their own dashboards |
| AUTH-03 | Signing out ends the session |
| AUTH-04 | Staff cannot open admin pages |
| ADM-01 | An admin adds a hospital location |
| ADM-02 | A new staff account must change its password at first sign-in |
| ADM-03 | A new service appears on the patient form |
| MGR-01 | The location manager's pages open |

The suite does not cover real SMS delivery, QR scanning with a phone, printed or visual layout, or MySQL. Check those by hand on the UAT server.
