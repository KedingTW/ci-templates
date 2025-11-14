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

def splitPrIntoChunks(pr, max_chunk_size=60000):
    """將 PR 分割成多個可處理的區塊，確保每個檔案都被完整審查"""
    all_files = list(pr.get_files())
    files_with_patches = [f for f in all_files if f.patch]
    
    if not files_with_patches:
        return []
    
    chunks = []
    current_chunk_files = []
    current_chunk_size = 0
    
    # 基礎 prompt 大小估算
    base_prompt_size = 3000
    
    for file in files_with_patches:
        file_diff = f"\n\n### {file.filename}\n{file.patch}"
        file_size = len(file_diff)
        
        # 如果單個檔案就超過限制，單獨處理
        if file_size + base_prompt_size > max_chunk_size:
            # 先處理當前累積的檔案
            if current_chunk_files:
                chunks.append(current_chunk_files)
                current_chunk_files = []
                current_chunk_size = 0
            
            # 單獨處理大檔案
            chunks.append([file])
            print(f"⚠️ 大檔案單獨處理: {file.filename} ({file_size:,} 字符)")
            continue
        
        # 檢查加入此檔案是否會超過限制
        if current_chunk_size + file_size + base_prompt_size > max_chunk_size:
            # 當前區塊已滿，開始新區塊
            if current_chunk_files:
                chunks.append(current_chunk_files)
            current_chunk_files = [file]
            current_chunk_size = file_size
        else:
            # 加入當前區塊
            current_chunk_files.append(file)
            current_chunk_size += file_size
    
    # 處理最後一個區塊
    if current_chunk_files:
        chunks.append(current_chunk_files)
    
    print(f"📊 將 {len(files_with_patches)} 個檔案分成 {len(chunks)} 個區塊進行審查")
    for i, chunk in enumerate(chunks):
        chunk_size = sum(len(f"\n\n### {f.filename}\n{f.patch}") for f in chunk)
        file_names = [f.filename for f in chunk]
        print(f"  區塊 {i+1}: {len(chunk)} 個檔案 ({chunk_size:,} 字符)")
        print(f"    檔案: {', '.join(file_names[:3])}{'...' if len(file_names) > 3 else ''}")
    
    return chunks

def buildChunkDiff(chunk_files):
    """為特定檔案區塊建構 diff"""
    diff = ""
    for file in chunk_files:
        if file.patch:
            diff += f"\n\n### {file.filename}\n{file.patch}"
    return diff

def is_frontend_repo(repo_name):
    """判斷是否為前端專案"""
    # 根據 repo 名稱判斷是否為前端專案
    frontend_keywords = [
        'frontend', 'front-end', 'web', 'ui', 'vue', 'react', 
        'angular', 'nuxt', 'next', 'client', 'app', 'portal','ai-chatbox'
    ]
    
    repo_name_lower = repo_name.lower()
    return any(keyword in repo_name_lower for keyword in frontend_keywords)

def get_backend_prompt(diff):
    """取得後端程式碼審查的 prompt"""
    return f"""
你是一位經驗豐富的軟體工程師，專長於程式碼審查。請協助我審查以下 Pull Request 的程式碼差異（diff），並以繁體中文回覆審查建議。

請注意：
1. 只聚焦於本次 PR 的變更內容。
2. 依照稽核面向提供具體建議。
3. 回饋僅需列出**需要修正或錯誤的部分**，程式碼若已符合規範或稽核重點，無需額外列出。
4. 如有必要，可附上簡短程式碼範例協助理解。

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

請注意：
1. 只聚焦於本次 PR 的變更內容。
2. 依照稽核面向提供具體建議。
3. 回饋僅需列出**需要修正或錯誤的部分**，程式碼若已符合規範或稽核重點，無需額外列出。
4. 如有必要，可附上簡短程式碼範例協助理解。

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

# 檢測專案類型
isFrontend = is_frontend_repo(repoName)
print(f"🔍 檢測到專案類型: {'前端' if isFrontend else '後端'} (repo: {repoName})")

# 將 PR 分割成可處理的區塊
chunks = splitPrIntoChunks(pr)

if not chunks:
    print("❌ 沒有找到需要審查的程式碼差異")
    exit(0)

# 如果只有一個區塊，使用原有邏輯
if len(chunks) == 1:
    print("📝 單一區塊審查模式")
    diff = buildChunkDiff(chunks[0])
    if isFrontend:
        prompt = get_frontend_prompt(diff)
        reviewType = "前端"
    else:
        prompt = get_backend_prompt(diff)
        reviewType = "後端"
else:
    print(f"📝 多區塊審查模式 ({len(chunks)} 個區塊)")
    reviewType = "前端" if isFrontend else "後端"

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
    """發布留言到 PR，支援長訊息自動分段，並顯示 token 使用量和費用"""
    taiwanTime = datetime.now(timezone(timedelta(hours=8)))
    timestamp = taiwanTime.strftime("%Y-%m-%d %H:%M:%S")

    usage_info = ""
    if tokenUsage and modelId:
        inputTokens = tokenUsage.get('inputTokens', 0)
        outputTokens = tokenUsage.get('outputTokens', 0)
        totalTokens = inputTokens + outputTokens

        cost = calculate_cost(modelId, inputTokens, outputTokens)

        usage_info += "\n---\n"
        usage_info += f"📊 **使用資訊** ({timestamp})\n"
        usage_info += f"- 模型: `{modelId}`\n"
        usage_info += f"- 輸入 Tokens: {inputTokens:,}\n"
        usage_info += f"- 輸出 Tokens: {outputTokens:,}\n"
        usage_info += f"- 總計 Tokens: {totalTokens:,}\n"

        if 'error' in cost:
            usage_info += f"- 估計費用: 無法計算 ({cost['error']})\n"
        else:
            usage_info += f"- 估計費用: ${cost['totalCost']:.6f} USD (輸入: ${cost['inputCost']:.6f}, 輸出: ${cost['outputCost']:.6f})\n"

    # 組合完整留言內容
    full_text = f"🧠 **{title}**\n\n{content.strip()}\n\n{usage_info}"

    # 分段發送
    def split_text(text, max_len=60000):
        lines = text.split('\n')
        chunks = []
        current = ""

        for line in lines:
            if len(current) + len(line) + 1 < max_len:
                current += line + '\n'
            else:
                chunks.append(current.strip())
                current = line + '\n'

        if current:
            chunks.append(current.strip())
        return chunks

    segments = split_text(full_text)

    for i, segment in enumerate(segments):
        if i == 0:
            pr.create_issue_comment(segment)
        else:
            pr.create_issue_comment(f"🧩 **續篇 Part {i+1}**\n\n{segment}")

    # Console log
    print(f"✅ 成功送出 {len(segments)} 則留言")
    if tokenUsage and modelId:
        print(f"📊 Token 使用量: 輸入={inputTokens:,}, 輸出={outputTokens:,}, 總計={totalTokens:,}")
        print(f"💰 估計費用: ${cost['totalCost']:.6f} USD")

# 選項 1: 使用 converse 方法 (僅支持 Claude 和部分 Amazon 模型)
modelId = 'us.anthropic.claude-sonnet-4-20250514-v1:0'

def processChunk(chunk_files, chunk_index, total_chunks):
    """處理單個區塊的審查"""
    chunk_diff = buildChunkDiff(chunk_files)
    chunk_prompt = get_backend_prompt(chunk_diff) if not isFrontend else get_frontend_prompt(chunk_diff)
    
    try:
        response = bedrockRuntime.converse(
            modelId=modelId,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": chunk_prompt
                        }
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": 4000,
                "temperature": 0.7
            }
        )
        
        aiFeedback = response['output']['message']['content'][0]['text']
        
        # 獲取 token 使用量
        tokenUsage = {
            'inputTokens': response.get('usage', {}).get('inputTokens', 0),
            'outputTokens': response.get('usage', {}).get('outputTokens', 0)
        }
        
        # 建構檔案清單
        file_names = [f.filename for f in chunk_files]
        file_list = ', '.join(file_names[:3])
        if len(file_names) > 3:
            file_list += f' 等 {len(file_names)} 個檔案'
        
        # 發布審查結果
        if total_chunks == 1:
            title = f"Hsuan AI Code Review 建議({reviewType}, claude-sonnet-4)"
        else:
            title = f"Hsuan AI Code Review 建議 - 第 {chunk_index + 1} 部分 ({file_list})"
        
        create_issue_comment(
            pr=pr,
            title=title,
            content=aiFeedback,
            modelId=modelId,
            tokenUsage=tokenUsage
        )
        
        return True, tokenUsage
        
    except Exception as e:
        if "Input is too long" in str(e):
            print(f"⚠️ 區塊 {chunk_index + 1} 輸入過長，嘗試進一步分割...")
            # 如果區塊仍然太大，嘗試進一步分割
            if len(chunk_files) > 1:
                # 將區塊分成兩半
                mid = len(chunk_files) // 2
                first_half = chunk_files[:mid]
                second_half = chunk_files[mid:]
                
                print(f"  分割成兩個子區塊: {len(first_half)} + {len(second_half)} 個檔案")
                
                # 遞迴處理兩個子區塊
                success1, usage1 = processChunk(first_half, chunk_index, total_chunks * 2)
                success2, usage2 = processChunk(second_half, chunk_index, total_chunks * 2)
                
                # 合併 token 使用量
                combined_usage = {
                    'inputTokens': usage1.get('inputTokens', 0) + usage2.get('inputTokens', 0),
                    'outputTokens': usage1.get('outputTokens', 0) + usage2.get('outputTokens', 0)
                }
                
                return success1 and success2, combined_usage
            else:
                # 單個檔案仍然太大，發送錯誤報告
                file_name = chunk_files[0].filename
                error_message = f"""## ⚠️ 檔案過大無法審查

**檔案**: `{file_name}`

**問題**: 此檔案的程式碼差異過大，超過 Claude Sonnet 4 模型的輸入限制

**建議**:
1. 將大型檔案的修改拆分成多個較小的提交
2. 檢查是否包含大量自動生成的程式碼
3. 考慮手動審查此檔案的關鍵變更

**技術詳情**:
- 檔案大小: {len(chunk_files[0].patch):,} 字符
- 時間: {datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}
"""
                pr.create_issue_comment(error_message)
                return False, {'inputTokens': 0, 'outputTokens': 0}
        else:
            print(f"❌ 區塊 {chunk_index + 1} 處理失敗: {str(e)}")
            raise e

# 處理所有區塊
total_usage = {'inputTokens': 0, 'outputTokens': 0}
successful_chunks = 0

if len(chunks) == 1:
    # 單一區塊模式
    success, usage = processChunk(chunks[0], 0, 1)
    if success:
        successful_chunks = 1
        total_usage = usage
else:
    # 多區塊模式
    for i, chunk in enumerate(chunks):
        print(f"🔄 處理區塊 {i + 1}/{len(chunks)}...")
        success, usage = processChunk(chunk, i, len(chunks))
        
        if success:
            successful_chunks += 1
            total_usage['inputTokens'] += usage.get('inputTokens', 0)
            total_usage['outputTokens'] += usage.get('outputTokens', 0)
    
    # 發送總結留言
    if successful_chunks > 1:
        summary_message = f"""## 📋 AI Code Review 總結

✅ **審查完成**: 成功審查了 {successful_chunks}/{len(chunks)} 個區塊

📊 **總計使用量**:
- 輸入 Tokens: {total_usage['inputTokens']:,}
- 輸出 Tokens: {total_usage['outputTokens']:,}
- 總計 Tokens: {total_usage['inputTokens'] + total_usage['outputTokens']:,}

💡 **說明**: 由於 PR 包含大量程式碼變更，已分批進行審查以確保每個檔案都能被完整分析。

⏰ **時間**: {datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}
"""
        pr.create_issue_comment(summary_message)

print(f"🎉 審查完成！成功處理 {successful_chunks} 個區塊")
print(f"📊 總計 Token 使用量: 輸入={total_usage['inputTokens']:,}, 輸出={total_usage['outputTokens']:,}")