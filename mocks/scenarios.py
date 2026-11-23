"""The fault scenarios and the demo customers that carry them.

Scenario assignment is data, so evals are reproducible. The demo customers are fictional and their PINs are
demo credentials (shown in the UI's demo list and the README); every other customer gets a random PIN.
"""
from dataclasses import dataclass

SCENARIOS = {
    "healthy": "Everything works.",
    "S1": "Area outage: an active internet outage in the customer's area.",
    "S2": "Line fault: the line test shows a low signal-to-noise ratio and many errors; the router is fine.",
    "S3": "Router frozen: the router answers management but has stopped handing out addresses; a reboot fixes it.",
    "S4": "Wi-Fi interference: the 2.4 GHz band is crowded; wired connections are fine.",
    "S5": "Wrong WAN setting after a factory reset: the plan needs PPPoE, the router is set to DHCP.",
    "S6": "Suspended for an unpaid bill.",
    "S7": "No fault on Quillnet's side: a single device of the customer has a problem.",
    "S8": "Intermittent drops: the line works now but dropped several times in the last 24 hours.",
    "S9": "Angry repeat caller: several recent cases for the same problem.",
    "S10": "TV not working, internet fine: a TV platform outage in the area.",
}


@dataclass(frozen=True)
class DemoCustomer:
    account_no: str
    first_name: str
    last_name: str
    pin: str
    dob: str
    language: str
    area_code: str
    plan_id: str
    router_model: str
    scenario: str


DEMO_CUSTOMERS = [
    DemoCustomer("QN-100001", "Daniel", "Brooks", "4821", "1986-03-14", "en", "SOF-02", "FIB500", "QN-Box 4", "S1"),
    DemoCustomer("QN-100002", "Elena", "Markova", "7305", "1979-11-02", "en", "SOF-01", "FIB100", "QN-Box 3", "S2"),
    DemoCustomer("QN-100003", "Мария", "Петрова", "1590", "1991-06-27", "bg", "PLD-01", "FIB500", "QN-Box 4", "S3"),
    DemoCustomer("QN-100004", "Oliver", "Grant", "2468", "1994-01-09", "en", "SOF-03", "FIB1000TV", "QN-Mesh Duo",
                 "S4"),
    DemoCustomer("QN-100005", "Георги", "Илиев", "9137", "1968-08-30", "bg", "VAR-01", "DSL50", "QN-Box 4", "S5"),
    DemoCustomer("QN-100006", "Sofia", "Lazarova", "6042", "1999-12-18", "en", "PLD-02", "FIB100", "QN-Box 3", "S6"),
    DemoCustomer("QN-100007", "Peter", "Novak", "3716", "1983-04-05", "en", "SOF-04", "FIB500", "QN-Box 4", "S7"),
    DemoCustomer("QN-100008", "Ивана", "Стоянова", "8254", "1975-09-21", "bg", "VAR-02", "FIB100", "QN-Box 3", "S8"),
    DemoCustomer("QN-100009", "Martin", "Kolev", "5903", "1988-02-11", "en", "SOF-05", "FIB500", "QN-Box 4", "S9"),
    DemoCustomer("QN-100010", "Николай", "Димитров", "2179", "1972-07-03", "bg", "PLD-03", "FIB1000TV", "QN-Mesh Duo",
                 "S10"),
]
DEMO_BY_ACCOUNT = {c.account_no: c for c in DEMO_CUSTOMERS}

# Areas with an outage get no random customers, so every "healthy" customer really is healthy.
OUTAGE_AREAS = {"SOF-02": "internet", "PLD-03": "tv"}
