import os
import json
import boto3
from github import Github
from datetime import datetime, timezone, timedelta

# 讀取環境變數
awsRegion = os.getenv("AWS_REGION")
githubToken = os.getenv("GITHUB_TOKEN")
repoName = os.getenv("GITHUB_REPOSITORY")
# 讀取 GitHub event payload
with open(os.getenv('GITHUB_EVENT_PATH')) as f:
    event = json.load(f)

# 從 payload 中取得 PR 編號
prNumber = event["pull_request"]["number"]
print(f"✅ PR 編號為: {prNumber}")

# 初始化 GitHub 與 AWS Bedrock
gh = Github(githubToken)
repo = gh.get_repo(repoName)
pr = repo.get_pull(prNumber)

print("🔍 正在初始化 AWS Bedrock 客戶端...")
try:
    bedrockRuntime = boto3.client(
        #  boto3 需要蛇形命名法
        service_name='bedrock-runtime',
        region_name=awsRegion
    )
    print("✅ AWS Bedrock 客戶端初始化成功")
except Exception as e:
    print(f"❌ AWS Bedrock 客戶端初始化失敗: {str(e)}")
    raise

def collectPrDiff(pr):
    """收集 PR 的程式碼差異"""
    diff = ""
    for file in pr.get_files():
        if file.patch:
            diff += f"\n\n### {file.filename}\n{file.patch}"
    return diff

def is_frontend_repo(repo_name):
    """判斷是否為前端專案"""
    # 根據 repo 名稱判斷是否為前端專案
    frontend_keywords = [
        'frontend', 'front-end', 'web', 'ui', 'vue', 'react', 
        'angular', 'nuxt', 'next', 'client', 'app', 'portal'
    ]
    
    repo_name_lower = repo_name.lower()
    return any(keyword in repo_name_lower for keyword in frontend_keywords)

def get_backend_prompt(diff):
    """取得後端程式碼審查的 prompt"""
    return f"""
你是一位經驗豐富的軟體工程師，專長於程式碼審查。請協助我審查以下 Pull Request 的程式碼差異（diff），並以繁體中文回覆審查建議。

請**聚焦於本次 PR 的變更內容**，並依下列稽核面向給予具體建議。請條列清楚，必要時可附上簡短程式碼範例協助理解。

---

### 稽核重點：

1. **邏輯正確性與潛在錯誤**
   - 是否有邏輯錯誤、潛在 Bug 或未處理的邊界條件？
   - 是否有不恰當的例外處理或錯誤忽略？

2. **可讀性與命名一致性**
   - 變數、函式、類別命名是否一致、清晰且語意明確？
   - 命名是否遵循專案慣例？

3. **程式碼結構與重構潛力**
   - 是否有重複、冗餘的邏輯？
   - 是否存在可抽取函式或模組化的機會？
   - 流程是否簡潔、易於維護？

4. **註解與文件**
   - 是否有缺乏註解、難以理解的邏輯？
   - 是否有必要補充文件或 TODO 提示？

5. **最佳實踐與風格一致性**
   - 是否符合常見設計原則（如 SRP、DRY 等）？
   - 是否正確處理錯誤與資源釋放？
   - 是否使用合適的資料結構與語法特性？

6. **系統分層設計（Controller / Service / Repository / Model）**
   - **Controller 是否只處理請求轉發與驗證，不包含商業邏輯？**
   - **Service 是否專注於業務邏輯，並適當協調多個 repository 或外部資源？**
   - **Repository 是否專注於資料存取，不包含業務邏輯？**
   - **Model 是否只定義資料結構與 ORM 行為？**
   - 針對本次修改，是否有**跨層職責混淆（例如 controller 寫入資料庫、 repository 包含流程邏輯）**等設計問題？
   - 是否有機會重構以改善分層與可測試性？

---

### 程式風格規範：

1. **排版**
   - 請確認縮排是否一致，1 個 tab 等於 4 個 spaces。
   
2. **命名規則**
  - 變數／函式：使用駝峰式命名（camelCase）。
  - 常數：使用大寫蛇式命名（UPPER_SNAKE_CASE）。
  - 資料庫欄位：使用小寫蛇式命名（lower_snake_case）。

---

請依上述面向進行審查，並僅針對此次程式碼差異提供具體建議與優化方向。
以下是 PR 的 diff：

{diff}
"""

def get_frontend_prompt(diff):
    """取得前端程式碼審查的 prompt"""
    return f"""
你是一位經驗豐富的前端工程師，專長於 Vue.js 與前端程式碼審查。請協助我審查以下 Pull Request 的程式碼差異（diff），並以繁體中文回覆審查建議。

請**聚焦於本次 PR 的變更內容**，並依下列稽核面向給予具體建議。請條列清楚，必要時可附上簡短程式碼範例協助理解。

---

### 稽核重點：

1. **邏輯正確性與潛在錯誤**
   - 是否有邏輯錯誤、潛在 Bug 或未處理的邊界條件？
   - 是否有不恰當的例外處理或錯誤忽略？
   - 是否處理好非同步邏輯（如 API 呼叫、事件綁定等）？

2. **可讀性與命名一致性**
   - 變數、函式、元件命名是否一致、清晰且語意明確？

3. **程式碼結構與重構潛力**
   - 是否有重複、冗餘的邏輯？
   - 是否有可抽取為 composable、元件或 utility 函式的機會？
   - 元件是否過於肥大？是否可以拆分子元件？
   - 流程是否簡潔、易於維護？

4. **註解與文件**
   - 是否有缺乏註解、難以理解的邏輯？
   - 是否有必要補充文件或 TODO 提示？
   - 是否有引入第三方函式庫但未說明用途？

5. **最佳實踐與風格一致性**
   - 是否符合常見設計原則（如 SRP、DRY、KISS 等）？
   - 是否正確處理錯誤與資源釋放（如移除事件監聽器、取消請求）？
   - 是否使用合適的 Vue 語法（如 `v-model`、`watchEffect`、`ref` 與 `reactive` 的正確使用）？
   - 是否有過度使用 watch 或 computed？

6. **前端分層設計（Component / Composable / Store / API 模組）**
   - **元件是否專注於 UI 呈現與使用者互動，不包含複雜邏輯？**
   - **composable 是否封裝了可重用的邏輯或狀態？**
   - **API 模組是否集中處理請求，未分散於各元件中？**
   - **Vuex 等狀態管理是否只處理應該共享的資料與行為？**
   - 是否有跨層職責混淆（如元件內直接調整全域狀態或處理複雜商業邏輯）？
   - 是否有機會重構以改善可測試性與模組化？

---

### 程式風格規範：

1. **排版**
   - 請確認縮排是否一致，1 個 tab 等於 4 個 spaces。
   
2. **命名規則**
   - 變數／函式：使用駝峰式命名（camelCase）。
   - 常數：使用大寫蛇式命名（UPPER_SNAKE_CASE）。
   - 元件名稱：使用帕斯卡命名（PascalCase）。
   - 資料欄位或 JSON key：使用小寫蛇式命名（lower_snake_case）。

---

請依上述面向進行審查，並僅針對此次程式碼差異提供具體建議與優化方向。
以下是 PR 的 diff：

{diff}
"""

# 收集 PR diff
diff = collectPrDiff(pr)

# 根據 repo 名稱決定使用哪個 prompt
isFrontend = is_frontend_repo(repoName)
print(f"🔍 檢測到專案類型: {'前端' if isFrontend else '後端'} (repo: {repoName})")

if isFrontend:
    prompt = get_frontend_prompt(diff)
    reviewType = "前端"
else:
    prompt = get_backend_prompt(diff)
    reviewType = "後端"

# 模型價格常數 (每百萬 token 的美元價格)
MODEL_PRICES = {
    'us.anthropic.claude-sonnet-4-20250514-v1:0': {
        'input': 15.00,  # $15.00 per 1M input tokens
        'output': 60.00  # $60.00 per 1M output tokens
    },
    'amazon.nova-pro-v1:0': {
        'input': 3.00,   # $3.00 per 1M input tokens
        'output': 6.00   # $6.00 per 1M output tokens
    }
}

def calculate_cost(modelId, inputTokens, outputTokens):
    """計算 API 呼叫的費用"""
    if modelId not in MODEL_PRICES:
        return {
            'inputCost': 0,
            'outputCost': 0,
            'totalCost': 0,
            'error': f"未知模型：{modelId}"
        }
    
    prices = MODEL_PRICES[modelId]
    inputCost = (inputTokens / 1000000) * prices['input']
    outputCost = (outputTokens / 1000000) * prices['output']
    totalCost = inputCost + outputCost
    
    return {
        'inputCost': inputCost,
        'outputCost': outputCost,
        'totalCost': totalCost
    }

def create_issue_comment(pr, title, content, modelId=None, tokenUsage=None):
    """發布留言到 PR，並顯示 token 使用量和費用"""
    # 使用台灣時間（UTC+8）
    taiwanTime = datetime.now(timezone(timedelta(hours=8)))
    timestamp = taiwanTime.strftime("%Y-%m-%d %H:%M:%S")
    comment = f"🧠 **{title}**\n\n{content}\n\n"
    
    # 如果有提供 token 使用量資訊，則添加到留言中
    if tokenUsage and modelId:
        inputTokens = tokenUsage.get('inputTokens', 0)
        outputTokens = tokenUsage.get('outputTokens', 0)
        totalTokens = inputTokens + outputTokens
        
        cost = calculate_cost(modelId, inputTokens, outputTokens)
        
        comment += f"---\n"
        comment += f"📊 **使用資訊** ({timestamp})\n"
        comment += f"- 模型: `{modelId}`\n"
        comment += f"- 輸入 Tokens: {inputTokens:,}\n"
        comment += f"- 輸出 Tokens: {outputTokens:,}\n"
        comment += f"- 總計 Tokens: {totalTokens:,}\n"
        
        if 'error' in cost:
            comment += f"- 估計費用: 無法計算 ({cost['error']})\n"
        else:
            comment += f"- 估計費用: ${cost['totalCost']:.6f} USD (輸入: ${cost['inputCost']:.6f}, 輸出: ${cost['outputCost']:.6f})\n"
    
    # 發布留言到 PR
    pr.create_issue_comment(comment)
    
    # 同時在控制台輸出資訊
    print("✅ 已成功送出 AI Code Review 回饋。")
    if tokenUsage and modelId:
        print(f"📊 Token 使用量: 輸入={inputTokens:,}, 輸出={outputTokens:,}, 總計={totalTokens:,}")
        print(f"💰 估計費用: ${cost['totalCost']:.6f} USD")

# 選項 1: 使用 converse 方法 (僅支持 Claude 和部分 Amazon 模型)
modelId = 'us.anthropic.claude-sonnet-4-20250514-v1:0'
# modelId = 'amazon.nova-pro-v1:0'

response = bedrockRuntime.converse(
    modelId=modelId,
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "text": prompt
                }
            ]
        }
    ],
    inferenceConfig={
        "maxTokens": 800,
        "temperature": 0.7
    }
)

aiFeedback = response['output']['message']['content'][0]['text']

# 獲取 token 使用量
tokenUsage = {
    'inputTokens': response.get('usage', {}).get('inputTokens', 0),
    'outputTokens': response.get('usage', {}).get('outputTokens', 0)
}

# 使用新函數發布留言到 PR
create_issue_comment(
    pr=pr,
    title=f"Hsuan AI Code Review 建議({reviewType}, claude-sonnet-4)",
    content=aiFeedback,
    modelId=modelId,
    tokenUsage=tokenUsage
)