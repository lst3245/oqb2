# Chinese UI glossary (Hong Kong Traditional Chinese)

> The fixed English → 繁體中文 terms for `app/translations/zh_Hant.json`. Every phase of the UI translation uses these words, so the same thing is never called two names. Spec: [../modules/i18n.md](../modules/i18n.md); decision: [ADR-017](../decisions/ADR-017-bilingual-ui-and-taxonomy.md).

Conventions:

- Hong Kong usage: 上載 (not 上傳), 用戶 (not 使用者), 資料夾, 列印, 網上.
- Full-width punctuation in Chinese text: ，。：；？！「」（）. Keep `{placeholders}`, QIDs, file names, version codes and `code` spans ASCII.
- Version codes (`EN`, `CH`, `BI`, `ENO`, `CHO`), `MC` / `CQ`, `QID`, `PDF`, `DOCX`, `MD`, `IMG`, `LLM`, `AI` stay untranslated.
- Put a space between Chinese and Latin text or numbers (`AI 標籤`, `{n} 個`).
- Teacher-facing wording only. Progress logs, SSE lines and AI prompts stay English.

## Library and taxonomy

| English | 中文 | Notes |
|---|---|---|
| Question Bank | 題庫 | "Online Question Bank" 網上題庫 |
| Question | 題目 | count word 條 (`{n} 條題目`) |
| Question part / stem | 分題 / 題幹 | |
| Subject | 科目 | |
| Topic / Subtopic | 課題 / 子課題 | Major topic 主要課題; minor topic 次要課題 |
| Chapter / Subchapter | 章節 / 子章節 | |
| Cross topic | 跨課題 | also printed in documents (`DOC_LABELS`) |
| Level | 程度 | Level 1–3 |
| Section | 部分 | paper section (A / B) |
| Marks | 分數 | |
| Question type | 題型 | MC 多項選擇題, CQ 傳統題 |
| Correct % | 答對率 | |
| Version | 版本 | paper version EN/CH/BI…, not the UI language |
| Answer | 答案 | |
| Solution | 題解 | |
| Asset (a question's file / image part) | 檔案 | |
| Tag / Tags | 標籤 | Auto Tag 自動標籤 |
| Verified | 已核實 | |
| Proofread | 校對 | checked-OK 檢查通過 |
| Hidden | 已隱藏 | |
| Description / hint | 描述 / 提示 | |

## Working with questions

| English | 中文 | Notes |
|---|---|---|
| Dashboard | 主頁 | |
| Filter | 篩選 | |
| Selection / Select | 選取 | ADR-008: selection is not the filter |
| Search | 搜尋 | |
| Sort / Reorder | 排序 / 重新排序 | |
| Search Profile | 搜尋設定檔 | |
| Question Set | 題目集 | |
| Past paper / Source | 歷屆試題 / 來源 | `DSE` / `CE` / `AL` / `QB` stay as codes |
| Year | 年份 | |
| Paper (the `P1` field of a QID) | 卷別 | `Paper 1` 卷一 |
| Question number | 題號 | sequential number 順序編號 |
| Part (a) / shared block | 分題 (a) / 共用段落 | stem 題幹, sub-part 子分題 |
| Split / Combine (question parts) | 分拆 / 合併 | |
| Whole (archived whole-question image) | 原題全圖 | |
| Thumbnail | 縮圖 | |
| Present / Slide | 簡報 / 投影片 | |
| Spacing / Lines / Page break | 間距 / 行 / 分頁 | answer lines 答題線 |
| Numbering | 編號 | |
| My Files section (folder of generated files) | 分組 | not the paper "Section" (部分) |
| Share / Shared | 分享 / 共用 | Shared folder 共用資料夾 |
| Generate / Generation | 生成 | |
| Paper (exam paper) | 試卷 | |
| Generation Preset | 生成預設 | |
| Section heading | 分節標題 | |
| Info line | 資料行 | |
| Heading language | 標題語言 | |
| My Files | 我的檔案 | |
| File / Folder | 檔案 / 資料夾 | |
| Present mode / Viewer | 簡報模式 / 題目檢視器 | |
| Markup | 標註 | |
| Toolbox | 工具箱 | |

## Admin

| English | 中文 | Notes |
|---|---|---|
| Admin / Admin Panel | 管理 / 管理面板 | |
| Admin user | 管理員 | |
| User / Username / Password | 用戶 / 用戶名稱 / 密碼 | |
| Login / Logout | 登入 / 登出 | |
| Import / Export | 匯入 / 匯出 | Smart Import 智能匯入 |
| Upload / Download | 上載 / 下載 | |
| Restore point | 還原點 | |
| System Settings | 系統設定 | |
| Endpoint (LLM) | 端點 | |
| Prompt (AI) | 提示詞 | prompt variant 提示詞版本 |
| Model (AI) | 模型 | |
| Proofread / Transcribe (AI) | 校對 / 轉錄 | |
| Database | 資料庫 | |
| Health / Sync / Dry run | 健康檢查 / 同步 / 試行 | |
| Permission / Role | 權限 / 角色 | View Only 僅檢視 |
| Crop / Bounding box | 裁剪 / 框選範圍 | |
| Redact / Highlight | 遮蓋 / 螢光標示 | |
| Pen / Eraser / Undo / Redo | 筆 / 橡皮擦 / 復原 / 重做 | |

## Buttons and messages

| English | 中文 |
|---|---|
| Save / Saved | 儲存 / 已儲存 |
| Cancel | 取消 |
| Close | 關閉 |
| Delete | 刪除 |
| Edit / Rename | 編輯 / 重新命名 |
| Add / Create | 新增 / 建立 |
| Open | 開啟 |
| Apply | 套用 |
| Reset | 重設 |
| Discard | 放棄 |
| Overwrite | 覆蓋 |
| Error / Failed | 出錯 / 失敗 |
| Warning | 警告 |
| Access denied | 拒絕存取 |
| Both (bilingual) | 雙語 |
| English / 中文 | English / 中文 (language names are shown in their own language) |
| Loading… / Saving… | 載入中… / 儲存中… |
| Refresh / Retry | 重新整理 / 重試 |
| Copy / Copied | 複製 / 已複製 |
| Move | 移動 |
| Select all / Deselect all / Clear | 全選 / 取消全選 / 清除 |
| Back / Next / Previous | 返回 / 下一個 / 上一個 (wizard steps: 上一步 / 下一步) |
| Confirm / OK / Done | 確認 / 確定 / 完成 |
| Yes / No / None / All | 是 / 否 / 無 / 全部 |
| Optional / Required | 選填 / 必填 |
| Name / Type / Size / Date / Status / Actions | 名稱 / 類型 / 大小 / 日期 / 狀態 / 操作 |
| Details / Options / Advanced / Settings | 詳情 / 選項 / 進階 / 設定 |
| Preview / View / Show / Hide | 預覽 / 檢視 / 顯示 / 隱藏 |
| Enlarge / Zoom / Fit | 放大 / 縮放 / 符合大小 |
| Image / Text / Page | 圖片 / 文字 / 頁 |
| Something went wrong / Request failed | 發生錯誤 / 請求失敗 |
| Network error | 網絡錯誤 |
| Are you sure? | 確定嗎？ |
| No results / Nothing selected | 沒有結果 / 未選取任何項目 |

### One key, one Chinese string

A catalogue key has no context, so every use of the same English text gets the same Chinese. These short words were settled when the Phase 2 fragments were merged; reuse them, or pick a more specific English string when the meaning differs.

| English key | 中文 | Why |
|---|---|---|
| `Created` / `Updated` | 建立 / 更新 | Used both as date-column headers and as result-count badges |
| `Prev` / `Next` | 上一個 / 下一個 | Pagination, viewer question navigation and Smart Import compare share the key |
| `OK` | 通過 | Every current use is a check-status badge. A confirm button should say `Confirm` (確認) |
| `Review` | 檢閱 | Question Management button and PDF import status |
| `Duplicate` | 建立複本 | Endpoints and file browser |
| `Ready` | 已就緒 | |
| Render / Rendering… | 渲染 / 渲染中… | Not 算圖 or 繪製 (繪製 = draw a box) |
| Batch | 批量 | Not 批次 |
| `...` in English | `…` in Chinese | Every value uses the single ellipsis character, except inside code tokens copied verbatim (`[FIGURE: ...]`) |
