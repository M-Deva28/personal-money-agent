import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import json
from detector import run_all_detectors
from feedback import load_profile, record_feedback, reset_profile, set_autopay
from audit import log_decision, decide_action, clear_log
from scoring import compute_score
from finance import finance_summary
from growth import growth_summary
from accounts import list_connected_accounts, connect_account, disconnect_account, add_manual_transaction, add_manual_subscription
from main import app
from fastapi.testclient import TestClient

def test_all():
    print("1. Testing data loading & detectors...")
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    with open(os.path.join(data_dir, "transactions.json"), encoding="utf-8") as f:
        txns = json.load(f)
    with open(os.path.join(data_dir, "subscriptions.json"), encoding="utf-8") as f:
        subs = json.load(f)
    with open(os.path.join(data_dir, "ground_truth.json"), encoding="utf-8") as f:
        gt = json.load(f)

    flags = run_all_detectors(txns, subs)
    print(f"   Flags generated: {len(flags)}")

    print("2. Testing scoring...")
    score = compute_score(flags, gt)
    print(f"   Precision: {score['precision']}, Recall: {score['recall']}, F1: {score['f1']}")

    print("3. Testing finance & growth...")
    f_sum = finance_summary(txns)
    g_sum = growth_summary(subscriptions=subs, transactions=txns)
    print(f"   Categories: {len(f_sum['category_totals'])}, Bills: {g_sum['count']}")

    print("4. Testing FastAPI endpoints via TestClient...")
    client = TestClient(app)
    r_root = client.get("/")
    assert r_root.status_code == 200, f"Root status: {r_root.status_code}"

    r_flags = client.get("/flags")
    assert r_flags.status_code == 200, f"Flags status: {r_flags.status_code}"

    r_score = client.get("/score")
    assert r_score.status_code == 200, f"Score status: {r_score.status_code}"

    r_finance = client.get("/finance")
    assert r_finance.status_code == 200, f"Finance status: {r_finance.status_code}"

    r_growth = client.get("/growth")
    assert r_growth.status_code == 200, f"Growth status: {r_growth.status_code}"

    r_audit = client.get("/audit")
    assert r_audit.status_code == 200, f"Audit status: {r_audit.status_code}"

    r_login = client.post("/auth/login", json={"username": "test", "password": "123"})
    assert r_login.status_code == 200, f"Login status: {r_login.status_code}"

    print("5. Testing feedback loop...")
    server_flags = r_flags.json()["flags"]
    assert len(server_flags) > 0, "No flags returned by /flags"
    test_flag = server_flags[0]
    r_feed = client.post("/feedback", json={"flag_id": test_flag["flag_id"], "verdict": "confirmed"})
    assert r_feed.status_code == 200, f"Feedback status: {r_feed.status_code}"

    print("\n>>> ALL CHECKS PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    test_all()
