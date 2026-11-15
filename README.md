# 台股籌碼盯盤（GitHub Pages 自動運行版）

不用開電腦，GitHub 每天自動跑兩件事，結果放在 GitHub Pages 網頁上：

| 工作 | 什麼時候跑 | 做什麼 | 網頁位置 |
| --- | --- | --- | --- |
| 盤中警示 | 週一至五 09:00–13:55，每 5 分鐘 | 照自選股清單檢查停損、停利、買進提醒價、突破、漲跌幅 3／5／7%、漲跌停、預估爆量、重大訊息、新聞；13:35 後送收盤摘要 | 「我的盯盤」（要密碼） |
| 盤後籌碼報告 | 週一至五 21:40（23:40 補跑） | 三大法人、融資融券、當沖、集保大戶／散戶，標出「法人賣、散戶接刀」等訊號 | 「盤後籌碼」（公開） |

> 本工具只做資料整理與提醒，不會下單，也不構成任何投資建議。

## 隱私怎麼保護

repo 與網頁都是公開的，所以：

- **自選股、持股、成本、提醒價**放在 GitHub Secret `WATCHLIST_CSV`，不進程式碼。
- 網頁上的私人資料、盤中警示紀錄都用 `SITE_PASSWORD` 加密（AES-256-GCM），只有輸入密碼後在你的瀏覽器裡解開。
- 公開的 Actions 執行紀錄只印「幾筆警示、幾則訊息」，不印股票代號與價格。
- 盤後籌碼報告是證交所、集保的公開資料，不含個人資料，所以公開。

密碼請用 12 字以上、別處沒用過的字串；加密檔在網路上任何人都能下載，密碼太短可能被猜到。

## 第一次設定（只要做一次）

在終端機、這個資料夾裡執行：

```bash
gh secret set SITE_PASSWORD
```

輸入你要的網頁密碼（畫面不會顯示），按 Enter。設好後執行一次盤中檢查，網頁就會出現「我的盯盤」：

```bash
gh workflow run auto.yml -f task=intraday
```

## 放到 iPhone 主畫面（PWA）

1. 用 iPhone 的 **Safari** 打開 https://hankcafetea-dev.github.io/private-investment/
2. 點下方「分享」按鈕 →「加入主畫面」→ 名稱會是「籌碼盯盤」→ 加入。
3. 從主畫面打開就是全螢幕 App。第一次進「我的盯盤」要再輸入一次密碼，勾「在這台裝置記住密碼」之後就不用再輸入（主畫面 App 的儲存空間跟 Safari 是分開的）。

- App 從背景切回來會自動抓最新資料，左上也有「↻ 更新」可以手動更新。
- 沒網路時照樣打得開，顯示上一次抓到的資料。
- 網頁改版後，App 下一次打開會自動換成新版（最多晚一次開啟）。

## 換成你自己的自選股

1. 複製 `watchlist.example.csv` 為 `watchlist.csv`（這個檔名已列入 `.gitignore`，不會被上傳）。
2. 用 Excel 編輯，存檔時選「CSV UTF-8」：

   | 欄位 | 說明 |
   | --- | --- |
   | 代號、名稱 | 必填代號（上市、上櫃都可以）；名稱用於新聞搜尋 |
   | 持有股數、成本 | 有持股才填，用於停損判斷與損益 |
   | 買進提醒價 | 跌到這個價格以下提醒 |
   | 突破提醒價 | 漲到這個價格以上提醒 |
   | 停損價 | 有持股且跌破時提醒 |
   | 停利價 | 漲到時提醒 |

3. 執行 `bash tools/set_watchlist.sh`，清單就會存進 GitHub Secret。

**在國泰買賣後，記得更新持有股數與成本，再跑一次第 3 步。**

## 接上 LINE 推播（之後再做也可以）

沒設定 LINE 時，警示照樣判斷，只是顯示在網頁「我的盯盤」，不推播。要推播時：

1. 到 LINE Official Account Manager 建立官方帳號並啟用 Messaging API。
2. 在 LINE Developers 後台拿到 **Channel access token（long-lived）** 與 **Your user ID**（U 開頭）。
3. 用手機 LINE 加這個官方帳號為好友。
4. 在終端機執行（各自貼上值）：

   ```bash
   gh secret set LINE_CHANNEL_TOKEN
   ```

   ```bash
   gh secret set LINE_USER_ID
   ```

關鍵警示（停損、停利、到價、突破、漲跌停）立即推播；一般警示每 15 分鐘合併一則，每天最多 12 則；收盤摘要不受每日上限。另有每月上限 200 則（LINE 免費方案有月額度），最後 40 則只留給關鍵警示與收盤摘要。這些數字都在 `tw_line_alert/settings.json`，請依你 LINE 官方帳號「方案」頁的額度調整。

## 手動執行

| 想做的事 | 指令 |
| --- | --- |
| 現在檢查一輪盤中警示 | `gh workflow run auto.yml -f task=intraday` |
| 補產生某天的盤後報告 | `gh workflow run auto.yml -f task=postmarket -f date=20261113` |
| 只重建網頁 | `gh workflow run auto.yml -f task=site` |
| 看執行紀錄 | `gh run list --workflow auto.yml` |

## 已知限制

- GitHub 排程不保證準時：尖峰時可能晚 5～15 分鐘，偶爾跳過一輪。需要秒級盯盤時，改用本機版（`tw_line_alert/README_本機版.md`，永豐 API，每 30 秒）。
- 盤中行情用證交所「基本市況報導」公開資料（約 5 秒延遲），抓不到時改用 Yahoo 備援。
- 盤後報告只分析上市股票；盤中警示上市、上櫃都支援。重大訊息只含上市公司。
- 證交所的行情 API 只提供「最新一個交易日」，所以盤後報告不能補做太久以前的日期。
- 公開 repo 60 天沒有活動時 GitHub 會停用排程；盤後工作每天會自動重新啟用，正常不會遇到。

## 檔案說明

| 路徑 | 用途 |
| --- | --- |
| `tw_postmarket/postmarket.py` | 盤後籌碼分析（也能在本機單獨跑，用法見檔頭） |
| `tw_line_alert/intraday.py` | 盤中警示規則（本機長時間版） |
| `tw_line_alert/tick.py` | 盤中警示 GitHub 版：每次只檢查一輪 |
| `tw_line_alert/settings.json` | 警示級距、推播上限、新聞時間等設定 |
| `tools/build_site.py` | 組網站、加密私人資料 |
| `tools/secure.py` | 加密工具 |
| `site/` | 網頁本體 |
| `.github/workflows/auto.yml` | 所有排程 |
| `data` 分支 | 自動產生的資料（盤後報告、集保週資料、加密的盤中狀態） |
