import gspread

# 1. 設定：告訴程式金鑰檔在哪裡
# 這裡假設您的 json 檔名叫做 service_account.json，並且跟程式碼在同一層目錄
gc = gspread.service_account(filename='service_account.json')

# 2. 連線：填入您的 Google Sheet 網址
# 請將下方的網址換成您自己試算表的網址
SHEET_URL = 'https://docs.google.com/spreadsheets/d/1eXfZQTp7r9moUJ0vetuOvMsB0a91HRq6V994_0L-oEo/edit?gid=1945406050#gid=1945406050'

try:
    # 嘗試開啟試算表
    sh = gc.open_by_url(SHEET_URL)
    
    print(f"成功連線！試算表名稱為：{sh.title}")

    # 3. 測試寫入：在第一個分頁 (Sheet1) 的 A1 格子寫字
    worksheet = sh.sheet1
    worksheet.update_acell('J2', '連線成功！Hello Hector')
    
    print("寫入測試完成！請去 Google Sheet 看看 A1 格子有沒有變。")

except Exception as e:
    print(f"發生錯誤，請檢查設定：{e}")