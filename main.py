from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
import gspread
import os
import json
from pydantic import BaseModel
from datetime import datetime

# --- 初始化 ---
app = FastAPI()

# --- CORS 設定 ---
origins = [
    "http://localhost",
    "http://localhost:8000",
    "https://hector0929.github.io",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # 明確指定允許的來源，解決 CORS 問題
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 連接 Google Sheet
# 優先從環境變數讀取 (Render 上使用)，否則從檔案讀取 (本機開發使用)
if os.environ.get("GOOGLE_CREDENTIALS"):
    creds_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    gc = gspread.service_account_from_dict(creds_dict)
else:
    # 本機開發時使用檔案
    gc = gspread.service_account(filename='service_account.json')

SHEET_URL = 'https://docs.google.com/spreadsheets/d/1eXfZQTp7r9moUJ0vetuOvMsB0a91HRq6V994_0L-oEo/edit?gid=1945406050#gid=1945406050'

def get_db():
    """連線並回傳試算表物件"""
    return gc.open_by_url(SHEET_URL)

# --- 定義資料模型 (給前端看的) ---
class BookResponse(BaseModel):
    book_id: str
    title: str
    status: str
    message: str

# --- API 路由 (功能區) ---

@app.get("/")
def home():
    return {"message": "圖書管理系統 API 已啟動"}

@app.get("/scan/{book_id}")
def scan_book(book_id: str):
    """
    模擬掃描 QR Code。
    輸入 Book_ID，回傳這本書的狀態。
    """
    try:
        sh = get_db()
        worksheet = sh.worksheet("Books")
        
        # 1. 搜尋 Book_ID 在哪一行
        # gspread 的 find 會回傳一個 Cell 物件，包含 row 和 col
        cell = worksheet.find(book_id)
        
        if not cell:
            raise HTTPException(status_code=404, detail="找不到這本書，請確認 QR Code 是否正確")
            
        # 2. 讀取該行的資料 (Row Values)
        # 假設欄位順序是: ID, ISBN, Title, Status, Holder...
        row_data = worksheet.row_values(cell.row)
        
        # 安全起見，確保資料長度足夠 (避免欄位空的報錯)
        title = row_data[2] if len(row_data) > 2 else "未命名"
        status = row_data[3] if len(row_data) > 3 else "Unknown"

        # 3. 根據狀態回傳訊息
        msg = ""
        if status == "Available":
            msg = "這本書在庫，可以借閱！"
        elif status == "Borrowed":
            msg = "這本書被借走了，要排隊嗎？"
        
        return {
            "book_id": book_id,
            "title": title,
            "status": status,
            "message": msg
        }

    except gspread.exceptions.CellNotFound:
         # 這是 gspread 特有的找不到錯誤
         raise HTTPException(status_code=404, detail="資料庫中找不到此 ID")
    except Exception as e:
        return {"error": str(e)}


# 取得學生姓名 Helper
def get_student_name(student_id):
    try:
        sh = get_db()
        users_sheet = sh.worksheet("Users")
        cell = users_sheet.find(student_id)
        if cell:
            return users_sheet.cell(cell.row, 2).value
        return "Unknown"
    except:
        return "Unknown"

# --- 定義傳入資料的模型 ---
class ActionRequest(BaseModel):
    book_id: str
    student_id: str
    password: str = "" # Default to empty string for backward compatibility/optional cases if needed, but logic enforces it

# --- 2. 借書 API ---
@app.post("/borrow")
def borrow_book(req: ActionRequest):
    try:
        sh = get_db()
        books_sheet = sh.worksheet("Books")
        trans_sheet = sh.worksheet("Transactions")
        users_sheet = sh.worksheet("Users")
        
        # A. 驗證使用者密碼
        user_cell = users_sheet.find(req.student_id)
        if not user_cell:
             return {"success": False, "message": "找無此學生ID"}
        
        # Password assumed to be in Column 3 (Index 2)
        # 欄位: [Student_ID, Name, Password]
        user_row = users_sheet.row_values(user_cell.row)
        stored_password = str(user_row[2]) if len(user_row) > 2 else ""
        
        # 簡單去除空白，避免因為格式問題導致錯誤
        if str(req.password).strip() != stored_password.strip():
             return {"success": False, "message": "密碼錯誤，請重新輸入"}

        # B. 找書
        cell = books_sheet.find(req.book_id)
        if not cell:
            raise HTTPException(status_code=404, detail="找不到這本書")
            
        # C. 檢查狀態
        row_data = books_sheet.row_values(cell.row)
        # 欄位順序: 
        # 0:ID, 1:ISBN, 2:Title, 3:Status, 
        # 4:Current_Holder_ID, 5:Current_Holder_Name
        # 6:Next_In_Line_ID, 7:Next_In_Line_Name
        
        current_status = row_data[3] if len(row_data) > 3 else "Unknown"
        # Next_In_Line_ID 在第 7 欄 (index 6)
        next_in_line_id = row_data[6] if len(row_data) > 6 else ""
        
        # === 邏輯判斷 ===
        if current_status == "Borrowed":
            return {"success": False, "message": "這本書已被借走，請選擇排隊。", "can_queue": True}
            
        if current_status == "Reserved":
            # 如果是保留狀態，檢查是否為保留給該學生
            if req.student_id != next_in_line_id:
                return {"success": False, "message": f"抱歉，這本書目前保留給同學 {next_in_line_id}。"}
        
        # === 執行借書 ===
        student_name = get_student_name(req.student_id)

        # Update cells
        # Col 4: Status -> Borrowed
        # Col 5: Current_Holder_ID -> req.student_id
        # Col 6: Current_Holder_Name -> student_name
        # Col 7: Next_In_Line_ID -> Clear
        # Col 8: Next_In_Line_Name -> Clear
        
        books_sheet.update_cell(cell.row, 4, "Borrowed")
        books_sheet.update_cell(cell.row, 5, req.student_id)
        books_sheet.update_cell(cell.row, 6, student_name)
        books_sheet.update_cell(cell.row, 7, "") 
        books_sheet.update_cell(cell.row, 8, "") 
        
        # 寫入交易紀錄
        trans_sheet.append_row([
            str(datetime.now()), 
            "Borrow", 
            req.book_id, 
            req.student_id
        ])
        
        return {"success": True, "message": f"借閱成功！書名：{row_data[2]}"}

    except Exception as e:
        return {"success": False, "error": str(e)}

# --- 3. (修改版) 還書 API ---
@app.post("/return")
def return_book(req: ActionRequest):
    try:
        sh = get_db()
        books_sheet = sh.worksheet("Books")
        trans_sheet = sh.worksheet("Transactions")
        queue_sheet = sh.worksheet("Queue")
        
        # A. 找書
        cell = books_sheet.find(req.book_id)
        if not cell:
            raise HTTPException(status_code=404, detail="找不到這本書")
            
        # B. 檢查有沒有人在排隊？
        # findall 找出所有這本書的排隊紀錄
        queue_cells = queue_sheet.findall(req.book_id)
        
        if queue_cells:
            # === 有人排隊 ===
            # 1. 找出排第一位的人 (假設按照順序加入，Row 號碼最小的就是最早排的)
            first_queue_cell = min(queue_cells, key=lambda c: c.row)
            
            # 2. 讀取該行資料來取得學生 ID
            # Queue 欄位順序: Queue_ID, Book_ID, Student_ID, Time
            # Student_ID 在第 3 欄
            queue_row_data = queue_sheet.row_values(first_queue_cell.row)
            next_student_id = queue_row_data[2]
            next_student_name = get_student_name(next_student_id)
            
            # 3. 更新書籍狀態為 Reserved (保留中)
            # Col 4: Status -> Reserved
            # Col 5: Current_Holder_ID -> Clear
            # Col 6: Current_Holder_Name -> Clear
            # Col 7: Next_In_Line_ID -> next_student_id
            # Col 8: Next_In_Line_Name -> next_student_name
            
            books_sheet.update_cell(cell.row, 4, "Reserved")
            books_sheet.update_cell(cell.row, 5, "")
            books_sheet.update_cell(cell.row, 6, "")
            books_sheet.update_cell(cell.row, 7, next_student_id)
            books_sheet.update_cell(cell.row, 8, next_student_name)
            
            # 4. 把這個人從排隊清單刪除 (以免重複排)
            queue_sheet.delete_rows(first_queue_cell.row)
            
            msg = f"還書成功！此書已保留給排隊同學：{next_student_name} ({next_student_id})"
            
        else:
            # === 沒人排隊 ===
            # 一樣變回 Available
            # Clear all holder and next info
            books_sheet.update_cell(cell.row, 4, "Available")
            books_sheet.update_cell(cell.row, 5, "")
            books_sheet.update_cell(cell.row, 6, "")
            books_sheet.update_cell(cell.row, 7, "")
            books_sheet.update_cell(cell.row, 8, "")
            
            msg = "還書成功，謝謝！"

        # C. 寫入交易紀錄 (不變)
        trans_sheet.append_row([
            str(datetime.now()), 
            "Return", 
            req.book_id, 
            req.student_id
        ])
        
        return {"success": True, "message": msg}

    except Exception as e:
        return {"success": False, "error": str(e)}
    
# --- 4. 加入排隊 API ---
@app.post("/queue")
def join_queue(req: ActionRequest):
    try:
        sh = get_db()
        books_sheet = sh.worksheet("Books")
        queue_sheet = sh.worksheet("Queue")
        
        # A. 確認書本存在
        cell = books_sheet.find(req.book_id)
        if not cell:
            raise HTTPException(status_code=404, detail="找不到這本書")
            
        # B. (選用) 檢查是否已經在排隊了，避免重複排
        # 這裡為了效能，簡單檢查該書ID是否有該學生ID (若要嚴謹需遍歷)
        # 暫時先略過嚴謹檢查，直接加入
        
        # C. 寫入排隊清單
        # 格式: Queue_ID(用時間充當), Book_ID, Student_ID, Order_Time
        now_str = str(datetime.now())
        queue_sheet.append_row([
            f"Q-{int(datetime.now().timestamp())}", # 簡單的 ID
            req.book_id, 
            req.student_id, 
            now_str
        ])
        
        # D. 取得目前排隊人數 (讓同學知道前面幾個人)
        # findall 會回傳所有符合 book_id 的格子
        queue_count = len(queue_sheet.findall(req.book_id))
        
        return {
            "success": True, 
            "message": f"排隊成功！您目前是第 {queue_count} 順位。"
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# --- 5. 修改密碼 API ---
class ChangePasswordRequest(BaseModel):
    student_id: str
    old_password: str
    new_password: str

@app.post("/change_password")
def change_password(req: ChangePasswordRequest):
    try:
        sh = get_db()
        users_sheet = sh.worksheet("Users")
        
        # 1. 找使用者
        cell = users_sheet.find(req.student_id)
        if not cell:
            return {"success": False, "message": "找無此學生ID"}
            
        # 2. 驗證舊密碼
        # 假設欄位順序: [Student_ID, Name, Password] -> Index 0, 1, 2
        # 注意：使用 cell(row, col) 取值，col 是 1-based，所以 Password 是第 3 欄
        # 或者使用 row_values 取整行
        user_row_values = users_sheet.row_values(cell.row)
        current_password = str(user_row_values[2]) if len(user_row_values) > 2 else ""
        
        if str(req.old_password).strip() != current_password.strip():
            return {"success": False, "message": "舊密碼錯誤"}
            
        # 3. 更新密碼
        # update_cell(row, col, value) -> col 3 is Password
        users_sheet.update_cell(cell.row, 3, req.new_password)
        
        return {"success": True, "message": "密碼修改成功！"}

    except Exception as e:
        return {"success": False, "error": str(e)}

# --- 6. 註冊帳號 API ---
class RegisterRequest(BaseModel):
    student_id: str
    name: str
    password: str

@app.post("/register")
def register_user(req: RegisterRequest):
    try:
        sh = get_db()
        users_sheet = sh.worksheet("Users")
        
        # 1. 檢查帳號是否存在
        cell = users_sheet.find(req.student_id)
        if cell:
            return {"success": False, "message": "此會員編號日已存在"}
            
        # 2. 新增使用者
        # 欄位順序: [Student_ID, Name, Password]
        users_sheet.append_row([req.student_id, req.name, req.password])
        
        return {"success": True, "message": "註冊成功！"}

    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)