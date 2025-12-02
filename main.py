from fastapi import FastAPI, HTTPException
import gspread
from pydantic import BaseModel
from datetime import datetime

# --- 初始化 ---
app = FastAPI()

# 連接 Google Sheet (複製您之前的邏輯)
gc = gspread.service_account(filename='apbolclibrary-8678b7a5e3ac.json')
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

# --- 定義傳入資料的模型 ---
class ActionRequest(BaseModel):
    book_id: str
    student_id: str

# --- 2. 借書 API ---
@app.post("/borrow")
def borrow_book(req: ActionRequest):
    try:
        sh = get_db()
        books_sheet = sh.worksheet("Books")
        trans_sheet = sh.worksheet("Transactions")
        
        # A. 找書
        cell = books_sheet.find(req.book_id)
        if not cell:
            raise HTTPException(status_code=404, detail="找不到這本書")
            
        # B. 檢查狀態
        row_data = books_sheet.row_values(cell.row)
        # 假設欄位順序: [ID, ISBN, Title, Status, Holder, Next_In_Line...]
        # Status=3(第4欄), Holder=4(第5欄), Next_In_Line=5(第6欄) (index從0開始)
        
        current_status = row_data[3] if len(row_data) > 3 else "Unknown"
        next_in_line = row_data[5] if len(row_data) > 5 else ""
        
        # === 邏輯判斷 ===
        if current_status == "Borrowed":
            return {"success": False, "message": "這本書已被借走，請選擇排隊。", "can_queue": True}
            
        if current_status == "Reserved":
            # 如果是保留狀態，檢查是否為保留給該學生
            if req.student_id != next_in_line:
                return {"success": False, "message": f"抱歉，這本書目前保留給同學 {next_in_line}。"}
        
        # === 執行借書 ===
        # 不論是 Available 還是 Reserved (如果是保留本人)，都允許借出
        books_sheet.update_cell(cell.row, 4, "Borrowed")
        books_sheet.update_cell(cell.row, 5, req.student_id)
        books_sheet.update_cell(cell.row, 6, "") # 清空預約欄
        
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
            next_student = queue_row_data[2]
            
            # 3. 更新書籍狀態為 Reserved (保留中)
            # Status (第4欄) -> Reserved
            # Current_Holder (第5欄) -> 清空 (書在庫存，但保留)
            # Next_In_Line (第6欄) -> 填入排隊者 ID
            books_sheet.update_cell(cell.row, 4, "Reserved")
            books_sheet.update_cell(cell.row, 5, "")
            books_sheet.update_cell(cell.row, 6, next_student)
            
            # 4. 把這個人從排隊清單刪除 (以免重複排)
            queue_sheet.delete_rows(first_queue_cell.row)
            
            msg = f"還書成功！此書已保留給排隊同學：{next_student}"
            
        else:
            # === 沒人排隊 ===
            # 一樣變回 Available
            books_sheet.update_cell(cell.row, 4, "Available")
            books_sheet.update_cell(cell.row, 5, "")
            books_sheet.update_cell(cell.row, 6, "") # 清空預約欄
            
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)