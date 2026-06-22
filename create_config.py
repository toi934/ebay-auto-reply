"""GitHub Actions 用: 環境変数から ebay_config.py を生成する

使い方: python create_config.py
GitHub Secrets に以下を登録しておくこと:
  TSUJOU_TOKEN, SENMON_TOKEN, APP_ID, DEV_ID, CERT_ID
"""

import os

tsujou_token = os.environ["TSUJOU_TOKEN"]
senmon_token = os.environ["SENMON_TOKEN"]
app_id       = os.environ["APP_ID"]
dev_id       = os.environ["DEV_ID"]
cert_id      = os.environ["CERT_ID"]

content = f'''"""eBayアカウント設定（GitHub Actions 自動生成）"""

ACCOUNTS = {{
    "tsujou": {{
        "name": "通常",
        "seller": "japanesehappinessshop",
        "TOKEN": "{tsujou_token}",
        "APP_ID": "{app_id}",
        "DEV_ID": "{dev_id}",
        "CERT_ID": "{cert_id}",
    }},
    "senmon": {{
        "name": "専門",
        "seller": "japanese_selectshop",
        "TOKEN": "{senmon_token}",
        "APP_ID": "{app_id}",
        "DEV_ID": "{dev_id}",
        "CERT_ID": "{cert_id}",
    }},
}}

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
SELL_API_BASE = "https://apiz.ebay.com/sell/finances/v1"

def get_account(name):
    if name not in ACCOUNTS:
        raise ValueError(f"未知のアカウント: {{name}}")
    return ACCOUNTS[name]
'''

with open("ebay_config.py", "w", encoding="utf-8") as f:
    f.write(content)

print("ebay_config.py を生成しました。")
